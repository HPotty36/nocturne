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
    def test_draft(self):
        t = self.text(".github/workflows/draft.yml")
        for s in ("queue/**", "cron:", "workflow_dispatch", "contents: write", "actions: write", "group: draft",
                  "timeout-minutes: 60", "'올림'", "'다시 시도'", "sleep 60", "worker.py --by github --check",
                  "worker.py --by github --run", "v0.35.1", "OLLAMA_MODELS", "ollama-gemma4-e4b-it-qat",
                  "NOCTURNE_MODEL: gemma4:e4b-it-qat", "gh workflow run site.yml"):
            self.assertIn(s, t)
    def test_draft_installs_requirements_before_worker(self):
        t = self.text(".github/workflows/draft.yml")
        self.assertIn("pip install -r requirements.txt", t)
        self.assertLess(t.index("actions/setup-python@"), t.index("pip install -r requirements.txt"))
        self.assertLess(t.index("pip install -r requirements.txt"), t.index("worker.py --by github --check"))
    def test_draft_gating(self):
        t = self.text(".github/workflows/draft.yml")
        self.assertIn("steps.check.outputs.names != ''", t)
        # published photos still trigger a rebuild when another item errored
        self.assertIn("if: ${{ !cancelled() && steps.run.outputs.published != '' && steps.run.outputs.published != '0' }}", t)
    def test_draft_model_cache_saved_right_after_pull(self):
        t = self.text(".github/workflows/draft.yml")
        for s in ("actions/cache/restore@", "actions/cache/save@", "steps.cache.outputs.cache-hit != 'true'"):
            self.assertIn(s, t)
        self.assertLess(t.index("actions/cache/restore@"), t.index("ollama pull"))
        self.assertLess(t.index("ollama pull"), t.index("actions/cache/save@"))
        self.assertLess(t.index("actions/cache/save@"), t.index("worker.py --by github --run"))
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
        self.assertEqual(t.count("GITHUB_TOKEN:"), 2)
        for name in ("맡을 사진 찾기", "초안 쓰고 게시"):
            self.assertIn("env:\n          GITHUB_TOKEN: ${{ github.token }}", self.step(t, name), name)
        for name in ("Ollama v0.35.1 내려받기", "Ollama 서버 켜기", "모델 내려받기"):
            self.assertNotIn("TOKEN", self.step(t, name), name)
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
