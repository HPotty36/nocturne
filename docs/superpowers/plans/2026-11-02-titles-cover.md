# Vertical Titles and AI-Picked Cover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Set the site name and room names vertically and large (one sticky title column per room, on wide screens and phones), make the cover fill its frame like a main picture, and let Claude re-pick the cover (and its focal point) whenever the photos on show change.

**Architecture:** Look = `src/template.html` CSS/JS plus small markup changes in `scripts/build.py` (room head, cover focus, a second subset font link). Data = three top-level keys in `src/photos.json` (`cover`, `cover_focus`, `cover_pick`), written byte-identically by `photolib.dumps_data` and the admin's `serialize`. Picking = `curator.claude_pick_cover` (one Claude call) driven by a new `scripts/cover.py` step in `draft.yml`, right after the Claude drafting step and before Ollama. Admin = pin on 대표로 지정, release with AI에게 맡기기, and a badge saying who picked.

**Tech Stack:** Python 3 (stdlib + Pillow, `anthropic` only in Actions), plain ES modules, CSS, `unittest`, `node --test` (Node 24), GitHub Actions YAML.

**Spec:** `docs/superpowers/specs/2026-11-02-titles-cover-design.md` (Korean). Read the section your task names.

## Global Constraints

- Colours only through the five tokens; no new tokens, no light theme, no accent. No italic, `letter-spacing`, `text-transform`, bold.
- Font: Noto Serif KR 400 stylesheet unchanged (`https://fonts.googleapis.com/css2?family=Noto+Serif+KR:wght@400&display=swap`, `crossorigin`), plus one 300 stylesheet `https://fonts.googleapis.com/css2?family=Noto+Serif+KR:wght@300&text=<chars>&display=swap` (`crossorigin`). Weight 300 only on the wide-screen `.mark` and room `h2`.
- Text sizes stay 14/13/12px except: phone `.mark` 20px; wide `.mark` `max(56px, min(7.6vw, 124px, (100svh - 22rem) / 4.6))`; room `h2` `clamp(40px, 4vw, 60px)` wide, 28px phone.
- Room photos are never cropped; only the cover is (`object-fit: cover`, `object-position: var(--fx, 50%) var(--fy, 50%)`).
- photos.json top-level order: `site`, `cover`, `cover_focus`, `cover_pick`, then the rest in their order; `cover_focus` and `cover_pick` on one line with compact separators. Focus values: 0–1, two decimals, 0 and 1 as integers.
- `ANTHROPIC_API_KEY` only on the steps `Claude로 초안 쓰고 게시` and `대표 사진 고르기`, both before `남은 사진 찾기`. Never log the key or Claude's raw reply.
- Only metadata-free copies go to Anthropic: `photos/thumb` copies re-encoded at long edge 512px.
- Exact copy: `Claude API 키가 없어서 대표 사진을 고르지 않아요`, `Claude가 대표 사진 고르기를 거절했어요`, commits `대표 사진: <제목> (AI)`, `대표 사진 중심: <제목> (AI)`, `대표 사진: AI에게 맡김`; badges `AI가 고름`, `직접 고름`, `AI가 고를 차례`; button `AI에게 맡기기`; message `AI에게 맡겼어요. 30분 안에 AI가 고릅니다`; warning title `대표 사진`.
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01N2Wa5c7ftK5p8v9aFnmQVH`.
- Tests: `python3 -m unittest discover -s tests` and `node --test "tests/js/*.test.js"`.

## Review Focus

1. `cover` names a photo that is no longer on show (deleted by hand) → the next run picks a new one. (Task 4 `test_plan_picks_when_the_cover_is_gone`)
2. A person presses 대표로 지정 while Claude is choosing → the person's choice stays. (Task 4 `test_a_person_who_pinned_meanwhile_wins`)
3. Claude answers a focus outside 0–1 or NaN → clamped into 0–1 (NaN → 0.5). (Task 3 `test_focus_is_clamped`)
4. A room name with `&`, `#`, a space or a quote → the 300 font URL is percent-encoded and the attribute escaped. (Task 2 `test_title_font_link_encodes_odd_names`)
5. More than 100 photos on show → 100 sent, the current cover among them. (Task 4 `test_candidates_cap_keeps_the_cover`)

