import re
import subprocess
import unittest

import helpers


class WorkflowTests(unittest.TestCase):
    def text(self, p): return (helpers.ROOT / p).read_text(encoding="utf-8")
    def ignored(self, path):
        # exit code 0 = ignored, 1 = not ignored, anything else = git itself failed
        return subprocess.run(["git", "check-ignore", "-q", path], cwd=helpers.ROOT, capture_output=True).returncode
    def test_site(self):
        t = self.text(".github/workflows/site.yml")
        for s in ("branches: [main]", "workflow_dispatch", "pages: write", "id-token: write", 'python-version: "3.14"',
                  "pip install -r requirements.txt", "python scripts/build.py --out _site", "path: _site", "actions/deploy-pages@",
                  "group: pages"):
            self.assertIn(s, t)
        self.assertNotIn("queue/**", t)
    def test_site_rebuilds_for_its_files(self):
        t = self.text(".github/workflows/site.yml")
        paths = t[t.index("    paths:\n"):t.index("  workflow_dispatch:")]
        self.assertEqual(re.findall(r'^      - "([^"]+)"$', paths, re.M),
                         ["src/**", "photos/**", "admin/**", "icons/**", "scripts/build.py", "scripts/photolib.py", "requirements.txt"])
    def test_draft(self):
        t = self.text(".github/workflows/draft.yml")
        for s in ("queue/**", "cron:", "workflow_dispatch", "contents: write", "actions: write", "group: draft",
                  "timeout-minutes: 60", "'올림'", "'다시 시도'", "worker.py --by github --check",
                  "worker.py --by github --run --ai claude", "worker.py --by github --run --ai ollama", "v0.35.1",
                  "OLLAMA_MODELS", "ollama-gemma4-e4b-it-qat", "NOCTURNE_MODEL: gemma4:e4b-it-qat",
                  "NOCTURNE_CLAUDE_MODEL: claude-opus-5-5", "gh workflow run site.yml"):
            self.assertIn(s, t)
    def test_draft_does_not_wait_for_the_pc(self):
        # GitHub takes an upload at once now: the PC is the one that waits (5 minutes, in the worker)
        t = self.text(".github/workflows/draft.yml")
        self.assertNotIn("sleep 60", t)
        self.assertNotIn("PC가 먼저 맡을 시간 주기", t)
    def test_draft_installs_requirements_before_worker(self):
        t = self.text(".github/workflows/draft.yml")
        # the site's own requirements install must not depend on the Claude SDK: a failed SDK install only leaves
        # the Claude pass to hand its photos to e4b (the worker reads the missing package as a Claude failure)
        base_install = "      - run: pip install -r requirements.txt\n"
        self.assertIn(base_install, t)
        sdk = self.step(t, "Claude SDK 설치")
        self.assertIn("continue-on-error: true", sdk)
        self.assertIn("run: pip install --require-hashes -r requirements-ai.txt", sdk)
        base, sdk_at = t.index(base_install), t.index("- name: Claude SDK 설치")
        self.assertLess(t.index("actions/setup-python@"), base)
        self.assertLess(base, sdk_at)
        self.assertLess(sdk_at, t.index("- name: Claude로 초안 쓰고 게시"))
        self.assertLess(sdk_at, t.index("worker.py"))
    def test_claude_sdk_is_pinned_with_hashes(self):
        # pip install --require-hashes takes only requirements pinned to one version with their hashes, dependencies
        # included; a file that changed on PyPI (or on the way) is not installed
        t = self.text("requirements-ai.txt")
        self.assertTrue(t.startswith("# "))  # says how the pins were made and how to refresh them
        self.assertIn("pip download", t)
        self.assertIn("https://pypi.org/pypi/<name>/<version>/json", t)
        lines = [line for line in t.replace("\\\n", " ").splitlines() if line.strip() and not line.startswith("#")]
        pins = {}
        for line in lines:
            found = re.fullmatch(r"([a-z0-9-]+)==([0-9][0-9a-z.]*)((?: +--hash=sha256:[0-9a-f]{64})+)", line)
            self.assertIsNotNone(found, line)
            pins[found[1]] = found[2]
        self.assertEqual(list(pins), sorted(pins))
        self.assertEqual(len(pins), len(lines))  # each package once
        major, minor = map(int, pins["anthropic"].split(".")[:2])
        self.assertTrue(major == 1 and minor >= 17, pins["anthropic"])
        for name in ("pydantic", "pydantic-core", "jiter", "anyio", "typing-extensions"):  # some of what it brings
            self.assertIn(name, pins)
    def test_draft_steps_run_claude_first_then_ollama_for_what_is_left(self):
        t = self.text(".github/workflows/draft.yml")
        self.assertEqual(re.findall(r"^      - name: (.+)$", t, re.M),
                         ["Claude SDK 설치", "Claude로 초안 쓰고 게시", "대표 사진 고르기", "남은 사진 찾기", "Ollama v0.35.1 내려받기",
                          "모델 폴더 캐시 불러오기",
                          "Ollama 서버 켜기", "모델 내려받기", "모델 폴더 캐시 저장", "e4b로 초안 쓰고 게시",
                          "사이트 다시 만들기"])
        claude = self.step(t, "Claude로 초안 쓰고 게시")
        self.assertIn("id: claude", claude)
        self.assertIn("continue-on-error: true", claude)  # a crash must not keep e4b from the rest
        self.assertIn("run: python scripts/worker.py --by github --run --ai claude", claude)
        self.assertNotIn("if:", claude)  # the worker itself takes nothing without a key
        cover = self.step(t, "대표 사진 고르기")  # before Ollama is downloaded: the key never meets the Ollama binary
        self.assertIn("id: cover", cover)
        self.assertIn("continue-on-error: true", cover)  # a failure here must not keep e4b from the rest
        self.assertIn("run: python scripts/cover.py", cover)
        self.assertNotIn("if:", cover)  # it runs on every trigger: photos published by e4b or the PC are picked up later
        check = self.step(t, "남은 사진 찾기")
        self.assertIn("id: check", check)
        self.assertIn("run: python scripts/worker.py --by github --check", check)
        self.assertNotIn("--ai", check)
        # nothing left for Ollama: it is not downloaded, cached, started or run
        for name in ("Ollama v0.35.1 내려받기", "모델 폴더 캐시 불러오기", "Ollama 서버 켜기", "모델 내려받기",
                     "모델 폴더 캐시 저장", "e4b로 초안 쓰고 게시"):
            self.assertIn("if: steps.check.outputs.names != ''", self.step(t, name), name)
        ollama = self.step(t, "e4b로 초안 쓰고 게시")
        self.assertIn("id: run", ollama)
        # only what was left when the check ran: a photo that lands while Ollama is set up waits for the next run's
        # Claude pass (its own push has already queued that run)
        self.assertIn('run: python scripts/worker.py --by github --run --ai ollama --only "${{ steps.check.outputs.names }}"',
                      ollama)
        self.assertNotIn("continue-on-error", ollama)
    def test_draft_gating(self):
        t = self.text(".github/workflows/draft.yml")
        self.assertIn("steps.check.outputs.names != ''", t)
        # published photos still trigger a rebuild when another item errored
        self.assertIn("if: ${{ !cancelled() && ((steps.claude.outputs.published != '' && steps.claude.outputs.published != '0')"
                      " || (steps.run.outputs.published != '' && steps.run.outputs.published != '0')"
                      " || steps.cover.outputs.changed == '1') }}", t)
        self.assertIn("if: ${{ !cancelled()", self.step(t, "사이트 다시 만들기"))
    def test_draft_model_cache_saved_right_after_pull(self):
        t = self.text(".github/workflows/draft.yml")
        for s in ("actions/cache/restore@", "actions/cache/save@", "steps.cache.outputs.cache-hit != 'true'"):
            self.assertIn(s, t)
        self.assertLess(t.index("actions/cache/restore@"), t.index("ollama pull"))
        self.assertLess(t.index("ollama pull"), t.index("actions/cache/save@"))
        self.assertLess(t.index("actions/cache/save@"), t.index("worker.py --by github --run --ai ollama"))
    def test_draft_concurrency_is_per_job(self):
        t = self.text(".github/workflows/draft.yml")
        # a push whose job `if:` is false (every PC claim commit) must not take the pending slot
        self.assertEqual(t.count("concurrency:"), 1)
        self.assertGreater(t.index("concurrency:"), t.index("jobs:"))
        self.assertGreater(t.index("concurrency:"), t.index("if: github.event_name != 'push'"))
        self.assertLess(t.index("concurrency:"), t.index("runs-on:"))
    def test_draft_gh_token_only_on_rebuild_step(self):
        t = self.text(".github/workflows/draft.yml")
        self.assertEqual(t.count("GH_TOKEN"), 1)
        self.assertGreater(t.index("GH_TOKEN"), t.index("!cancelled()"))
        self.assertLess(t.index("GH_TOKEN"), t.index("gh workflow run site.yml"))
        self.assertIn("GITHUB_TOKEN: ${{ github.token }}", t)
    def step(self, t, name):
        """The text of the draft step called `name`, up to the next step."""
        start = t.index(f"- name: {name}")
        end = t.find("\n      - ", start + 1)
        return t[start:] if end == -1 else t[start:end]
    def test_draft_checkout_keeps_no_token(self):
        t = self.text(".github/workflows/draft.yml")
        checkout = t[t.index("- uses: actions/checkout@"):t.index("- uses: actions/setup-python@")]
        self.assertIn("persist-credentials: false", checkout)
    def test_draft_write_token_only_on_the_worker_steps(self):
        # the downloaded Ollama binary runs in other steps: it must never see the write-capable token
        t = self.text(".github/workflows/draft.yml")
        job_env = t[t.index("runs-on:"):t.index("    steps:")]
        self.assertNotIn("GITHUB_TOKEN", job_env)
        self.assertEqual(t.count("GITHUB_TOKEN:"), 4)
        for name in ("Claude로 초안 쓰고 게시", "대표 사진 고르기", "남은 사진 찾기", "e4b로 초안 쓰고 게시"):
            self.assertIn("env:\n          GITHUB_TOKEN: ${{ github.token }}", self.step(t, name), name)
        for name in ("Ollama v0.35.1 내려받기", "모델 폴더 캐시 불러오기", "Ollama 서버 켜기", "모델 내려받기",
                     "모델 폴더 캐시 저장"):
            self.assertNotIn("TOKEN", self.step(t, name), name)
    def test_draft_anthropic_key_only_on_the_claude_steps(self):
        # the key is a secret: only the Claude pass and the cover step get it, both before Ollama is downloaded; never the
        # job env, the Ollama steps, or the steps after them
        t = self.text(".github/workflows/draft.yml")
        job_env = t[t.index("runs-on:"):t.index("    steps:")]
        self.assertNotIn("ANTHROPIC", job_env)
        # nothing before the Claude step names the key: not the triggers, the job env, nor the unnamed or SDK steps
        self.assertNotIn("ANTHROPIC", t[:t.index("- name: Claude로 초안 쓰고 게시")])
        self.assertLess(t.index("jobs:"), t.index("- name: Claude로 초안 쓰고 게시"))
        self.assertEqual(t.count("secrets."), 2)
        self.assertEqual(t.count("ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}"), 2)
        for name in ("Claude로 초안 쓰고 게시", "대표 사진 고르기"):
            self.assertIn("env:\n          GITHUB_TOKEN: ${{ github.token }}\n          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}",
                          self.step(t, name), name)
        self.assertNotIn("ANTHROPIC", t[t.index("- name: 남은 사진 찾기"):])
        for name in ("남은 사진 찾기", "Ollama v0.35.1 내려받기", "모델 폴더 캐시 불러오기", "Ollama 서버 켜기",
                     "모델 내려받기", "모델 폴더 캐시 저장", "e4b로 초안 쓰고 게시", "사이트 다시 만들기"):
            self.assertNotIn("ANTHROPIC", self.step(t, name), name)
        # the model names are not secrets: the job env carries both, so changing a model is one line
        self.assertIn("NOCTURNE_CLAUDE_MODEL: claude-opus-5-5", job_env)
        self.assertIn("NOCTURNE_MODEL: gemma4:e4b-it-qat", job_env)
    def test_draft_ollama_archive_checksum(self):
        t = self.text(".github/workflows/draft.yml")
        download = self.step(t, "Ollama v0.35.1 내려받기")
        # sha256 of ollama-linux-amd64.tar.zst from the v0.35.1 release's sha256sum.txt
        self.assertIn("9fcd79ac4575b2bd31b992eee18b1000c8ad126b451627c8f8cd091714cfbb10", download)
        self.assertIn("sha256sum -c", download)
        self.assertLess(download.index("curl "), download.index("sha256sum -c"))
        self.assertLess(download.index("sha256sum -c"), download.index("tar --zstd"))
    def test_index_not_tracked(self):
        lines = self.text(".gitignore").splitlines()
        for s in ("/index.html", "_site/", "tests/browser/out/"):
            self.assertIn(s, lines)
        self.assertEqual(self.ignored("index.html"), 0)
        self.assertEqual(self.ignored("admin/index.html"), 1)  # the admin page must stay committable
