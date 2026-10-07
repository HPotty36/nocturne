# Nocturne — 이름 변경, 전시실 리디자인, 어디서나 올리고 AI가 바로 거는 사진 관리

작성 2026-10-06 · 개정 2 2026-10-06 (모바일 업로드, 자동 게시, 공개 저장소 하나, 12B 자동 다듬기) · 브랜치 `nocturne`

## 목표

1. 사이트 이름을 "건너편"에서 **Nocturne**으로 바꾸고, 저장소와 주소도 `nocturne`으로 옮긴다.
2. 공개 사이트를 실제 온라인 갤러리(Fotografiska, Magnum의 어두운 전시 페이지)를 참고해 **어두운 전시실** 분위기로 다시 디자인한다. 세련되고 정갈하게. 밝은 테마도 유지한다.
3. **폰이든 PC든 브라우저로** 여는 관리 페이지에서 사진을 올린다. 집 PC가 꺼져 있어도 되고, 올린 뒤 페이지를 닫아도 된다.
4. 올린 사진은 AI가 방을 고르고 제목·대체 텍스트를 써서 **바로 게시**한다. 사람은 나중에 관리 페이지에서 고치거나 지운다. 유료 API는 쓰지 않는다.
   - 집 PC가 켜져 있으면 PC의 Ollama + `gemma4:12b-it-qat` (고품질)
   - 꺼져 있으면 GitHub Actions 무료 실행기의 Ollama + `gemma4:e4b-it-qat`
5. 작은 모델(e4b)이 쓴 설명은 PC가 켜져 있을 때 **12B가 자동으로 다시 써서 교체**한다. 사람이 고친 칸과 방 배정은 건드리지 않는다.

## 하지 않는 것

- 게시 전 사람 확인 단계 (사용자 결정: 바로 게시, 수정은 나중에).
- 사진 순서 바꾸기, 새 방 만들기 (지금처럼 `src/photos.json`에서 직접).
- HEIC 파일 그대로 받기. iPhone 사진 앱에서 고르면 iOS가 JPEG로 넘겨준다. HEIC 파일이 그대로 오면 "사진 앱에서 골라 주세요" 안내.
- 같은 사진 중복 감지. 이름이 겹치면 뒤에 번호를 붙인다. 중복은 나중에 지운다.
- 옛 주소(`hpotty36.github.io/across-gallery/`) 이동 안내. 이름을 바꾸면 옛 주소는 404 (사용자 결정).
- 원본 보관. 브라우저에서 줄인 사본만 올라간다. 원본은 폰·PC에 그대로 있다.
- 비공개 저장소. 대기 중인 사진은 공개 저장소의 `queue/`에서 몇 분 기다린다 (사이트에는 안 보이지만 저장소에서는 보임, 위치 정보는 이미 지운 상태).

## 확인된 환경과 시험 결과

- Windows 11, Python 3.14, Node 24, git, `gh`(HPotty36 로그인됨). Ryzen 5 5600 + RX 6600, RAM 32GB.
- Ollama 0.35.1 (자동 업데이트됨), `gemma4:12b-it-qat` 설치됨. Pillow는 아직 설치 안 됨.
- **Ollama 버그**: 이 PC의 0.35.1은 요청의 약 30%가 `wsarecv: An existing connection was forcibly closed` 500으로 실패한다 (GPU·CPU 무관, 모델이 답을 다 만든 뒤 Ollama 본체가 받는 단계). 바로 다시 보내면 성공한다 → **최대 3번 시도**.
- 모델 비교 (기존 사진 7장, 같은 프롬프트, 실패 제외):

| | 12B (PC) | e4b (GitHub용) | e2b (탈락) |
|---|---|---|---|
| 방 정답 | 4/4 | 6/6 | 5/5 |
| 장당 | GPU 약 25초 / CPU 54초 | CPU 4코어 29초 | 19초 |
| 설명 | 가장 정확, 제목 자연스러움 | 사진을 묘사함, 세부를 가끔 놓침 | 2/5가 방 설명 문구를 베낌 |

