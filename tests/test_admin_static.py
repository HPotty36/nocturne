import unittest

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
        self.assertIn("connect-src https://api.github.com http://127.0.0.1:*;", t)
        self.assertIn("script-src 'self';", t)
        self.assertNotRegex(t, r"<script(?![^>]*\bsrc=)")
        self.assertNotIn(" style=", t)
    def test_tokens_match_site(self):
        self.assertEqual(helpers.theme_tokens(helpers.ROOT / "admin" / "admin.css"),
                         helpers.theme_tokens(helpers.ROOT / "src" / "template.html"))
    LABELS = ["새 사진", "전시 중", "잠금 비밀번호", "토큰을 다시 넣어 주세요", "로그아웃", "대표로 지정", "AI로 다시 쓰기",
              "AI가 다시 쓰는 중…", "이미 게시한 사진은 GitHub 기록에 남습니다",
              "대표 사진은 다른 사진을 대표로 지정한 뒤 지울 수 있어요", "사이트 반영 중…", "반영됨", "반영 실패",
              "올리는 중…", "올리지 못했어요", "다시 올리기", "AI 대기", "PC AI가 보는 중…", "GitHub AI가 보는 중… (몇 분)",
              "AI 실패", "다시 시도", "직접 입력해서 게시", "버리기", "게시됨", "흑백 감지", "위치 정보 삭제됨",
              "AI가 오래 걸리고 있어요", "버려도 올린 사진은 공개 저장소의 기록에 남아요"]
    def test_labels(self):
        t = "".join(p.read_text(encoding="utf-8") for p in js_files()) + (helpers.ROOT / "admin" / "index.html").read_text(encoding="utf-8")
        for s in self.LABELS:
            self.assertIn(s, t)


if __name__ == "__main__":
    unittest.main()
