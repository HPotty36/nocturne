# Nocturne — 사진 전시 사이트

직접 찍은 사진을 "방"(강, 경기장, 밤, 순간)으로 나눠 어두운 전시실처럼 거는 정적 사이트. 폰이나 PC 브라우저의 관리 페이지에서 사진을 올리면 AI가 방을 고르고 제목·설명(한국어와 영어)을 써서 바로 게시하고, 사람은 나중에 관리 페이지에서 고치거나 지운다. AI는 GitHub Actions에서 Claude API(`claude-opus-5-5`, Max 요금제에 딸린 API 크레딧으로)를 쓴다. Claude가 못 쓰면 같은 실행에서 Ollama의 e4b가, Actions가 돌지 못하면 집 PC의 Ollama(12B)가 대신 쓴다. 사이트는 `index.html`(한국어)과 `en/index.html`(영어), 설치와 오프라인용 `manifest.webmanifest`·`sw.js`, `photos/`, `icons/`, `admin/`뿐이라 GitHub Pages에 그대로 올라간다. 관리 페이지도 폰에 따로 설치되고, 안드로이드에서는 사진 앱의 공유로 사진을 넣을 수 있다.

## 구조

```
src/template.html        페이지 뼈대: CSS(디자인 토큰), 마크업, JS(정렬 레이아웃, 크게 보기)
src/photos.json          사이트 문구, 대표 사진(자를 중심, 누가 골랐는지), 방 목록, 사진별 제목·대체 텍스트·날짜·카메라·ai (정규 형식, 아래)
src/redirect.js          한국어 페이지 맨 앞에 인라인으로 들어가는 첫 방문 언어 이동 스크립트 (한국어 페이지에만)
src/sw.js                서비스 워커 틀: 오프라인 캐시 규칙 (설치와 오프라인). 빌드가 VERSION, PHOTOS, COVER를 채워 sw.js로 쓴다
icons/                   설치 아이콘 넷 (192, 512, maskable 512, apple-touch 180). 손으로 만든 것, 자동으로 다시 만들지 않는다
photos/thumb/<name>.jpg  그리드용, 긴 변 1000px
photos/full/<name>.jpg   크게 보기·대표 사진용, 긴 변 2400px
queue/<name>/            대기 중인 업로드: full.jpg, thumb.jpg, item.json. 게시되면 폴더째 지워진다.
                         전시 중 사진의 설명 다시 쓰기는 queue/redraft-<file>/item.json 하나뿐.
admin/                   관리 페이지 (정적, 외부 스크립트 없음)
  index.html admin.css app.js config.js
  manifest.webmanifest   관리 페이지 설치 정보와 공유 대상 (사이트 앱과 따로 설치된다)
  sw.js                  공유로 받은 사진만 담아 두는 서비스 워커 (빌드가 만지지 않는 정적 파일)
  icons/                 관리 앱 아이콘 넷 (사이트 아이콘을 뒤집은 것. 손으로 만든 것, 자동으로 다시 만들지 않는다)
  views/queue.js         새 사진 탭 (공유한 사진 올리기 포함)
  views/exhibit.js       전시 중 탭
  lib/                   names exif jpeg image grayscale auth github gallery queue sitestatus shared (.js)
auth/worker.js           Cloudflare Worker: 관리 페이지 로그인 코드를 토큰으로 바꿔 준다 (대시보드에 붙여 넣음)
scripts/
  photolib.py            이름 규칙, EXIF 힌트, 흑백 판별, 메타데이터 없는 사본, photos.json 읽기·쓰기, ai 함수
  build.py               template + photos.json -> index.html (한국어)와 en/index.html (영어), 옆에 manifest.webmanifest와
                         sw.js. --out 이면 _site 조립 (photos/, icons/, admin/ 복사)
  prepare_photos.py      originals/ 의 원본을 웹용 사본으로 변환 (명령줄 흐름)
  curator.py             Claude API나 Ollama에게 방·제목·설명(한국어와 영어) 초안 요청, 확신도, alt 후처리, Claude의 대표 사진 고르기
  cover.py               대표 사진 고르기: 전시 중인 사진이 바뀌면 Claude가 대표와 자를 중심을 다시 고름 (draft.yml)
  queue_items.py         item.json 형식과 누가 맡을 수 있는지 규칙
  github_api.py          표준 urllib GitHub 클라이언트 (파일 읽기·쓰기, 한 커밋 쓰기)
  worker.py              대기열 항목 맡기 -> 초안 -> 한 커밋 게시, 다듬기; Actions용 --check / --run (--ai claude|ollama)
  pc_helper.py           집 PC에서 도는 도우미 (20초마다 대기열, 한가하면 30분마다 다듬기)
.github/workflows/
  site.yml               Pages 빌드·배포
  draft.yml              올린 사진을 GitHub에서 바로 초안 쓰고 게시 (Claude, 못 쓴 사진만 e4b), 대표 사진 고르기
.github/assets/          README·소셜 미리보기 그림 (손으로 만든 것, 알아둘 것)
tests/                   Python unittest, tests/js (node), fake_github.py(가짜 GitHub), browser/(브라우저 점검)
docs/superpowers/        설계 문서(specs)와 구현 계획(plans)
originals/               원본 사진. git에 올리지 않음 (.gitignore)
index.html, en/index.html, manifest.webmanifest, sw.js
                         로컬 빌드 결과. git에 올리지 않음 (.gitignore, 루트만). 직접 고치지 말 것
requirements-ai.txt      Claude SDK(anthropic), 버전과 해시로 고정. draft.yml만 설치한다 (사이트 빌드·시험·PC에는 필요 없음)
README.md / README.ko.md 저장소 첫 화면 소개 (영어 기본 / 한국어, 내용 같음)
LICENSE                  코드는 MIT (GitHub가 MIT로 알아보게 표준 문구만 둔다)
NOTICE.md                photos/·queue/의 사진은 MIT가 아님 (모든 권리 보유)
```

## 명령 (Windows, Python 3, Node 24)

```
py -m pip install -r requirements.txt            # 처음 한 번 (Pillow)
py scripts\build.py                              # 로컬 index.html, en/index.html, manifest.webmanifest, sw.js 만들기 (커밋하지 않음)
py scripts\build.py --out _site                  # Pages에 올라가는 모양 그대로 _site 조립 (시험 뒤 지울 것)
py tests\browser\serve.py --port 8000            # http://localhost:8000 미리보기
py -m unittest discover -s tests -v              # Python 시험
node --test "tests/js/*.test.js"                 # JS 시험. npm test 도 같다 (node --test tests/js/ 처럼 폴더만 주면 Node 24에서 안 돈다)
py scripts\prepare_photos.py                     # 명령줄 흐름: originals/ 의 새 사진 변환
py scripts\pc_helper.py --setup                  # PC 도우미 토큰 저장 (처음 한 번)
py scripts\pc_helper.py                          # PC 도우미 실행. 콘솔 창 없이는 pyw
```

- 미리보기에 `py -m http.server`를 쓰지 말 것. 이 PC에서는 연결이 끊긴다. `serve.py`를 쓴다 (127.0.0.1 전용).
- `index.html`을 브라우저로 바로 열어도 동작한다.
- PC 도우미 기록은 `%APPDATA%\nocturne\helper.log`. 로그온 때 자동 실행은 Windows 작업 스케줄러에 "로그온할 때" 작업으로 등록한다.
- 시험은 둘 다 네트워크·Ollama 없이 통과한다. Python 시험은 `anthropic` 패키지와 Claude API 키도 필요 없다.

### 관리 페이지를 컴퓨터에서만 시험하기

가짜 GitHub 서버를 띄우고 관리 페이지를 거기에 연결한다. 진짜 저장소는 건드리지 않는다.

```
py tests\fake_github.py --port 8790 --seed       # --seed: 지금 photos.json과 photos/로 채움
py tests\browser\serve.py --port 8000
```

Windows가 이 포트 대역(이 PC에서는 8698~8897)을 막아 두면 가짜 서버가 뜨지 않는다 (WinError 10013이나 10014). 그러면 `--port 9300`처럼 다른 포트로 띄우고 아래 `?api=`의 포트도 같이 바꾼다. 막힌 대역은 `netsh interface ipv4 show excludedportrange protocol=tcp`로 본다.

`http://localhost:8000/admin/?api=http://127.0.0.1:8790` 을 열고 **GitHub로 로그인**을 누르면 가짜 서버가 바로 들여보낸다 (토큰을 넣지 않는다). 그다음부터는 열기만 해도 저절로 로그인된다. `?api=`는 관리 페이지를 `localhost`나 `127.0.0.1`에서 열었을 때만 먹고, 주소는 `http://127.0.0.1:<포트>` 꼴이어야 한다 (`localhost:<포트>` 등 다른 주소는 무시하고 진짜 GitHub에 붙는다). 올린 사진을 가짜 AI(제목 "초안")로 게시해 보려면 `py tests\browser\run_worker.py http://127.0.0.1:8790 --by pc` (또는 `--by github`). 둘 다 방금 올린 사진을 맡는다: `--by github`은 진짜처럼 바로, `--by pc`는 시계를 6분 앞당겨 PC의 5분 기다림을 건너뛴다. `--by github`은 e4b 단계처럼 써서 `ai.model`이 e4b가 된다 (다듬기 대상).

공유로 올리기는 컴퓨터에서 흉내 낸다. 관리 워커가 하는 일(캐시에 담기)을 관리 페이지의 개발자 도구 콘솔에서 대신 하고 새로고침하면, 로그인 화면의 안내와 새 사진 탭의 "공유한 사진 1장"이 나온다 (관리 페이지의 CSP가 같은 출처로의 `fetch`를 막으므로 사진은 캔버스로 만든다).