## 지켜야 할 규칙

- 실명을 어디에도 넣지 않는다.
- 저장소·사이트에 들어가는 사진은 반드시 메타데이터를 지운 사본이다 (관리 페이지의 브라우저 변환 또는 `prepare_photos.py`). 원본을 직접 넣지 않는다.
- 장소 이름 추측 금지. AI 프롬프트에 장소 이름을 쓰지 말라고 넣는다. 사람 확인 없이 게시되므로 잘못 들어가면 관리 페이지에서 고친다.
- `alt`는 무엇이 찍혔는지 한 문장, 흑백이면 끝에 "흑백 사진".
- 문구는 한국어, 짧고 평이하게.
- 사람이 고친 제목·설명과 방 배정은 AI가 절대 다시 바꾸지 않는다.

---

## 1. 공개 사이트 디자인

### 구성 (위에서 아래로)

1. **상단 바** (스크롤해도 위에 고정): 왼쪽 워드마크 *Nocturne*, 오른쪽 방 목차 `I 강 · II 경기장 · III 밤 · IV 순간`. 보고 있는 방은 `--lamp` 색. 아래에 1px 선. 좁은 화면에서는 목차를 가로로 밀어 본다.
2. **대표 사진**: 페이지 폭에 맞춰 크게. 아래에 벽 라벨 — 왼쪽 제목, 오른쪽 날짜·카메라.
3. **소개 한 줄** (`site.lede`): 대표 사진 아래, 흐린 글자, 46자 폭.
4. **방**마다:
   - 머리: 로마 숫자(I, II, …, `build.py`가 순서로 계산) · 한글 방 이름(큰 세리프) · 영문 이름(작은 대문자, 자간 넓게) · 오른쪽 끝 "6점". 아래 설명 한 줄.
   - 사진: 행마다 가로폭을 꽉 채우는 정렬 배치(동적 계획법, 사진은 잘리지 않음). 간격 `clamp(14px, 2vw, 24px)`. 한 행의 높이가 목표의 1.6배를 넘으면 그 행은 목표 높이로 두고 가로를 채우지 않는다 (사진이 1~2장인 방 대비).
   - 사진마다 아래에 늘 보이는 작은 라벨: 제목(세리프 13px) 왼쪽, 날짜(있으면, 11px 흐리게) 오른쪽. 마우스를 올려야 보이던 캡션 덮개는 없앤다.
5. **바닥글**: `© 2026 Nocturne` · `사진 N점 · 사진을 누르면 크게 볼 수 있어요`.
6. **크게 보기**: 동작은 그대로(방향키, 스와이프, Esc, 바깥 클릭으로 닫기, 썸네일 먼저 보이고 원본으로 교체). 버튼·글꼴만 새 토큰으로.

`site.kicker`는 쓰지 않으므로 `photos.json`과 템플릿에서 뺀다. 사진 항목의 `ai` 필드(아래 2장)는 사이트에 표시하지 않는다.

### 토큰

색은 반드시 토큰으로 쓴다. 기본은 어두운 테마, `prefers-color-scheme: light`와 `data-theme="light"`에서 같은 토큰만 바꾼다.

| 토큰 | 어두운 전시실 | 밝은 테마 (흰 벽) | 쓰임 |
|---|---|---|---|
| `--ground` | `#141312` | `#f4f2ee` | 페이지 바탕, 상단 바 |
| `--wall` | `#1c1a18` | `#e8e5df` | 사진이 오기 전 자리 |
| `--ink` | `#e9e4da` | `#1a1917` | 본문 글자 |
| `--ink-dim` | `#8f8a82` | `#6b665f` | 설명, 메타 |
| `--rule` | `#2b2926` | `#d9d5ce` | 구분선 |
| `--lamp` | `#d9a759` | `#8a5a12` | 포커스, 현재 방 표시에만 |

크게 보기 화면(`--lb-*`)은 두 테마 모두 어둡게 유지한다. 대비 기준: `--ink`/`--ground` 7:1, `--ink-dim`/`--ground` 4.5:1, `--lamp`/`--ground` 3:1 이상. 모자라면 `--ink-dim`만 조정한다.

