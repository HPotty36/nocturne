"""GitHub REST client for the queue worker: contents API reads and writes, and one-commit multi-file writes.

Standard library only. The worker and the PC helper talk to GitHub through this; tests point `api` at
tests/fake_github.py.
"""
import base64
import hashlib
import http.client
import json
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.github.com"
API_VERSION = "2022-11-28"
USER_AGENT = "nocturne-worker"
TIMEOUT = 60  # seconds for one request
BRANCH = "main"
ATTEMPTS = 3  # a commit is tried again from the start this many times when main moved underneath it
LOOKBACK = 20  # how many commits down from the head of main to look for our own commit after a lost answer
RETRY_DELAYS = (0.2, 0.5, 1.0)  # seconds before each repeat of a request whose connection dropped
CONFLICT_MESSAGE = "다른 곳에서 동시에 바뀌었어요. 새로고침 후 다시 해 주세요"
# Author and committer of every commit made through the API (the worker and the PC helper). Without them GitHub uses
# the account's profile name and email settings; this is the identity the repository's own commits use.
AUTHOR = {"name": "HPotty36", "email": "112685098+HPotty36@users.noreply.github.com"}

# This PC resets some loopback connections (WinError 10054) and sometimes cuts a response short. The same
# request goes through the second time, so these are repeated. An HTTP error status is an answer and is not.
_DROPPED = (ConnectionResetError, ConnectionAbortedError, BrokenPipeError,
            http.client.RemoteDisconnected, http.client.IncompleteRead)


class GitHubError(Exception):
    """GitHub said no, or could not be reached (status 0)."""

    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status


class Conflict(GitHubError):
    """A write with a stale or missing sha (the contents API answers 409 or 422)."""


class CommitError(GitHubError):
    """commit_files gave up: main kept moving."""


def dump_json(data):
    """JSON text as the queue files are written: Korean unescaped, 2-space indent, final newline."""
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _blob_sha(data):
    """The git blob sha of these bytes, which is the `sha` the contents API reports for a file holding them."""
    return hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()


def _dropped(err):
    return isinstance(err.reason if isinstance(err, urllib.error.URLError) else err, _DROPPED)


def _message(body):
    try:
        return str(json.loads(body)["message"])
    except (ValueError, KeyError, TypeError):
        return body.decode("utf-8", "replace").strip()[:200]