```
const k = document.createElement('canvas'); k.width = k.height = 64; const b = await new Promise((r) => k.toBlob(r, 'image/jpeg'));
await (await caches.open('nocturne-share')).put(new URL('share/' + Date.now() + '-0', new URL('./', location.href)).href, new Response(b, { headers: { 'Content-Type': 'image/jpeg', 'X-Name': 'test.jpg' } }));
```

관리 워커 자체(공유 POST → 캐시 → 303)는 `tests/js/admin-sw.test.js`가 본다. 앱 안의 브라우저 창은 서비스 워커를 등록하지 못할 수 있으니, 워커까지 보려면 Chrome에서 연다.

로컬 미리보기에서 사이트의 "관리"(넓은 화면은 왼쪽 열 맨 아래 작은 글씨, 폰은 바닥글)를 누르면 `?api=` 없는 `admin/`이 열려 진짜 GitHub에 붙는다. 시험할 때는 위 `?api=` 주소를 바로 연다.

로그인 실패는 가짜 서버의 로그인 방식을 바꿔 보고, 버튼을 다시 누른다 (Git Bash나 cmd).

```
curl -X PUT http://127.0.0.1:8790/_fake/login -d "{\"mode\":\"deny\"}"
```

- `deny`: GitHub 화면에서 취소한 것처럼 → "로그인을 취소했어요"
- `down`: Worker가 GitHub에 닿지 못한 것처럼 → "로그인하지 못했어요. 잠시 뒤 다시 해 주세요"
- `reader`: 이 저장소에 쓸 수 없는 계정 → "이 GitHub 계정으로는 관리할 수 없어요"
- `uninstalled`: App이 어디에도 설치되지 않은 것처럼 → "GitHub App이 nocturne 저장소에 설치되어 있지 않아요"
- `ok`: 원래대로 (처음 값)
- `"expires_in": <초>`를 같이 주면 그다음 받는 토큰이 그만큼만 간다. 예를 들어 `{"mode":"ok","expires_in":900}`으로 로그인하면 다른 탭에 갔다 돌아오자마자 "로그인이 끝났어요. 다시 로그인해 주세요".

## 사진 올리는 흐름

1. 관리 페이지(주소는 배포 항목)를 폰이나 PC에서 열고 사진을 고른다. 안드로이드에서 관리 앱을 설치했다면 사진 앱의 공유 → "Nocturne 관리"로 넣고 **올리기**를 눌러도 된다 (아래 설치와 오프라인). 브라우저 안에서 메타데이터를 지우고(JFIF·ICC 구간만 남김), 긴 변 2400px·1000px로 줄이고, 날짜·카메라(GPS는 읽지 않음)와 흑백 여부를 알아낸다.
2. 사본 두 장과 `item.json`이 `queue/<name>/`에 한 커밋으로 올라간다. 이 순간부터 몇 분간 공개 저장소에서는 보이지만 사이트에는 나오지 않는다. 올린 뒤 페이지를 닫아도 된다.
3. 누가 맡는다.
   - 올림 커밋이 push되면 GitHub Actions(`draft.yml`)가 바로 맡아 Claude(`claude-opus-5-5`)로 초안을 쓴다.
   - Claude가 못 쓴 사진은 같은 실행에서 e4b(`gemma4:e4b-it-qat`, Ollama)가 이어서 쓴다. Claude API 키를 넣기 전에는 e4b가 전부 쓴다.
   - 올린 지 5분이 지나도 아무도 맡지 않은 사진(Draft가 꺼짐, GitHub 장애 등)은 집 PC 도우미가 맡아 12B(`gemma4:12b-it-qat`)로 쓴다. PC가 켜져 있고 Ollama가 돌 때만.
4. 초안(방, 제목, alt와 영어 제목, 영어 alt)을 쓰면 누가 썼든 사람 확인 없이 바로 게시한다: 사진 두 장 이동 + `photos.json` 추가 + 대기열 삭제가 한 커밋. `site.yml`이 사이트를 다시 만든다 (1~2분).
5. 잘못된 건 관리 페이지의 **전시 중** 탭에서 고친다 (방, 제목, 설명, 영어 제목, 영어 설명 저장 / AI로 다시 쓰기 / 대표로 지정 / AI에게 맡기기 / 삭제). 영어 칸은 비워 저장할 수 있다 (그 키를 지우고, 영어 페이지는 한국어를 대신 보여 준다). 배지: 방 배정 확신도가 0.8 미만이면 "확인 필요", AI가 쓴 글이 남아 있으면 "AI 한국어"·"AI 영어", 그 글이 e4b 것이면 "다듬기 대기". AI가 쓴 칸은 라벨 뒤에 " · AI"가 붙는다. 대표 사진에는 "대표 사진" 뒤에 누가 골랐는지("AI가 고름", "직접 고름", "AI가 고를 차례", "AI가 못 고름")가 붙는다.
6. Claude가 못 쓰면 같은 실행의 e4b가, PC의 AI가 못 쓰면 GitHub가 이어서 맡는다 (그 사진은 다시 대기로 돌아간다). e4b도 실패한 사진은 **새 사진** 탭에 남는다: 다시 시도 / 직접 입력해서 게시 / 버리기.
7. e4b가 쓴 설명은 PC가 켜져 있고 대기열이 비어 있을 때 PC 도우미가 30분마다 12B로 다시 써서 바꾼다. Claude나 12B가 쓴 글은 다시 쓰지 않는다. 사람이 고친 칸과 방은 AI가 절대 바꾸지 않는다.

대기열 항목 `status`: `waiting` → `pc` 또는 `github`(맡아서 처리 중) → 게시되면 폴더 삭제 / `failed`. GitHub는 `waiting`을 바로, PC는 올린 지 5분 지난 것만 맡는다. 10분 넘게 끝나지 않은 맡김은 죽은 것으로 보고 다시 맡을 수 있다 (관리 페이지에서 버릴 수도 있다). 동시에 맡으려 하면 파일 sha 때문에 늦은 쪽이 물러난다.

- Claude 단계에서 Claude가 못 쓰면(키·크레딧·API 오류, 거절, 맡은 뒤 예상 못 한 오류) `failed`로 두지 않고 `waiting`으로 넘긴다 (커밋 메시지 `대기열: <name> Claude 실패, Ollama로 넘김`). 이유는 그 넘김 커밋의 `item.json` `error`와 Actions 기록의 `Claude` 경고에 남는다. e4b가 맡으면 `error`가 지워지므로 그 뒤에는 넘김 커밋이나 경고에서 본다. 같은 실행의 e4b 단계가 이어 맡는다. 이렇게 넘긴 사진(`error`가 Claude 실패 문구나 `AI 처리 중 문제가 생겼어요`인 `waiting`)은 다음 실행의 Claude 단계도 다시 맡지 않는다 (같은 실패를 되풀이하고 비용만 든다). e4b와 PC는 맡는다. 관리 페이지의 다시 시도는 `error`를 지우므로 그 뒤에는 Claude가 다시 맡는다. 넘김 커밋 메시지에는 `올림`도 `다시 시도`도 넣지 않는다 (아래).
- PC의 AI가 못 쓰면(Ollama가 도중에 꺼짐, 모델이 답을 못 줌) `failed`로 두지 않고 `waiting`으로 돌려놓는다 (커밋 메시지 `대기열: <name> 다시 시도 (PC AI 실패)`, 이유는 `error`에 남는다). 그러면 `draft.yml`이 GitHub에서 맡고, PC 도우미는 그 항목을 30분 동안 건드리지 않는다.
- `failed`가 되는 경우: e4b 단계의 AI가 실패, 대기열 사진 파일이 없거나 읽을 수 없음, 같은 이름의 사진이 이미 있음, e4b 단계나 PC에서 맡은 뒤 예상 못 한 오류(`AI 처리 중 문제가 생겼어요`, 자세한 내용은 Actions 기록이나 `helper.log`; Claude 단계에서는 위처럼 넘긴다). `failed`는 사람이 다시 시도를 눌러야 `waiting`이 된다.
- GitHub에 닿지 못한 오류는 기록하지 않는다. 그 항목은 10분 뒤 다른 쪽이 다시 맡는다.

`draft.yml`은 `queue/**` push 중 커밋 메시지에 `올림` 또는 `다시 시도`가 들어 있을 때만 일한다 (30분마다 도는 `schedule`과 수동 실행도 있다). 대기열 커밋 메시지를 바꿀 때 이 말을 지우지 말 것. 거꾸로 Claude가 넘기는 커밋에는 넣지 않는다. 이 커밋은 `GITHUB_TOKEN`으로 push되어 어차피 draft.yml을 다시 시작하지 못하지만, 다른 토큰으로 바뀌더라도 같은 실패를 되풀이하는 실행이 생기지 않게 막아 두는 것이다. 한 실행 안에서 Claude 단계가 맡을 수 있는 사진을 모두 쓰고 (Claude 단계가 도는 사이에 새로 올라온 사진도 Claude가 쓴다), 남은 사진이 있을 때만 Ollama와 모델을 준비해 e4b 단계가 그 남은 사진만 쓴다. Ollama를 준비하는 사이에 올라온 사진은 다음 실행의 Claude 단계가 쓴다 (그 사진의 올림 push로 다음 실행이 이미 줄 서 있다). 두 단계 중 하나라도 게시했으면 `gh workflow run site.yml`로 사이트를 직접 다시 만든다 (`GITHUB_TOKEN`으로 한 push는 다른 워크플로를 시작하지 않는다).