### 글꼴 (Google Fonts)

- 워드마크·영문 표기: **Cormorant Garamond** 이탤릭 400
- 한글 제목·사진 제목: **Noto Serif KR** 400
- 본문·메타: **IBM Plex Sans KR** 400/500, 숫자는 `tabular-nums`

### 정렬 배치

`.print`는 "사진 버튼 + 라벨"을 감싸는 상자. JS는 행 높이를 계산해 **사진 부분**의 폭·높이를 정하고, 라벨은 그 아래에 붙는다. 목표 행 높이는 가로폭 1100px 이상 340px, 720px 이상 260px, 그 아래 200px.

---

## 2. 전체 구조

```
폰/PC 브라우저 ── 관리 페이지 (hpotty36.github.io/nocturne/admin/)
   │  사진 고르기 → 브라우저 안에서 메타데이터 제거·축소·흑백 판별
   ▼
공개 저장소 nocturne ── queue/<name>/{full.jpg, thumb.jpg, item.json}   (대기열)
   │                         ▲                         ▲
   │       20초마다 확인, 먼저 맡음          push로 시작, 60초 기다린 뒤 남은 것만
   │       PC 도우미 (12B)                   Actions draft.yml (e4b)
   │                         └──── 초안 → 바로 게시 (한 커밋) ────┘
   ▼
photos/ + src/photos.json 갱신 ──▶ Actions site.yml ──▶ GitHub Pages (1~2분)

PC 도우미는 대기열이 비면 30분마다 e4b가 쓴 칸을 찾아 12B로 다시 써서 교체한다.
```

### 저장소 `HPotty36/nocturne` (공개, 지금 `across-gallery`의 이름을 바꿈)

- `src/`, `photos/`: 사이트 원본. `index.html`은 커밋하지 않는다 (`.gitignore`).
- `admin/`: 관리 페이지.
- `queue/<name>/`: 대기 중인 업로드. 게시되면 지워진다.
- `scripts/`, `tests/`, `docs/`.
- `.github/workflows/site.yml`: `main`에 `src/**`, `photos/**`, `admin/**`, `scripts/build.py`, `scripts/photolib.py`, `requirements.txt`가 바뀌거나 수동 실행하면 `build.py --out _site`로 `index.html`, `photos/`, `admin/`만 Pages에 배포. `queue/`, `src/`, `scripts/` 등은 사이트로 서빙되지 않는다.
- `.github/workflows/draft.yml`: 아래 4장.

### queue/<name>/item.json

```json
{
  "name": "1234",
  "kind": "new",
  "file": null,
  "uploaded_at": "2026-10-06T12:00:00Z",
  "date": "2025.11.30",
  "camera": "iPhone 12 mini · f/1.6 · 1/121s · ISO 100",
  "grayscale": false,
  "status": "waiting",
  "claimed_at": null,
  "by": null,
  "error": null
}
```

- `kind`: `"new"`(새 사진: `full.jpg`, `thumb.jpg`가 같이 있음) | `"redraft"`(전시 중 사진 `file`의 제목·설명 다시 쓰기, `item.json`만, 이름은 `redraft-<file>`)
- `status`: `"waiting"` | `"pc"` / `"github"`(맡아서 처리 중) | `"failed"`. 게시되면 폴더째 지워지므로 "완료" 상태는 없다.
- **맡기 규칙**
  - PC: `waiting`이거나 10분 넘게 끝나지 않은 `pc`/`github`(`claimed_at` 기준). 단, PC가 AI 실패로 돌려놓은 항목은 30분 동안 다시 맡지 않는다 (PC 도우미 메모리에만 기억하므로 도우미를 다시 켜면 잊는다).
  - GitHub: `waiting`이고 올린 지 60초가 지났거나, 10분 넘게 끝나지 않은 `pc`/`github`.
  - `failed`는 자동으로 다시 맡지 않는다. 사람이 **다시 시도**를 누르면 `waiting`이 된다.
  - 상태 변경은 contents API의 파일 `sha`를 붙여 쓰므로, 동시에 맡으려 하면 늦은 쪽이 409를 받고 물러난다.

