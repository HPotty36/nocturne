import re
import unittest
from urllib.parse import urlsplit


import helpers


def js_files(): return list((helpers.ROOT / "admin").rglob("*.js"))
class AdminStaticTests(unittest.TestCase):
    def test_no_html_injection(self):
        for p in js_files():
            t = p.read_text(encoding="utf-8")
            for bad in ("innerHTML", "outerHTML", "insertAdjacentHTML", "eval(", "new Function"):
                self.assertNotIn(bad, t, f"{p.name}: {bad}")
    def test_csp_and_no_inline(self):
        t = (helpers.ROOT / "admin" / "index.html").read_text(encoding="utf-8")
        config = (helpers.ROOT / "admin" / "config.js").read_text(encoding="utf-8")
        exchange = urlsplit(re.search(r"exchange: '([^']+)'", config).group(1))
        origin = f"{exchange.scheme}://{exchange.netloc}"  # the Worker: the CSP and config.js never drift apart
        self.assertIn(f"connect-src https://api.github.com {origin} http://127.0.0.1:*;", t)
        self.assertIn("script-src 'self';", t)
        self.assertNotRegex(t, r"<script(?![^>]*\bsrc=)")
        self.assertNotIn(" style=", t)
    def test_no_stored_token(self):
        self.assertFalse((helpers.ROOT / "admin" / "lib" / "vault.js").exists())
        texts = {p: p.read_text(encoding="utf-8") for p in js_files()}
        for p, t in texts.items():
            self.assertNotIn("seal(", t, p.name)
        # the old sealed token's key: only the constant that removes it on boot
        self.assertEqual(sum(t.count("nocturne.token") for t in texts.values()), 1)
        self.assertIn("'nocturne.token'", texts[helpers.ROOT / "admin" / "lib" / "auth.js"])
    def test_site_link_opens_a_new_tab(self):
        t = (helpers.ROOT / "admin" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<a class="mark" href="../" target="_blank" rel="noopener">Nocturne</a>', t)
    # --- the look: the site's black room and its one serif ---
    SITE_PAGE = helpers.ROOT / "src" / "template.html"
    ADMIN_CSS = helpers.ROOT / "admin" / "admin.css"
    TOKENS = ("--ground", "--wall", "--ink", "--ink-dim", "--rule")

    @staticmethod
    def first_root(path):
        """Body of the first `:root { }` block of a page or stylesheet (comments stripped)."""
        css = re.sub(r"/\*.*?\*/", "", path.read_text(encoding="utf-8"), flags=re.S)
        return re.search(r":root\s*\{([^}]*)\}", css).group(1)
    def test_tokens_match_site(self):
        site, admin = self.first_root(self.SITE_PAGE), self.first_root(self.ADMIN_CSS)
        scheme = lambda body: re.search(r"color-scheme:\s*([^;]+);", body).group(1).strip()
        self.assertEqual(scheme(admin), scheme(site))
        site_tokens, admin_tokens = helpers.root_tokens(self.SITE_PAGE), helpers.root_tokens(self.ADMIN_CSS)
        for name in self.TOKENS:
            self.assertEqual(admin_tokens[name], site_tokens[name], name)
        for name, value in admin_tokens.items():
            if name not in self.TOKENS:
                self.assertNotRegex(value, r"#[0-9a-fA-F]{3,8}\b|rgb|hsl", f"{name} is a colour the site does not have")
    def test_no_colour_outside_the_tokens(self):
        css = re.sub(r"/\*.*?\*/", "", self.ADMIN_CSS.read_text(encoding="utf-8"), flags=re.S)
        css = css.replace(self.first_root(self.ADMIN_CSS), "")
        css = re.sub(r"color-mix\([^;]*\)", "", css)  # tokens mixed with transparent
        self.assertNotRegex(css, r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(")
    def test_one_font_no_ornament(self):
        css = self.ADMIN_CSS.read_text(encoding="utf-8")
        for gone in ("Cormorant", "IBM Plex", "--lamp", "--lb-", "prefers-color-scheme", "data-theme", "italic",
                     "letter-spacing", "text-transform", "font-weight: 500", "animation", "@keyframes", "border-radius: 2px"):
            self.assertNotIn(gone, css)
        self.assertEqual(set(re.findall(r"font-size:\s*([^;]+);", css)) - {"12px", "13px", "14px", "16px"}, set())
        self.assertEqual(set(re.findall(r"font-weight:\s*([^;]+);", css)) - {"400"}, set())
    def test_font_link(self):
        t = (helpers.ROOT / "admin" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Serif+KR:wght@400&display=swap" crossorigin>', t)
        self.assertNotIn("Cormorant", t)
        self.assertNotIn("site-dot", t)  # the status is a line of text now
    def test_no_room_numerals(self):
        for p in js_files():
            t = p.read_text(encoding="utf-8")
            for gone in ("roman", "room-num", "room-en"):
                self.assertNotIn(gone, t, f"{p.name}: {gone}")

    LABELS = ["새 사진", "전시 중", "GitHub로 로그인", "로그인하는 중…", "로그인을 취소했어요",
              "로그인하지 못했어요. 잠시 뒤 다시 해 주세요", "이 GitHub 계정으로는 관리할 수 없어요",
              "GitHub App이 nocturne 저장소에 설치되어 있지 않아요",
              "로그인이 끝났어요. 다시 로그인해 주세요", "로그아웃했어요", "로그아웃", "대표로 지정", "AI로 다시 쓰기",
              "AI가 다시 쓰는 중…", "이미 게시한 사진은 GitHub 기록에 남습니다",
              "대표 사진은 다른 사진을 대표로 지정한 뒤 지울 수 있어요", "사이트 반영 중…", "반영됨", "반영 실패",
              "올리는 중…", "올리지 못했어요", "다시 올리기", "AI 대기", "PC AI가 보는 중…", "GitHub AI가 보는 중… (몇 분)",
              "AI 실패", "다시 시도", "직접 입력해서 게시", "버리기", "게시됨", "흑백 감지", "위치 정보 삭제됨",
              "AI가 오래 걸리고 있어요", "버려도 올린 사진은 공개 저장소의 기록에 남아요",
              "영어 제목", "영어 설명", "AI 한국어", "AI 영어", "다듬기 대기",
              "공유한 사진", "올리기", "로그인하면 올릴 수 있어요",
              "AI에게 맡기기", "AI가 고름", "직접 고름", "AI가 고를 차례", "AI가 못 고름", "AI에게 맡겼어요. 30분 안에 AI가 고릅니다"]
    def test_labels(self):
        t = "".join(p.read_text(encoding="utf-8") for p in js_files()) + (helpers.ROOT / "admin" / "index.html").read_text(encoding="utf-8")
        for s in self.LABELS:
            self.assertIn(s, t)

    # --- one installed app: the site's; the admin page has no manifest, icons or worker of its own ---
    def test_head_links(self):
        # the site's manifest and icons: installing from the admin page installs the site app, the one app of this
        # origin (two apps on one origin clash on Android)
        t = (helpers.ROOT / "admin" / "index.html").read_text(encoding="utf-8")
        for tag in ('<link rel="manifest" href="../manifest.webmanifest">', '<link rel="icon" href="../icons/icon-192.png">',
                    '<link rel="apple-touch-icon" href="../icons/apple-touch-icon.png">', '<meta name="theme-color" content="#000">'):
            self.assertEqual(t.count(tag), 1, tag)
        self.assertLess(t.index('<link rel="manifest"'), t.index('<script'))
        for gone in ("manifest.webmanifest", "icons"):
            self.assertFalse((helpers.ROOT / "admin" / gone).exists(), gone)
    def test_no_worker_of_its_own(self):
        # the page registers none; admin/sw.js only retires the one older devices still have
        app = (helpers.ROOT / "admin" / "app.js").read_text(encoding="utf-8")
        self.assertNotIn("serviceWorker", app)
        t = (helpers.ROOT / "admin" / "sw.js").read_text(encoding="utf-8")
        self.assertIn("self.registration.unregister()", t)
        self.assertNotIn("fetch", t)

if __name__ == "__main__":
    unittest.main()