### 대표 사진

사이트 맨 위의 대표 사진은 AI가 고른다.

- `draft.yml`의 `대표 사진 고르기` 단계(`scripts/cover.py`)가 Claude 단계 바로 뒤, Ollama를 준비하기 전에 돈다. 실행마다 `photos.json`을 보고, 전시 중인 사진이 AI가 지난번 고른 때와 다르면(사진이 들어오거나 빠짐, `cover_pick.seen`) Claude에게 전시 중인 사진을 모두 보여 주고(썸네일을 긴 변 512px로 줄인 사본, 100장까지) 대표 한 장과 자를 중심(`cover_focus`)을 고르게 한다. 지금 대표보다 뚜렷이 낫지 않으면 그대로 두라고 한다.
- Claude가 게시한 사진은 같은 실행에서, e4b·PC가 게시한 사진과 지운 사진은 다음 실행(30분마다 도는 `schedule`)에서 반영된다. 할 일이 없는 실행은 `photos.json`을 한 번 읽고 끝난다.
- 관리 페이지에서 사람이 **대표로 지정**한 사진은 AI가 바꾸지 않는다(`cover_pick` `{"by":"person"}`). 자를 중심만 다음 실행에서 그 사진 한 장을 보고 정한다. 다시 AI에게 맡기려면 그 행의 **AI에게 맡기기**. 사람이 고정한 대표가 (손으로 지워서) 전시에 없으면 고정은 없는 것으로 보고 AI가 고른다.
- 결과는 `photos.json`만 바꾸는 한 커밋이다 (`대표 사진: <제목> (AI)`, 중심만이면 `대표 사진 중심: <제목> (AI)`). 커밋할 때 다시 읽은 `photos.json`이 여전히 같은 판단일 때만 쓰므로, 그 사이 사람이 정한 대표가 이긴다. 대표나 중심이 바뀌었으면 사이트를 다시 만든다.
- 키가 없으면 아무것도 하지 않는다 (대표는 그대로). Claude가 못 고르면 Actions 기록에 `대표 사진` 경고를 남기고 대표는 그대로 둔다. 키·크레딧·요청 과다·연결 같은 실패는 다음 실행이 다시 해 본다. 돈이 들고 다시 물어도 같을 실패(거절, 끊긴 답, 읽을 수 없는 답)는 `cover_pick.tried`에 그때의 지문을 적고(커밋 `대표 사진: Claude가 못 고름`), 전시 사진이 바뀌거나 관리 페이지에서 **AI에게 맡기기**를 누를 때까지 다시 묻지 않는다 (배지 "AI가 못 고름").

### 명령줄 대안

관리 페이지 없이 PC에서도 넣을 수 있다.

1. 원본을 `originals/`에 넣는다.
2. `py scripts\prepare_photos.py` → `photos/thumb`, `photos/full` 생성, 아직 등록 안 된 사진의 JSON 항목이 출력된다 (EXIF에 날짜·카메라가 있으면 채워져 있음).
3. 그 항목을 `src/photos.json`의 알맞은 방 `photos` 배열에 넣고 `title`, `alt`를 채운다. 영어 페이지용 `title_en`, `alt_en`도 손으로 넣을 수 있다 (비워 두면 영어 페이지는 한국어를 대신 보여 주고, 나중에 관리 페이지의 전시 중 탭에서 쓰거나 "AI로 다시 쓰기"로 채울 수도 있다). 이렇게 넣은 사진에는 `ai`가 없다.
4. 커밋하고 push하면 `site.yml`이 사이트를 만든다. 로컬에서 보려면 `py scripts\build.py`.