### photos.json 사진 항목의 `ai`

```json
{"file":"1234","title":"공중전화","alt":"…","date":"2025.11.30","ai":{"model":"gemma4:e4b-it-qat","fields":["title","alt"],"confidence":0.61}}
```

- AI가 게시할 때 `ai = {"model", "fields": ["title", "alt"], "confidence"}` (확신도를 못 구하면 `confidence` 없음).
- 관리 페이지에서 사람이 저장하면: 바뀐 칸은 `fields`에서 빠지고, `confidence`는 지운다(사람이 봤음). `fields`가 비면 `ai`를 지운다.
- **다듬기 대상**: `ai.fields`가 비어 있지 않고 `ai.model`이 PC 모델(`gemma4:12b-it-qat`)이 아닌 사진.
- 사람이 직접 등록한 사진(AI 실패 후 수동)에는 `ai`가 없다.

---

## 3. 관리 페이지 (`admin/`)

정적 페이지. 외부 스크립트 없음 (Google Fonts CSS만). 모듈로 나눈 바닐라 JS.

### 로그인

- 사용자가 GitHub에서 **fine-grained 토큰**을 직접 만든다: 저장소 `nocturne`만 / Contents 읽기·쓰기 / Actions 읽기 / 만료 1년. 만드는 방법은 `CLAUDE.md`에 단계별로 적는다.
- 처음: 토큰과 **잠금 비밀번호**(8자 이상)를 입력 → WebCrypto PBKDF2-SHA256(310,000회, 무작위 16바이트 salt)로 키를 만들어 AES-GCM(무작위 12바이트 IV)으로 토큰을 암호화해 `localStorage["nocturne.token"]`에 `{v:1, salt, iv, ct}`(base64)로 저장.
- 이후: 비밀번호만 입력 → 복호화한 토큰은 그 탭의 메모리에만. 틀리면 "비밀번호가 맞지 않아요".
- 로그인 직후 `GET /repos/HPotty36/nocturne`로 쓰기 권한(`permissions.push`)을 확인. 없거나 401이면 "토큰을 다시 넣어 주세요".
- **로그아웃**: 저장된 값을 지운다.
- 토큰은 `https://api.github.com`으로만 보낸다. URL에 넣지 않는다.
- CSP(`<meta http-equiv>`): `default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' blob: data:; connect-src https://api.github.com http://127.0.0.1:*; base-uri 'none'; form-action 'none'`. 인라인 스크립트·스타일 없음. (`127.0.0.1`은 로컬 시험용 가짜 GitHub. 앱은 `localhost`에서 열렸을 때만 그 주소를 쓴다.)
- 주의: `hpotty36.github.io`는 이 계정의 모든 Pages 사이트가 같은 출처라서 토큰을 평문으로 두지 않는다.

### 화면

탭 두 개: **새 사진**, **전시 중**. 디자인 토큰은 공개 사이트와 같다. 375px 폭에서 쓸 수 있어야 한다. 위에는 사이트 반영 상태(`site.yml` 최신 실행: "사이트 반영 중…" / "반영됨" / "반영 실패" + Actions 링크, 실행 중일 때만 10초마다 확인).

**새 사진 탭**

- 고르기 버튼(`accept="image/jpeg,image/png"`, 여러 장) + 끌어다 놓기.
- 사진마다 브라우저에서 처리(아래) → `queue/<name>/`에 세 파일을 한 커밋(`대기열: <name> 올림`). 실패하면 "올리지 못했어요" + **다시 올리기** (페이지가 열려 있는 동안 사본을 메모리에 둠).
- 대기열 목록(이 기기에서 올린 것뿐 아니라 `queue/` 전체): 썸네일, 상태.

