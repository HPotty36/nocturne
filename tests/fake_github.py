"""A fake GitHub for tests: the slice of the REST API that Nocturne's clients use, served from memory.

The Python client (scripts/github_api.py) uses it in-process; the JavaScript client and the admin page
use it over HTTP:  py tests\\fake_github.py [--port N] [--seed] [--token T]
It prints its URL on the first line (--port 0 picks a free port) and runs until stopped.

Standard library only. The JSON shapes follow the real API, so a client written against the GitHub docs
works here: the contents API (file and directory JSON, base64 content, git blob / tree shas), git/ref,
git/commits, git/blobs, git/trees (base_tree, sha: null deletes), PATCH git/refs (422 unless fast-forward),
and actions/workflows/{file}/runs.

A repo is a set of git objects: blobs by sha, trees as {path: blob sha} snapshots, commits that point at a
snapshot. `main` is the only branch. The fake is stricter than GitHub in a few places, so clients
cannot lean on leniency: tree entries must be 100644 blobs given by sha, and deleting a path that is not
in the tree is a 422.
"""
import argparse
import base64
import binascii
import hashlib
import itertools
import json
import re
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
SEED_REPO = "HPotty36/nocturne"
MAIN = "heads/main"
DOCS = "https://docs.github.com/rest"
CORS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Authorization, Content-Type, Accept, X-GitHub-Api-Version",
    "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
}
_RAW_ACCEPT = re.compile(r"application/vnd\.github(\.v3)?\.raw")
_FIRST_COMMIT_TIME = 1_760_000_000  # fake commit times count up from here, one second per commit
_ACCOUNT = {"name": "Fake", "email": "fake@example.test"}  # who a commit is by when the request names nobody


# --- git hashing -------------------------------------------------------------------------------

def blob_sha(data):
    return hashlib.sha1(b"blob %d\x00" % len(data) + data).hexdigest()


def _tree_object(node):
    entries = []
    for name, child in node.items():
        if isinstance(child, dict):
            entries.append((name + "/", b"40000 " + name.encode() + b"\x00" + bytes.fromhex(_tree_object(child))))
        else:
            entries.append((name, b"100644 " + name.encode() + b"\x00" + bytes.fromhex(child)))
    body = b"".join(raw for _, raw in sorted(entries, key=lambda e: e[0].encode()))
    return hashlib.sha1(b"tree %d\x00" % len(body) + body).hexdigest()


def tree_sha(files):
    """Git tree sha of a flat {path: blob sha} snapshot, hashed as nested trees the way git does."""
    root = {}
    for path, sha in files.items():
        *folders, name = path.split("/")
        node = root
        for folder in folders:
            node = node.setdefault(folder, {})
        node[name] = sha
    return _tree_object(root)


def _wrapped_base64(data):
    """base64 in lines of 60 characters with a closing newline, as the contents API sends it."""
    text = base64.b64encode(data).decode("ascii")
    return "".join(text[i:i + 60] + "\n" for i in range(0, len(text), 60))


def _decode_base64(text):
    """Strict base64, whitespace (the line breaks GitHub puts in) ignored; anything else is a 422 as on GitHub."""
    try:
        return base64.b64decode(re.sub(r"\s", "", text), validate=True)
    except (binascii.Error, ValueError):
        raise _HttpError(422, "content is not valid Base64") from None


def _check_paths(files):
    """No path may be both a file and a folder."""
    for path in files:
        parts = path.split("/")
        if "" in parts:
            raise _HttpError(422, f"invalid path {path!r}")
        for i in range(1, len(parts)):
            if "/".join(parts[:i]) in files:
                raise _HttpError(422, f"{'/'.join(parts[:i])} is both a file and a folder")


def _entries(files, prefix):
    """Direct children of the folder `prefix` ("" or "a/b/") as contents-API entries, in git order."""
    kinds = {}
    for path in files:
        if path.startswith(prefix):
            name, _, rest = path[len(prefix):].partition("/")
            kinds[name] = "dir" if rest else "file"
    out = []
    for name, kind in kinds.items():
        full = prefix + name
        if kind == "file":
            out.append({"name": name, "path": full, "sha": files[full], "type": "file"})
        else:
            below = {p[len(full) + 1:]: s for p, s in files.items() if p.startswith(full + "/")}
            out.append({"name": name, "path": full, "sha": tree_sha(below), "type": "dir"})
    return sorted(out, key=lambda e: (e["name"] + ("/" if e["type"] == "dir" else "")).encode())


