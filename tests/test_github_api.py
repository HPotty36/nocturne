import base64
import hashlib
import http.client
import io
import json
import re
import unittest
import urllib.error
import urllib.request
from unittest import mock

import helpers  # puts scripts/ on sys.path
import github_api
from fake_github import FakeGitHub

R = "HPotty36/nocturne"
class GitHubApiTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGitHub(); self.fake.start(); self.addCleanup(self.fake.stop)
        self.fake.put_file(R, "queue/a/item.json", b'{"status": "waiting"}')
        self.fake.put_file(R, "queue/a/full.jpg", b"FULL")
        self.fake.put_file(R, "src/photos.json", b'{"n": 1}')
        self.gh = github_api.GitHub("test-token", api=self.fake.url)
    def test_list_get_put(self):
        self.assertEqual([d["name"] for d in self.gh.list_dir(R, "queue")], ["a"])
        self.assertEqual(self.gh.list_dir(R, "nope"), [])
        data, sha = self.gh.get_json(R, "queue/a/item.json")
        self.gh.put_json(R, "queue/a/item.json", {"status": "pc"}, sha=sha, message="m")
        with self.assertRaises(github_api.Conflict):
            self.gh.put_json(R, "queue/a/item.json", {"status": "x"}, sha=sha, message="m")
        self.assertEqual(self.gh.get_bytes(R, "queue/a/full.jpg"), b"FULL")
    def test_commit_moves_blob_and_deletes(self):
        full_sha = next(e["sha"] for e in self.gh.list_dir(R, "queue/a") if e["name"] == "full.jpg")
        before = self.fake.commits(R)
        self.gh.commit_files(R, "m", lambda read: {"photos/full/a.jpg": {"sha": full_sha}, "src/photos.json": json.dumps({"n": read("src/photos.json")["n"] + 1}),
                                                   "queue/a/full.jpg": None, "queue/a/item.json": None})
        self.assertEqual(self.fake.commits(R), before + 1)
        self.assertEqual(self.fake.read_file(R, "photos/full/a.jpg"), b"FULL")
        self.assertIsNone(self.fake.read_file(R, "queue/a/item.json"))
        self.assertEqual(json.loads(self.fake.read_file(R, "src/photos.json")), {"n": 2})
    def test_commit_retries_and_rereads(self):
        calls = []
        def build(read):
            calls.append(1)
            if len(calls) == 1:
                self.fake.put_file(R, "src/photos.json", b'{"n": 10}')   # 다른 쪽이 먼저 커밋
            return {"src/photos.json": json.dumps({"n": read("src/photos.json")["n"] + 1})}
        self.gh.commit_files(R, "m", build)
        self.assertEqual(len(calls), 2)
        self.assertEqual(json.loads(self.fake.read_file(R, "src/photos.json")), {"n": 11})
    def test_empty_change_no_commit(self):
        before = self.fake.commits(R)
        self.assertIsNone(self.gh.commit_files(R, "m", lambda read: {}))
        self.assertEqual(self.fake.commits(R), before)
    def test_commit_gives_up(self):
        def build(read):
            self.fake.put_file(R, "x.txt", str(len(self.fake.requests)).encode())
            return {"y.txt": "y"}
        with self.assertRaises(github_api.CommitError):
            self.gh.commit_files(R, "m", build)
    def test_auth_and_failures(self):
        with self.assertRaises(github_api.GitHubError) as cm:
            github_api.GitHub("wrong", api=self.fake.url).list_dir(R, "queue")
        self.assertEqual(cm.exception.status, 401)
        self.fake.fail_next("GET", f"/repos/{R}/contents", 502)
        with self.assertRaises(github_api.GitHubError):
            self.gh.list_dir(R, "queue")
        self.assertEqual(len(self.gh.list_dir(R, "queue")), 1)


class CommitFilesTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGitHub(); self.fake.start(); self.addCleanup(self.fake.stop)
        self.fake.put_files(R, {"src/photos.json": b'{"n": 1}', "a.txt": b"x"})
        self.gh = github_api.GitHub("test-token", api=self.fake.url)
    def test_new_blobs_from_bytes_and_text(self):
        binary = bytes(range(256))
        sha = self.gh.commit_files(R, "사진 게시: 공중전화", lambda read: {"b.bin": binary, "k.txt": "한글\n", "empty.txt": b""})
        self.assertEqual([self.fake.read_file(R, p) for p in ("b.bin", "k.txt", "empty.txt", "a.txt")], [binary, "한글\n".encode("utf-8"), b"", b"x"])
        commit = self.gh._json("GET", f"/repos/{R}/git/commits/{sha}")
        self.assertEqual((commit["message"], len(commit["parents"])), ("사진 게시: 공중전화", 1))
        self.assertEqual(self.fake.commits(R), 2)
    def test_commits_are_by_the_noreply_identity_not_the_account_profile(self):
        me = {"name": "HPotty36", "email": "112685098+HPotty36@users.noreply.github.com"}
        self.assertEqual(github_api.AUTHOR, me)
        self.gh.commit_files(R, "one commit", lambda read: {"b.txt": "b"})
        _, sha = self.gh.get_json(R, "src/photos.json")
        self.gh.put_json(R, "src/photos.json", {"n": 2}, sha=sha, message="contents write")
        self.assertEqual(self.fake.last_commits(R, 2), [{"message": "contents write", "author": me, "committer": me},
                                                         {"message": "one commit", "author": me, "committer": me}])
    def test_requests_in_order(self):
        photos_blob = next(e["sha"] for e in self.gh.list_dir(R, "src") if e["path"] == "src/photos.json")
        self.fake.requests.clear()
        self.gh.commit_files(R, "m", lambda read: {"y.txt": str(read("src/photos.json")), "a.txt": None, "z.txt": {"sha": photos_blob}})
        shapes = [(m, re.sub(r"[0-9a-f]{40}", "<sha>", p)) for m, p in self.fake.requests]
        shapes = [s for i, s in enumerate(shapes) if i == 0 or s != shapes[i - 1]]  # a request this PC dropped is sent, and logged, twice
        self.assertEqual(shapes, [("GET", f"/repos/{R}/git/ref/heads/main"), ("GET", f"/repos/{R}/git/commits/<sha>"),
                                  ("GET", f"/repos/{R}/contents/src/photos.json"), ("POST", f"/repos/{R}/git/blobs"),
                                  ("POST", f"/repos/{R}/git/trees"), ("POST", f"/repos/{R}/git/commits"),
                                  ("PATCH", f"/repos/{R}/git/refs/heads/main")])
    def test_read_json_reads_the_base_commit_and_missing_is_none(self):
        seen = {}
        def build(read):
            seen.update(photos=read("src/photos.json"), missing=read("nope.json"))
            return {}
        self.assertIsNone(self.gh.commit_files(R, "m", build))
        self.assertEqual(seen, {"photos": {"n": 1}, "missing": None})
    def test_get_json_reads_at_a_ref_and_missing_is_none(self):
        old = self.gh.commit_files(R, "m", lambda read: {"d.json": '{"v": 1}'})
        self.fake.put_file(R, "d.json", b'{"v": 2}')
        self.assertEqual(self.gh.get_json(R, "d.json", ref=old)[0], {"v": 1})
        self.assertEqual(self.gh.get_json(R, "d.json")[0], {"v": 2})
        self.assertIsNone(self.gh.get_json(R, "nope.json"))
        self.assertIsNone(self.gh.get_json(R, "nope.json", ref=old))
    def test_gives_up_after_three_attempts_with_the_message(self):
        calls = []
        def build(read):
            calls.append(1)
            self.fake.put_file(R, "x.txt", str(len(calls)).encode())  # someone commits during every attempt
            return {"y.txt": "y"}
        with self.assertRaises(github_api.CommitError) as cm:
            self.gh.commit_files(R, "m", build)
        self.assertEqual((len(calls), cm.exception.status, str(cm.exception)),
                         (3, 422, "다른 곳에서 동시에 바뀌었어요. 새로고침 후 다시 해 주세요"))
        self.assertIsNone(self.fake.read_file(R, "y.txt"))
    def test_other_errors_are_raised_not_retried(self):
        real, patches = urllib.request.urlopen, []
        def urlopen(request, **kwargs):
            if request.get_method() != "PATCH":
                return real(request, **kwargs)
            patches.append(1)
            raise urllib.error.HTTPError(request.full_url, 500, "boom", {}, io.BytesIO(b'{"message": "boom"}'))
        with mock.patch.object(github_api.urllib.request, "urlopen", urlopen), self.assertRaises(github_api.GitHubError) as cm:
            self.gh.commit_files(R, "m", lambda read: {"y.txt": "y"})
        self.assertNotIsInstance(cm.exception, github_api.CommitError)
        self.assertEqual((cm.exception.status, len(patches)), (500, 1))
    def test_unsupported_change_makes_no_commit(self):
        with self.assertRaises(TypeError):
            self.gh.commit_files(R, "m", lambda read: {"y.txt": 5})
        self.assertEqual(self.fake.commits(R), 1)

    # A PATCH can land and still lose its answer (this PC resets some connections after the server answered). The client
    # sends it again; what it then hears must not make it publish twice.
    def lose_patch_answers(self, count, *, landing, between=None):
        """The first `count` PATCH requests get no answer (the connection resets). With `landing` the first one reaches
        the server before that; `between` runs once after it, before the client sends again."""
        real, state = urllib.request.urlopen, {"patches": 0}
        def urlopen(request, **kwargs):
            if request.get_method() != "PATCH":
                return real(request, **kwargs)
            state["patches"] += 1
            if state["patches"] > count:
                return real(request, **kwargs)
            if state["patches"] == 1:
                if landing:
                    with real(request, **kwargs) as response:
                        response.read()
                if between:
                    between()
            raise ConnectionResetError(10054, "reset")
        return mock.patch.object(github_api.urllib.request, "urlopen", urlopen)
    def build_y(self, calls):
        def build(read):
            calls.append(1)
            return {"y.txt": "y"}
        return build
    def head(self):
        return self.gh._json("GET", f"/repos/{R}/git/ref/heads/main")["object"]["sha"]
    def parent_of_head(self):
        return self.gh._json("GET", f"/repos/{R}/git/commits/{self.head()}")["parents"][0]["sha"]
    def other_commit(self):
        self.fake.put_file(R, "other.txt", b"o")
    def test_a_patch_that_landed_and_lost_its_answer_is_not_built_again(self):
        calls = []
        with self.lose_patch_answers(1, landing=True, between=self.other_commit), mock.patch.object(github_api, "time"):
            commit = self.gh.commit_files(R, "m", self.build_y(calls))
        self.assertEqual(len(calls), 1)  # the resend gets 422 because another commit came first, but ours is in main
        self.assertEqual(commit, self.parent_of_head())
        self.assertEqual((self.fake.read_file(R, "y.txt"), self.fake.read_file(R, "other.txt"), self.fake.commits(R)), (b"y", b"o", 3))
    def test_every_patch_try_lost_but_the_first_landed(self):
        calls = []
        with self.lose_patch_answers(4, landing=True), mock.patch.object(github_api, "time"):
            commit = self.gh.commit_files(R, "m", self.build_y(calls))
        self.assertEqual((len(calls), commit, self.fake.commits(R)), (1, self.head(), 2))
    def test_every_patch_try_lost_and_another_commit_landed_after_ours(self):
        calls = []
        with self.lose_patch_answers(4, landing=True, between=self.other_commit), mock.patch.object(github_api, "time"):
            commit = self.gh.commit_files(R, "m", self.build_y(calls))
        self.assertEqual((len(calls), commit, self.fake.commits(R)), (1, self.parent_of_head(), 3))
    def test_a_patch_that_never_landed_starts_over_on_the_new_main(self):
        calls = []
        with self.lose_patch_answers(1, landing=False, between=self.other_commit), mock.patch.object(github_api, "time"):
            commit = self.gh.commit_files(R, "m", self.build_y(calls))
        self.assertEqual((len(calls), commit, self.fake.commits(R)), (2, self.head(), 3))
        self.assertEqual((self.fake.read_file(R, "y.txt"), self.fake.read_file(R, "other.txt")), (b"y", b"o"))
    def test_every_patch_try_lost_and_none_landed_starts_over(self):
        calls = []
        with self.lose_patch_answers(4, landing=False), mock.patch.object(github_api, "time"):
            commit = self.gh.commit_files(R, "m", self.build_y(calls))
        self.assertEqual((len(calls), commit, self.fake.commits(R)), (2, self.head(), 2))
    def test_our_commit_is_found_a_few_commits_down(self):
        def three_commits():
            for name in ("o1", "o2", "o3"):
                self.fake.put_file(R, name, b"o")
        calls = []
        with self.lose_patch_answers(1, landing=True, between=three_commits), mock.patch.object(github_api, "time"):
            commit = self.gh.commit_files(R, "m", self.build_y(calls))
        walk = self.head()
        for _ in range(3):
            walk = self.gh._json("GET", f"/repos/{R}/git/commits/{walk}")["parents"][0]["sha"]
        self.assertEqual((len(calls), commit, self.fake.commits(R)), (1, walk, 5))