| 상태 | 표시 | 버튼 |
|---|---|---|
| 처리·업로드 중 | "올리는 중…" | |
| 업로드 실패 | "올리지 못했어요" | 다시 올리기 |
| `waiting` | "AI 대기" (올린 지 15분이 넘으면 "AI가 오래 걸리고 있어요") | 버리기 |
| `pc` | "PC AI가 보는 중…" (맡은 지 10분이 넘으면 "AI가 오래 걸리고 있어요") | 10분이 넘으면 버리기 |
| `github` | "GitHub AI가 보는 중… (몇 분)" (맡은 지 10분이 넘으면 "AI가 오래 걸리고 있어요") | 10분이 넘으면 버리기 |
| `failed` | "AI 실패" + 오류 문구 | 다시 시도 · 직접 입력해서 게시 · 버리기 |
| 대기열에서 사라짐 | "게시됨" (몇 초 뒤 목록에서 빠짐) | |

- 버리기 옆에는 올린 사진이 공개 저장소 기록에 남는다는 한 줄 안내를 둔다. 버리기·다시 시도·직접 게시는 모두 기준 커밋에서 같은 업로드인지(이름·종류·올린 시각) 확인한 뒤에만 쓴다.
- **직접 입력해서 게시**(실패한 것만): 방·제목·설명 입력 → 공개 사진 두 장(대기열 파일의 blob 재사용) + `photos.json` 추가 + 대기열 삭제를 한 커밋(`사진 게시: <제목>`). `ai`는 붙이지 않는다.
- **다시 시도**: `status`를 `waiting`으로(커밋 메시지 `대기열: <name> 다시 시도`, Actions도 다시 시작됨).
- 대기열이 비어 있지 않은 동안 15초마다 다시 읽는다 (폴더 sha가 바뀐 항목만 `item.json`을 다시 읽음).

**전시 중 탭**

- 방별 목록: 썸네일(`../photos/thumb/<name>.jpg`), 제목, 설명, 표시:
  - "확인 필요": `ai.confidence`가 0.8 미만
  - "AI 설명 · 다듬기 대기": `ai.fields`가 있고 `ai.model`이 `gemma4:e4b-it-qat`
  - "AI 설명": `ai.fields`가 있고 그 밖의 모델
- 행마다: 방 드롭다운, 제목, 설명, **저장**(위 `ai` 규칙 적용, `사진 수정: <제목>`), **AI로 다시 쓰기**(`queue/redraft-<file>/item.json`을 올림 → "AI가 다시 쓰는 중…" → 대기열에서 사라지면 목록 새로고침), **대표로 지정**(`대표 사진 변경: <제목>`), **삭제**.
- 방을 옮기면 새 방의 맨 뒤로.
- 삭제 확인 문구: `이 사진을 전시에서 뺄까요? 사이트용 사본이 지워집니다. 이미 게시한 사진은 GitHub 기록에 남습니다.` 대표 사진은 삭제 대신 `대표 사진은 다른 사진을 대표로 지정한 뒤 지울 수 있어요`.

### 브라우저 사진 처리

1. `createImageBitmap(file, {imageOrientation: "from-image"})`로 방향을 바로잡아 읽는다. 읽을 수 없으면(HEIC 등) "이 사진을 읽을 수 없어요. 사진 앱에서 JPEG로 골라 주세요".
2. 긴 변 2400px(`full`, JPEG 품질 0.84), 1000px(`thumb`, 0.78)로 줄여 `canvas.toBlob("image/jpeg")`. 원본보다 작으면 늘리지 않는다. 캔버스 인코딩은 메타데이터를 쓰지 않으므로 GPS를 포함한 모든 EXIF가 사라진다. 색은 sRGB로 저장된다.
3. 내장 EXIF 판독기(외부 라이브러리 없음)는 원본 파일 앞부분에서 `DateTimeOriginal`(없으면 `DateTime`), `Model`, `FNumber`, `ExposureTime`, `ISOSpeedRatings`만 읽는다. GPS IFD는 따라가지 않는다. 날짜·카메라 문자열 형식은 Python `exif_hints`와 같다.
4. 흑백 판별: 썸네일을 긴 변 256px로 줄여 픽셀마다 `max(R,G,B)-min(R,G,B)`, 평균 < 4 그리고 상위 1% < 16이면 흑백 (Python과 같은 규칙).
5. 이름: 파일명에 Python `name_for`와 같은 규칙(비면 `photo`). 이미 전시 중이거나 대기열에 있거나 같은 묶음에 있는 이름이면 `-2`, `-3`, … 중 가장 작은 빈 번호를 붙인다.