---

### Task 1: photos.json cover keys (Python and admin)

**Files:**
- Modify: `scripts/photolib.py` (`_dump`, `dumps_data`; add `COVER_KEYS`, `fingerprint`)
- Modify: `admin/lib/gallery.js` (`dump`, `serialize`)
- Modify: `tests/helpers.py` (`SITE` gains `"cover_focus": [0.42, 0.5]` and `"cover_pick": {"by": "ai", "model": "claude-opus-5-5", "seen": <fingerprint of SITE>}` after `"cover"`), regenerate `tests/fixtures/site.json` and `tests/fixtures/site.canonical.json`
- Test: `tests/test_photodata.py`, `tests/js/gallery.test.js`

**Interfaces:**
- Produces: `photolib.COVER_KEYS = ("cover", "cover_focus", "cover_pick")`; `photolib.fingerprint(data) -> str` (8 hex: sha256 of `"\n".join(sorted(listed(data)))`); `dumps_data` and `serialize` put `COVER_KEYS` (those present) right after `site`, write `cover_focus`/`cover_pick` on one line.

- [ ] **Step 1: Failing tests** — Python `test_cover_keys_follow_site_on_one_line`: `dumps_data({"rooms": [], "cover_pick": {"by": "person"}, "site": {...}, "cover_focus": [0.42, 0.5], "cover": "a1"})` has lines `"cover": "a1",`, `"cover_focus": [0.42,0.5],`, `"cover_pick": {"by":"person"},` in that order between `site` and `rooms`. `test_fingerprint` : equals `hashlib.sha256("a1\na2\nn1".encode()).hexdigest()[:8]` for SITE and changes when a photo is added. JS `serialize puts the cover keys after site on one line` with the same input and the same expected text; existing `serialize matches Python` covers the fixture.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_photodata -v` and `node --test tests/js/gallery.test.js` — FAIL.
- [ ] **Step 3: Implement** in both serializers: reorder top-level keys before dumping (non-cover keys keep their order; cover keys go right after `site`, or first if there is no `site`); one-line paths `("cover_focus",)`, `("cover_pick",)` like the photo path. Update `SITE` and rewrite both fixtures from it.
- [ ] **Step 4: Run** the full Python and JS suites — PASS (fixtures changed for every reader).
- [ ] **Step 5: Commit** `photos.json: cover focus and who picked the cover`.

### Task 2: Vertical titles and the cover frame on the site

**Files:**
- Modify: `scripts/build.py` (`room_html`, `cover_html`, `render_page`, new `title_font_href`)
- Modify: `src/template.html` (head link placeholder `__TITLEFONT__`, CSS, `targetHeight`)
- Test: `tests/test_build.py`

**Interfaces:**
- Consumes: `cover_focus` from Task 1.
- Produces: `build.title_font_href(data) -> str` (full URL); room head markup `<header class="room-head"><h2>…</h2><p class="room-meta"><span class="room-count">…</span>[<span class="room-span">…</span>]</p></header>`; cover button style gains `--fx:<x*100>%;--fy:<y*100>%` when `cover_focus` is set.

- [ ] **Step 1: Failing tests** in `tests/test_build.py`:
  - update the markup asserts to `<h2>강</h2><p class="room-meta"><span class="room-count">2점</span></p></header>` (and the dated/English/empty-room variants).
  - `test_cover_focus`: with SITE's `[0.42, 0.5]` the cover button style contains `--fx:42%;--fy:50%`; without `cover_focus` it contains neither.
  - `test_title_font_link`: both pages contain `<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Serif+KR:wght@300&amp;text=…&amp;display=swap" crossorigin>` whose `text` decodes to the sorted unique characters of `Nocturne강밤RiverNight`.
  - `test_title_font_link_encodes_odd_names` (Review Focus 4): a room named `A&B "#1"` → `title_font_href` has no raw `&`, `#`, space or `"` inside the text value, and the page attribute is escaped.
  - `test_vertical_titles_css`: the CSS has `writing-mode: vertical-rl` for `.room-head` (both widths) and for `.side .mark` inside `@media (min-width: 960px)`, `position: sticky` on `.room-head`, `font-size: max(56px, min(7.6vw, 124px, (100svh - 22rem) / 4.6))`, `font-size: clamp(40px, 4vw, 60px)`, `object-fit: cover` and `object-position: var(--fx, 50%) var(--fy, 50%)` on `.cover-btn img`, `aspect-ratio: 4 / 5` for the phone cover, `text-combine-upright: digits 2`.
  - `test_phone_rows_lower_target`: the script contains `return w < 400 ? 150 : 180;`.
  - update `test_one_dark_theme_one_font` (400 link still there; 300 only via the subset link) and drop `test_cover_label_is_never_wider_than_the_print` (the `min-content` cover is gone).
