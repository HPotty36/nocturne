"""The fake GitHub's behaviour. The JavaScript client and the admin page are written against the real GitHub docs and
tested against this fake, so what it answers has to match the real API.

FakeApiTests ask in-process (`FakeGitHub.handle`): the API logic is the same with or without sockets, and nothing
can go wrong on the wire. FakeHttpTests check the wire itself. This PC resets some connections after the server
has already answered, and a repeated write would then fail for the wrong reason, so those tests send only
requests that are safe to send twice."""
import base64
import http.client
import json
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request

import helpers  # puts scripts/ on sys.path
from fake_github import FakeGitHub, blob_sha, tree_sha

R = "HPotty36/nocturne"
AUTH = "Bearer test-token"
ORIGIN = "Access-Control-Allow-Origin"
ALLOWED_HEADERS = "Authorization, Content-Type, Accept, X-GitHub-Api-Version"
FILES = {"queue/a/item.json": b'{"status": "waiting"}', "queue/a/full.jpg": b"FULL", "src/photos.json": b'{"n": 1}'}
b64 = lambda data: base64.b64encode(data).decode("ascii")
entry = lambda path, sha: {"path": path, "mode": "100644", "type": "blob", "sha": sha}


class FakeApiTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGitHub()
        self.fake.put_files(R, FILES)

    def call(self, method, path, body=None, *, auth=AUTH, headers=None):
        """(status, payload) as an HTTP client would see them: JSON payloads go through a JSON round trip."""
        sent = dict(headers or {})
        if auth:
            sent["Authorization"] = auth
        status, payload = self.fake.handle(method, path, sent, json.dumps(body).encode("utf-8") if body is not None else b"")
        return status, payload if isinstance(payload, bytes) else json.loads(json.dumps(payload))

    def head(self):
        return self.call("GET", f"/repos/{R}/git/ref/heads/main")[1]["object"]["sha"]

    def test_hashes_match_real_git(self):
        # expected values come from `git hash-object` and `git write-tree` on the same files
        files = {"queue/a/item.json": b'{"status": "waiting"}', "queue/a/full.jpg": b"FULL", "queue/ab/x": b"1", "queue/a.txt": b"dot",
                 "src/photos.json": b'{"n": 1}', "README": b"hello world\n", "photos/사진.jpg": b"\xff\xd8\xff", "z": b""}
        self.assertEqual(blob_sha(b"hello world\n"), "3b18e512dba79e4c8300dd08aeb37f8e728b8dad")
        self.assertEqual(tree_sha({p: blob_sha(d) for p, d in files.items()}), "a7a65929e51428a978bd2a0fa2b8c74bde5a186a")
        queue = {p[len("queue/"):]: blob_sha(d) for p, d in files.items() if p.startswith("queue/")}
        self.assertEqual(tree_sha(queue), "3611ac658166832ae3b574d239190cfaae70f280")

    def test_repository(self):
        status, repo = self.call("GET", f"/repos/{R}")
        self.assertEqual(status, 200)
        self.assertTrue(repo["permissions"]["push"])
        self.assertEqual(self.call("GET", "/repos/nobody/nothing")[0], 404)

    def test_directory_listing(self):
        _, root = self.call("GET", f"/repos/{R}/contents")
        self.assertEqual([(e["name"], e["path"], e["type"]) for e in root], [("queue", "queue", "dir"), ("src", "src", "dir")])
        queue_a = tree_sha({"item.json": blob_sha(b'{"status": "waiting"}'), "full.jpg": blob_sha(b"FULL")})
        _, queue = self.call("GET", f"/repos/{R}/contents/queue")
        self.assertEqual(queue, [{"name": "a", "path": "queue/a", "sha": queue_a, "type": "dir", "size": 0}])
        _, files = self.call("GET", f"/repos/{R}/contents/queue/a/")
        self.assertEqual([(e["name"], e["sha"], e["type"], e["size"]) for e in files],
                         [("full.jpg", blob_sha(b"FULL"), "file", 4), ("item.json", blob_sha(b'{"status": "waiting"}'), "file", 21)])
        self.assertEqual(self.call("GET", f"/repos/{R}/contents/nope")[0], 404)

    def test_folder_sha_follows_its_contents(self):
        shas = lambda: {e["name"]: e["sha"] for e in self.call("GET", f"/repos/{R}/contents")[1]}
        before = shas()
        self.fake.put_file(R, "queue/a/new.txt", b"new")
        after = shas()
        self.assertNotEqual(after["queue"], before["queue"])
        self.assertEqual(after["src"], before["src"])
        self.fake.delete_file(R, "queue/a/new.txt")
        self.assertEqual(shas(), before)

    def test_file_json_and_raw(self):
        data = bytes(range(100))
        self.fake.put_file(R, "d/한글.bin", data)
        status, info = self.call("GET", f"/repos/{R}/contents/d/%ED%95%9C%EA%B8%80.bin")
        self.assertEqual((status, info["type"], info["encoding"], info["size"], info["name"], info["path"], info["sha"]),
                         (200, "file", "base64", 100, "한글.bin", "d/한글.bin", blob_sha(data)))
        self.assertEqual([len(line) for line in info["content"].split("\n")], [60, 60, 16, 0])  # the way GitHub wraps it
        self.assertEqual(base64.b64decode(info["content"]), data)
        for accept in ("application/vnd.github.raw", "application/vnd.github.raw+json", "application/vnd.github.v3.raw"):
            self.assertEqual(self.call("GET", f"/repos/{R}/contents/d/%ED%95%9C%EA%B8%80.bin", headers={"Accept": accept}), (200, data))

    def test_ref_reads_an_older_commit(self):
        old = self.head()
        self.fake.put_file(R, "src/photos.json", b'{"n": 2}')
        read = lambda ref: json.loads(base64.b64decode(self.call("GET", f"/repos/{R}/contents/src/photos.json?ref={ref}")[1]["content"]))
        self.assertEqual((read(old), read("main"), read("heads/main")), ({"n": 1}, {"n": 2}, {"n": 2}))
        self.assertEqual(self.call("GET", f"/repos/{R}/contents/src/photos.json?ref=nope")[0], 404)

    def test_contents_write_and_delete_need_the_current_sha(self):
        path = f"/repos/{R}/contents/new.txt"
        before = self.fake.commits(R)
        status, made = self.call("PUT", path, {"message": "add", "content": b64(b"one")})
        self.assertEqual((status, made["content"]["sha"], made["commit"]["sha"], made["commit"]["message"]), (201, blob_sha(b"one"), self.head(), "add"))
        self.assertEqual(self.call("PUT", path, {"message": "again", "content": b64(b"two")})[0], 422)   # no sha for an existing file
        self.assertEqual(self.call("PUT", path, {"message": "stale", "content": b64(b"two"), "sha": blob_sha(b"zero")})[0], 409)
        status, _ = self.call("PUT", path, {"message": "edit", "content": b64(b"two"), "sha": blob_sha(b"one")})
        self.assertEqual((status, self.fake.read_file(R, "new.txt")), (200, b"two"))
        self.assertEqual(self.fake.commits(R), before + 2)
        self.assertEqual(self.call("PUT", path, {"content": b64(b"x"), "sha": blob_sha(b"two")})[0], 422)  # message is required
        self.assertEqual(self.call("DELETE", path, {"message": "rm"})[0], 422)
        self.assertEqual(self.call("DELETE", path, {"message": "rm", "sha": blob_sha(b"one")})[0], 409)
        status, removed = self.call("DELETE", path, {"message": "rm", "sha": blob_sha(b"two")})
        self.assertEqual((status, removed["content"], self.fake.read_file(R, "new.txt")), (200, None, None))
        self.assertEqual(self.call("DELETE", path, {"message": "rm", "sha": blob_sha(b"two")})[0], 404)
        self.assertEqual(self.fake.commits(R), before + 3)
        self.assertEqual(self.call("PUT", f"/repos/{R}/contents/queue", {"message": "m", "content": b64(b"x")})[0], 422)  # a folder

    def test_content_must_be_valid_base64(self):
        path, before = f"/repos/{R}/contents/x.json", self.fake.commits(R)
        for label, content in (("plain text", '{"status": "pc"}'), ("missing padding", "YWJjZA"), ("stray character", "YWJj!GVm"),
                               ("not text", None)):
            with self.subTest(label):
                status, error = self.call("PUT", path, {"message": "m", "content": content})
                self.assertEqual((status, "Base64" in error["message"] or content is None), (422, True))
        for label, body in (("plain text", {"content": "not base64!", "encoding": "base64"}), ("missing padding", {"content": "YWJjZA", "encoding": "base64"})):
            with self.subTest("blob, " + label):
                self.assertEqual(self.call("POST", f"/repos/{R}/git/blobs", body)[0], 422)
        self.assertEqual((self.fake.read_file(R, "x.json"), self.fake.commits(R)), (None, before))
        # what GitHub itself sends and accepts: lines of 60 characters, or an empty file
        self.assertEqual(self.call("PUT", path, {"message": "m", "content": "YWJj\nZGVm\n"})[0], 201)
        self.assertEqual(self.fake.read_file(R, "x.json"), b"abcdef")
        self.assertEqual(self.call("PUT", f"/repos/{R}/contents/empty.txt", {"message": "m", "content": ""})[0], 201)
        self.assertEqual(self.fake.read_file(R, "empty.txt"), b"")
        status, blob = self.call("POST", f"/repos/{R}/git/blobs", {"content": "YWJj\nZGVm\n", "encoding": "base64"})
        self.assertEqual((status, blob["sha"]), (201, blob_sha(b"abcdef")))

    def test_git_data_commit_in_steps(self):
        git = f"/repos/{R}/git"
        head = self.head()
        _, commit = self.call("GET", f"{git}/commits/{head}")
        full = blob_sha(b"FULL")
        status, new = self.call("POST", f"{git}/blobs", {"content": b64(b"NEW"), "encoding": "base64"})
        self.assertEqual((status, new["sha"]), (201, blob_sha(b"NEW")))
        _, text = self.call("POST", f"{git}/blobs", {"content": "한글", "encoding": "utf-8"})
        self.assertEqual(text["sha"], blob_sha("한글".encode("utf-8")))
        status, tree = self.call("POST", f"{git}/trees", {"base_tree": commit["tree"]["sha"], "tree": [
            entry("queue/a/moved.jpg", full), entry("new.txt", new["sha"]), entry("queue/a/item.json", None)]})
        expected = {"queue/a/full.jpg": full, "queue/a/moved.jpg": full, "new.txt": new["sha"], "src/photos.json": blob_sha(b'{"n": 1}')}
        self.assertEqual((status, tree["sha"]), (201, tree_sha(expected)))
        status, made = self.call("POST", f"{git}/commits", {"message": "m", "tree": tree["sha"], "parents": [head]})
        self.assertEqual((status, made["parents"], made["tree"]), (201, [{"sha": head}], {"sha": tree["sha"]}))
        self.assertEqual(self.head(), head)  # nothing moves until the ref does
        status, ref = self.call("PATCH", f"{git}/refs/heads/main", {"sha": made["sha"], "force": False})
        self.assertEqual((status, ref["ref"], ref["object"]["sha"]), (200, "refs/heads/main", made["sha"]))
        self.assertEqual(self.fake.files(R), {"queue/a/full.jpg": b"FULL", "queue/a/moved.jpg": b"FULL", "new.txt": b"NEW", "src/photos.json": b'{"n": 1}'})
        self.assertEqual(self.fake.commits(R), 2)

    def test_commit_author_and_committer_are_recorded(self):
        git, contents = f"/repos/{R}/git", f"/repos/{R}/contents/new.txt"
        me = {"name": "HPotty36", "email": "112685098+HPotty36@users.noreply.github.com"}
        other = {"name": "Someone", "email": "someone@example.test"}
        who = lambda commit: [{k: commit[role][k] for k in ("name", "email")} for role in ("author", "committer")]
        tree = self.call("GET", f"{git}/commits/{self.head()}")[1]["tree"]["sha"]

        status, made = self.call("POST", f"{git}/commits", {"message": "m", "tree": tree, "parents": [self.head()], "author": me, "committer": other})
        self.assertEqual((status, who(made)), (201, [me, other]))
        self.assertEqual(who(self.call("GET", f"{git}/commits/{made['sha']}")[1]), [me, other])
        _, alone = self.call("POST", f"{git}/commits", {"message": "m", "tree": tree, "parents": [self.head()], "author": me})
        self.assertEqual(who(alone), [me, me])  # as on GitHub: the committer defaults to the author
        _, plain = self.call("POST", f"{git}/commits", {"message": "m", "tree": tree, "parents": [self.head()]})
        self.assertEqual(who(plain), [{"name": "Fake", "email": "fake@example.test"}] * 2)  # GitHub would use the account

        status, put = self.call("PUT", contents, {"message": "add", "content": b64(b"x"), "author": other, "committer": me})
        self.assertEqual((status, who(put["commit"]), who(self.call("GET", f"{git}/commits/{self.head()}")[1])), (201, [other, me], [other, me]))
        status, put = self.call("PUT", contents, {"message": "edit", "content": b64(b"y"), "sha": blob_sha(b"x"), "committer": me})
        self.assertEqual((status, who(put["commit"])), (200, [me, me]))  # the author defaults to the committer
        status, removed = self.call("DELETE", contents, {"message": "rm", "sha": blob_sha(b"y"), "author": me, "committer": me})
        self.assertEqual((status, who(removed["commit"])), (200, [me, me]))
        self.assertEqual([c["author"] for c in self.fake.last_commits(R, 2)], [me, me])

        before = self.fake.commits(R)
        for bad in ("HPotty36", {"name": "x"}, {"email": "x@example.test"}, {"name": "", "email": "x@example.test"}, {"name": 1, "email": "e"}):
            for role in ("author", "committer"):
                with self.subTest(role=role, bad=bad):
                    self.assertEqual(self.call("POST", f"{git}/commits", {"message": "m", "tree": tree, "parents": [], role: bad})[0], 422)
                    self.assertEqual(self.call("PUT", contents, {"message": "m", "content": b64(b"z"), role: bad})[0], 422)
        self.assertEqual(self.fake.commits(R), before)

    def test_tree_without_base_tree_holds_only_its_entries(self):
        git = f"/repos/{R}/git"
        _, blob = self.call("POST", f"{git}/blobs", {"content": "x", "encoding": "utf-8"})
        status, tree = self.call("POST", f"{git}/trees", {"tree": [entry("only.txt", blob["sha"])]})
        self.assertEqual((status, tree["sha"], [e["path"] for e in tree["tree"]]), (201, tree_sha({"only.txt": blob["sha"]}), ["only.txt"]))

    def test_bad_trees_and_commits_are_422(self):
        git = f"/repos/{R}/git"
        _, commit = self.call("GET", f"{git}/commits/{self.head()}")
        base = commit["tree"]["sha"]
        for label, body in (("unknown blob", {"base_tree": base, "tree": [entry("x", "ab" * 20)]}),
                            ("delete a path that is not there", {"base_tree": base, "tree": [entry("nope", None)]}),
                            ("empty", {"base_tree": base, "tree": []}),
                            ("unknown base tree", {"base_tree": "cd" * 20, "tree": [entry("x", blob_sha(b"FULL"))]}),
                            ("file under a file", {"base_tree": base, "tree": [entry("src/photos.json/x", blob_sha(b"FULL"))]})):
            with self.subTest(label):
                self.assertEqual(self.call("POST", f"{git}/trees", body)[0], 422)
        self.assertEqual(self.call("POST", f"{git}/commits", {"message": "m", "tree": "ef" * 20, "parents": []})[0], 422)
        self.assertEqual(self.call("POST", f"{git}/commits", {"message": "m", "tree": base, "parents": ["ab" * 20]})[0], 422)
        self.assertEqual(self.call("GET", f"{git}/commits/{'ab' * 20}")[0], 404)

    def test_ref_moves_only_forward_unless_forced(self):
        git = f"/repos/{R}/git"
        head = self.head()
        tree = self.call("GET", f"{git}/commits/{head}")[1]["tree"]["sha"]
        mine = self.call("POST", f"{git}/commits", {"message": "mine", "tree": tree, "parents": [head]})[1]["sha"]
        self.fake.put_file(R, "other.txt", b"someone else committed first")
        theirs = self.head()
        status, error = self.call("PATCH", f"{git}/refs/heads/main", {"sha": mine, "force": False})
        self.assertEqual((status, error["message"], self.head()), (422, "Update is not a fast forward", theirs))
        self.assertEqual(self.call("PATCH", f"{git}/refs/heads/main", {"sha": mine})[0], 422)  # force defaults to false
        self.assertEqual(self.call("PATCH", f"{git}/refs/heads/main", {"sha": mine, "force": True})[0], 200)
        self.assertEqual(self.head(), mine)
        self.assertEqual(self.call("PATCH", f"{git}/refs/heads/main", {"sha": mine})[0], 200)  # no change is a fast-forward
        self.assertEqual(self.call("PATCH", f"{git}/refs/heads/main", {"sha": "ab" * 20})[0], 422)
        self.assertEqual(self.call("PATCH", f"{git}/refs/heads/other", {"sha": mine})[0], 404)
        self.assertEqual(self.call("GET", f"{git}/ref/heads/other")[0], 404)

    def test_auth(self):
        path = f"/repos/{R}/contents/src/photos.json"
        self.assertEqual(self.call("GET", path, auth=None)[0], 401)
        self.assertEqual(self.call("GET", path, auth="Bearer wrong")[0], 401)
        self.assertEqual(self.call("GET", path, auth="Bearer ")[0], 401)
        self.assertEqual(self.call("GET", path, auth="token test-token")[0], 200)
        self.assertEqual(self.call("GET", "/_fake/log", auth=None)[0], 200)  # control paths take no token
        status, error = self.call("GET", path, auth=None)
        self.assertEqual((status, error["message"]), (401, "Bad credentials"))

    def test_failures_are_injected_and_logged(self):
        prefix = f"/repos/{R}/contents"
        self.call("POST", "/_fake/fail_next", {"method": "GET", "path_prefix": prefix, "status": 503, "times": 2}, auth=None)
        self.fake.fail_next("PUT", prefix + "/x", 500)
        path = f"{prefix}/src/photos.json"
        self.assertEqual([self.call("GET", path + "?ref=main")[0] for _ in range(3)], [503, 503, 200])
        self.assertEqual(self.call("PUT", prefix + "/x", {"message": "m", "content": b64(b"x")})[0], 500)
        self.assertEqual(self.call("PUT", prefix + "/x", {"message": "m", "content": b64(b"x")})[0], 201)
        _, log = self.call("GET", "/_fake/log", auth=None)  # no /_fake requests, no query strings
        self.assertEqual(log, {"requests": [["GET", path]] * 3 + [["PUT", prefix + "/x"]] * 2})
        self.assertEqual(self.fake.requests, [("GET", path)] * 3 + [("PUT", prefix + "/x")] * 2)
        self.fake.fail_next("GET", "/repos/other", 500)
        self.assertEqual(self.call("GET", path)[0], 200)  # a rule only matches its own prefix

    def test_control_files_token_and_runs(self):
        status, ok = self.call("PUT", "/_fake/file", {"repo": R, "path": "c/d.txt", "content_base64": b64(b"ctl")}, auth=None)
        self.assertEqual((status, ok, self.fake.read_file(R, "c/d.txt")), (200, {"ok": True}, b"ctl"))
        self.assertEqual(self.fake.commits(R), 2)
        self.assertEqual(self.call("DELETE", "/_fake/file", {"repo": R, "path": "c/d.txt"}, auth=None)[0], 200)
        self.assertEqual((self.fake.read_file(R, "c/d.txt"), self.fake.commits(R)), (None, 3))
        self.assertEqual(self.call("DELETE", "/_fake/file", {"repo": R, "path": "c/d.txt"}, auth=None)[0], 404)
        self.assertEqual(self.call("POST", "/_fake/fail_next", {"method": "GET"}, auth=None)[0], 400)
        self.assertEqual(self.call("POST", "/_fake/nope", {}, auth=None)[0], 404)

        self.call("PUT", "/_fake/token", {"token": "rotated"}, auth=None)
        self.assertEqual(self.call("GET", f"/repos/{R}")[0], 401)
        self.assertEqual(self.call("GET", f"/repos/{R}", auth="Bearer rotated")[0], 200)

        runs = f"/repos/{R}/actions/workflows/site.yml/runs"
        self.assertEqual(self.call("GET", runs, auth="Bearer rotated")[1]["workflow_runs"], [])
        run = {"id": 7, "status": "in_progress", "conclusion": None, "html_url": "https://example.test/run/7"}
        self.call("PUT", "/_fake/run", {"repo": R, "workflow": "site.yml", "run": run}, auth=None)
        self.assertEqual(self.call("GET", runs + "?per_page=1", auth="Bearer rotated")[1], {"total_count": 1, "workflow_runs": [run]})
        self.assertEqual(self.call("GET", f"/repos/{R}/actions/workflows/draft.yml/runs", auth="Bearer rotated")[1]["workflow_runs"], [])

    def test_python_helpers(self):
        self.assertEqual(self.fake.commits(R), 1)
        self.assertEqual(self.fake.commits("nobody/nothing"), 0)
        self.assertEqual(self.fake.files("nobody/nothing"), {})
        self.assertEqual(self.fake.files(R), FILES)
        self.assertIsNone(self.fake.read_file(R, "nope"))
        with self.assertRaises(FileNotFoundError):
            self.fake.delete_file(R, "nope")
        self.fake.put_file(R, "src/photos.json", b'{"n": 1}')  # the same bytes still make a commit
        self.assertEqual(self.fake.commits(R), 2)