### 커밋 방식 (관리 페이지와 Python 공통)

- 여러 파일 변경은 Git Data API로 한 커밋: 브랜치 ref → 기준 커밋 → 필요하면 그 커밋의 `photos.json`을 읽어 변경 적용 → blob(이미 저장소에 있는 파일을 옮길 때는 그 blob sha 재사용) → tree(`base_tree`, 지울 파일은 `sha: null`) → commit → ref 갱신(`force: false`). fast-forward가 아니라서 실패하면 처음부터 다시(최대 3번), 그래도 실패하면 "다른 곳에서 동시에 바뀌었어요. 새로고침 후 다시 해 주세요".
- `item.json` 상태 변경은 contents API `PUT`(sha 포함). 409/422면 다시 읽고 판단.
- `photos.json`은 정규 형식(들여쓰기 2칸, 사진 한 줄에 하나 `{"file":…}` 공백 없는 구분자, 한글 그대로, 끝 줄바꿈)으로 쓴다. Python과 JS가 글자까지 같다.

---

## 4. AI 초안과 게시

### curator (공용, Python)

- `POST {OLLAMA_URL}/api/generate`, 표준 `urllib`만. `model`은 `NOCTURNE_MODEL`(기본 `gemma4:12b-it-qat`). `format: "json"`, `think: false`, `stream: false`, `logprobs: true`, `top_logprobs: 5`, `options.temperature: 0.2`, 제한 300초.
- 프롬프트: 역할, 방 목록(`id`, `name`, `note`), 방마다 기존 사진 최대 2장의 제목·alt 예시, 규칙(제목 2~8글자 명사구 / alt는 한 문장, "입니다"·마침표로 끝내지 않음 / 방 설명 문구를 베끼지 말고 사진에 보이는 것을 / 장소 이름 금지 / 흑백 언급 금지), 출력 `{"room","title","alt"}`.
- 시도: 최대 3번. 연결 거부("Ollama가 꺼져 있어요…")와 모델 없음은 바로 포기.
- 후처리: 없는 방 id → 첫 방, 확신도 0. alt 끝 마침표·"입니다" 제거, 흑백이면 `, 흑백 사진`.
- 확신도: `logprobs`에서 `"room": "` 다음 토큰의 `top_logprobs` 중 방 id 앞부분과 맞는 확률의 합 (소수 셋째 자리 반올림). 못 구하면 없음.
- `status()`: `GET /api/tags`로 `"ok"` / `"off"` / `"no-model"`.

### worker (공용, Python) — PC 도우미와 Actions가 같이 쓴다

한 항목 처리:
1. 맡기: `item.json`을 `pc` 또는 `github`로 (sha 확인, 409면 건너뜀). 메시지 `대기열: <name> PC가 맡음` / `GitHub가 맡음`.
2. 그림: `new`는 `queue/<name>/thumb.jpg`, `redraft`는 `photos/thumb/<file>.jpg`. 흑백은 `item.grayscale`(`redraft`는 썸네일로 판별).
3. `photos.json`(그 순간의 `main`)으로 프롬프트 → curator.
4. 게시 (한 커밋):
   - `new`: 고른 방 맨 뒤에 `{file, title, alt, date?, camera?, ai}` 추가 + `photos/full|thumb/<name>.jpg`(대기열 blob 재사용) + 대기열 폴더 삭제. 메시지 `사진 게시: <제목>`.
   - `redraft`: 그 사진의 제목·설명을 바꾸고 `ai = {model, fields: ["title","alt"]}`(확신도·방은 그대로). 사진이 이미 지워졌으면 대기열만 삭제. 메시지 `AI 설명 다시 쓰기: <제목>`.
