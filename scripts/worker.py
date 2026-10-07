"""Worker: takes uploads waiting in queue/, has the vision model draft them, and publishes each in one commit.

The PC helper (by="pc", the 12B model) and the GitHub Actions fallback (by="github", e4b) run the same code.
An item is claimed by rewriting its item.json with the file's sha, so of two workers only the first gets it.
Publishing moves the photo blobs out of the queue, adds the photo to src/photos.json and deletes the queue
folder, all in one commit, and only while the claim is still ours; so is recording a failure. A failure that
asking again cannot fix (the AI gave up, a photo file is missing or unreadable, the name is taken, an unexpected
error after the claim) is recorded as "failed" for a person to look at; trouble reaching GitHub is not, and the
stale rule hands the item on. When the PC's model gives up, the PC does not record a failure: it hands the item
back to waiting for GitHub's model and leaves it alone for RELEASE_SKIP_SECONDS.
The PC helper also lets the PC model rewrite text that another model wrote (upgrade_one), never touching a
field a person edited or the room.

    python scripts/worker.py --by github --check   names this worker may take, comma-separated
    python scripts/worker.py --by github --run     processes them all
Both use GITHUB_TOKEN and the model from NOCTURNE_MODEL. When GITHUB_OUTPUT is set, --check writes
names=<comma-separated names> (empty when there is nothing to take) and --run writes published=<count>.
--run exits 0 when every item was handled, 1 when at least one hit an error (photos it published still count).
"""
import argparse
import logging
import os
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

import curator
import photolib
import queue_items
from github_api import Conflict, GitHub, GitHubError, dump_json

REPO = "HPotty36/nocturne"
DATA = "src/photos.json"
NAME_TAKEN = "같은 이름의 사진이 이미 있어요"
FILES_MISSING = "대기열 사진 파일이 없어요"
UNREADABLE = "사진을 읽을 수 없어요"
CRASHED = "AI 처리 중 문제가 생겼어요"
RELEASE_SKIP_SECONDS = 1800  # how long the PC leaves an item alone after handing it back
_CLAIMED = {"pc": "PC가 맡음", "github": "GitHub가 맡음"}
_CLAIM_KEYS = ("status", "by", "claimed_at")  # these tell one claim (or its failure record) from another

log = logging.getLogger("nocturne.worker")


def _item_path(name):
    return f"queue/{name}/item.json"


def _same_claim(found, item):
    return isinstance(found, dict) and all(found.get(key) == item[key] for key in _CLAIM_KEYS)


def _find(data, file):
    """The photo entry for `file`, or None if it is not on show."""
    try:
        return photolib.find_photo(data, file)[1]
    except photolib.PhotoError:
        return None


def _usable(item, now, by):
    """Can a worker judge this item.json at all: an object whose status and times can be read?"""
    if not isinstance(item, dict):
        return False
    try:
        queue_items.can_take(item, now, by)
        queue_items.parse_iso(item["uploaded_at"])  # the order the queue is taken in
    except (KeyError, TypeError, ValueError):
        return False
    return True