def call(base, method, path, body=None, *, auth=AUTH, headers=None):
    """(status, response headers, parsed body) over HTTP. A connection this PC drops is tried again."""
    sent = dict(headers or {})
    if auth:
        sent["Authorization"] = auth
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        sent["Content-Type"] = "application/json"
    for attempt in range(6):
        request = urllib.request.Request(base + path, data=data, method=method, headers=sent)
        try:
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    status, head, raw = response.status, response.headers, response.read()
            except urllib.error.HTTPError as err:
                with err:
                    status, head, raw = err.code, err.headers, err.read()
        except (ConnectionError, http.client.HTTPException, urllib.error.URLError):
            if attempt == 5:
                raise
            time.sleep(0.2)
            continue
        break
    return status, head, json.loads(raw) if head.get_content_type() == "application/json" else raw


def retrying(body, attempts=4):
    """Run a test body again, from the start, if this PC drops its connection."""
    for attempt in range(attempts):
        try:
            return body()
        except (ConnectionError, http.client.HTTPException, urllib.error.URLError):
            if attempt == attempts - 1:
                raise
            time.sleep(0.2)


class FakeHttpTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGitHub(); self.fake.start(); self.addCleanup(self.fake.stop)
        self.fake.put_files(R, FILES)
        self.fake.put_file(R, "d/한글.bin", bytes(range(100)))

    def call(self, method, path, body=None, **kw):
        return call(self.fake.url, method, path, body, **kw)

    def test_cors_headers_on_every_response(self):
        self.fake.fail_next("GET", f"/repos/{R}/contents/boom", 502, times=6)  # a dropped connection is tried again
        file = f"/repos/{R}/contents/src/photos.json"
        cases = {"ok": ("GET", file, AUTH, 200), "not found": ("GET", file + ".nope", AUTH, 404), "bad token": ("GET", file, None, 401),
                 "injected failure": ("GET", f"/repos/{R}/contents/boom", AUTH, 502), "control": ("GET", "/_fake/log", None, 200),
                 "preflight": ("OPTIONS", file, None, 204), "preflight of a control path": ("OPTIONS", "/_fake/file", None, 204)}
        for label, (method, path, auth, expected) in cases.items():
            with self.subTest(label):
                status, head, _ = self.call(method, path, auth=auth, headers={"Origin": "http://localhost:8000"})
                self.assertEqual(status, expected)
                self.assertEqual(head[ORIGIN], "*")
                self.assertEqual(head["Access-Control-Allow-Headers"], ALLOWED_HEADERS)
                self.assertIn("PATCH", head["Access-Control-Allow-Methods"])

    def test_json_and_raw_bodies(self):
        _, head, listing = self.call("GET", f"/repos/{R}/contents/d")
        self.assertEqual([e["name"] for e in listing], ["한글.bin"])  # UTF-8 on the wire
        self.assertEqual(head["Content-Type"], "application/json; charset=utf-8")
        status, _, info = self.call("GET", f"/repos/{R}/contents/d/%ED%95%9C%EA%B8%80.bin")  # percent-encoded path
        self.assertEqual((status, base64.b64decode(info["content"])), (200, bytes(range(100))))
        _, head, raw = self.call("GET", f"/repos/{R}/contents/d/%ED%95%9C%EA%B8%80.bin", headers={"Accept": "application/vnd.github.raw"})
        self.assertEqual(raw, bytes(range(100)))
        self.assertEqual(head["Content-Type"], "application/vnd.github.raw; charset=utf-8")
        _, head, error = self.call("GET", f"/repos/{R}/contents/nope")
        self.assertEqual((error["message"], error["status"]), ("Not Found", "404"))

    def test_posts_and_ref_updates_over_http(self):
        git = f"/repos/{R}/git"
        _, _, ref = self.call("GET", f"{git}/ref/heads/main")
        head = ref["object"]["sha"]
        _, _, commit = self.call("GET", f"{git}/commits/{head}")
        status, _, blob = self.call("POST", f"{git}/blobs", {"content": b64(b"NEW"), "encoding": "base64"})
        self.assertEqual((status, blob["sha"]), (201, blob_sha(b"NEW")))
        status, _, tree = self.call("POST", f"{git}/trees", {"base_tree": commit["tree"]["sha"], "tree": [entry("new.txt", blob["sha"])]})
        self.assertEqual(status, 201)
        status, _, made = self.call("POST", f"{git}/commits", {"message": "m", "tree": tree["sha"], "parents": [head]})
        self.assertEqual(status, 201)
        self.assertEqual(self.call("PATCH", f"{git}/refs/heads/main", {"sha": made["sha"], "force": False})[0], 200)
        self.assertEqual(self.fake.read_file(R, "new.txt"), b"NEW")
        status, _, error = self.call("PATCH", f"{git}/refs/heads/main", {"sha": head, "force": False})  # back to where it was
        self.assertEqual((status, error["message"]), (422, "Update is not a fast forward"))

    def test_one_connection_serves_many_requests(self):
        def run():
            conn = http.client.HTTPConnection("127.0.0.1", int(self.fake.url.rsplit(":", 1)[1]), timeout=10)
            try:
                seen = []
                for method, path, headers in (("GET", f"/repos/{R}/contents/src/photos.json", {"Authorization": AUTH}),
                                              ("OPTIONS", f"/repos/{R}/contents/src/photos.json", {}),   # 204 has no body
                                              ("GET", f"/repos/{R}/contents/src/photos.json", {}),       # 401
                                              ("GET", "/_fake/log", {}),
                                              ("GET", f"/repos/{R}/contents/src/photos.json", {"Authorization": AUTH})):
                    conn.request(method, path, headers=headers)
                    response = conn.getresponse()
                    response.read()
                    seen.append(response.status)
                return seen
            finally:
                conn.close()
        self.assertEqual(retrying(run), [200, 204, 401, 200, 200])

    def test_stop_is_repeatable_and_closes_the_server(self):
        url = self.fake.url
        self.fake.stop()
        self.fake.stop()
        with self.assertRaises(OSError):
            urllib.request.urlopen(url + "/_fake/log", timeout=5).close()

    def test_command_line(self):
        proc = subprocess.Popen([sys.executable, str(helpers.ROOT / "tests" / "fake_github.py"), "--port", "0", "--seed", "--token", "cli-token"],
                                stdout=subprocess.PIPE, text=True)
        watchdog = threading.Timer(60, proc.kill)
        watchdog.start()
        def stop():
            watchdog.cancel()
            proc.kill()
            proc.wait()
            proc.stdout.close()
        self.addCleanup(stop)
        url = proc.stdout.readline().strip()
        self.assertRegex(url, r"^http://127\.0\.0\.1:[1-9]\d*$")
        photos = "/repos/HPotty36/nocturne/contents/photos/thumb"
        status, _, listing = call(url, "GET", photos, auth="Bearer cli-token")
        self.assertEqual(status, 200)
        self.assertEqual([e["name"] for e in listing], sorted(p.name for p in (helpers.ROOT / "photos" / "thumb").glob("*.jpg")))
        status, _, raw = call(url, "GET", "/repos/HPotty36/nocturne/contents/src/photos.json", auth="Bearer cli-token",
                              headers={"Accept": "application/vnd.github.raw"})
        self.assertEqual((status, raw), (200, (helpers.ROOT / "src" / "photos.json").read_bytes()))
        self.assertEqual(call(url, "GET", photos, auth="Bearer test-token")[0], 401)
        self.assertIsNone(proc.poll())  # keeps running after printing the URL