5. curator가 끝내 실패하면:
   - GitHub: `failed` + 오류 문구 (`대기열: <name> AI 실패`).
   - PC: `waiting`으로 돌려놓고 오류 문구를 `error`에 남긴다 (`대기열: <name> 다시 시도 (PC AI 실패)`, 이 커밋으로 draft.yml이 시작되어 GitHub가 이어받는다). PC 도우미는 그 항목을 30분 동안 다시 맡지 않는다.
   - 맡은 뒤 AI 밖의 예상 못 한 오류: `failed` + "AI 처리 중 문제가 생겼어요". 대기열 사진 파일이 없거나 읽을 수 없으면 그 사유로 `failed`.
   - 모든 기록은 `item.json`이 아직 내 맡기 상태인지 기준 커밋에서 확인한 뒤 한 커밋으로 쓴다 (그 사이 지워졌으면 아무것도 쓰지 않는다).

### PC 도우미 (`scripts/pc_helper.py`)

- `py scripts\pc_helper.py --setup`: 사용자가 터미널에서 토큰(저장소 `nocturne`, Contents 읽기·쓰기만)을 직접 입력 → `%APPDATA%\nocturne\helper.json`에 저장.
- `py scripts\pc_helper.py`: 20초마다 한 번 "틱":
  1. `curator.status()`가 `"ok"`가 아니면 아무것도 하지 않는다.
  2. PC가 맡을 수 있는 대기 항목이 있으면 가장 오래된 하나를 처리한다.
  3. 대기열이 비었으면 **다듬기**: 30분마다 `photos.json`에서 다듬기 대상을 찾고, 대상이 남아 있는 동안 틱마다 한 장씩: 썸네일로 12B 초안 → 그 순간의 `photos.json`을 다시 읽어 그 사진이 아직 다듬기 대상인지 확인 → `ai.fields`에 남은 칸만 바꾸고 `ai.model`을 12B로 → 한 커밋(`AI 설명 개선: <제목>`). 방·확신도는 그대로. 실패한 사진은 다음 30분 검사까지 건너뛴다.
- 로그는 `%APPDATA%\nocturne\helper.log`에 한 줄씩. 콘솔 창 없이 돌릴 때는 `pyw`.
- 자동 실행: 사용자 확인 후 Windows 작업 스케줄러에 "로그온할 때" 작업 등록.

### GitHub 대타 (`.github/workflows/draft.yml`)

- 시작 조건: `main`에 `queue/**` push(커밋 메시지에 `올림` 또는 `다시 시도`가 있을 때만 작업 실행), 30분마다 `schedule`(맡다 멈춘 항목 대비), 수동 실행.
- `permissions: {contents: write, actions: write}`, `concurrency: {group: draft, cancel-in-progress: false}`, `ubuntu-latest`, `timeout-minutes: 60`.
- 단계: checkout → Python 3.14 → push로 시작했으면 60초 대기 → `python scripts/worker.py --by github --check`(GitHub가 맡을 이름 목록, 없으면 여기서 끝) → Ollama 공식 Linux 릴리스 v0.35.1 내려받기 → 모델 폴더 `actions/cache`(키 `ollama-gemma4-e4b-it-qat`) → `ollama serve` → `ollama pull gemma4:e4b-it-qat` → `python scripts/worker.py --by github --run`(`NOCTURNE_MODEL=gemma4:e4b-it-qat`, `GITHUB_TOKEN`으로 API) → 게시한 것이 있으면 `gh workflow run site.yml` (GITHUB_TOKEN으로 만든 push는 다른 워크플로를 시작하지 않으므로).
- 비용: 공개 저장소 Actions는 무료·무제한.

---

## 5. 코드 구조