class Worker:
    def __init__(self, gh, *, by, model, workdir, drafter=curator.draft,
                 now_fn=lambda: datetime.now(timezone.utc), pc_model=curator.DEFAULT_MODEL):
        self.gh, self.by, self.model, self.workdir = gh, by, model, Path(workdir)
        self.drafter, self.now_fn, self.pc_model = drafter, now_fn, pc_model
        self.skip = set()  # photos whose upgrade failed in this round; the PC helper clears it for the next round
        self.released = {}  # queue items this worker handed back after its AI gave up -> when; left alone for a while
        self._items = {}   # queue folder -> (folder sha, its item.json or None), so an unchanged item is not read again

    # --- the queue ---

    def takeable(self):
        """Names of the queue items this worker may claim now, oldest upload first.

        An item.json that cannot be read (not JSON, not an object, no readable status or times) is skipped and logged
        once; the admin page shows it as "확인할 수 없는 항목", to be thrown away there.
        """
        folders = {e["name"]: e["sha"] for e in self.gh.list_dir(REPO, "queue") if e["type"] == "dir"}
        for gone in self._items.keys() - folders.keys():
            del self._items[gone]
        now, found = self.now_fn(), []
        for name, when in list(self.released.items()):
            if (now - when).total_seconds() >= RELEASE_SKIP_SECONDS:
                del self.released[name]
        for name, sha in folders.items():
            if name not in self._items or self._items[name][0] != sha:
                item = self._read_item(name, now)
                if item is False:
                    log.warning("대기열: queue/%s/item.json을 읽을 수 없어서 건너뛰어요 (관리 페이지에서 버릴 수 있어요)", name)
                self._items[name] = (sha, item or None)
            item = self._items[name][1]
            if item and name not in self.released and queue_items.can_take(item, now, self.by):
                found.append((item["uploaded_at"], name))
        return [name for _, name in sorted(found)]

    def _read_item(self, name, now):
        """item.json of a queue folder; None when there is none, False when it cannot be read or judged."""
        try:
            got = self.gh.get_json(REPO, _item_path(name))
        except ValueError:  # not UTF-8 JSON; a GitHubError is not a ValueError and passes
            return False
        if got is None:
            return None
        return got[0] if _usable(got[0], now, self.by) else False

    def process(self, name):
        """Claim one queue item, draft it and publish it: "published", "failed" (recorded in item.json) or "skipped".

        Once the item is claimed, an unexpected error (a malformed model reply, a bug) is logged with its traceback and
        recorded as a failure: left claimed, the item would be claimed again after STALE_SECONDS and fail the same
        way forever. Trouble with GitHub is raised instead; the stale rule hands the item on.
        """
        try:
            found = self.gh.get_json(REPO, _item_path(name))
        except ValueError:  # not JSON: nothing a worker can take
            return "skipped"
        now = self.now_fn()
        if found is None or not _usable(found[0], now, self.by) or not queue_items.can_take(found[0], now, self.by):
            return "skipped"
        claimed = queue_items.claim(found[0], self.by, now)
        if not self._claim(name, claimed, found[1]):
            return "skipped"
        try:
            return self._publish(name, claimed)
        except GitHubError:
            raise
        except Exception:
            log.exception("대기열: %s 처리 중 오류", name)
            return self._fail(name, claimed, CRASHED)

    def _publish(self, name, claimed):
        """Draft a claimed item and publish it in one commit (or record why not)."""
        redraft = claimed["kind"] == "redraft"
        blobs = {e["name"]: e["sha"] for e in self.gh.list_dir(REPO, f"queue/{name}") if e["type"] == "file"}
        if not redraft and not {"full.jpg", "thumb.jpg"} <= blobs.keys():
            return self._fail(name, claimed, FILES_MISSING)
        data = self.gh.get_json(REPO, DATA)[0]
        asked = _find(data, claimed["file"]) if redraft else None  # the texts the redraft request replaces
        try:
            if redraft:
                draft = self._draft(f"photos/thumb/{claimed['file']}.jpg", name, data, None, self.model)
            else:
                draft = self._draft(f"queue/{name}/thumb.jpg", name, data, claimed["grayscale"], self.model)
        except curator.CuratorError as err:
            if self.by == "pc":  # GitHub's model may manage it; only a GitHub failure is left for a person
                return self._release(name, claimed, str(err))
            return self._fail(name, claimed, str(err))
        except OSError:  # Pillow cannot read the picture
            return self._fail(name, claimed, UNREADABLE)
        except GitHubError as err:
            if err.status != 404:
                raise  # trouble reaching GitHub passes; the stale rule hands the item on
            if not redraft:
                return self._fail(name, claimed, FILES_MISSING)
            draft = None  # the photo was taken down meanwhile; only its queue entry is left to remove

        def build(read_json):
            if not _same_claim(read_json(_item_path(name)), claimed):
                return {}  # no longer ours: taken over after STALE_SECONDS, or already gone
            current = read_json(DATA)
            changes = {f"queue/{name}/{file}": None for file in blobs}
            if not redraft:
                if name in photolib.listed(current):
                    raise photolib.PhotoError(NAME_TAKEN)
                entry = {"file": name, "title": draft["title"], "alt": draft["alt"]}
                entry.update({key: claimed[key] for key in ("date", "camera") if claimed.get(key)})
                entry["ai"] = photolib.ai_entry(draft["model"], draft["confidence"])
                photolib.add_photo(current, draft["room"], entry)  # PhotoError if the room is gone
                changes[f"photos/full/{name}.jpg"] = {"sha": blobs["full.jpg"]}
                changes[f"photos/thumb/{name}.jpg"] = {"sha": blobs["thumb.jpg"]}
            else:
                photo = _find(current, claimed["file"]) if draft and asked else None
                # Only a text that is still the one the request was made for is replaced; one a person changed
                # meanwhile is theirs, so it is not replaced and not marked as AI text.
                fields = [f for f in photolib.AI_FIELDS if photo and photo.get(f) == asked.get(f)]
                if not fields:
                    return changes  # taken down, or both texts rewritten by a person: only the queue entry goes
                if photo.get("ai"):
                    photo["ai"]["fields"] = []
                photolib.apply_ai_text(photo, draft, draft["model"], fields)
            changes[DATA] = photolib.dumps_data(current)
            return changes

        if redraft:
            message = f"AI 설명 다시 쓰기: {draft['title'] if draft else claimed['file']}"
        else:
            message = f"사진 게시: {draft['title']}"
        try:
            published = self.gh.commit_files(REPO, message, build)
        except photolib.PhotoError as err:  # the name is taken, or the drafted room is gone
            return self._fail(name, claimed, str(err))
        return "published" if published else "skipped"

    def _claim(self, name, claimed, sha):
        """Write our claim over item.json's `sha`, so that any change since makes it fail. True if it is ours.

        When every try of the write lost its connection, it may still have landed: the file is read back, and if it
        holds our claim the item is ours.
        """
        path = _item_path(name)
        try:
            self.gh.put_json(REPO, path, claimed, sha=sha, message=f"대기열: {name} {_CLAIMED[self.by]}")
            return True
        except Conflict:
            return False
        except GitHubError as err:
            if err.status != 0:
                raise
        found = self.gh.get_json(REPO, path)
        return bool(found) and _same_claim(found[0], claimed)

    def _fail(self, name, claimed, error):
        """Record the failure in item.json, in a commit made only while the item still holds our claim.

        A contents write with the claim's sha is not used: if another worker has published the item meanwhile,
        it might bring back a lone item.json.
        """
        path = _item_path(name)

        def build(read_json):
            if not _same_claim(read_json(path), claimed):
                return {}
            return {path: dump_json(queue_items.fail(claimed, error))}

        if self.gh.commit_files(REPO, f"대기열: {name} AI 실패", build) is None:
            log.warning("대기열: %s 실패를 기록하지 않았어요 (다른 쪽이 맡았거나 이미 없음): %s", name, error)
        return "failed"

    def _release(self, name, claimed, note):
        """Hand the item back to waiting (`note` kept in error) in a commit made only while it holds our claim, and
        leave it alone for RELEASE_SKIP_SECONDS so the other worker gets it. "released", or "skipped" if it was no
        longer ours. The message says 다시 시도, which starts draft.yml."""
        path = _item_path(name)

        def build(read_json):
            if not _same_claim(read_json(path), claimed):
                return {}
            return {path: dump_json(queue_items.release(claimed, note))}

        if self.gh.commit_files(REPO, f"대기열: {name} 다시 시도 (PC AI 실패)", build) is None:
            log.warning("대기열: %s 돌려놓지 않았어요 (다른 쪽이 맡았거나 이미 없음): %s", name, note)
            return "skipped"
        self.released[name] = self.now_fn()
        return "released"

    def _draft(self, source, stem, data, grayscale, model):
        """Draft from the repository image `source`, copied to workdir/<stem>.jpg while the model looks at it.
        grayscale None: judge it from the image."""
        raw = self.gh.get_bytes(REPO, source)
        self.workdir.mkdir(parents=True, exist_ok=True)
        image = self.workdir / f"{stem}.jpg"
        try:
            image.write_bytes(raw)
            if grayscale is None:
                with Image.open(image) as im:
                    grayscale = photolib.is_grayscale(im)
            return self.drafter(image, data, grayscale, model=model)
        finally:
            image.unlink(missing_ok=True)

    # --- upgrades: the PC model rewrites what another model wrote ---

    def upgrade_one(self):
        """Rewrite the AI text of the first photo that needs it: ("upgraded" | "failed" | "changed", file), or None.

        Only the fields still listed in ai.fields when the commit is made are replaced; the room and the confidence
        stay. "changed": by then a person had edited the photo (or taken it down), so nothing was written.
        """
        data = self.gh.get_json(REPO, DATA)[0]
        target = next((photo for room in data["rooms"] for photo in room["photos"]
                       if photolib.needs_upgrade(photo, self.pc_model) and photo["file"] not in self.skip), None)
        if target is None:
            return None
        file = target["file"]
        try:
            draft = self._draft(f"photos/thumb/{file}.jpg", file, data, None, self.pc_model)
        except (curator.CuratorError, GitHubError, OSError) as err:
            if isinstance(err, GitHubError) and err.status != 404:
                raise
            log.info("다듬기 실패 %s: %s", file, err)
            self.skip.add(file)
            return ("failed", file)

        def build(read_json):
            current = read_json(DATA)
            photo = _find(current, file)
            if photo is None or not photolib.needs_upgrade(photo, self.pc_model):
                return {}
            photolib.apply_ai_text(photo, draft, draft["model"], list(photo["ai"]["fields"]))
            return {DATA: photolib.dumps_data(current)}

        title = draft["title"] if "title" in target["ai"]["fields"] else target["title"]
        if self.gh.commit_files(REPO, f"AI 설명 개선: {title}", build) is None:
            return ("changed", file)
        return ("upgraded", file)