class GitHub:
    def __init__(self, token, *, api=API, author=AUTHOR):
        self.token = token
        self.api = api.rstrip("/")
        self.author = dict(author)  # {name, email}: author and committer of the commits this client makes

    # --- requests ---

    def _headers(self, accept, has_body):
        headers = {"Accept": accept, "X-GitHub-Api-Version": API_VERSION, "User-Agent": USER_AGENT}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if has_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _once(self, method, path, data, accept):
        request = urllib.request.Request(self.api + path, data=data, method=method,
                                         headers=self._headers(accept, data is not None))
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as err:  # before URLError: it is a subclass
            with err:  # close the error response, or Python warns about the unclosed file
                try:
                    return err.code, err.read()
                except (OSError, http.client.HTTPException):  # the connection died while the error body came
                    return err.code, b""

    def _request(self, method, path, body=None, *, accept="application/vnd.github+json"):
        """(status, body bytes, resent) of any HTTP answer. A dropped connection is repeated, and `resent` says an
        earlier try was lost: the server may have acted on it, so a repeated write can hear about its own effect.
        Other failures, and a connection that drops every time, raise GitHubError (status 0)."""
        data = json.dumps(body).encode("utf-8") if body is not None else None
        resent = False
        for delay in (*RETRY_DELAYS, None):
            try:
                status, raw = self._once(method, path, data, accept)
                return status, raw, resent
            except (OSError, http.client.HTTPException) as err:  # URLError is an OSError
                if delay is None or not _dropped(err):
                    reason = getattr(err, "reason", err)
                    raise GitHubError(f"GitHub에 연결하지 못했어요: {reason!r}", 0) from err
            resent = True
            time.sleep(delay)

    @staticmethod
    def _check(status, raw):
        if status >= 400:
            raise GitHubError(f"GitHub {status}: {_message(raw)}", status)

    def _json(self, method, path, body=None):
        """Parsed JSON of a successful answer; any error status raises GitHubError."""
        status, raw, _ = self._request(method, path, body)
        self._check(status, raw)
        return json.loads(raw) if raw else None

    def _signed(self):
        """The author and committer fields of a commit-making request."""
        return {"author": dict(self.author), "committer": dict(self.author)}

    @staticmethod
    def _contents_path(repo, path):
        return f"/repos/{repo}/contents/{urllib.parse.quote(path, safe='/')}"

    # --- files ---

    def list_dir(self, repo, path):
        """[{name, path, sha, type}] of a folder ("file" or "dir"); [] if it does not exist."""
        status, raw, _ = self._request("GET", self._contents_path(repo, path))
        if status == 404:
            return []
        self._check(status, raw)
        entries = json.loads(raw)
        if not isinstance(entries, list):
            raise GitHubError(f"{path}는 폴더가 아니에요", 0)
        return [{key: e[key] for key in ("name", "path", "sha", "type")} for e in entries]

    def get_json(self, repo, path, ref=None):
        """(parsed JSON, sha) of a file, read at `ref` (a branch or commit sha) if given; None if it does not exist."""
        url = self._contents_path(repo, path) + (f"?ref={urllib.parse.quote(ref, safe='')}" if ref else "")
        status, raw, _ = self._request("GET", url)
        if status == 404:
            return None
        self._check(status, raw)
        info = json.loads(raw)
        return json.loads(base64.b64decode(info["content"]).decode("utf-8")), info["sha"]

    def get_bytes(self, repo, path):
        status, raw, _ = self._request("GET", self._contents_path(repo, path), accept="application/vnd.github.raw")
        self._check(status, raw)
        return raw

    def put_json(self, repo, path, data, *, sha, message):
        """Write a JSON file with its current sha (None for a new file). Returns the new sha; a stale sha is a Conflict.

        If the connection dropped and the write was sent again, a 409 or 422 may be about our own first try, which
        landed. Then the file holds exactly what we wrote, and that is success, not a conflict.
        """
        text = dump_json(data).encode("utf-8")
        body = {"message": message, "content": base64.b64encode(text).decode("ascii"), **self._signed()}
        if sha is not None:
            body["sha"] = sha
        status, raw, resent = self._request("PUT", self._contents_path(repo, path), body)
        if status in (409, 422):
            if resent and self._sha_of(repo, path) == _blob_sha(text):
                return _blob_sha(text)
            raise Conflict(f"GitHub {status}: {_message(raw)}", status)
        self._check(status, raw)
        return json.loads(raw)["content"]["sha"]

    def _sha_of(self, repo, path):
        """The sha of the file now, or None if there is no such file."""
        status, raw, _ = self._request("GET", self._contents_path(repo, path))
        if status == 404:
            return None
        self._check(status, raw)
        info = json.loads(raw)
        return info.get("sha") if isinstance(info, dict) else None

    # --- one commit, many files ---

    def commit_files(self, repo, message, build):
        """Commit several file changes at once on main; returns the new commit's sha, or None if there was nothing to change.

        `build(read_json)` returns {path: change}: bytes or str is a new blob, None deletes the file, and
        {"sha": ...} reuses a blob that is already in the repository. `read_json(path)` reads a JSON file (None
        if missing) from the commit this attempt builds on. If main moved before the update, everything starts
        over, build included, on top of the new main.
        """
        for _ in range(ATTEMPTS):
            head = self._json("GET", f"/repos/{repo}/git/ref/heads/{BRANCH}")["object"]["sha"]
            base_tree = self._json("GET", f"/repos/{repo}/git/commits/{head}")["tree"]["sha"]

            def read_json(path):
                found = self.get_json(repo, path, ref=head)
                return found[0] if found else None

            changes = build(read_json)
            if not changes:
                return None
            tree = [self._tree_entry(repo, path, change) for path, change in changes.items()]
            tree_sha = self._json("POST", f"/repos/{repo}/git/trees", {"base_tree": base_tree, "tree": tree})["sha"]
            commit = self._json("POST", f"/repos/{repo}/git/commits",
                                {"message": message, "tree": tree_sha, "parents": [head], **self._signed()})["sha"]
            try:
                status, raw, resent = self._request("PATCH", f"/repos/{repo}/git/refs/heads/{BRANCH}", {"sha": commit, "force": False})
            except GitHubError:  # every try dropped; the first may still have landed
                status, raw, resent = 0, b"", True
            if resent and status in (0, 422) and self._in_main(repo, commit):
                return commit  # our own earlier try moved main; building again would publish twice
            if status in (0, 422):  # not a fast-forward: someone else committed first
                continue
            self._check(status, raw)
            return commit
        raise CommitError(CONFLICT_MESSAGE, 422)

    def _in_main(self, repo, sha):
        """Is `sha` the head of main, or one of its last LOOKBACK commits?"""
        todo, seen = [self._json("GET", f"/repos/{repo}/git/ref/heads/{BRANCH}")["object"]["sha"]], set()
        while todo and len(seen) < LOOKBACK:
            current = todo.pop(0)
            if current == sha:
                return True
            if current not in seen:
                seen.add(current)
                todo.extend(p["sha"] for p in self._json("GET", f"/repos/{repo}/git/commits/{current}")["parents"])
        return False

    def _tree_entry(self, repo, path, change):
        entry = {"path": path, "mode": "100644", "type": "blob"}
        if change is None:
            entry["sha"] = None
        elif isinstance(change, dict):
            entry["sha"] = change["sha"]
        elif isinstance(change, (bytes, str)):
            raw = change.encode("utf-8") if isinstance(change, str) else change
            blob = {"content": base64.b64encode(raw).decode("ascii"), "encoding": "base64"}
            entry["sha"] = self._json("POST", f"/repos/{repo}/git/blobs", blob)["sha"]
        else:
            raise TypeError(f"{path}: bytes, str, None, or {{'sha': ...}} expected, not {type(change).__name__}")
        return entry
