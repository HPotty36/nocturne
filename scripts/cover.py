"""Cover: Claude picks the photo the site opens with, and where it is cropped (draft.yml's 대표 사진 고르기 step).

    python scripts/cover.py          (GITHUB_TOKEN and ANTHROPIC_API_KEY)

It reads src/photos.json from GitHub and decides (plan) whether there is anything to do:
- "pick": nobody pinned the cover (no cover_pick, or {"by": "ai"}) and the photos on show changed since the AI last
  picked (cover_pick.seen is not their photolib.fingerprint), or the cover is not on show. Claude sees every photo on
  show (MAX_PHOTOS at most) and names the cover and its focus.
- "focus": a person pinned the cover on the admin page ({"by": "person"}) and it has no cover_focus yet. Claude sees
  only that photo and says where to crop it; the photo stays.
- None: nothing to do; the step ends after one read.
Each photo goes to Claude as a copy of its thumbnail (already free of metadata) shrunk to EDGE px. The result is one
commit of photos.json, made only while the fresh photos.json still calls for the same thing (a person who pinned a
cover meanwhile wins). When GITHUB_OUTPUT is set it gets changed=1 if the cover or its focus changed (draft.yml then
rebuilds the site), else changed=0.

Without ANTHROPIC_API_KEY it says so and does nothing. When Claude cannot answer, a ::warning line names why and the
cover stays; the next run tries again. Exit 1 only for another error (GitHub, a bug), with its traceback.
"""
import io
import os
import sys
import traceback

from PIL import Image

import curator
import photolib
from github_api import GitHub
from worker import DATA, REPO, _command_data, _set_output

MAX_PHOTOS = 100  # photos shown to Claude at most (the cover always among them)
EDGE = 512  # long edge of the copies Claude sees
QUALITY = 80
NO_KEY = "Claude API 키가 없어서 대표 사진을 고르지 않아요"
WARNING = "대표 사진"  # the title of the ::warning line on the run's page


def plan(data):
    """"pick", "focus" or None: what the cover needs, judged from photos.json (see the module docstring). A person's
    pin on a photo that is no longer on show counts for nothing."""
    on_show = photolib.listed(data)
    if not on_show:
        return None
    pick, cover = data.get("cover_pick"), data.get("cover")
    by = pick.get("by") if isinstance(pick, dict) else None
    if cover not in on_show:
        return "pick"
    if by == "person":
        return "focus" if "cover_focus" not in data else None
    if by != "ai" or pick.get("seen") != photolib.fingerprint(data):
        return "pick"
    return None


def candidates(data, limit=MAX_PHOTOS):
    """[(room, photo)] that Claude is shown, in page order: every photo on show, or when there are more than `limit`,
    the cover and the first ones before it."""
    every = [(room, photo) for room in data["rooms"] for photo in room["photos"]]
    if len(every) <= limit:
        return every
    keep = {photo["file"] for _, photo in every[:limit - 1]} | {data.get("cover")}
    return [(room, photo) for room, photo in every if photo["file"] in keep][:limit]


def small_copy(raw):
    """A JPEG of `raw` with its long edge at EDGE px and nothing but the picture in it (no EXIF, no colour profile)."""
    with Image.open(io.BytesIO(raw)) as im:
        copy = im.convert("RGB")
    copy.thumbnail((EDGE, EDGE), Image.LANCZOS)
    copy.info.clear()
    out = io.BytesIO()
    copy.save(out, "JPEG", quality=QUALITY, optimize=True)
    return out.getvalue()


def run(gh, picker=None, model=None):
    """Do what plan() says: {"result": "picked" | "focused" | "skipped" | None, "changed": bool, "file": the cover
    Claude named or None}. "skipped": photos.json changed meanwhile so that the answer no longer fits; nothing written.
    A CuratorError from the picker passes to the caller."""
    picker = picker or curator.claude_pick_cover
    found = gh.get_json(REPO, DATA)
    data = found[0] if found else None
    todo = plan(data) if data else None
    if todo is None:
        return {"result": None, "changed": False, "file": None}
    shown = [photolib.find_photo(data, data["cover"])] if todo == "focus" else candidates(data)
    pictures = [{"file": photo["file"], "title": photo["title"], "room": room["name"],
                 "image": small_copy(gh.get_bytes(REPO, f"photos/thumb/{photo['file']}.jpg"))} for room, photo in shown]
    answer = picker(pictures, data.get("cover"), focus_only=todo == "focus", model=model)
    seen = photolib.fingerprint(data)  # what Claude was shown: a photo that lands meanwhile is picked up next time
    changed = []

    def build(read_json):
        fresh = read_json(DATA)
        if fresh is None or plan(fresh) != todo or answer["file"] not in photolib.listed(fresh):
            return {}
        if todo == "focus" and fresh.get("cover") != data["cover"]:
            return {}
        before = (fresh.get("cover"), fresh.get("cover_focus"))
        fresh["cover"], fresh["cover_focus"] = answer["file"], answer["focus"]
        if todo == "pick":
            fresh["cover_pick"] = {"by": "ai", "model": answer["model"], "seen": seen}
        changed[:] = [before != (fresh["cover"], fresh["cover_focus"])]
        return {DATA: photolib.dumps_data(fresh)}

    title = photolib.find_photo(data, answer["file"])[1]["title"]
    message = f"대표 사진: {title} (AI)" if todo == "pick" else f"대표 사진 중심: {title} (AI)"
    if gh.commit_files(REPO, message, build) is None:
        return {"result": "skipped", "changed": False, "file": answer["file"]}
    return {"result": "picked" if todo == "pick" else "focused", "changed": changed[0], "file": answer["file"]}


def main(argv=None):
    """Run once for draft.yml; see the module docstring. argv is not read (there are no options)."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN이 없어요", file=sys.stderr)
        return 2
    if not os.environ.get("ANTHROPIC_API_KEY"):  # not set, or empty: a secret that does not exist reads as ""
        print(NO_KEY)
        _set_output(changed=0)
        return 0
    try:
        result = run(GitHub(token), model=curator.claude_model_name())
    except curator.CuratorError as err:
        print(f"::warning title={WARNING}::{_command_data(str(err))}")
        _set_output(changed=0)
        return 0
    except Exception:
        traceback.print_exc()
        _set_output(changed=0)
        return 1
    _set_output(changed=int(result["changed"]))
    print(f"{result['result']}: {result['file']}" if result["result"] else "대표 사진: 할 일 없음")
    return 0


if __name__ == "__main__":
    sys.exit(main())