- [ ] **Step 2: Run** `python3 -m unittest tests.test_build -v` — FAIL.
- [ ] **Step 3: Implement.** `title_font_href`: chars = sorted set of `site.title` + every `name` and `name_en`, `urllib.parse.quote(chars, safe="")`. CSS per spec §1 (the mock-ups' rules: phone grid column gap 10px, sticky top `calc(var(--bar) + env(safe-area-inset-top, 0px) + var(--lead))`; wide grid gap `clamp(20px, 2.4vw, 36px)`, sticky top `var(--lead)`, meta gap 14px (10px phone); cover wide height `calc(100svh - 2 * var(--edge) - 28px)`, phone full-bleed `margin-inline: calc(-1 * var(--edge))` with label `padding-inline: var(--edge)`; `.side nav` margin-top 36px under the vertical mark; rooms `clamp(96px, 11vw, 160px)` apart). Update the header comment in the template.
- [ ] **Step 4: Run** `python3 -m unittest tests.test_build -v` — PASS; build into the scratchpad and look at 1440×900, 1280×720, 960×600 and 390×844 (ko and en).
- [ ] **Step 5: Commit** `Site: vertical site and room names, the cover fills its frame`.

### Task 3: Claude picks a cover

**Files:**
- Modify: `scripts/curator.py` (constants, `cover_schema`, `cover_prompt`, `claude_pick_cover`)
- Test: `tests/test_curator.py`

**Interfaces:**
- Consumes: `_claude_client`, `_claude_failure`, `_unreadable`, `claude_model_name`, `CLAUDE_BETA`, `CLAUDE_MAX_TOKENS`.
- Produces: `claude_pick_cover(candidates, current, *, focus_only=False, model=None, client=None) -> {"file": str, "focus": [x, y], "model": str}` where `candidates` is a list of `{"file", "title", "room", "image": bytes}` (page order) and `current` a file name or None; raises `CuratorError`. `CLAUDE_COVER_REFUSED = "Claude가 대표 사진 고르기를 거절했어요"`.

- [ ] **Step 1: Failing tests** with the existing `FakeClient`/`claude_response`:
  - `test_pick_cover_request`: one call; `content` alternates a text `사진 a1: 스카이라인 (방: 강)` and an `image` block per candidate, then the instructions; the schema's `file.enum` is the candidate files; `output_config.effort == "low"`; `fallbacks == "default"`; with `current="a1"` the instructions contain `지금 대표 사진은 a1입니다`.
  - `test_pick_cover_reply`: reply `{"file": "n1", "focus_x": 0.333, "focus_y": 1.0}` → `{"file": "n1", "focus": [0.33, 1], "model": "claude-opus-5-5"}`.
  - `test_focus_is_clamped` (Review Focus 3): `-0.2 → 0`, `1.7 → 1`, `NaN`/string → `0.5`.
  - `test_focus_only_request`: `focus_only=True` sends one image and no `지금 대표` sentence.
  - `test_pick_cover_failures`: refusal → `CLAUDE_COVER_REFUSED`; `max_tokens` → `CLAUDE_CUT_OFF`; text not JSON or unknown file → message starting `CLAUDE_UNREADABLE`; status 401 → `CLAUDE_BAD_KEY`.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_curator -v` — FAIL.
- [ ] **Step 3: Implement.** Same request settings as `claude_draft`. Instructions (Korean, spec §2 보내는 것) end with `JSON으로만 답하세요: {"file": "<사진 이름>", "focus_x": 0~1, "focus_y": 0~1}`. Focus: clamp to 0–1, `round(v, 2)`, whole values as int (reuse `_tidy`).
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `AI: Claude picks the cover and where to crop it`.

### Task 4: The cover step

**Files:**
- Create: `scripts/cover.py`
- Test: `tests/test_cover.py`

**Interfaces:**
- Consumes: `photolib.fingerprint`, `photolib.listed`, `photolib.find_photo`, `photolib.dumps_data`, `curator.claude_pick_cover`, `curator.CuratorError`, `curator.claude_model_name`, `github_api.GitHub`, `worker.REPO`, `worker.DATA`, `worker._set_output`, `worker._command_data`.
- Produces: `plan(data) -> "pick" | "focus" | None`; `candidates(data, limit=MAX_PHOTOS) -> [(room, photo)]`; `small_copy(raw: bytes) -> bytes`; `run(gh, picker=curator.claude_pick_cover, model=None) -> {"result": "picked" | "focused" | "skipped" | None, "changed": bool}`; `main(argv=None) -> int`. Constants `MAX_PHOTOS = 100`, `EDGE = 512`, `NO_KEY`.

- [ ] **Step 1: Failing tests** (`FakeGitHub` filled like `test_worker.FakeRepo`, a fake picker):
  - `test_plan`: no photos → None; `cover_pick` absent → "pick"; by ai with a stale `seen` → "pick"; by ai with the current fingerprint → None; by person without `cover_focus` → "focus"; by person with focus → None.
  - `test_plan_picks_when_the_cover_is_gone` (Review Focus 1).
  - `test_small_copy`: a 1000×750 JPEG with EXIF camera/GPS → 512×384, no EXIF.
  - `test_candidates_cap_keeps_the_cover` (Review Focus 5): 120 photos with the cover at position 110 → 100 candidates, the cover among them, page order kept.
  - `test_run_picks_and_commits`: picker returns `{"file": "n1", "focus": [0.2, 0.7], "model": "claude-opus-5-5"}` → photos.json has `cover` n1, `cover_focus` [0.2, 0.7], `cover_pick` by ai with that model and the fingerprint; one commit `대표 사진: 공중전화 (AI)`; result `{"result": "picked", "changed": True}`; the picker got 512px images.
  - `test_run_same_cover_same_focus_is_not_a_change`: same file and focus → commit records `seen`, `changed` False.
  - `test_run_focus_only`: pinned cover a2 without focus → picker called with one candidate and `focus_only=True`; commit `대표 사진 중심: 다리 아래 (AI)`; `cover_pick` stays `{"by": "person"}`.
  - `test_a_person_who_pinned_meanwhile_wins` (Review Focus 2): the picker pins a1 by person in the fake repo while it runs → no cover commit, result "skipped".
  - `test_main_without_key`: prints `NO_KEY`, writes `changed=0`, exit 0, no commit. `test_main_warns_on_claude_failure`: picker raising `CuratorError("Claude API 요청이 너무 많아요")` → stdout has `::warning title=대표 사진::Claude API 요청이 너무 많아요`, `changed=0`, exit 0.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_cover -v` — FAIL.
- [ ] **Step 3: Implement.** `run` reads photos.json via `gh.get_json`, decides with `plan`, fetches each thumbnail with `gh.get_bytes`, shrinks it, calls the picker, then `gh.commit_files` whose build re-reads photos.json and writes only if `plan` of the fresh data still gives the same answer (and for "focus" the cover is still the same file); `changed` = cover or focus differs from the fresh data. `main` reads `GITHUB_TOKEN`/`ANTHROPIC_API_KEY`, sets `changed`, prints the warning for `CuratorError`, returns 1 on any other exception (traceback printed).
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `Cover: a Draft step re-picks the cover when the photos on show change`.

### Task 5: draft.yml

**Files:**
- Modify: `.github/workflows/draft.yml`
- Test: `tests/test_workflows.py`

**Interfaces:**
- Consumes: `python scripts/cover.py` writing `changed=0|1`.

- [ ] **Step 1: Failing tests:** step order `Claude SDK 설치, Claude로 초안 쓰고 게시, 대표 사진 고르기, 남은 사진 찾기, …`; `대표 사진 고르기` has `id: cover`, `continue-on-error: true`, env `GITHUB_TOKEN` and `ANTHROPIC_API_KEY`; the key appears exactly twice; no `ANTHROPIC` at or after `남은 사진 찾기`; the rebuild `if:` contains `steps.cover.outputs.changed == '1'`.
- [ ] **Step 2: Run** `python3 -m unittest tests.test_workflows -v` — FAIL.
- [ ] **Step 3: Implement** the step (with a Korean comment like its neighbours) and the rebuild condition.
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `Draft: pick the cover after the Claude pass`.

### Task 6: Admin pin, release and badge

**Files:**
- Modify: `admin/lib/gallery.js` (`setCover`, new `releaseCover`, `coverBadge`)
- Modify: `admin/views/exhibit.js` (store `releaseCover`, button, badge, message)
- Modify: `admin/admin.css` only if the new button needs nothing new (expected: nothing)
- Test: `tests/js/gallery.test.js`, `tests/js/exhibit.test.js`, `tests/test_admin_static.py` (label list)

**Interfaces:**
- Produces: `setCover(data, name)` sets `cover`, `cover_pick = {by: 'person'}`, deletes `cover_focus`; `releaseCover(data)` deletes `cover_pick`; `coverBadge(data) -> 'AI가 고름' | '직접 고름' | 'AI가 고를 차례'`; `ExhibitStore.releaseCover()` commits `대표 사진: AI에게 맡김`.

- [ ] **Step 1: Failing tests:** gallery — `setCover pins the cover for a person and drops its focus` (serialized text equals what `dumps_data` would write: order checked by `serialize`), `releaseCover hands the cover back to the AI`, `coverBadge says who picked`. exhibit — the cover row's tags read `대표 사진 · AI가 고름`; with `{by: 'person'}` they read `대표 사진 · 직접 고름` and `AI에게 맡기기` is shown (hidden otherwise); pressing it makes one commit `대표 사진: AI에게 맡김` without `cover_pick` and shows `AI에게 맡겼어요. 30분 안에 AI가 고릅니다`. Add `AI에게 맡기기` to `LABELS` in `tests/test_admin_static.py`.
- [ ] **Step 2: Run** `node --test "tests/js/*.test.js"` and `python3 -m unittest tests.test_admin_static` — FAIL.
- [ ] **Step 3: Implement.** The badge goes right after `대표 사진` (dim). The button sits with the row's actions and uses the same `act`/busy pattern as 대표로 지정.
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `Admin: a cover picked by hand stays; hand it back to the AI`.

### Task 7: Docs, full check, merge

**Files:**
- Modify: `CLAUDE.md` (구조, 사진 올리는 흐름, photos.json 형식, 관리 페이지, 디자인 메모, 문제가 생기면, 알아둘 것), `README.md`/`README.ko.md` only where they describe the type sizes or the cover.

- [ ] **Step 1:** Update the docs per spec §5 (Korean, short).
- [ ] **Step 2: Run** both full suites — PASS. Build to the scratchpad and screenshot the sizes listed in Task 2 Step 4.
- [ ] **Step 3: Review** the whole branch with one fresh reviewer; fix what it finds; re-run the suites.
- [ ] **Step 4: Commit** `Docs: vertical titles, the AI-picked cover and its step`.
- [ ] **Step 5: Merge**: fast-forward `main` to `titles-cover`, push `main` (retry on network errors), confirm `site.yml` runs.