새 방은 `rooms`에 `id`(영문 소문자, 주소 #앵커로 쓰임), `name`, `name_en`(영어 페이지의 방 이름), `note`(AI에게만 주는 방 설명, 사이트에는 나오지 않음), `photos`를 모두 갖춘 항목을 추가하면 방 목록과 본문에 자동으로 생긴다. 방 추가와 사진 순서 바꾸기는 `src/photos.json`을 직접 고친다 (관리 페이지에는 없다).

## photos.json 형식

사이트 칸 `site`: `title`, `description`(검색·공유 미리보기, `{total}`에 사진 수가 들어간다), `description_en`(영어 페이지의 설명, 같은 방식), `copyright`. 소개 문단(`lede`)은 없다.

대표 사진: `cover`(사진 이름), `cover_focus`(잘릴 때 남길 곳 `[가로, 세로]`, 0~1, 왼쪽 위가 0,0. 두 자리 소수이고 0과 1은 정수. 없으면 가운데), `cover_pick`(AI가 고르면 `{"by":"ai","model":"…","seen":"<지문>"}`, 사람이 고르면 `{"by":"person"}`, 없으면 AI가 고를 차례. AI가 돈이 드는 실패로 못 고른 사진 묶음은 `"tried":"<지문>"`이 붙는다). `seen`은 그때 전시 중이던 사진 이름을 정렬해 줄바꿈으로 이은 글의 sha256 앞 8자리다 (`photolib.fingerprint`). 이 셋은 `site` 바로 뒤에 이 순서로 오고, `cover_focus`와 `cover_pick`은 한 줄이다. `photos.json`에서 대표를 손으로 바꿀 때는 `cover_pick`을 `{"by":"person"}`으로 하고 `cover_focus`를 지운다 (그대로 두면 AI가 다시 바꿀 수 있다).

방: `id`, `name`, `name_en`, `note`, `photos` (위 "새 방"). `note`는 AI가 방을 고를 때만 읽고 사이트에는 나오지 않는다.

사진 항목: `{"file":"1234","title":"…","alt":"…","title_en":"…","alt_en":"…","date":"2025.11.30","camera":"…","ai":{…}}` (`title_en`, `alt_en`, `date`, `camera`, `ai`는 없을 수 있다. 칸 순서는 위 순서로 쓰지만 읽는 쪽에는 상관없고, 이미 있는 항목은 있던 순서 그대로 다시 쓴다). `title_en`·`alt_en`은 영어 페이지의 제목과 대체 텍스트이고, 없으면 영어 페이지가 한국어 `title`·`alt`를 `lang="ko"`를 붙여 대신 보여 준다. 파일은 정규 형식으로 쓴다: 들여쓰기 2칸, 사진은 한 줄에 하나(공백 없는 구분자, `cover_focus`·`cover_pick`도 한 줄), 한글 그대로, 끝 줄바꿈. Python(`photolib.dumps_data`)과 관리 페이지(`lib/gallery.js`)가 글자까지 같게 쓴다. 직접 고칠 때도 이 모양을 지킨다.

`ai`는 AI가 쓴 칸을 기록한다. 사이트에는 표시하지 않는다.

- AI가 게시하면 `{"model": "gemma4:e4b-it-qat", "fields": ["title","alt","title_en","alt_en"], "confidence": 0.61}`. AI는 한국어와 영어 제목·alt를 함께 쓰므로 `fields`에 영어 칸(`title_en`, `alt_en`)도 들어간다. 영어를 쓰기 전에 게시된 사진은 `["title","alt"]`처럼 한국어 칸만 있을 수 있다. 확신도를 못 구하면 `confidence`가 없다.
- `model`은 실제로 답한 모델이다. Claude는 보통 `claude-opus-5-5`이고, 거절에 대비한 대체 모델이 답했으면 그 이름이 남는다.
- `confidence`(방 배정 확신도): Ollama는 답에서 방 이름의 확률로 계산하고, Claude는 Claude가 스스로 매긴 값(0~1로 자름)을 쓴다. 어느 쪽이든 0.8 미만이면 "확인 필요" 배지가 붙는다.
- 관리 페이지에서 사람이 저장하면 바뀐 칸은 `fields`에서 빠지고 `confidence`는 지운다 (사람이 봤으니). `fields`가 비면 `ai` 전체를 지운다. 영어 칸도 같다. 영어 칸을 비워 저장하면 그 키가 지워지고, 그것도 바뀐 것으로 본다 (없던 영어 칸을 빈 채로 두는 것은 바뀐 것이 아니다).
- 다듬기 대상: `ai.fields`가 비어 있지 않고 `ai.model`이 e4b(`gemma4:e4b-it-qat`, `curator.UPGRADE_FROM`)인 사진. Claude나 12B가 쓴 글은 다듬지 않는다. 다듬기는 `ai.fields`에 남은 칸만 바꾸고(영어 칸이 남아 있으면 영어도 다시 쓰고, `fields`에 없는 영어를 새로 채우지는 않는다), 방과 확신도는 그대로 둔다.
- "AI로 다시 쓰기"(`redraft`)는 AI가 맡아서 처리를 시작한 때와 글자가 같은 칸만 바꾼다 (그 사이 사람이 고친 칸은 그대로). 바꾼 칸이 `ai.fields`가 되고 `ai.model`은 쓴 모델, 방과 확신도는 그대로. 영어 칸이 없던 사진은 없던 그대로라 같은 칸으로 보아서, 다시 쓰기로 영어가 채워진다.
- `photos.json`에서 AI가 쓴 제목이나 설명(영어 `title_en`·`alt_en` 포함)을 직접 고쳤다면 그 칸을 `ai.fields`에서도 빼야 한다 (비면 `ai`를 통째로 지운다). 그대로 두면 PC 도우미의 12B 다듬기가 고친 글을 덮어쓸 수 있다. 관리 페이지에서 저장하면 이건 저절로 된다.

## 관리 페이지 로그인 준비

관리 페이지는 **GitHub로 로그인**으로 들어간다. 이 저장소에 쓸 수 있는 GitHub 계정만 관리할 수 있다. 처음 한 번 GitHub App과 Cloudflare Worker를 만들어 둔다.

1. **GitHub App 만들기**: GitHub 로그인 → 오른쪽 위 프로필 → **Settings** → 왼쪽 맨 아래 **Developer settings** → **GitHub Apps** → **New GitHub App**.

   | 항목 | 값 |
   |---|---|
   | 이름 | 전역에서 겹치지 않는 이름. 실명 금지 (예: `nocturne-admin-hpotty36`). 허용 화면에 보인다 |
   | Homepage URL | `https://hpotty36.github.io/nocturne/` |
   | Callback URL | `https://hpotty36.github.io/nocturne/admin/` |
   | Expire user authorization tokens | 켬 (8시간) |
   | Request user authorization during installation | 끔 |
   | Enable Device Flow | 끔 |
   | Webhook Active | 끔 |
   | Repository permissions | Contents: Read and write, Actions: Read-only (Metadata: Read-only는 자동) |
   | 설치할 수 있는 곳 | Only on this account |

   **Create GitHub App** → **General** 화면 위쪽 About에 보이는 **Client ID**(`Iv23…`, 숫자인 App ID가 아님)를 적어 둔다 (공개 값). client secret은 General이 아니라 왼쪽 **Credentials** 메뉴에 있다: **Generate a new client secret** → client secret을 복사한다 (비밀 값, 다시 볼 수 없다. Worker에만 넣는다).
2. **App 설치**: App 화면 왼쪽 **Install App** → **Install** → **Only select repositories** → `nocturne`만. 설치하지 않으면 로그인할 때 "GitHub App이 nocturne 저장소에 설치되어 있지 않아요"가 나온다. **만든 App(Developer settings → GitHub Apps)과 설치된 App(Settings → Applications → Installed GitHub Apps)은 다르다**: Installed 목록에 있어야 설치된 것이다.
3. **Worker 올리기**: Cloudflare 무료 계정을 만든다. `<하위주소>.workers.dev`의 하위 주소는 관리 페이지 설정과 CSP에 공개로 들어가므로 실명이 아닌 이름으로 정한다. 대시보드 → **Workers & Pages** → **Create** → **Hello World**, 이름 `nocturne-auth` → **Edit code**에 `auth/worker.js` 내용을 통째로 붙여 넣고 **Deploy**. 그다음 Worker의 **Settings** → **Variables and Secrets**에 넣고 다시 Deploy:
   - `CLIENT_ID` (Text): App의 Client ID
   - `CLIENT_SECRET` (Secret): App의 client secret

   Worker는 `https://hpotty36.github.io`에서 온 `POST /token`만 받는다. `auth/worker.js`를 고치면 대시보드에 다시 붙여 넣는다 (push로는 바뀌지 않는다). 잘 도는지는 아래 "문제가 생기면"의 로그인 항목으로 확인한다.
4. **값 넣는 곳**
   - `admin/config.js`: `clientId`에 Client ID, `exchange`에 `https://nocturne-auth.<하위주소>.workers.dev/token`. `Iv23-not-set`이나 `nocturne-auth.invalid`가 남아 있으면 아직 넣지 않은 것이다.
   - `admin/index.html`: CSP `connect-src`에 Worker 출처 `https://nocturne-auth.<하위주소>.workers.dev`. `config.js`와 다르면 `tests/test_admin_static.py`가 실패한다.
   - 커밋하고 push하면 `site.yml`이 관리 페이지를 다시 올린다.

로그인은 이렇게 된다.

- 처음엔 **GitHub로 로그인** → GitHub 허용 화면(처음 한 번) → 관리 화면.
- 한 번 로그인한 기기에서는 관리 페이지를 열기만 하면 GitHub에 다녀와 저절로 들어간다 (1~2초). 저절로 하다 실패하면 그 탭에서는 다시 하지 않고 버튼을 보여 준다.
- 토큰은 8시간 뒤 끝난다. 끝났거나 15분 안에 끝날 때 탭으로 돌아오면 "로그인이 끝났어요. 다시 로그인해 주세요"가 나온다.
- 로그아웃하면 이 기기에서 저절로 로그인하지 않는다. GitHub 자체 로그아웃은 github.com에서 한다.
- 폰을 잃어버렸다면 GitHub **Settings** → **Sessions**에서 그 폰의 세션을 지운다. 열려 있던 관리 페이지의 토큰까지 바로 끊으려면 **Settings** → **Applications** → **Authorized GitHub Apps**에서 이 App을 **Revoke**한다 (다음 로그인 때 허용 화면이 다시 나온다).

## PC 도우미 토큰

PC 도우미는 따로 GitHub **fine-grained 토큰**을 쓴다.

1. GitHub 로그인 → 오른쪽 위 프로필 → **Settings** → 왼쪽 맨 아래 **Developer settings** → **Personal access tokens** → **Fine-grained tokens** → **Generate new token**.
2. 이름은 알아보기 쉽게 (예: `nocturne-pc`). **Expiration**: 만료일은 Custom으로 오늘부터 1년 뒤 날짜 (366일 이내).
3. **Repository access**: **Only select repositories** → `nocturne`만.
4. **Permissions → Repository permissions**: **Contents** Read and write만. (Metadata Read-only는 자동으로 붙는다.)
5. **Generate token** → 보이는 토큰을 복사한다 (다시 볼 수 없다).
6. `py scripts\pc_helper.py --setup` 에 붙여 넣으면 `%APPDATA%\nocturne\helper.json`에 저장된다.
7. 만료되기 전에 새로 만들어 다시 넣는다. 토큰이 샜다면 같은 화면(Fine-grained tokens)에서 그 토큰을 **Delete**로 지운다.

## Claude API 키 넣기

GitHub Actions의 Claude 단계와 대표 사진 고르기 단계는 저장소 비밀값 `ANTHROPIC_API_KEY`로 Claude API를 부른다. 비용은 Max 요금제에 딸린 API 크레딧에서 나간다. 키가 없으면 Claude 단계는 건너뛰고 e4b로만 돌며, 대표 사진은 그대로다.

1. **크레딧 연결**: claude.ai → **Settings** → **Billing** → **API credits**에서 Console 조직을 연결한다 (Max 요금제).
2. **API 키 만들기**: Claude Console(`platform.claude.com`) → **API keys** → **Create key**. 이름은 알아보기 쉽게 (예: `nocturne-draft`). **Linked account**는 나(개인 키, `sk-ant-usr…`로 시작), 워크스페이스는 **Default Workspace** 하나로 묶고, 만료는 **Never**(또는 1년). 워크스페이스에 묶지 않은 키는 요청마다 워크스페이스 ID가 필요해 `Claude API 오류 400`이 나고, 만료일은 만든 뒤에 바꿀 수 없다. 보이는 키를 복사한다 (다시 볼 수 없다). Claude Code 로그인 토큰(`sk-ant-oat01-…`)은 API 키가 아니라서 넣으면 `Claude API 키가 맞지 않아요`가 난다.
3. **GitHub에 넣기**: 저장소 **Settings** → **Secrets and variables** → **Actions** → **New repository secret**. Name은 `ANTHROPIC_API_KEY`, Secret에 키를 붙여 넣고 **Add secret**.
4. **한도 걸기**: Console에서 월 사용 한도를 건다. 크레딧보다 많이 쓰지 않게 한다.
5. **확인**: 사진 한 장을 올리고 Actions의 Draft 실행에서 `Claude로 초안 쓰고 게시` 단계에 `published: <name>`이 찍히는지 본다. 그 사진의 `ai.model`은 `claude-opus-5-5`다.

- 키는 GitHub 비밀값에만 둔다. PC 도우미는 Claude를 부르지 않으니 PC에는 두지 않는다. 파일이나 커밋에 넣지 말 것.
- 키를 바꾸려면 Console에서 새 키를 만들어 같은 비밀값을 고치고(**Update secret**), 옛 키는 Console에서 지운다. 키가 샜을 때도 같다.
- 모델을 바꾸려면 `draft.yml`의 `NOCTURNE_CLAUDE_MODEL` 값만 고친다 (기본 `claude-opus-5-5`).

## 지켜야 할 것

- 사이트 어디에도 소유자의 실명을 넣지 않는다. 사이트 이름은 "Nocturne".
- 저장소와 사이트에 들어가는 사진은 반드시 메타데이터를 지운 사본이다 (관리 페이지의 브라우저 변환 또는 `prepare_photos.py`). 원본에는 GPS 위치가 들어 있을 수 있다. `photos/`나 `queue/`에 원본을 직접 복사하지 말 것.
- 저장소가 공개라서 올린 파일은 누구나 볼 수 있다. 대기 중인 사진은 `queue/`에서 몇 분간 공개 상태로 머물고, 버리거나 전시에서 빼도 git 기록에는 남는다. 원본(`originals/`)은 `.gitignore`로 제외되어 있다.
- `index.html`, `en/index.html`, `manifest.webmanifest`, `sw.js`는 Actions가 만든다. 커밋하지 않는다 (`.gitignore`는 루트의 `/index.html`, `/en/`, `/manifest.webmanifest`, `/sw.js`만 막는다).
- 장소 이름은 확인된 것만 쓴다. 사진만 보고 추측한 장소를 제목이나 설명에 넣지 않는다 (AI 프롬프트에도 쓰지 말라고 넣어 두었다. 잘못 들어가면 전시 중 탭에서 고친다).
- `alt`는 화면 낭독기용 설명이다. 무엇이 찍혔는지 한 문장으로, 흑백이면 끝에 "흑백 사진"을 덧붙인다. 영어 `alt_en`은 같은 규칙으로 끝에 ", black-and-white photo"를 덧붙인다.
- 문구는 한국어, 짧고 평이하게.
- 사람이 고친 제목·설명과 방 배정은 AI가 다시 바꾸지 않는다.
- 사이트와 관리 페이지는 외부 스크립트를 불러오지 않는다. 이 계정의 다른 Pages 사이트에도 믿을 수 없는 내용이나 스크립트를 두지 않는다. 같은 출처라서 거기서 도는 스크립트가 관리 페이지 로그인을 몰래 끝낼 수 있다 (알아둘 것). 이걸 바꿔야 한다면 먼저 관리 페이지를 관리 페이지만 쓰는 출처로 옮긴다.

## 디자인 메모

- 검은 전시실 하나뿐이다. 토큰은 `src/template.html`의 첫 `:root`에 있고 `color-scheme: dark`만 둔다. 밝은 테마도 강조색도 없다. 강약은 진한 글자(`--ink`)와 흐린 글자(`--ink-dim`)로 준다. 색은 반드시 토큰으로 쓴다.

  | 토큰 | 값 | 쓰임 |
  |---|---|---|
  | `--ground` | `#000` | 페이지 바탕, 폰의 위 줄, 크게 보기 |
  | `--wall` | `#111` | 사진이 오기 전 자리 (사진마다 평균색 `--c`가 먼저) |
  | `--ink` | `#ebe9e4` | 글자, 지금 보는 방, 키보드 포커스 테두리 (2px, 3px 띄움) |
  | `--ink-dim` | `#8f8c86` | 날짜, 점수, 기간, 작은 글씨, 지금 아닌 방, 언어 링크 |
  | `--rule` | `#262624` | 구분선 (폰 목록 상자 테두리만) |

- 대비: `--ink` 7:1, `--ink-dim` 4.5:1 이상 (`tests/test_build.py`가 본다).
- 관리 페이지(`admin/admin.css`)도 같은 다섯 토큰과 같은 글꼴 하나를 쓴다 (`tests/test_admin_static.py`가 두 파일의 토큰을 비교하고, 옛 토큰·글꼴·장식이 없는지 본다). 크기는 14·13·12px이고 입력칸만 16px이다 (아이폰 사파리는 16px보다 작은 입력칸을 누르면 화면을 확대한다). 위 줄(52px)은 "Nocturne 관리"와 "로그아웃", 탭은 글자뿐이고 지금 탭 앞에 "•"가 붙는다. 사이트 반영 상태는 한 줄 글자다. 배지는 흐린 글자를 " · "로 잇고 "대표 사진"·"확인 필요"만 진하다. 버튼은 모서리 없는 1px 테두리 상자(높이 44px), 주 버튼은 `--ink` 바탕, 입력칸은 `--wall` 바탕에 아래 선만 `--ink-dim`이다. 점, 숨쉬는 움직임, 로마 숫자, 영어 대문자 방 이름은 없다.
- 글꼴은 Noto Serif KR 하나(Google Fonts, `crossorigin` 붙인 스타일시트)다. 굵기는 400과, 사이트 이름·방 이름에만 쓰는 300이다. 300은 두 번째 스타일시트가 `text=`로 그 글자만 받는다 (빌드가 사이트 이름과 모든 방의 `name`, `name_en`에서 글자를 모은다).
- 크기는 본문·목록 14px, 사진 라벨·크게 보기 글 13px, 기간·작은 글씨 12px이다. 큰 글자는 둘뿐이다: 넓은 화면의 사이트 이름 `max(56px, min(7.6vw, 124px, (100svh - 22rem) / 4.6))`(창 높이도 따라가서 왼쪽 열이 넘치지 않는다. 1440×900에서 109px, 1280×720에서 80px), 방 이름(넓은 화면 `clamp(40px, 4vw, 60px)`, 폰 28px). 폰 위 줄의 사이트 이름은 20px. 숫자는 `tabular-nums`, 한글은 `keep-all`. 이탤릭, 자간, 대문자 변환, 굵게는 쓰지 않는다.
- 960px 이상은 두 열이다. 왼쪽 180px 열이 `sticky`로 따라오고(오른쪽 열과 `clamp(32px, 4vw, 64px)`, 바깥 여백 `clamp(16px, 3vw, 40px)`), 위에서부터 세로쓰기로 크게 선 "Nocturne"(대표 사진 윗선에서 시작), 36px 띄우고 방 목록, 28px 띄우고 언어 링크, 맨 아래 작은 글씨 세 줄(사진 수, ©, 관리)이 온다. 지금 보는 방은 `--ink`에 앞에 "•"(글자 밖으로 내어 씀, 화면 낭독기는 읽지 않는다)가 붙는다. 창이 낮으면 왼쪽 열만 스크롤된다.
- 960px 미만은 52px 위 줄이 따라온다: 왼쪽 "Nocturne"(20px), 오른쪽 언어 링크와 "목록"(`<details>`). 목록은 위 줄 바로 아래 오른쪽에 펼쳐지고, 방을 고르거나 Esc나 바깥을 누르거나 포커스가 목록 밖으로 나가면 접힌다. 접으려고 바깥을 누른 것은 그 아래 사진이나 링크를 누르지 않는다. 맨 아래 작은 글씨 한 줄(사진 수 · © · 관리). 방으로 건너뛰면 위 줄 아래 8px에 선다(`scroll-margin-top`).
- 지금 보는 방은 화면 가운데를 지난 마지막 방이다. 두 목록의 그 방 링크에 `is-here`와 `aria-current="true"`를 붙인다. 마지막 방은 최소 화면 반 높이라 맨 아래에서도 표시된다.
- 대표 사진은 틀을 꽉 채운다: 넓은 화면은 오른쪽 열 폭에 첫 화면 아래 끝까지(라벨 한 줄은 첫 화면에 남는다), 폰은 좌우 여백 없이 4:5. `cover_focus`를 중심으로 잘라 맞추고(`object-fit: cover`, 빌드가 `--fx`·`--fy`로 넣는다), 크게 보기에서는 원본 비율 그대로 보인다. 아래로 `clamp(72px, 9vw, 120px)` 띄우고 첫 방이 온다.
- 방은 두 칸이다: 왼쪽 좁은 기둥에 방 머리가 세로쓰기(`vertical-rl`)로 서고 오른쪽에 사진이 걸린다 (사이 `clamp(20px, 2.4vw, 36px)`, 폰 10px). 방 머리는 방 이름(굵기 300), 그 아래로 "7점"과 기간(12px, `--ink-dim`)이다. 점수의 숫자는 바로 서고 기간과 영어 방 이름은 책등처럼 옆으로 눕는다. 방 머리는 방을 내려가는 동안 화면 위쪽(폰은 위 줄 아래 8px)에 붙어 따라온다. 방 사이는 `clamp(96px, 11vw, 160px)`.
- 그리드는 JS가 방마다 행을 나눠 모든 행을 가로폭에 꽉 맞춘다 (동적 계획법으로 목표 높이에 가장 가깝게. 목표는 걸리는 폭 1100px 이상 300, 720px 이상 240, 그 아래 180이고 400px 미만은 150: 폰에서 방 이름 기둥만큼 좁아진 줄에도 두 장씩 서게). 사진은 잘리지 않고 원래 비율을 유지한다 (자르는 것은 대표 사진뿐이다). 행 높이가 목표의 1.6배를 넘으면 그 행은 목표 높이로 두고 가로를 채우지 않는다 (1~2장인 방). 사진 사이는 `clamp(10px, 1.3vw, 16px)`. 사진이 없는 방은 건너뛴다.
- 라벨은 사진 아래 7px에 "제목, 날짜"(날짜는 `--ink-dim`, 없으면 제목만)다. 대표 사진도 같다 (카메라는 쓰지 않는다). 길면 줄을 바꾼다.
- 크게 보기는 검은 화면에 사진을 들어가는 만큼 크게 둔다. 아래 한 줄: 왼쪽 "제목, 날짜", 오른쪽 "3 / 23"과 글자 버튼 "이전 · 다음 · 닫기"(평소 `--ink-dim`, 마우스나 포커스에 `--ink`, 높이 24px 이상, 960px 미만이나 손가락 화면은 40px 이상). 좁으면 두 줄로 내려간다. 처음엔 썸네일을 보여주고 원본이 받아지면 바꾸며(닫은 뒤나 다른 사진으로 넘어간 뒤 늦게 받아진 원본은 쓰지 않는다) 앞뒤 원본을 미리 받는다. 방향키, 스와이프, Esc, 사진 바깥 클릭으로 닫기. 열면 "닫기"에 포커스, Tab은 세 버튼 안에서 돌고, 닫으면 연 자리로 포커스가 돌아간다. 영어 페이지에서 영어 제목이 없으면 캡션에 `lang="ko"`를 붙인다.
- 언어 링크("English"/"한국어")를 누르면 `localStorage`의 `nocturne.lang`에 그 언어를 적는다. 한국어 페이지 머리의 작은 스크립트(`src/redirect.js`를 빌드가 그대로 인라인으로 넣는다)가 이 값(없으면 브라우저 언어)을 보고 영어 페이지로 보낸다. 영어 페이지의 "한국어" 링크는 `../?lang=ko`로 간다: 주소에 `lang=ko`가 있으면 그 스크립트는 아무것도 하지 않으므로, 저장소가 막힌 브라우저에서도 한국어를 고른 사람이 영어로 되돌려 보내지지 않는다. 한국어 페이지의 "English" 링크는 `en/` 그대로다.
- 관리 페이지는 375px 폭에서 쓸 수 있어야 한다. CSP 때문에 인라인 스크립트·스타일을 쓰지 않는다.
- 사이트의 "관리"(작은 글씨, `--ink-dim`, 누구에게나 보임)는 관리 페이지로 가고, 관리 페이지 위쪽의 "Nocturne"은 사이트를 새 탭에 연다.

## 설치와 오프라인

- 사이트는 폰 홈 화면에 앱처럼 설치할 수 있다 (`manifest.webmanifest`, `icons/`). 안드로이드 Chrome: ⋮ 메뉴 → "앱 설치"(또는 "홈 화면에 추가"). 아이폰 Safari: 공유 → "홈 화면에 추가". 설치한 앱은 사이트 루트(`./`)로 열리고, 기억한 언어로 간다.
- 서비스 워커(`sw.js`, 틀은 `src/sw.js`)는 처음 연 기기에 두 페이지, 모든 썸네일, 대표 사진(전체 크기. 대표 사진은 워커가 일하기 전에 이미 받아지므로 워커가 직접 한 번 더 담는다)을 담아 두고, 그 뒤로 받는 글꼴·크게 본 원본도 담는다. 글꼴 파일은 미리 알 수 없어 처음 받을 때 담는다. 그래서 두 번째로 연 뒤부터는 네트워크 없이도 전시가 다 열린다. 크게 본 적 없는 사진은 오프라인에서 썸네일 크기에 머문다. 한 번도 열지 않은 기기는 오프라인에서 열리지 않는다. 설치한 앱은 설치한 뒤 온라인으로 한 번 열어야 오프라인에서 열린다. 아이폰은 홈 화면 앱이 사파리와 저장소를 따로 쓰므로, 홈 화면의 앱 자체를 온라인으로 한 번 열어야 한다.
- 페이지는 늘 네트워크를 먼저 본다. 4초 안에 제대로 된 답이 오면 그것을 쓰고 담아 둔 사본도 바꾼다. 네트워크가 없거나 4초가 넘거나 404·500 같은 오류면 담아 둔 사본을 보여 준다. 오류 페이지는 담지 않는다. 네트워크가 다른 주소로 보내면(사이트를 옮긴 뒤) 그 이동을 그대로 따르고 담지 않는다.
- 새 버전: 빌드가 `sw.js`의 `VERSION`을 두 페이지·manifest·`src/sw.js` 틀·`icons/` 파일의 해시로 정하므로 (아이콘만 바뀌어도 새 버전이다), 사이트가 바뀌면 다음에 온라인으로 열 때 새 서비스 워커가 들어와 바로 바뀐다 (새 사진의 썸네일을 받고, 전시에서 빠졌거나 바뀐 사진은 기기에서도 지운다). 페이지 자체는 그 열기에서 이미 새것이다.
- 캐시는 `nocturne-page`(두 페이지, manifest, 아이콘), `nocturne-thumbs`, `nocturne-full`, `nocturne-fonts` 넷이다. 관리 워커가 쓰는 `nocturne-share`(공유로 받은 사진)는 사이트 워커가 지우지 않는다 (사이트 워커는 모르는 `nocturne-*` 캐시를 지운다). 사진 주소의 `?v=` 해시 덕분에 같은 이름으로 바뀐 사진도 옛것이 나오지 않는다.
- 관리 페이지(`admin/`, 로그인 뒤 돌아오는 `admin/?code=…` 포함)에는 자기 서비스 워커(`admin/sw.js`, 범위 `admin/`)가 있다. 사이트 워커보다 범위가 길어서 관리 페이지와 그 페이지가 부르는 모든 것(GitHub API, 글꼴, 썸네일)은 이 워커가 맡는데, 이 워커는 공유로 오는 POST 말고는 손대지 않으므로 늘 네트워크로 간다. 관리 페이지는 오프라인에서 열리지 않는다. 관리 워커가 등록되기 전의 첫 방문에서만 사이트 워커가 관리 페이지의 글꼴과 썸네일 요청을 본다 (사이트 워커는 `admin/` 주소 자체는 늘 그냥 보낸다).
- 관리 페이지도 따로 설치된다 (`admin/manifest.webmanifest`, 이름 "Nocturne 관리", 아이콘은 크림색 바탕에 검은 N, `admin/icons/`). 안드로이드 Chrome: 관리 페이지에서 ⋮ → "앱 설치". 아이폰 Safari: 공유 → "홈 화면에 추가". 두 manifest의 `id`는 `/nocturne/`과 `/nocturne/admin/`이다 (`id`는 시작 주소의 출처를 기준으로 풀리므로 `./`이면 두 앱이 같은 앱이 된다). 1부에서 배포한 사이트 manifest는 `id`가 `./`였으므로, 그때 설치한 사이트 앱은 지우고 다시 설치해야 새 manifest를 따른다. 사이트 앱의 범위(`/nocturne/`)가 관리 페이지를 품고 있어서, 사이트 앱이 설치된 안드로이드 폰에서는 관리 페이지의 ⋮ 메뉴에 "앱 설치" 대신 "Nocturne 열기"가 나올 수 있다. 그러면 사이트 앱을 지우고, 관리 앱을 먼저 설치한 뒤 사이트 앱을 다시 설치한다.
- 공유로 올리기 (안드로이드 Chrome만. 아이폰 사파리는 웹 앱의 공유 대상을 지원하지 않는다): 관리 앱을 설치하고 한 번 연 뒤(그때 관리 워커가 등록된다) 사진 앱에서 공유 → "Nocturne 관리". JPEG·PNG만 받는다 (사진 고르기와 같다. 다른 형식이 섞이면 그것만 빠진다). 관리 워커가 사진을 캐시 `nocturne-share`에 담고(키 `share/<받은 시각 ms>-<순번>`, 머리 `X-Name`에 파일 이름) 303으로 관리 페이지에 보낸다. 로그인 화면에는 "공유한 사진 N장이 기다리고 있어요. 로그인하면 올릴 수 있어요", 로그인 뒤에는 새 사진 탭이 열리고 고르는 곳 위에 "공유한 사진 N장"과 **올리기**·**버리기**가 나온다. 올리기는 사진 고르기와 똑같이 올린다. 버리기는 누르는 순간 지우고, 올리기는 사진마다 대기열에 올라간 뒤에 지운다 (올리는 도중 앱이 닫히거나 새 공유로 페이지가 바뀌면 아직 못 올린 사진은 다음에 다시 보인다). 누르기 전에는 올리지 않는다: 올린 사진은 공개 저장소에 남으므로, 잘못 공유했거나 잊은 사진이 저절로 올라가면 안 된다. 로그인에 실패해도 사진은 기다린다.
- 다른 사이트의 폼도 `admin/share`로 POST를 보내 `nocturne-share`에 사진을 넣을 수 있다 (공유 시트가 보내는 POST와 출처로 가릴 수 없다). 그래도 올리기를 누르기 전에는 아무것도 올라가지 않는다. 모르는 "공유한 사진"이 보이면 버리기를 누른다.
- 서비스 워커 걷어내기: 사이트를 옮기는 등으로 워커를 없애야 하면, 스스로 등록을 풀고 `nocturne-*` 캐시를 모두 지우는 `sw.js`를 배포한다. 이미 설치된 기기가 다음에 온라인으로 열 때 그 `sw.js`를 받아 워커가 사라진다. `sw.js`만 지워서는 옛 워커가 기기에 남을 수 있다.
- `index.html`을 파일로 바로 열면 서비스 워커 없이 동작한다. `localhost:8000` 미리보기에서는 등록된다 (`localhost:8000` 아래 모든 페이지가 같은 출처라 캐시도 함께 쓴다). 지우려면 개발자 도구 → Application → Service workers → Unregister, 그리고 Storage → Clear site data.

## 배포

- 저장소: https://github.com/HPotty36/nocturne (공개)
- 사이트: https://hpotty36.github.io/nocturne/
- 관리 페이지: https://hpotty36.github.io/nocturne/admin/
- GitHub Pages는 Actions로 배포한다 (`build_type=workflow`). `main`에 `src/**`, `photos/**`, `admin/**`, `icons/**`, `scripts/build.py`, `scripts/photolib.py`, `requirements.txt`가 바뀌어 push되면 `site.yml`이 `build.py --out _site`로 `index.html`, `en/index.html`, `manifest.webmanifest`, `sw.js`, `photos/`, `icons/`, `admin/`만 올린다 (1~2분). 문서만 바뀐 push는 다시 만들지 않는다. 수동 실행은 `gh workflow run site.yml`. `queue/`, `src/`, `scripts/`, `tests/`, `auth/`는 사이트로 서빙되지 않는다 (`auth/worker.js`는 Cloudflare에 손으로 붙여 넣는다).
- `.nojekyll`은 Actions 배포에서는 필요 없지만 두어도 해롭지 않다.
- 저장소 이름을 `across-gallery`에서 `nocturne`으로 바꿨기 때문에 옛 주소(`hpotty36.github.io/across-gallery/`)는 열리지 않는다. 이동 안내는 두지 않았다.

## 문제가 생기면

- **Actions 기록**: https://github.com/HPotty36/nocturne/actions 에서 `Draft`(GitHub의 AI: Claude, e4b)와 `Site`(사이트 빌드)의 실행마다 단계별 기록을 본다. 관리 페이지 위쪽의 "반영 실패"도 여기로 이어진다. Draft를 잠시 끄려면 Actions 탭 왼쪽에서 `Draft` → 오른쪽 위 `…` → **Disable workflow** (명령줄은 `gh workflow disable draft.yml`, 다시 켜기는 `gh workflow enable draft.yml`). 꺼 둔 동안에는 PC 도우미가 올린 지 5분 지난 사진을 맡고, PC도 꺼져 있으면 사진은 대기열에 남는다.
- **대표 사진이 바뀌지 않을 때**: Draft 실행의 `대표 사진 고르기` 단계 기록을 본다. `대표 사진` 경고는 Claude가 못 고른 이유다 (위 Claude 경고와 같은 문구이고, 거절은 `Claude가 대표 사진 고르기를 거절했어요`). `대표 사진: 할 일 없음`이면 전시 중인 사진이 그대로이거나, 사람이 고정한 대표다 (관리 페이지의 배지가 "직접 고름"이면 **AI에게 맡기기**). 배지가 "AI가 못 고름"이면 지난번 고르기가 돈이 드는 실패로 끝난 것이다: 이유는 `대표 사진: Claude가 못 고름` 커밋 무렵 실행의 경고에서 보고, 다시 하려면 **AI에게 맡기기**. e4b나 PC가 게시한 사진은 다음 실행(30분 안)에서 반영된다.
- **Claude가 못 쓸 때**: Draft 실행 화면에 `Claude` 경고가 `<name>: <이유>`로 뜬다 (같은 이유가 넘김 커밋의 `item.json` `error`에도 남는다). 그 사진은 같은 실행의 e4b가 이어 쓴다 (e4b도 실패하면 새 사진 탭에 남는다). 이유별로 할 일:
  - `Claude API 키가 맞지 않아요`: 비밀값 `ANTHROPIC_API_KEY`를 새 키로 바꾼다 (Claude API 키 넣기). Claude Code 로그인 토큰(`sk-ant-oat01-…`)을 넣었거나, 키가 만료·삭제된 경우다.
  - `Claude API 권한이나 크레딧을 확인해 주세요`: Console에서 크레딧이 남았는지, 월 한도에 걸리지 않았는지, claude.ai의 크레딧 연결이 풀리지 않았는지 본다.
  - `Claude API 요청이 너무 많아요`: 잠시 뒤 저절로 풀린다. 계속되면 Console의 한도를 본다.
  - `Claude API 오류 <상태>`, `Claude API에 연결하지 못했어요`, `Claude의 답이 끊겼어요`, `Claude의 답을 읽을 수 없어요 (…)`: 대개 잠깐 문제다. 계속되면 Claude 단계 기록을 본다. `Claude API 오류` 뒤의 괄호에는 알려진 오류 종류(`api_error` 등)와 요청 ID(`req_…`)가 있으면 붙는다.
  - `Claude API 오류 400`이나 `404`가 계속되면: 키를 워크스페이스에 묶어 만들었는지(Claude API 키 넣기 2번), Console에서 크레딧이 남았는지, 월 한도에 걸리지 않았는지 보고, `draft.yml`의 `NOCTURNE_CLAUDE_MODEL`이 맞는 모델 이름인지 본다.
  - `Claude가 이 사진의 초안을 거절했어요`: e4b가 대신 썼으니 전시 중 탭에서 그 사진의 방·제목·설명을 확인한다.
  - `anthropic 패키지가 없어요`: `Claude SDK 설치` 단계가 실패했다. 그 단계 기록을 본다.
  - `AI 처리 중 문제가 생겼어요`: Claude 단계의 예상 못 한 오류다. 그 단계 기록의 traceback을 본다.
  - 키를 넣었는데 Claude 단계 기록에 `Claude API 키가 없어서 Claude 단계를 건너뛰어요`가 찍히면 비밀값 이름이 `ANTHROPIC_API_KEY`인지, 저장소 비밀값(Actions)으로 넣었는지 본다.
- **PC 도우미 멈추기·다시 켜기**: 콘솔에서 돌렸으면 그 창에서 Ctrl+C. `pyw`로 돌렸으면 작업 관리자의 세부 정보에서 `pythonw.exe`(명령줄에 `pc_helper.py`가 있는 것)를 끝내거나, 작업 스케줄러에 등록했다면 그 작업을 **끝내기**. 다시 켜려면 `pyw scripts\pc_helper.py` (또는 작업 스케줄러에서 **실행**). `scripts/`가 바뀐 것을 이 PC로 받아 온(pull) 뒤에는 도우미를 껐다 켠다: 돌고 있는 도우미는 켤 때의 옛 코드를 그대로 쓴다.
- **`helper.log` 읽기** (`%APPDATA%\nocturne\helper.log`, 한 줄에 하나): `시작`·`멈춤`, `published:<name>`(게시), `released:<name>`(PC의 AI가 못 써서 GitHub에 넘김), `upgraded:<file>`(다듬기), `failed:<…>`, `다듬기 중 오류 (<file>)`(다듬기에서 난 예상 못 한 오류. traceback이 붙고 그 라운드는 거기서 끝나 30분 뒤에 다시 한다. 다듬던 사진을 커밋하다 GitHub 오류가 나면 `오류 …` 한 줄이 남고 그 사진만 이번 라운드에서 건너뛴다), `오류 …`(같은 오류가 이어지면 처음 한 번만 적힌다). 맡은 뒤에 난 예상 못 한 오류는 그 아래에 traceback이 여러 줄로 붙는다.
- **로그인이 안 될 때** (관리 페이지)
  - Worker가 응답하는지 본다 (Git Bash). 다른 출처의 요청은 `403`이어야 한다.

    ```
    curl -s -o /dev/null -w "%{http_code}\n" -X POST https://nocturne-auth.<하위주소>.workers.dev/token -H "Origin: https://evil.example"
    ```

    사이트 출처로 엉터리 코드를 보내면 `{"error":"bad_verification_code"}`가 정상이다. `{"error":"Not Found"}`면 `CLIENT_ID`가 틀렸다 (숫자로 된 App ID가 아니라 `Iv23…`으로 시작하는 Client ID를 넣는다). `incorrect_client_credentials`면 `CLIENT_SECRET`(또는 `CLIENT_ID`)이 틀렸고, `not_configured`면 변수가 빠졌고, `github_unreachable`이면 Worker가 GitHub에 닿지 못한 것이다.

    ```
    curl -s -X POST https://nocturne-auth.<하위주소>.workers.dev/token -H "Origin: https://hpotty36.github.io" -H "Content-Type: application/json" -d "{\"code\":\"x\",\"code_verifier\":\"y\",\"redirect_uri\":\"https://hpotty36.github.io/nocturne/admin/\"}"
    ```

  - 로그인 바로 뒤 "GitHub App이 nocturne 저장소에 설치되어 있지 않아요"가 나오면 App이 `nocturne`에 설치되지 않은 것이다. App 화면의 **Install App**에서 `nocturne`에 설치한다. 설치된 목록은 GitHub **Settings** → **Applications** → **Installed GitHub Apps**에서 본다 (만든 App과 설치된 App은 다르다).
  - "이 GitHub 계정으로는 관리할 수 없어요"는 로그인한 계정 또는 App에 `nocturne`의 쓰기 권한이 없다는 뜻이다. 로그인 뒤 일하다가 GitHub가 403으로 거절해도 같은 말이 뜬다.
  - App의 client secret을 새로 만들었다면 Worker의 `CLIENT_SECRET`도 바꿔 넣는다.
  - 폰에서만 안 되면 그 폰 브라우저가 github.com에 로그인돼 있는지 본다.
  - 저절로 로그인하다 실패한 탭은 다시 저절로 하지 않는다. 고친 뒤 **GitHub로 로그인**을 누른다.
- **배포 바로 뒤 관리 페이지가 "불러오는 중…"에서 멈추면**: GitHub Pages는 배포 뒤 최대 10분 동안 옛 관리 페이지 파일을 캐시에서 내줄 수 있다 (새 파일과 옛 파일이 섞인다). 새로고침하거나 10분 기다린 뒤 다시 연다.
- **PC 도우미 토큰이 만료되면**: 도우미는 아무것도 하지 못하고 `helper.log`에 `오류 GitHubError: GitHub 401: Bad credentials`가 남는다. 새 토큰을 만들어(위 PC 도우미 토큰) `py scripts\pc_helper.py --setup`으로 저장하고 도우미를 다시 켠다.
- **공유 목록에 "Nocturne 관리"가 없으면**: 관리 앱을 설치했는지(사이트 앱 "Nocturne"과 다른 앱이다), 설치한 뒤 온라인으로 한 번 열었는지 본다. 고른 사진이 JPEG·PNG가 아니면(HEIC 등) 나오지 않는다. 안드로이드 Chrome만 된다. 관리 페이지에서 "앱 설치"가 없고 "Nocturne 열기"만 보이면 설치와 오프라인의 순서대로 다시 설치한다. 공유했더니 오류 화면(405)이 나오면 관리 워커가 없어진 것이다 (사이트 데이터를 지웠을 때 등): 관리 앱을 온라인으로 한 번 열고 다시 공유한다.
- **"확인할 수 없는 항목"**: `queue/<name>/`에 `item.json`이 없거나 읽을 수 없는 폴더다 (망가진 업로드, 손으로 만든 폴더). AI는 이것을 건너뛰고 기록(`helper.log`, Actions)에 한 번 적는다. 관리 페이지에서 **버리기**로 지운다.
- **"AI 대기"가 오래 가면**: 아무도 맡지 않은 것이다. 올린 지 15분이 넘으면 카드에 "AI가 오래 걸리고 있어요"가 붙는다. 대개 PC 도우미나 Ollama가 꺼져 있고 Draft도 돌지 못한 경우다. Actions 탭에서 Draft가 꺼져 있거나 실패하지 않았는지 본다 (저장소에 60일 동안 활동이 없으면 GitHub가 30분마다 도는 `schedule`을 멈춘다). 바로 돌리려면 Actions 탭의 **Run workflow** 또는 `gh workflow run draft.yml`. PC에서 Ollama와 도우미를 켜도 된다. 필요 없는 사진이면 버린다.
- **"PC AI가 보는 중…"·"GitHub AI가 보는 중…"이 10분 넘게 그대로면** 맡은 쪽이 멈춘 것이다. 다른 쪽이 곧 다시 맡는다 (PC는 20초마다, GitHub는 30분마다 확인). 이때도 "AI가 오래 걸리고 있어요"가 붙고 **버리기**가 나온다.

## 알아둘 것

- 이 PC는 로컬 루프백 연결의 일부를 끊는다 (WinError 10054). 그래서 `github_api.py`와 `curator.py`는 끊긴 요청을 다시 보내고, 브라우저 점검은 `serve.py`를 쓴다.
- Ollama 0.35.1이 같은 증상을 보인다: 요청의 일부가 `wsarecv: An existing connection was forcibly closed` 500으로 실패하고 바로 다시 보내면 성공한다. 그래서 `curator.py`는 최대 3번 시도한다 (연결 거부와 모델 없음은 바로 포기). `draft.yml`도 같은 0.35.1로 고정했고, 내려받은 파일은 그 릴리스의 `sha256sum.txt` 값으로 확인한다. 버전을 올릴 때 이 값도 같이 바꾼다.
- `draft.yml`에서 쓰기 권한이 있는 `GITHUB_TOKEN`은 worker 단계(`--check`, `--run`)와 대표 사진 고르기 단계에만, `ANTHROPIC_API_KEY`는 Claude 단계와 대표 사진 고르기 단계에만 준다 (둘 다 Ollama를 내려받기 전에 돈다). 내려받은 Ollama가 도는 단계에 토큰이나 키를 넣지 말 것. 키와 Claude API의 답 원문은 기록이나 `item.json`에 남기지 않는다.
- Anthropic에는 썸네일(긴 변 1000px, 메타데이터를 지운 사본)과 지시문(방 목록, 기존 사진의 제목·alt 예시. 영어가 있으면 영어도)만 보낸다. 대표 사진을 고를 때는 전시 중인 사진의 썸네일을 긴 변 512px로 줄인 사본(100장까지)과 각 사진의 이름·제목·방 이름을 보낸다. 큰 사본이나 원본은 보내지 않는다.
- Claude 비용은 사진 한 장(또는 다시 쓰기 한 번)에 약 $0.02다 (Opus 5.5, effort low). 대표 사진 고르기는 전시 사진이 바뀐 실행에서만 한 번 들고, 512px 사본은 1000px 썸네일의 약 4분의 1 토큰이라 지금(23장) 어림 $0.05다 (어림값이다. 실제 값은 Console의 사용량에서 확인한다). Max 요금제의 API 크레딧에서 나간다. 키가 없으면 Claude 단계는 건너뛰고 e4b로만 돈다.
- Claude SDK(`anthropic`)는 `draft.yml`의 `Claude SDK 설치` 단계가 `requirements-ai.txt`로 설치한다. 이 단계가 실패해도 실행은 이어지고, Claude 단계가 사진을 모두 e4b로 넘긴다 (`anthropic 패키지가 없어요`).
- `requirements-ai.txt`는 의존 패키지까지 버전과 sha256 해시로 고정돼 있고 `pip install --require-hashes`로 설치한다. 새 버전으로 올릴 때는 파일 맨 위 주석대로 저장소 밖 빈 폴더에서 `pip download`를 다시 하고, 받은 이름==버전마다 PyPI의 해시를 모두 다시 적는다.
- API로 만드는 커밋(worker, PC 도우미, 관리 페이지)은 작성자를 `HPotty36 <112685098+HPotty36@users.noreply.github.com>`로 적는다 (`github_api.AUTHOR`, `admin/config.js`의 `COMMIT_AUTHOR`). 계정 프로필의 이름·이메일이 공개 기록에 들어가지 않게 하려는 것이니 지우지 말 것.
- AI 제목은 40자, 설명은 200자, 영어 제목은 60자, 영어 설명은 250자에서 자른다 (낱말 경계에서). 흑백 표시(", 흑백 사진", ", black-and-white photo")는 자른 뒤에 붙는다. 제목·alt·영어 제목·영어 alt 네 칸 중 하나라도 비어 있는 답은 쓸 수 없다 (Ollama는 다시 요청하고, Claude는 읽을 수 없는 답으로 보고 다음 단계로 넘긴다).
- 모델: Actions는 Claude `claude-opus-5-5` (환경변수 `NOCTURNE_CLAUDE_MODEL`), 못 쓴 사진만 `gemma4:e4b-it-qat` (`NOCTURNE_MODEL`). PC는 `gemma4:12b-it-qat` (기본). 더 작은 e2b는 방 설명 문구를 베껴 써서 쓰지 않는다.
- README 그림(`.github/assets/`)은 자동으로 다시 만들지 않는다. 디자인이 바뀌면 손으로 다시 찍는다. 화면 그림은 공개 사이트의 한국어 페이지를 headless Chrome으로 찍었다: 주소는 `https://hpotty36.github.io/nocturne/?lang=ko`다 (`?lang=ko`가 없으면 첫 방문 규칙이 영어 페이지로 보낸다). 데스크톱 1440×900, 폰 390×844 2배(모바일 에뮬레이션), 둘 다 맨 위, 어두운 그림 하나뿐이다 (밝은 테마가 없다). JPEG 품질 82. 이 PC는 로컬 소켓이 불안정해서 DevTools를 디버깅 포트 대신 `--remote-debugging-pipe`(fd 3·4의 파이프, NUL로 끝나는 JSON)로 쓰고, 실행이 실패하면 다시 띄운다. 이 PC의 Chrome에는 정책으로 깔린 확장이 있어 모든 페이지에 초록 방패 배지를 넣을 수 있으므로, 찍기 전에 `div.shell`, `div.lb`, `script`가 아닌 최상위 노드를 모두 지운다. `document.fonts.ready`와 보이는 사진이 모두 받아질 때까지 기다리고 맨 위로 스크롤한 뒤 찍는다. 워드마크(Noto Serif KR 400, 이탤릭 없이 곧게, 1200×300, 배경 투명, 글자색은 어두운 쪽 `#ebe9e4`와 GitHub 밝은 테마용 `#1a1917`)와 소셜 미리보기(1280×640, 검은 바탕에 워드마크, 한 줄 소개, `photos/thumb/`의 사진 다섯 장)는 저장소 밖의 작은 HTML을 같은 방법으로 찍었다. 모두 Pillow로 메타데이터 없이 저장한다 (JPEG 품질은 화면 82, 소셜 86). 관리 페이지는 찍지 않는다. `social-preview.jpg`는 저장소 **Settings** → **Social preview**에 손으로 올린다.
- 관리 페이지의 토큰은 로그인으로 받는 8시간짜리 `ghu_…` 토큰이고, 열린 페이지의 메모리에만 있다. localStorage·sessionStorage·쿠키·주소·기록 어디에도 쓰지 않는다. 브라우저에 남는 것은 localStorage `nocturne.signedIn`("이 기기에서 로그인한 적 있음", 비밀 아님)과, 로그인하러 다녀오는 동안의 sessionStorage `nocturne.login`(위조 방지 값과 PKCE 원문, 돌아오자마자 지움)·`nocturne.auto`(이 탭에서 저절로 로그인을 이미 해 봄)뿐이다. 옛 방식의 `nocturne.token`은 페이지를 열 때 지운다. 공유로 받은 사진은 대기열에 올라가거나 버리기를 누를 때까지 캐시 `nocturne-share`에 남는다 (사진 앱의 원본 그대로라 위치 정보가 들어 있을 수 있다. 올리기는 사진 고르기처럼 브라우저에서 메타데이터를 지운 사본만 올린다).
- 관리 페이지는 `hpotty36.github.io` 출처에서 돈다. 이 계정의 모든 Pages 사이트가 같은 출처라서, 그중 어느 페이지에서 도는 스크립트든 혼자서 로그인을 끝낼 수 있다: 자기 PKCE 값으로 로그인 주소를 팝업으로 열고, 같은 출처인 팝업에서 `?code=`를 읽고, Worker에서 토큰으로 바꾼다 (Worker는 출처만 보니 통과한다). 그 기기 브라우저가 GitHub에 로그인돼 있고 App을 허용한 적이 있으면 아무것도 묻지 않는다. 토큰을 저장하지 않아도 이건 막지 못한다. 그래서 이 출처에는 믿을 수 있는 스크립트만 둔다 (지켜야 할 것). `nocturne.signedIn`도 그 사이트들이 볼 수 있다. 같은 이유로 다른 Pages 사이트의 스크립트는 오프라인 방문자가 받는 `nocturne-*` 캐시(페이지, 사진)에도 쓸 수 있고, 공유로 받은 사진(`nocturne-share`)을 읽거나 바꿀 수도 있다.