class _HttpError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


def _error_body(status, message):
    return {"message": message, "documentation_url": DOCS, "status": str(status)}


def _identity(data, role):
    """data[role] ("author" or "committer") as {name, email}, None when absent; anything else is a 422 as on GitHub."""
    if role not in data:
        return None
    who = data[role]
    if not (isinstance(who, dict) and isinstance(who.get("name"), str) and who["name"] and isinstance(who.get("email"), str)):
        raise _HttpError(422, f'Invalid request.\n\n"{role}" needs a "name" and an "email".')
    return {"name": who["name"], "email": who["email"]}


# --- repository state ------------------------------------------------------------------------

class _Repo:
    def __init__(self):
        self.blobs = {}    # blob sha -> bytes
        self.trees = {}    # tree sha -> {path: blob sha}
        self.commits = {}  # commit sha -> {"tree", "parents", "message", "time"}
        self.refs = {}     # "heads/main" -> commit sha

    def add_blob(self, data):
        sha = blob_sha(data)
        self.blobs[sha] = data
        return sha

    def add_tree(self, files):
        sha = tree_sha(files)
        self.trees[sha] = dict(files)
        return sha

    def head(self):
        return self.refs.get(MAIN)

    def snapshot(self, commit_sha):
        return self.trees[self.commits[commit_sha]["tree"]]

    def is_ancestor(self, ancestor, descendant):
        seen, todo = set(), [descendant]
        while todo:
            sha = todo.pop()
            if sha == ancestor:
                return True
            if sha not in seen:
                seen.add(sha)
                todo.extend(self.commits[sha]["parents"])
        return False