# --- command line, for GitHub Actions -----------------------------------------------------------------------

def _set_output(**values):
    """Hand values to later workflow steps (steps.<id>.outputs.<key>) when running in GitHub Actions."""
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.writelines(f"{key}={value}\n" for key, value in values.items())


def main(argv=None):
    """--check: print the names this worker may take, comma-separated, and write names=<them> (empty when there
    are none) to GITHUB_OUTPUT. --run: process them all and write published=<count>, even when something crashed
    on the way. Exit 0 when every item was handled, 1 when at least one hit an error; published photos still count.
    """
    parser = argparse.ArgumentParser(description="대기열의 사진을 AI 초안으로 바로 게시해요 (GitHub Actions용).")
    parser.add_argument("--by", choices=("pc", "github"), required=True, help="맡는 쪽")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true", help="맡을 수 있는 항목 이름을 쉼표로 출력")
    action.add_argument("--run", action="store_true", help="맡을 수 있는 항목을 모두 처리")
    args = parser.parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        parser.error("GITHUB_TOKEN이 없어요")
    with tempfile.TemporaryDirectory(prefix="nocturne-") as tmp:
        worker = Worker(GitHub(token), by=args.by, model=curator.model_name(), workdir=tmp)
        names = worker.takeable()
        if args.check:
            print(",".join(names))
            _set_output(names=",".join(names))
            return 0
        published, broken = 0, False
        try:
            for name in names:
                try:
                    result = worker.process(name)
                except GitHubError as err:  # the item waits for the stale rule; the others still get their turn
                    print(f"error: {name}: {err}", file=sys.stderr)
                    broken = True
                    continue
                except Exception as err:  # any other crash that got out of process: the others still get their turn
                    print(f"error: {name}: {type(err).__name__}: {err}", file=sys.stderr)
                    traceback.print_exc()
                    broken = True
                    continue
                print(f"{result}: {name}")
                published += result == "published"
        finally:
            _set_output(published=published)  # the site rebuild step needs it even after a crash
        return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