class Reply:
    """What urlopen returns, for tests that need no server."""
    def __init__(self, body=b"", status=200):
        self.body, self.status = body, status
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return False
    def read(self):
        return self.body


def http_error(status, message="boom"):
    return urllib.error.HTTPError("http://github.test/", status, "reason", {}, io.BytesIO(json.dumps({"message": message}).encode("utf-8")))


LISTING = Reply(json.dumps([{"name": "a.txt", "path": "a.txt", "sha": "s1", "type": "file", "size": 1, "url": "u"}]).encode("utf-8"))


class RequestTests(unittest.TestCase):
    """The client by itself, against canned answers: no server, no real network."""

    def setUp(self):
        self.gh = github_api.GitHub("test-token", api="http://github.test")
        patcher = mock.patch.object(github_api, "time")  # no real waiting between repeats
        self.time = patcher.start(); self.addCleanup(patcher.stop)
        self.sent = []
    def answering(self, *replies):
        """urlopen answers with these in turn (an exception is raised, the last one repeats) and records each request."""
        pending = list(replies)
        def urlopen(request, **kwargs):
            self.sent.append((request.get_method(), request.full_url, dict(request.header_items()), request.data))
            reply = pending.pop(0) if len(pending) > 1 else pending[0]
            if isinstance(reply, BaseException):
                raise reply
            return reply
        return mock.patch.object(github_api.urllib.request, "urlopen", urlopen)
    def waits(self):
        return [call.args[0] for call in self.time.sleep.call_args_list]

    def test_each_kind_of_drop_is_repeated(self):
        for err in (ConnectionResetError(10054, "reset"), ConnectionAbortedError(10053, "aborted"), BrokenPipeError(32, "pipe"),
                    http.client.RemoteDisconnected("closed"), http.client.IncompleteRead(b"par"),
                    urllib.error.URLError(ConnectionResetError(10054, "reset"))):
            with self.subTest(type(err).__name__), self.answering(err, LISTING):
                self.sent.clear(); self.time.reset_mock()
                self.assertEqual(self.gh.list_dir(R, ""), [{"name": "a.txt", "path": "a.txt", "sha": "s1", "type": "file"}])
                self.assertEqual((len(self.sent), self.waits()), (2, [0.2]))
    def test_backoff_and_last_chance(self):
        with self.answering(*[ConnectionResetError()] * 3, LISTING):
            self.assertEqual(len(self.gh.list_dir(R, "")), 1)
        self.assertEqual((len(self.sent), self.waits()), (4, [0.2, 0.5, 1.0]))
    def test_gives_up_with_a_github_error(self):
        with self.answering(*[ConnectionResetError(10054, "reset")] * 4, LISTING), self.assertRaises(github_api.GitHubError) as cm:
            self.gh.list_dir(R, "")
        self.assertEqual((cm.exception.status, len(self.sent)), (0, 4))
        self.assertIsInstance(cm.exception.__cause__, ConnectionResetError)
    def test_http_errors_are_not_repeated(self):
        with self.answering(http_error(502, "bad gateway"), LISTING), self.assertRaises(github_api.GitHubError) as cm:
            self.gh.list_dir(R, "")
        self.assertEqual((cm.exception.status, str(cm.exception), len(self.sent), self.waits()), (502, "GitHub 502: bad gateway", 1, []))
    def test_an_error_whose_body_is_cut_short_is_still_that_error(self):
        class Cut(io.BytesIO):
            def read(self, *args):
                raise ConnectionResetError(10054, "reset")
        with self.answering(urllib.error.HTTPError("http://github.test/", 500, "boom", {}, Cut()), LISTING), self.assertRaises(github_api.GitHubError) as cm:
            self.gh.list_dir(R, "")
        self.assertEqual((cm.exception.status, len(self.sent)), (500, 1))
    def test_other_connection_failures_are_not_repeated(self):
        for err in (ConnectionRefusedError(10061, "refused"), TimeoutError("timed out"), urllib.error.URLError("no route")):
            with self.subTest(type(err).__name__), self.answering(err, LISTING), self.assertRaises(github_api.GitHubError) as cm:
                self.sent.clear()
                self.gh.list_dir(R, "")
            self.assertEqual((cm.exception.status, len(self.sent), self.waits()), (0, 1, []))
    def test_request_shape(self):
        with self.answering(Reply(b"x")):
            self.assertEqual(self.gh.get_bytes(R, "d/한글 1.jpg"), b"x")
        with self.answering(Reply(json.dumps({"content": base64.b64encode(b"{}").decode("ascii"), "sha": "s"}).encode("utf-8"))):
            self.gh.get_json(R, "a.json", ref="a/b")
        method, url, headers, body = self.sent[0]
        self.assertEqual((method, url, body), ("GET", f"http://github.test/repos/{R}/contents/d/%ED%95%9C%EA%B8%80%201.jpg", None))
        self.assertEqual((headers["Authorization"], headers["Accept"]), ("Bearer test-token", "application/vnd.github.raw"))
        self.assertEqual((headers["X-github-api-version"], headers["User-agent"]), ("2022-11-28", "nocturne-worker"))
        self.assertNotIn("Content-type", headers)
        self.assertEqual(self.sent[1][1], f"http://github.test/repos/{R}/contents/a.json?ref=a%2Fb")
        self.assertEqual(self.sent[1][2]["Accept"], "application/vnd.github+json")
    def test_without_a_token_there_is_no_authorization_header(self):
        with self.answering(LISTING):
            github_api.GitHub(None, api="http://github.test/").list_dir(R, "")
        self.assertEqual(self.sent[0][1], f"http://github.test/repos/{R}/contents/")  # the trailing slash of api is dropped
        self.assertNotIn("Authorization", self.sent[0][2])
    def test_put_json_body(self):
        with self.answering(Reply(b'{"content": {"sha": "new"}}')):
            self.assertEqual(self.gh.put_json(R, "queue/k/item.json", {"error": "꺼짐", "n": [1]}, sha="old", message="m"), "new")
            self.gh.put_json(R, "queue/k/item.json", {}, sha=None, message="m")
        method, url, headers, body = self.sent[0]
        sent = json.loads(body)
        self.assertEqual((method, headers["Content-type"]), ("PUT", "application/json"))
        self.assertEqual((sent["message"], sent["sha"]), ("m", "old"))
        self.assertEqual(base64.b64decode(sent["content"]).decode("utf-8"), '{\n  "error": "꺼짐",\n  "n": [\n    1\n  ]\n}\n')
        self.assertNotIn("sha", json.loads(self.sent[1][3]))
        self.assertEqual((sent["author"], sent["committer"]), (github_api.AUTHOR, github_api.AUTHOR))
    def test_another_identity_can_be_given(self):
        other = {"name": "x", "email": "x@example.test"}
        with self.answering(Reply(b'{"content": {"sha": "new"}}')):
            github_api.GitHub("t", api="http://github.test", author=other).put_json(R, "a.json", {}, sha=None, message="m")
        sent = json.loads(self.sent[0][3])
        self.assertEqual((sent["author"], sent["committer"]), (other, other))
    def test_put_json_that_landed_before_a_lost_answer_is_not_a_conflict(self):
        data = {"status": "pc"}
        text = github_api.dump_json(data).encode("utf-8")
        ours = hashlib.sha1(b"blob %d\0" % len(text) + text).hexdigest()
        url = f"http://github.test/repos/{R}/contents/q/item.json"
        for status in (409, 422):  # 409: an existing file's sha is stale now; 422: the file we meant to create exists now
            with self.subTest(status), self.answering(ConnectionResetError(10054, "reset"), http_error(status),
                                                      Reply(json.dumps({"type": "file", "sha": ours}).encode("utf-8"))):
                self.sent.clear()
                self.assertEqual(self.gh.put_json(R, "q/item.json", data, sha=None if status == 422 else "old", message="m"), ours)
                self.assertEqual([(m, u) for m, u, _, _ in self.sent], [("PUT", url), ("PUT", url), ("GET", url)])
                self.assertEqual(self.sent[2][2]["Accept"], "application/vnd.github+json")
    def test_put_json_resent_and_the_file_holds_something_else_is_a_conflict(self):
        for label, answer in (("another writer", Reply(json.dumps({"type": "file", "sha": "someone-elses"}).encode("utf-8"))),
                              ("gone", http_error(404)), ("a folder", Reply(b'[{"name": "x"}]'))):
            with self.subTest(label), self.answering(ConnectionResetError(10054, "reset"), http_error(409), answer), \
                    self.assertRaises(github_api.Conflict) as cm:
                self.gh.put_json(R, "q/item.json", {"status": "pc"}, sha="old", message="m")
            self.assertEqual(cm.exception.status, 409)
    def test_put_json_conflict_without_a_resend_is_not_second_guessed(self):
        with self.answering(http_error(409), Reply(json.dumps({"type": "file", "sha": "whatever"}).encode("utf-8"))), \
                self.assertRaises(github_api.Conflict):
            self.gh.put_json(R, "q/item.json", {"status": "pc"}, sha="old", message="m")
        self.assertEqual([m for m, *_ in self.sent], ["PUT"])
    def test_put_json_conflicts(self):
        for status in (409, 422):
            with self.subTest(status), self.answering(http_error(status, "sha")), self.assertRaises(github_api.Conflict) as cm:
                self.gh.put_json(R, "x.json", {}, sha="old", message="m")
            self.assertEqual(cm.exception.status, status)
        for status in (401, 403, 404, 500):
            with self.subTest(status), self.answering(http_error(status)), self.assertRaises(github_api.GitHubError) as cm:
                self.gh.put_json(R, "x.json", {}, sha="old", message="m")
            self.assertNotIsInstance(cm.exception, github_api.Conflict)
            self.assertEqual(cm.exception.status, status)
    def test_missing_things(self):
        with self.answering(http_error(404)):
            self.assertEqual(self.gh.list_dir(R, "nope"), [])
        with self.answering(http_error(404)):
            self.assertIsNone(self.gh.get_json(R, "nope.json"))
        with self.answering(http_error(404)), self.assertRaises(github_api.GitHubError) as cm:
            self.gh.get_bytes(R, "nope.jpg")
        self.assertEqual(cm.exception.status, 404)
    def test_a_file_is_not_a_folder(self):
        with self.answering(Reply(b'{"type": "file", "name": "a.txt"}')), self.assertRaises(github_api.GitHubError):
            self.gh.list_dir(R, "a.txt")
