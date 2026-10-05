# 건너편 — 사진 전시 사이트

직접 찍은 사진을 "방"(강, 경기장, 밤, 순간)으로 나눠 거는 정적 사이트. 빌드 결과는 `index.html` 한 장과 `photos/` 폴더뿐이라 GitHub Pages 같은 정적 호스팅에 그대로 올릴 수 있다.

## 구조

```
index.html               빌드 결과물. 직접 고치지 말 것 (build.py가 덮어씀)
src/template.html        페이지 뼈대: CSS(디자인 토큰), 마크업, JS(정렬 레이아웃, 크게 보기)
src/photos.json          사이트 문구, 대표 사진, 방 목록, 사진별 제목·대체 텍스트·날짜·카메라
photos/thumb/<name>.jpg  그리드용, 긴 변 1000px
photos/full/<name>.jpg   크게 보기·대표 사진용, 긴 변 2400px
scripts/prepare_photos.py originals/ 의 원본을 메타데이터 없는 웹용 사본으로 변환
scripts/build.py         template + photos.json -> index.html
originals/               원본 사진 넣는 곳. git에 올리지 않음 (.gitignore)
```

## 명령 (Windows, Python 3)

```
py -m pip install -r requirements.txt   # 처음 한 번 (Pillow)
py scripts\prepare_photos.py            # originals/ 의 새 사진 변환
py scripts\build.py                     # index.html 다시 만들기
py -m http.server 8000                  # http://localhost:8000 에서 미리보기
```

`index.html`을 브라우저로 바로 열어도 동작한다.

## 사진 추가 흐름

1. 원본을 `originals/`에 넣는다.
2. `prepare_photos.py` 실행 → `photos/thumb`, `photos/full` 생성, 아직 등록 안 된 사진의 JSON 항목이 출력된다 (EXIF에 날짜·카메라가 있으면 채워져 있음).
3. 그 항목을 `src/photos.json`의 알맞은 방 `photos` 배열에 넣고 `title`, `alt`를 채운다.
4. `build.py` 실행.

새 방은 `rooms`에 `id`(영문 소문자, 주소 #앵커로 쓰임), `name`, `name_en`, `note`, `photos`를 갖춘 항목을 추가하면 상단 목차와 본문에 자동으로 생긴다.

## 지켜야 할 것

- 사이트 어디에도 소유자의 실명을 넣지 않는다. 사이트 이름은 "건너편".
- 사진은 반드시 `prepare_photos.py`를 거쳐서 넣는다. 원본에는 GPS 위치가 들어 있을 수 있고, 이 스크립트가 EXIF를 전부 지운다. `photos/`에 원본을 직접 복사하지 말 것.
- 장소 이름은 확인된 것만 쓴다. 사진만 보고 추측한 장소를 제목이나 설명에 넣지 않는다.
- `alt`는 화면 낭독기용 설명이다. 무엇이 찍혔는지 한 문장으로, 흑백이면 "흑백 사진"을 덧붙인다.
- 문구는 한국어, 짧고 평이하게.

## 디자인 메모

- 토큰은 `src/template.html`의 `:root`에 있다. 기본은 어두운 테마(밤 사진이 많아서), 밝은 테마는 `prefers-color-scheme: light`와 `data-theme`에서 같은 토큰만 바꾼다. 색은 반드시 토큰으로 쓴다.
- 강조색 `--lamp`(나트륨 가로등 호박색)는 포커스 표시와 현재 방 표시에만 쓴다.
- 글꼴: 제목 Hahmlet, 본문 IBM Plex Sans KR, 숫자·메타 IBM Plex Mono (Google Fonts).
- 그리드는 JS가 방마다 행을 나눠 모든 행을 가로폭에 꽉 맞춘다 (동적 계획법으로 목표 높이에 가장 가깝게). 사진은 잘리지 않고 원래 비율을 유지한다.
- 크게 보기: 방향키, 스와이프, Esc, 사진 바깥 클릭으로 닫기. 처음엔 썸네일을 보여주고 원본이 받아지면 바꾼다.

## 배포

- 저장소: https://github.com/HPotty36/across-gallery (공개)
- 사이트: https://hpotty36.github.io/across-gallery/
- GitHub Pages가 `main` 브랜치 루트를 그대로 서빙한다. `py scripts\build.py`로 `index.html`을 다시 만든 뒤 커밋하고 `git push`하면 1분 안팎으로 반영된다.
- `.nojekyll`이 있어야 파일이 가공 없이 올라간다. 지우지 말 것.
- 저장소가 공개라서 올린 파일은 누구나 볼 수 있다. 원본 사진(`originals/`)은 `.gitignore`로 제외되어 있다.