class FakeGitHub:
    def __init__(self, token="test-token"):
        self.token = token
        self.url = None
        self.requests = []  # (method, path without query) of every /repos request; OPTIONS and /_fake are not logged
        self._repos = {}
        self._failures = []  # [method, path prefix, status, times left]
        self._runs = {}      # (repo, workflow file) -> run
        self._clock = itertools.count(_FIRST_COMMIT_TIME)
        self._lock = threading.RLock()
        self._server = None
        self._thread = None

    # --- running it ---

    def start(self, port=0):
        self._server = _Server(("127.0.0.1", port), _Handler)
        self._server.fake = self
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self._thread.start()

    def stop(self):
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()
        self._server = self._thread = None

    # --- seeding and inspecting from tests ---

    def put_files(self, repo, files):
        """One commit that sets every path in `files` ({path: bytes}); the repo is created by its first commit."""
        return self._commit(repo, files, "fake commit")

    def put_file(self, repo, path, data):
        self.put_files(repo, {path: data})

    def delete_file(self, repo, path):
        with self._lock:
            if self.read_file(repo, path) is None:
                raise FileNotFoundError(path)
            self._commit(repo, {path: None}, "fake commit")

    def read_file(self, repo, path):
        with self._lock:
            state = self._repos.get(repo)
            sha = state.snapshot(state.head()).get(path) if state else None
            return state.blobs[sha] if sha else None

    def files(self, repo):
        with self._lock:
            state = self._repos.get(repo)
            return {p: state.blobs[s] for p, s in state.snapshot(state.head()).items()} if state else {}

    def last_commits(self, repo, count):
        """[{message, author, committer}] of the last `count` commits on main, newest first (identities as {name, email})."""
        with self._lock:
            state, out = self._repos[repo], []
            sha = state.head()
            while sha and len(out) < count:
                commit = state.commits[sha]
                out.append({key: commit[key] for key in ("message", "author", "committer")})
                sha = commit["parents"][0] if commit["parents"] else None
            return out

    def commits(self, repo):
        """Number of commits on main."""
        with self._lock:
            state = self._repos.get(repo)
            sha, count = (state.head() if state else None), 0
            while sha:
                count += 1
                parents = state.commits[sha]["parents"]
                sha = parents[0] if parents else None
            return count

    def fail_next(self, method, path_prefix, status, times=1):
        """The next `times` requests with this method and a path starting with `path_prefix` get `status`."""
        with self._lock:
            self._failures.append([method.upper(), path_prefix, status, times])

    def set_run(self, repo, workflow, run):
        """The workflow run that actions/workflows/<workflow>/runs reports."""
        with self._lock:
            self._runs[(repo, workflow)] = run

    # --- commits ---

    def _store_commit(self, state, tree, parents, message, author=None, committer=None):
        """A commit object; author and committer ({name, email}) are the account's when not given."""
        stamp = next(self._clock)
        author, committer = author or dict(_ACCOUNT), committer or dict(_ACCOUNT)
        line = lambda who: f"{who['name']} <{who['email']}> {stamp} +0000"
        text = "\n".join([f"tree {tree}", *[f"parent {p}" for p in parents], f"author {line(author)}",
                          f"committer {line(committer)}", "", message])
        body = text.encode("utf-8")
        sha = hashlib.sha1(b"commit %d\x00" % len(body) + body).hexdigest()
        state.commits[sha] = {"tree": tree, "parents": list(parents), "message": message, "time": stamp,
                              "author": author, "committer": committer}
        return sha

    def _commit(self, repo, changes, message, author=None, committer=None):
        """Commit on main: {path: bytes} sets, {path: None} deletes. Returns the new commit's sha."""
        with self._lock:
            state = self._repos.setdefault(repo, _Repo())
            head = state.head()
            files = dict(state.snapshot(head)) if head else {}
            for path, data in changes.items():
                if data is None:
                    files.pop(path, None)
                else:
                    files[path] = state.add_blob(data)
            try:
                _check_paths(files)
            except _HttpError as err:
                raise ValueError(err.message) from None
            sha = self._store_commit(state, state.add_tree(files), [head] if head else [], message, author, committer)
            state.refs[MAIN] = sha
            return sha

    # --- one HTTP request ---

    def handle(self, method, target, headers, body):
        """(status, payload) for one request, without a socket: `target` is the path with its query string, `headers`
        a mapping, `body` bytes. The payload is a dict, a list, or bytes (a raw file). OPTIONS never gets here."""
        url = urlsplit(target)
        path, query = unquote(url.path), parse_qs(url.query)
        try:
            if path.startswith("/_fake/"):
                return self._control(method, path, body)
            if not path.startswith("/repos/"):
                raise _HttpError(404, f"Not Found: the fake has no route for {method} {path}")
            with self._lock:
                self.requests.append((method, path))
                if not self._authorized(headers):
                    raise _HttpError(401, "Bad credentials")
                for rule in self._failures:
                    if rule[0] == method and path.startswith(rule[1]):
                        rule[3] -= 1
                        if rule[3] <= 0:
                            self._failures.remove(rule)
                        raise _HttpError(rule[2], "Injected failure (fail_next)")
                return self._route(method, path, query, headers, body)
        except _HttpError as err:
            return err.status, _error_body(err.status, err.message)
        except Exception as err:  # a bug in the fake: tell the client and show it here
            traceback.print_exc()
            return 500, _error_body(500, f"fake server error: {err!r}")

    def _authorized(self, headers):
        scheme, _, token = headers.get("Authorization", "").partition(" ")
        return scheme.lower() in ("bearer", "token") and token.strip() != "" and token.strip() == self.token

    @staticmethod
    def _json(body):
        try:
            data = json.loads(body.decode("utf-8")) if body else {}
        except ValueError:
            raise _HttpError(400, "Problems parsing JSON") from None
        if not isinstance(data, dict):
            raise _HttpError(400, "Problems parsing JSON")
        return data

    def _route(self, method, path, query, headers, body):
        parts = path.split("/")  # "", "repos", owner, repo, ...
        if len(parts) < 4:
            raise _HttpError(404, "Not Found")
        name, rest = f"{parts[2]}/{parts[3]}", parts[4:]
        state = self._repos.get(name)
        if state is None:
            raise _HttpError(404, "Not Found")
        if not rest and method == "GET":
            return 200, {"full_name": name, "default_branch": "main", "private": False,
                         "permissions": {"push": True, "pull": True}}
        if rest[:1] == ["contents"]:
            subpath = "/".join(rest[1:]).strip("/")
            if method == "GET":
                return self._contents_get(state, subpath, query.get("ref", [None])[0], headers.get("Accept", ""))
            if method in ("PUT", "DELETE"):
                return self._contents_write(state, name, method, subpath, self._json(body))
        elif rest[:1] == ["git"]:
            return self._git(state, method, rest[1:], body)
        elif rest[:2] == ["actions", "workflows"] and rest[3:] == ["runs"] and method == "GET":
            run = self._runs.get((name, rest[2]))
            return 200, {"total_count": 1 if run else 0, "workflow_runs": [run] if run else []}
        raise _HttpError(404, f"Not Found: the fake has no route for {method} {path}")

    # --- contents API ---

    @staticmethod
    def _files_at(state, ref):
        if ref in (None, "main", MAIN, "refs/" + MAIN):
            return state.snapshot(state.head())
        if ref in state.commits:
            return state.snapshot(ref)
        raise _HttpError(404, f"No commit found for the ref {ref}")

    def _contents_get(self, state, path, ref, accept):
        files = self._files_at(state, ref)
        if path in files:
            data = state.blobs[files[path]]
            if _RAW_ACCEPT.search(accept):
                return 200, data
            return 200, {"type": "file", "encoding": "base64", "size": len(data), "name": path.rsplit("/", 1)[-1],
                         "path": path, "sha": files[path], "content": _wrapped_base64(data)}
        entries = _entries(files, path + "/" if path else "")
        if not entries:
            raise _HttpError(404, "Not Found")
        for entry in entries:
            entry["size"] = len(state.blobs[entry["sha"]]) if entry["type"] == "file" else 0
        return 200, entries

    def _commit_or_422(self, repo, changes, message, author, committer):
        try:
            return self._commit(repo, changes, message, author, committer)
        except ValueError as err:
            raise _HttpError(422, str(err)) from None

    def _contents_write(self, state, repo, method, path, data):
        message, sha, branch = data.get("message"), data.get("sha"), data.get("branch")
        if not isinstance(message, str) or not message:
            raise _HttpError(422, 'Invalid request.\n\n"message" wasn\'t supplied.')
        # as on GitHub: the committer defaults to the account, the author to the committer
        committer = _identity(data, "committer")
        author = _identity(data, "author") or committer
        if branch not in (None, "main"):
            raise _HttpError(404, f"Branch {branch} not found")
        if sha is not None and not isinstance(sha, str):
            raise _HttpError(422, 'Invalid request.\n\n"sha" must be a string.')
        files = state.snapshot(state.head())
        current = files.get(path)
        if method == "PUT":
            content = data.get("content")
            if not isinstance(content, str):
                raise _HttpError(422, 'Invalid request.\n\n"content" wasn\'t supplied.')
            new = _decode_base64(content)
            if not path or any(p.startswith(path + "/") for p in files):
                raise _HttpError(422, "Invalid request.\n\nThe path is a directory.")
            if current is not None and sha is None:
                raise _HttpError(422, 'Invalid request.\n\n"sha" wasn\'t supplied.')
            if sha is not None and sha != current:
                raise _HttpError(409, f"{path} does not match {sha}")
            commit = self._commit_or_422(repo, {path: new}, message, author, committer)
            status = 200 if current else 201
            content_info = {"name": path.rsplit("/", 1)[-1], "path": path, "sha": blob_sha(new), "size": len(new), "type": "file"}
        else:
            if current is None:
                raise _HttpError(404, "Not Found")
            if sha is None:
                raise _HttpError(422, 'Invalid request.\n\n"sha" wasn\'t supplied.')
            if sha != current:
                raise _HttpError(409, f"{path} does not match {sha}")
            commit = self._commit_or_422(repo, {path: None}, message, author, committer)
            status, content_info = 200, None
        return status, {"content": content_info, "commit": self._commit_json(state, commit)}

    # --- git data API ---

    @staticmethod
    def _commit_json(state, sha):
        commit = state.commits[sha]
        when = datetime.fromtimestamp(commit["time"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {"sha": sha, "message": commit["message"], "tree": {"sha": commit["tree"]},
                "parents": [{"sha": p} for p in commit["parents"]],
                "author": {**commit["author"], "date": when}, "committer": {**commit["committer"], "date": when}}

    def _git(self, state, method, rest, body):
        kind, ref = (rest[0] if rest else None), "/".join(rest[1:])
        if method == "GET" and kind == "ref":
            if ref not in state.refs:
                raise _HttpError(404, "Not Found")
            return 200, {"ref": "refs/" + ref, "object": {"sha": state.refs[ref], "type": "commit"}}
        if method == "GET" and kind == "commits" and ref:
            if ref not in state.commits:
                raise _HttpError(404, "Not Found")
            return 200, self._commit_json(state, ref)
        if method == "POST" and kind == "blobs" and not ref:
            return self._git_blob(state, self._json(body))
        if method == "POST" and kind == "trees" and not ref:
            return self._git_tree(state, self._json(body))
        if method == "POST" and kind == "commits" and not ref:
            return self._git_commit(state, self._json(body))
        if method == "PATCH" and kind == "refs":
            return self._git_update_ref(state, ref, self._json(body))
        raise _HttpError(404, f"Not Found: the fake has no route for {method} git/{'/'.join(rest)}")

    @staticmethod
    def _git_blob(state, data):
        content, encoding = data.get("content"), data.get("encoding", "utf-8")
        if not isinstance(content, str):
            raise _HttpError(422, 'Invalid request.\n\n"content" wasn\'t supplied.')
        if encoding == "base64":
            raw = _decode_base64(content)
        elif encoding == "utf-8":
            raw = content.encode("utf-8")
        else:
            raise _HttpError(422, f"Invalid encoding {encoding!r}")
        return 201, {"sha": state.add_blob(raw)}

    @staticmethod
    def _git_tree(state, data):
        entries, base = data.get("tree"), data.get("base_tree")
        if not isinstance(entries, list) or not entries:
            raise _HttpError(422, 'Invalid request.\n\n"tree" wasn\'t supplied.')
        if base is None:
            files = {}
        elif base in state.trees:
            files = dict(state.trees[base])
        else:
            raise _HttpError(422, "base_tree is not a tree of this repository")
        for i, entry in enumerate(entries):
            path = entry.get("path") if isinstance(entry, dict) else None
            if not isinstance(path, str) or not path or path.startswith("/"):
                raise _HttpError(422, f"tree[{i}].path is invalid")
            if entry.get("mode") != "100644" or entry.get("type") != "blob" or "sha" not in entry:
                raise _HttpError(422, f"tree[{i}]: the fake takes only 100644 blobs given by sha")
            sha = entry["sha"]
            if sha is None:
                if path not in files:
                    raise _HttpError(422, "GitRPC::BadObjectState")
                del files[path]
            elif sha in state.blobs:
                files[path] = sha
            else:
                raise _HttpError(422, "GitRPC::BadObjectState")
        _check_paths(files)
        sha = state.add_tree(files)
        listing = [{"path": e["name"], "mode": "100644" if e["type"] == "file" else "040000",
                    "type": "blob" if e["type"] == "file" else "tree", "sha": e["sha"]} for e in _entries(files, "")]
        return 201, {"sha": sha, "tree": listing, "truncated": False}

    def _git_commit(self, state, data):
        message, tree, parents = data.get("message"), data.get("tree"), data.get("parents", [])
        if not isinstance(message, str) or not message:
            raise _HttpError(422, 'Invalid request.\n\n"message" wasn\'t supplied.')
        if tree not in state.trees:
            raise _HttpError(422, "Tree SHA does not exist")
        if not isinstance(parents, list) or any(p not in state.commits for p in parents):
            raise _HttpError(422, "Parent SHA does not exist")
        # as on GitHub: the author defaults to the account, the committer to the author
        author = _identity(data, "author")
        committer = _identity(data, "committer") or author
        return 201, self._commit_json(state, self._store_commit(state, tree, parents, message, author, committer))

    @staticmethod
    def _git_update_ref(state, ref, data):
        if ref not in state.refs:
            raise _HttpError(404, "Reference does not exist")
        sha = data.get("sha")
        if sha not in state.commits:
            raise _HttpError(422, "Object does not exist")
        if data.get("force") is not True and not state.is_ancestor(state.refs[ref], sha):
            raise _HttpError(422, "Update is not a fast forward")
        state.refs[ref] = sha
        return 200, {"ref": "refs/" + ref, "object": {"sha": sha, "type": "commit"}}

    # --- control endpoints for tests (no auth) ---

    def _control(self, method, path, body):
        try:
            data = self._json(body)
            if (method, path) == ("GET", "/_fake/log"):
                return 200, {"requests": [list(r) for r in self.requests]}
            if (method, path) == ("POST", "/_fake/fail_next"):
                self.fail_next(data["method"], data["path_prefix"], int(data["status"]), int(data.get("times", 1)))
            elif (method, path) == ("PUT", "/_fake/file"):
                self.put_file(data["repo"], data["path"], base64.b64decode(data["content_base64"]))
            elif (method, path) == ("DELETE", "/_fake/file"):
                self.delete_file(data["repo"], data["path"])
            elif (method, path) == ("PUT", "/_fake/token"):
                self.token = str(data["token"])
            elif (method, path) == ("PUT", "/_fake/run"):
                self.set_run(data["repo"], data["workflow"], data["run"])
            else:
                raise _HttpError(404, f"Not Found: no control route for {method} {path}")
        except FileNotFoundError as err:
            raise _HttpError(404, f"Not Found: {err}") from None
        except (KeyError, TypeError, ValueError, binascii.Error) as err:
            raise _HttpError(400, f"bad control request: {err!r}") from None
        return 200, {"ok": True}


# --- HTTP plumbing -----------------------------------------------------------------------------

class _Server(ThreadingHTTPServer):
    request_queue_size = 64      # this PC resets a share of loopback connections; a deep queue helps
    allow_reuse_address = False  # on Windows SO_REUSEADDR lets a second server take over a busy port
    daemon_threads = True

    def handle_error(self, request, client_address):
        if not isinstance(sys.exception(), (ConnectionError, TimeoutError)):  # a client that went away is no news
            super().handle_error(request, client_address)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive: a browser reuses its connection
    timeout = 30                   # seconds a connection may sit idle
    wbufsize = -1                  # buffered: a response's headers and body leave in one write

    def log_message(self, *args):
        pass

    def _send(self, status, body=b"", content_type="application/json; charset=utf-8"):
        self.send_response(status)
        for name, value in CORS.items():
            self.send_header(name, value)
        if status != 204:
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if status != 204:
            self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204)

    def _serve(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        status, payload = self.server.fake.handle(self.command, self.path, self.headers, body)
        if isinstance(payload, bytes):
            self._send(status, payload, "application/vnd.github.raw; charset=utf-8")
        else:
            self._send(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _serve


# --- command line ------------------------------------------------------------------------------

def seed(fake, root=ROOT, repo=SEED_REPO):
    """Put a checkout's src/photos.json and photos/ into `repo` as one commit."""
    root = Path(root)
    files = {"src/photos.json": (root / "src" / "photos.json").read_bytes()}
    for path in sorted((root / "photos").rglob("*")):
        if path.is_file():
            files[path.relative_to(root).as_posix()] = path.read_bytes()
    fake.put_files(repo, files)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fake GitHub server for Nocturne tests.")
    parser.add_argument("--port", type=int, default=0, help="0 picks a free port")
    parser.add_argument("--seed", action="store_true", help=f"fill {SEED_REPO} with this checkout's photos.json and photos/")
    parser.add_argument("--token", default="test-token")
    args = parser.parse_args(argv)
    fake = FakeGitHub(args.token)
    if args.seed:
        seed(fake)
    fake.start(args.port)
    print(fake.url, flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        fake.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