```
scripts/
  photolib.py        name_for, exif_hints, make_web_copies(Pillow), is_grayscale,
                     photos.json 읽기·정규 형식 쓰기, find_photo, add_photo, ai_entry, needs_upgrade, apply_ai_text
  build.py           build(root, out=None) → index.html (+ _site 조립)
  prepare_photos.py  PC 명령줄 흐름 (원본 → 사본) 유지
  curator.py         프롬프트, Ollama 호출(3번 시도), 응답 해석, 확신도, alt 후처리, status
  queue_items.py     item.json 규칙: new_item, can_take(item, now, by), claim, fail
  github_api.py      표준 urllib: contents 읽기/쓰기(sha), 폴더 목록, 바이트, commit_files(한 커밋, blob 재사용, 3번)
  worker.py          Worker: 맡을 항목 찾기, 처리·게시, upgrade_one; Actions용 명령줄(--check, --run)
  pc_helper.py       설정, 틱(대기열 → 다듬기), 반복
admin/
  index.html  admin.css  app.js  config.js
  views/queue.js  views/exhibit.js
  lib/names.js  exif.js  image.js  grayscale.js  vault.js  github.js  gallery.js  queue.js
.github/workflows/site.yml  .github/workflows/draft.yml
src/template.html  src/photos.json
tests/               Python unittest (+ fake_github.py)
tests/js/            node --test
tests/cases.json     이름 규칙·흑백 판별 공용 사례
```

---

## 6. 테스트

- `py -m unittest discover -s tests -v`, `node --test "tests/js/*.test.js"` — 둘 다 네트워크·Ollama 없이 통과.
- **Python**: photolib(메타데이터 제거, 회전, 흑백, 이름, 정규 형식, `ai` 함수들), build, curator, queue_items(맡기 규칙), github_api(가짜 GitHub 서버: 한 커밋, blob 재사용, 재시도), worker(게시 한 커밋, 확신도 낮아도 게시, 실패 기록, 맡기 충돌, GitHub는 60초 뒤에만, redraft, 다듬기는 `ai.fields`만 바꾸고 그 사이 사람이 고친 칸은 안 바꿈), pc_helper(Ollama 꺼짐이면 아무것도 안 함, 대기열 먼저, 30분 다듬기 주기).
- **JS**: names·grayscale(공용 사례), exif(리틀·빅엔디언 시험 파일, GPS 없음), vault, github(한 커밋, blob 재사용, 재시도·실패), gallery(정규 형식이 Python과 같음, 저장 시 `ai` 규칙, 표시 규칙, 대표 삭제 거부), queue(상태 문구, 다시 시도).
- **브라우저**: GPS가 든 시험 JPEG를 처리 함수로 변환해 Pillow로 EXIF 없음 확인, 세로 회전 확인. 가짜 GitHub 서버로 관리 페이지 전체 흐름(올리기 → worker 한 번 → 게시됨 → 전시 중에서 수정·다듬기 표시), 끊김·같은 이름·실패 후 직접 게시. 375px 폭과 밝은 테마.
- **실제 시험(내보낸 뒤)**: 이미 공개된 사진 한 장으로 PC 도우미 켠 상태 → PC 게시, 끈 상태 → 60초 뒤 GitHub 게시, PC를 켜면 다듬기로 12B 교체 → 전시 중에서 시험 사진 삭제.

---

## 7. 내보내는 순서

공개되거나 계정 설정이 바뀌는 단계는 모두 실행 직전에 사용자 확인을 받는다.

1. `nocturne` 브랜치에서 구현·테스트. `CLAUDE.md`, `README.md`도 이때 새 이름·구조·주소·사용법(토큰 만들기 단계 포함)으로 고친다.
2. 확인 후:
   1. `gh repo rename nocturne` → `git remote set-url origin https://github.com/HPotty36/nocturne.git`
   2. Pages 배포 방식을 Actions로 (`gh api -X PUT repos/HPotty36/nocturne/pages -f build_type=workflow`)
   3. `main`에 병합·push → `site.yml` 실행 → `https://hpotty36.github.io/nocturne/` 확인
3. 사용자가 토큰 두 개(관리 페이지용, PC 도우미용)를 직접 만들고 입력.
4. 실제 시험 (6장 마지막 항목).
5. 사용자 확인 후 PC 도우미 자동 실행 등록.
