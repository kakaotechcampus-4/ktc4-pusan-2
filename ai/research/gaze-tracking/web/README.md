# web — 시선 엔진 브라우저 포팅 + 로컬 데모

v1.1 시선 엔진(고개 방향 `head_pose` + 기준점 분류기)을 **브라우저에서 도는 TypeScript**로 옮긴 것과,
그 엔진을 쓰는 **로컬 데모 화면**입니다.

- `src/engine/` — 엔진. Web Worker 안에서 돌고(DOM 없음), 프론트엔드의 시선 분류기 계약
  (`frontend/src/workers/gaze.contract.ts`의 `GazeClassifier`, "A안")에 맞는 모양으로 값을 냅니다.
  나중에 이 폴더를 통째로 프론트엔드에 넣으면 됩니다([FE에 붙이는 법](#fe에-붙이는-법)).
- `src/camera/` + `src/worker/` — **카메라 화면 모듈**(`GazeCameraView`). 프론트엔드가 준 상자 하나를 채우고,
  카메라 영상과 그 안에 뜨는 것(얼굴 원, 3D 십자선·화살표, 보정 과녁, 안내 카드, 게이지, 결과, 실시간 상태)을 모두 그립니다.
  얼굴 확인(준비 점검 + 고개 원) → 3점 보정 → 실시간 확인이 이 상자 안에서 끊김 없이 이어집니다.
  나중에 이 폴더도 그대로 프론트엔드에 넣습니다([카메라 화면 붙이기](#카메라-화면-붙이기)).
- `src/demo/` — 로컬 데모 페이지. **프론트엔드 페이지의 대역**입니다. 상자 크기, 전체 화면, 시작 카드, 실시간 개발 패널처럼
  카메라 화면 바깥은 원래 프론트엔드가 맡으니, 데모는 모듈의 이벤트만 받아서 그립니다.
- 다른 곳(OTHER)을 보면 **어느 쪽인지**(발표자 기준 8방향)가 붙고, 프레임 판정을 1초 기록으로 모아
  **코치·리뷰 에이전트가 읽는 값**(시선 이슈, 테이크 요약, 개입 효과)을 만듭니다([에이전트 입력](#에이전트-입력-코치리뷰)).
- Python 패키지(`../src/gaze_lab`)가 기준 구현입니다. 이 엔진은 같은 입력에 같은 답을 내는지
  Python이 만든 기준 답으로 검사합니다([검증](#검증)).

카메라 영상은 이 기기 밖으로 나가지 않고, 아무것도 저장하지 않습니다.

---

## 실행

```bash
cd ai/research/gaze-tracking/web
npm install
npm run dev        # http://localhost:5180
```

- Node 24 이상, `../artifacts/face_landmarker.task`가 있어야 합니다(없으면 `../`에서 `uv run python tools/fetch_assets.py`).
- `npm run dev`는 먼저 `npm run assets`로 MediaPipe wasm과 `face_landmarker.task`를 `public/models/`에
  복사합니다(커밋하지 않음). CDN에서는 아무것도 받지 않습니다.
- 개발 서버는 프론트엔드와 같은 격리 헤더(COOP `same-origin`, COEP `credentialless`)로 뜹니다.

### 데모 흐름

`카메라 켜고 시작하기`를 누르면 **전체 화면 하나**에서 아래가 버튼 없이 이어집니다. 화면 가운데의 **원 하나**가
모든 단계를 잇습니다. 처음엔 얼굴을 담고, 고개를 돌리면 눈금이 켜지고, 그다음 보정 과녁이 됩니다.

**맡는 범위: 카메라 화면 안쪽만.** 카메라와 인식은 AI 파트가 맡고, 전체 화면으로 띄우는 일과 그 바깥 페이지는
프론트엔드가 맡습니다. 그래서 화면을 둘로 나눴습니다.

- **카메라 화면 모듈**(`src/camera/`): 받은 상자를 꽉 채웁니다. 상자 크기는 정하지 않고 따라갑니다. 창 크기가 아니라 상자 크기로
  배치가 바뀌고(컨테이너 쿼리), 클래스 이름은 모두 `gzc-`로 시작해서 프론트엔드 스타일(Tailwind)과 섞이지 않습니다.
- **데모 페이지**(`src/demo/`): 프론트엔드가 할 일을 흉내 냅니다. 상자를 창 전체로 잡고(`?box`이면 프론트엔드 카메라 미리보기처럼
  16:9 카드), 시작할 때 카메라 권한을 받아 전체 화면으로 띄우고, 실시간에서는 모듈 이벤트로 개발 패널을 그립니다.
  카메라는 프론트엔드와 같은 640×480을 씁니다.

카메라 화면 안에 뜨는 것(모듈):

| 위치 | 무엇이 뜨나 |
|---|---|
| 상자 전체 | 카메라 영상(거울상). 기본은 자르지 않음(`fit: 'contain'`, 비율이 다르면 어두운 여백), `fit: 'cover'`면 채우고 자름 |
| 얼굴 위 | 얼굴을 따라다니는 원(설정 단계), 3D 십자선, 코끝 3D 화살표 |
| 왼쪽 위 | 진행 단계(얼굴 확인 · 시선 보정 · 완료), 설정 단계에서만 |
| 오른쪽 위 | `이대로 진행`(준비 점검 거절 시), `고개를 돌리기 어렵다면 건너뛰기`(고개 원) |
| 아래 가운데 | 얼굴 맞추기·고개 원 단계의 안내 카드 |
| 오른쪽 가운데 | 보정 중 안내 카드(위쪽 렌즈 과녁, 아래쪽 대본 띠, 가운데 얼굴을 모두 피함). 상자 폭이 900px보다 좁으면 아래(대본 단계에서는 위) |
| 위 가운데 / 아래 띠 | 렌즈 과녁과 화살표 / 대본 자리 띠(180px, 상자 높이 560px 미만이면 110px) |
| 가운데(어둡게) | 보정 결과 카드: 알릴 것이 있을 때만 |
| 위 가운데(실시간) | 지금 보는 곳(이 프레임): 청중·화면·대본·다른 곳 + 방향, 판정 보류면 이유, 신뢰도가 50% 아래면 그 이유 |
| 상자 가장자리(실시간) | 1초 판정 테두리(청중 초록 · 화면/대본 노랑 · 판정 불가 빨강 점선). 1초 판정은 페이지가 내서 `setZone()`으로 넘김 |
| 왼쪽 아래 | 얼굴 인식 상태 |
| 아래 가운데(잠깐) | 알림: "보정 완료", "렌즈 기준을 다시 맞췄어요" 등 |

데모 페이지가 그리는 것(프론트엔드 몫): 시작 카드, 실시간 오른쪽 개발 패널(지금 보는 곳, 좌우·상하 값과 지도, 확률, 신뢰도,
처음 위치에서 움직인 거리, 코치 신호, `calibrated` 이벤트 JSON, 에이전트 입력 JSON). 전체 화면은 시작 버튼을 누를 때 페이지가
켜고, 실시간으로 넘어가도 유지합니다(ESC로 나감).

| 단계 | 화면 | 넘어가는 조건 |
|---|---|---|
| 1-1 얼굴 맞추기 | **카메라 화면**(상자를 채움, 기본은 자르지 않음, 거울상)과 그 위에서 얼굴을 따라다니는 원. 아래에 지금 고칠 것 한 줄과 점검 칩 9개(얼굴, 한 명, 가운데, 거리 2개, 정면, 밝기, 역광, 카메라 속도 — 모두 인식에 필요한 최소 조건). 조건이 어긋나면 화면이 흐려지고, 맞으면 원 테두리가 1초 동안 차오름 | 모든 항목이 1초 유지되면 자동 진행. 3초 넘게 안 맞으면 `이대로 진행`이 나타남 |
| 1-2 고개 원 | 원 둘레 32칸과 바깥의 **8방향 게이지 호**(그쪽으로 고개를 돌린 만큼 차오르고 다 차면 초록). 얼굴에는 고개를 따라 휘는 3D 십자선. 고개를 돌린 쪽 칸이 초록으로 켜지고, 지금 고개 방향은 **코끝 3D 화살표**(원 중심 기준, 원근이 있어 정면이면 나를 향한 원뿔 끝)와 주황 칸으로 보임. 처음엔 주황 칸이 한 바퀴 돌며 할 일을 보여 줌. 너무 빠르면 "천천히", 얼굴을 놓치면 "조금 덜 돌려 주세요", 멈추면 빈 쪽으로 화살표 | 다 채우면 초록 원과 체크 → 1.2초 뒤 자동 진행. 25초가 지나면 못 채운 방향을 알려 주고 5초 뒤 진행(`다시 하기` 가능). `고개를 돌리기 어렵다면 건너뛰기` |
| 2 시선 보정 | 카메라 화면이 **계속 보임**(3D 십자선과 원 중심 기준 3D 화살표 포함), 안내 카드는 오른쪽. **화면 가운데(내 얼굴의 원) → 렌즈(위 화살표와 과녁) → 대본 자리(아래 180px 띠의 과녁)** 순서 | 각 지점에서 좋은 프레임이 차면 다음으로(렌즈·대본 16개, 화면 가운데는 고개 원 중심을 확인하는 8개 — 4° 넘게 다르면 16개로 늘려 다시 잼). 안내 문구 아래에 **세 단계와 지금 단계의 큰 진행 막대·퍼센트·좋은 프레임 수**(멈추면 회색 + "멈춤"), 과녁 링도 진행률. **고개 원 중심 기준으로 그쪽을 볼 때만**(렌즈 = 위로 2°+, 대본 = 아래로 3°+, 화면 = 7° 이내, 옆으로 10° 이내) 진행도가 오르고, 아니면 "고개를 조금 더 들어 주세요" 같은 안내. 한 지점이 두 번 시간 초과되면 세 번째는 방향 확인 없이 받음 |
| 보정 결과 | **알릴 것이 있을 때만** 나옴: 실패, 화면 합침, 고개 원 건너뜀 등. 품질, 렌즈–대본 분리(σ), 자체 검증 정확도, 판정 가능한 곳, 알릴 것 | 통과했고 알릴 것이 없으면 이 화면 없이 "보정 완료" 알림과 함께 바로 실시간 확인으로. 통과했지만 알릴 것이 있으면 잠시 보여 주고 스스로 넘어감(`바로 시작`/`다시 보정`). 실패면 `다시 보정` 또는 `그래도 시작` |
| 3 실시간 확인 | 카메라 화면 안: 3D 십자선·렌즈 방향 기준 3D 화살표, 위 가운데에 지금 보는 곳(판정 보류면 이유), 가장자리에 1초 판정 테두리. 데모 페이지의 오른쪽 개발 패널에 지금 보는 곳(판정 보류면 **이유**), 화면 가운데 기준 **좌우·상하 값과 판별 기준 지도**(보정 영역·'다른 곳' 점선 경계·지금 위치), **처음 위치에서 움직인 거리와 판정 오차/한계 막대**(넘으면 측정 불가)(4분류 + 판정 보류, 다른 곳이면 `↗ 오른쪽 위` 같은 방향), 1초 판정, 클래스별 확률, 측정 신뢰도와 이유, 코치에게 가는 시선 신호와 테이크 요약 | `렌즈 다시 맞추기`(렌즈를 1초 보면 기준점 이동, 결과는 카메라 화면 알림), `보정 다시 하기` |

- 화면 가운데부터 보는 이유: 원이 이미 가운데에 있어 사용자가 그 자리를 보고 있기 때문입니다. 렌즈와 화면 가운데가
  모이면 바로 카메라 위치를 판정하고, 대본 자리는 마지막입니다.
- 미리보기는 거울상입니다. 엔진은 항상 원본 프레임을 받습니다.
- 얼굴 가이드(타원 + 십자)는 얼굴 랜드마크 6개로 그리고, 초당 8번 오는 결과를 화면 주사율로 부드럽게 따라갑니다.
- 실시간 테두리는 프론트엔드 리허설 화면과 같은 규칙입니다. 청중은 초록 2px, 화면·대본은 노랑 5px,
  판정 불가는 빨강 점선 5px입니다. 1초 판정은 데모 페이지가 **프론트엔드의 `TemporalVoter`를 그대로 불러와** 내고
  (읽기 전용 import) `setZone()`으로 카메라 화면에 넘깁니다. 그래서 이 테두리가 곧 Take에 기록될 값입니다.
- 보정 중 카메라 위치가 **화면 아래·옆으로 판정되면** 멈추고 알려 줍니다. 판정 불가(렌즈와 화면 가운데를
  같은 고개 자세로 봄)는 막지 않습니다.
- 방향은 발표자 자신의 왼쪽·오른쪽입니다. 미리보기가 거울상이라 화살표는 화면에서 보이는 쪽과 같습니다.
- "코치에게 가는 시선 신호"는 1초 기록이 하나 쌓일 때마다 갱신됩니다. 측정할 수 없을 때는 회색 카드
  (`시선을 잴 수 없어요 · 시선 피드백 보류`)가 나옵니다. 아래 `에이전트 입력 (JSON)`을 펼치면 에이전트가 받는 값
  그대로(마지막 1초 기록, 코치 이슈, 테이크 요약)를 볼 수 있습니다.

---

## 구조

```
web/
├── src/camera/                    ★ 카메라 화면 모듈 — 프론트엔드에 넣을 폴더
│   ├── index.ts                   GazeCameraView와 타입 — 진입점
│   ├── view.ts                    상자 하나를 채우는 화면: 영상, 프레임 펌프, 설정 흐름(①②③ → 결과), 실시간 표시, 이벤트
│   ├── camera.css                 .gzc 아래로만 적용되는 스타일 (gzc- 접두사, 컨테이너 쿼리)
│   ├── facetrack.ts               얼굴 위 원 배치 · 3D 십자선 · 3D 화살표 (contain/cover 둘 다)
│   ├── ring.ts · arrow3d.ts · cross3d.ts · guide.ts   고개 원 눈금 · 코끝 화살표 · 휘는 십자선 · 얼굴 가이드
│   └── text.ts                    화면 문구 (한국어)
├── src/worker/                    카메라 화면의 Worker (gaze.worker.ts) + 메시지 형식 (protocol.ts)
├── index.html, src/demo/          데모 페이지 = 프론트엔드 페이지 대역 (main.ts 시작 카드 · 개발 패널 · styles.css)
├── src/engine/                    ★ 엔진 — 프론트엔드에 넣을 폴더
│   ├── index.ts                   createClassifier({ assetDir }) — 진입점
│   ├── engine.ts                  GazeEngine: FE 계약 메서드 + 게이지 흐름 + 다시 맞추기
│   ├── contract.ts                FE 어댑터 입력 모양, 4분류 → FE 3구역 변환
│   ├── landmarker.ts              MediaPipe Face Landmarker (모듈 Worker용 설정)
│   ├── observe.ts                 프레임 → Observation (유효성 게이트, 주 얼굴, 장면)
│   ├── headpose.ts                변환 행렬 → yaw/pitch/roll
│   ├── geometry.ts                랜드마크 기하 (bbox, EAR, 홍채 지름, 주 얼굴 선택)
│   ├── luma.ts                    OffscreenCanvas로 얼굴/배경 밝기
│   ├── preconditions.ts           준비 점검 (PASS / RETRY / REJECT)
│   ├── sweep.ts                   고개 원 확인 (방향별 칸, 놓친 방향, 힌트)
│   ├── gauge.ts                   보정 게이지
│   ├── reference.ts               기준점 분류기 + 품질 판정 + SCREEN 합치기 + OTHER 방향
│   ├── evidence.ts                에이전트 입력: 1초 기록 → 코치 이슈 · 테이크 요약 · 이전 테이크 차이 · 개입 효과
│   ├── placement.ts               카메라 배치 판정
│   ├── condition.ts               촬영 중 조건 감시 (신뢰도)
│   ├── math.ts                    log Φ(scipy log_ndtr 동일 분기), logsumexp, median
│   ├── config.ts, defaults.ts     설정. defaults.ts는 Python 설정에서 생성
│   └── types.ts
├── test/                          vitest — parity(Python 기준 답) · engine · fe-contract
│   └── fe-contract/conformance.ts FE의 modelClassifier.ts가 될 코드 (FE 계약 파일을 읽기 전용으로 import)
├── scripts/                       copy-assets · smoke · bench (Node) (Python 생성기는 `../tools/`)
├── bench.html, src/bench.ts       화면 없는 성능 측정 페이지
└── tsconfig.json / tsconfig.fe.json (FE 컴파일러 옵션 그대로) / tsconfig.node.json
```

### Python에서 옮기지 않은 것

| 빠진 것 | 이유 |
|---|---|
| 눈 기반 백본(`mediapipe_geom`, L2CS, GazeTR), 적응형 깜빡임 | 기본 판정이 고개 방향이라 보관 중 (`../src/gaze_lab/eye/`) |
| solvePnP 헤드포즈 대체 경로 | MediaPipe가 얼굴마다 변환 행렬을 줍니다. 행렬이 없으면 "측정 안 됨"으로 거절합니다 |
| `logistic` 분류기, 시간 평활(`GAZE_STATE`) | 비교 실험용이고, 1초 판정은 프론트엔드 몫이라 엔진은 프레임 판정만 냅니다 |
| 녹화·평가 도구 | 브라우저 범위 밖 |

---

## 검증

```bash
npm test             # vitest 197개
npm run typecheck    # tsc 3번: 전체 / 설정 파일 / FE 컴파일러 옵션(tsconfig.fe.json: 엔진·Worker·카메라 화면)
npm run smoke        # 헤드리스 Chrome + 가짜 카메라로 데모 전체 (검사 23개, 화면 캡처 .cache/screens/)
npm run bench        # Worker 프레임당 ms (CPU·GPU delegate), -- --no-isolation 으로 격리 헤더 없이
npm run fixtures     # Python에서 defaults.ts와 기준 답 다시 생성 (Python 쪽을 바꾼 뒤)
```

| 무엇을 | 어떻게 | 결과 |
|---|---|---|
| Python과 같은 답 | Python 코드가 만든 기준 답(`test/fixtures/parity.json`)과 비교 — log Φ, 소프트 박스 밀도, 헤드포즈(두 메모리 순서), 랜드마크 기하, 분류기 시나리오 11개(SCREEN 합치기·각 실패 사유·OTHER 방향 포함), 배치 8개, 준비 점검·조건 감시(눈 기반 / 고개 기준 / 얼굴 메시 거리 / 다른 사람·얼굴 바뀜 규칙 / 고개 방향 흔들림)·별개의 얼굴 판정·게이지(큐별 목표·늘리기)·큐 방향 확인·고개 원 시퀀스(1초 중심과 흔들림), 에이전트 입력(66초 테이크의 1초 기록·창 통계·코치 이슈·테이크 요약·이전 테이크 차이·개입 효과, **키 이름까지**) | 136개 통과. 수치는 1e-9 ~ 1e-12 안에서 일치 |
| 엔진 흐름 | 가짜 얼굴 검출기로 FE 순서(배치 → 보정 → 판정), 게이지 흐름, 조건 감시, 다시 맞추기, 준비 점검, 고개 원(채우기·얼굴 손실), 원 중심 기준 큐 방향 확인, 화면 가운데가 원의 중심을 확인·합침 / 자세가 바뀌었으면 새로 재고 렌즈·대본 기준도 갱신, 고개를 돌려도 신뢰도 유지, 너무 멀리 움직이면 측정 불가와 이동 cm, 조금 들거나 돌린 고개는 가장 가까운 대상, 눈꺼풀이 내려간 숙인 고개도 판정(고개 방향은 눈 없이), OTHER 방향, 판정 → 에이전트 입력 | 28개 통과 |
| 판별 시나리오 | v1 README §7-13의 시나리오 28개를 가짜 얼굴 검출기로 엔진 전체에 — 렌즈·화면·대본, 화면 가장자리(좌우 값), 옆·위·아래의 다른 곳, 고개를 많이 드는 사람의 좌우, 눈이 안 보이는 고개, 혼자 물러나기·중복 검출·배경 오검출은 다른 사람 아님, 다른 사람 1초(참고 신호), 얼굴 바뀜(보정 직후 포함), 자리 이동, 얼굴 사라짐, 조명 변화는 신뢰도 그대로, 고개 방향 흔들림, 고개 원 기준 확인·자세가 바뀐 뒤 다시 재기, 준비 점검 | 28개 통과 |
| FE 계약 | `conformance.ts`(FE `modelClassifier.ts`의 TODO를 채운 모양)를 FE의 `aiAdapter.ts`로 실제 실행 | 5개 통과 |
| FE에서 컴파일되는가 | `tsconfig.fe.json` = FE `tsconfig.app.json` 옵션(`erasableSyntaxOnly`, `noUnusedLocals/Parameters`, JSON import 없음 …)으로 엔진·Worker·카메라 화면 모듈 | 통과 |
| 실제 브라우저 | `npm run smoke`: 모듈 Worker 안 MediaPipe 로드, ImageBitmap 입력, OffscreenCanvas 밝기, 격리 헤더, 지연, 하나로 이어진 흐름(얼굴 맞추기 → 고개 원 → 보정 → 결과), 고개 원의 중심 측정, 고개를 움직이지 않는 얼굴은 렌즈·대본 진행도가 0(`LOOK_HIGHER`/`LOOK_LOWER`), 위치 기준선(정지 얼굴은 이동 0°), 실제 MediaPipe의 정지 얼굴 흔들림(0.06°), 실시간 판정에서 1초 기록·코치 이슈·테이크 요약 생성, **카메라 화면이 창이 아니라 받은 상자(16:9 카드)를 채움** | 23개 통과. 테스트 영상에서 yaw 6.9°·pitch −4.4°·밝기 146.5 (Python 6.8°·−4.8°·147.4) |
| Python 쪽이 바뀌면 | `../tests/test_web_engine_sources.py`가 defaults.ts·기준 답이 낡았는지 검사 | pytest에 포함 |

**지연 시간** (이 PC, 헤드리스 Chrome, 640×480 테스트 영상): Worker 한 프레임 중앙값 약 40~90 ms이고
대부분이 MediaPipe 검출입니다. 엔진 계산은 약 5 ms입니다. 같은 설정도 PC 부하에 따라 40~80 ms로 흔들려,
실제 사용자 기기에서 `npm run bench`로 다시 재야 합니다. 예산은 프레임당 125 ms(8 FPS)입니다.

- 첫 프레임들은 초기화 비용이 100~200 ms라, 엔진을 만들 때 빈 이미지로 두 번 검출해 미리 치릅니다(`warmUp`).
- MediaPipe GPU delegate도 Worker에서 돕니다(`createClassifier({ delegate: 'GPU' })`). 기본값은 Python과
  수치가 같은 CPU입니다.
- 프레임을 줄여 보내면 빨라지지만 권하지 않습니다. 640px 폭에서는 90 cm 거리의 홍채가 4 px 남짓이라
  준비 점검의 거리 판정(홍채 7 px 이상)이 49 cm부터 "너무 멀다"로 깨집니다.

---

## 에이전트 입력 (코치·리뷰)

코치 에이전트와 리뷰 에이전트는 프레임을 읽지 않고 **1초 기록**과 그 요약을 읽습니다. `evidence.ts`가
Python `../src/gaze/core.py`(1초 기록 이후) · `../src/gaze_lab/evidence/gaze.py`(1초 기록까지)와 같은 값을 냅니다(규칙과 수치는 archive `v1/README.md` §7-11).

```ts
import { GazeEvidenceRecorder, makeConfig, sampleToDict } from './vendor/gaze';

const rec = new GazeEvidenceRecorder(makeConfig().evidence);   // 테이크마다 새로 (또는 rec.reset())
const decision = impl.classify(bitmap, tMs);                     // FrameDecision (direction 포함)
for (const sample of rec.record(decision)) post(sampleToDict(sample));  // 1초가 끝날 때마다 하나

rec.issues();     // 코치: 지금의 시선 이슈 (공통 평가기 형식, 심각도 순)
rec.summary();    // 리뷰: 테이크 통계 · 문제 구간 · OTHER 방향별 시간
// compareSummaries(prev, cur), interventionOutcome(rec.timeline, tMs, issueType, cfg) 도 같은 모듈에
```

| 값 | 모양 | 쓰는 곳 |
|---|---|---|
| 1초 기록 | `{t_ms, duration_ms, state, direction, confidence, reliability, issues, frames}` | 저장·전송 단위. `state`는 CAMERA / SCREEN / BOTTOM / OTHER / UNCERTAIN / UNMEASURED |
| 코치 이슈 | `{evaluator: "gaze", issue_type, t_ms, severity, confidence, persistence_sec, evidence, actionable}` | `GAZE_ON_SCRIPT` · `GAZE_ON_SCREEN` · `GAZE_AWAY`(+`direction`) · `GAZE_LOW_EYE_CONTACT` → `LOOK_AT_CAMERA`. `GAZE_UNMEASURABLE`은 `actionable: false` = 시선 피드백 금지 |
| 테이크 요약 | 측정 시간, `coverage`, 상태별 비율, `eye_contact_ratio`, `other_direction_ms`, 가장 긴 구간, 구간 목록, 문제 구간 | 리뷰 근거 |
| 이전 테이크 차이 | `{previous, current, delta}` | 리뷰의 "지난번보다" |
| 개입 효과 | `{intervention, before, after_5s, effective}` | 코치 피드백이 먹혔는지 |

비율은 **측정된 시간**(판정된 4상태)으로 나눕니다. FE의 `measuredMs`와 같은 생각입니다.

---

## FE에 붙이는 법

프론트엔드 코드는 이 폴더에서 고치지 않았습니다. 붙일 때 할 일은 셋입니다.

1. **엔진 복사** — `src/engine/` 폴더를 통째로 `frontend/src/workers/vendor/gaze/`로 복사합니다.
   (`@mediapipe/tasks-vision` 1.0.1은 프론트엔드에 이미 의존성으로 있습니다.)
2. **자산** — `frontend/public/models/`에 아래 파일을 둡니다. `npm run assets`가 만드는 `public/models/`와 같은 구성입니다.

   | 파일 | 크기 | 출처 |
   |---|---|---|
   | `face_landmarker.task` | 3.6 MB | `../artifacts/` |
   | `vision_wasm_module_internal.js` · `.wasm` | 약 12 MB | `node_modules/@mediapipe/tasks-vision/wasm/` |

   프론트엔드 `public/models/README.md`에는 `vision_wasm_internal.*`이 적혀 있지만, **모듈 Worker에서는
   `vision_wasm_module_internal.*`이 필요합니다**(나머지 wasm 파일은 함께 두어도 무방).
3. **`modelClassifier.ts`** — TODO 자리를 `test/fe-contract/conformance.ts`와 같은 모양으로 채웁니다.
   팩토리만 `() => import('./vendor/gaze').then((m) => m.createClassifier({ assetDir: '/models/' }))`입니다.

위 셋은 프론트엔드의 지금 계약(2점 보정, 3구역)에 엔진만 붙이는 길입니다. 준비 점검, 고개 원, 3점 보정 화면까지 그대로 쓰려면
아래 카메라 화면 모듈을 붙입니다.

### 카메라 화면 붙이기

`src/camera/`, `src/worker/`, `src/engine/`을 함께 복사합니다(예: `frontend/src/features/gaze/camera`, `…/worker`, `…/engine`.
서로 상대 경로로 부르므로 세 폴더를 나란히 둡니다). 프론트엔드가 할 일은 **상자, 카메라 스트림, 결과 처리** 셋입니다.
상자 안은 모듈이 그립니다.

```tsx
import { GazeCameraView, type SetupResult } from '@/features/gaze/camera';

function GazeCamera({ stream, onCalibrated }: { stream: MediaStream; onCalibrated: (r: SetupResult) => void }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const view = new GazeCameraView(box.current!, { assetDir: '/models/' });
    const off = view.on('calibrated', onCalibrated);
    view.attach(stream).then(() => view.startSetup());
    return () => { off(); view.destroy(); };       // Worker도 같이 닫힘. 스트림은 프론트엔드가 닫음
  }, [stream]);
  return <div ref={box} className="h-full w-full" />;   // 크기는 프론트엔드가 정함 (전체 화면이면 이 상자를 전체로)
}
```

- **상자**: 모듈은 받은 상자를 100%로 채우고, 크기를 스스로 정하지 않습니다. 상자에 높이가 있어야 합니다. 전체 화면도
  프론트엔드가 정합니다(상자를 화면 전체로 잡거나, 상자나 페이지에 `requestFullscreen()`을 부름. 사용자 클릭 안에서만 됨).
- **스트림**: `attach(stream)`은 영상만 보여 주고 분석합니다. 트랙을 멈추지 않으니 `useCameraStream`이 계속 맡습니다.
  같은 스트림을 프론트엔드의 다른 `<video>`에 함께 걸어도 됩니다. 엔진에는 원본 프레임이 가고 화면만 거울상입니다.
- **Worker**: 기본으로 모듈이 `new Worker(new URL('../worker/gaze.worker.ts', import.meta.url))`로 직접 만듭니다.
  프론트엔드처럼 `?worker&url`로 만들고 싶으면 `new GazeCameraView(box, { worker })`로 넘깁니다(새 Worker여야 함).
- **스타일**: `camera.css`는 `view.ts`가 import합니다. 규칙은 모두 `.gzc` 아래, 클래스는 `gzc-`로 시작해 Tailwind와 섞이지 않습니다.
  배치는 상자 크기로 바뀝니다(폭 900px 미만이면 보정 카드를 아래로, 560px 미만이면 작은 글씨, 높이 560px 미만이면 대본 띠 110px).

| 옵션 | 기본값 | 뜻 |
|---|---|---|
| `assetDir` | `/models/` | `face_landmarker.task`와 wasm 위치 |
| `worker` | 모듈이 만듦 | 직접 만든 Worker (`src/worker/gaze.worker.ts`) |
| `fit` | `contain` | `contain` 영상 전체가 보임(여백) / `cover` 상자를 채우고 자름 |
| `autoLive` | `true` | 알릴 것 없는 보정이 끝나면 바로 실시간. `false`면 결과 카드를 늘 보여 주고 버튼이나 `goLive()`를 기다림 |
| `otherAs` | `UNCERTAIN` | OTHER를 프론트엔드 3구역 중 어디로 셀지 |
| `delegate` | `CPU` | MediaPipe delegate |
| `unattended` | `false` | 헤드리스 검사 전용(기다림 없이 모든 탈출구를 스스로 누름) |

| 메서드 | 하는 일 |
|---|---|
| `attach(stream)` / `detach()` | 스트림을 보여 주고 분석 시작 / 멈춤 (`idle`에서도 얼굴 십자선과 인식 상태는 보임) |
| `startSetup('align' \| 'calib')` | ① 준비 점검부터, 또는 고개 원을 둔 채 ③ 세 지점부터 |
| `goLive()` / `cancel()` | 실시간 / 영상만 (`idle`) |
| `useCalibration(model)` | 저장해 둔 `SetupResult.model`을 Worker에 넣고 바로 실시간. 다른 엔진·설정의 값이면 `false` |
| `reanchor()` | 실시간에서 렌즈를 1초 보면 기준점을 옮김(결과는 화면 알림과 `reanchor` 이벤트) |
| `setZone(zone)` | 프론트엔드 1초 판정(`TemporalVoter`)을 테두리로 (`CAMERA` / `BOTTOM` / `UNCERTAIN` / `null`) |
| `destroy()` | 타이머·Worker·DOM 정리 |

| 이벤트 | 값 | 언제 |
|---|---|---|
| `ready` | `{version, isolated}` | 엔진이 뜸. `version`은 Take에 고정할 문자열 |
| `phase` | `{phase, previous}` | `idle` · `align` · `sweep` · `swept` · `calib` · `result` · `live` 사이를 옮길 때 |
| `frame` | 처리 시간, 얼굴 여부와 이유, 고개 각도, 얼굴 가이드 | 분석한 프레임마다(약 8번/초) |
| `check` | 준비 점검 결과와 측정값(거리 cm, 위치, 밝기 …) | ① 동안 |
| `sweep` | 고개 원 상태(중심과 흔들림, 채운 방향·못 채운 방향) | ② 동안 |
| `gauge` | `{status, baseline}`: 지점별 좋은 프레임, 멈춘 이유, 정면 기준 확인 결과 | ③ 동안 |
| `placement` | 카메라 위치 판정 | 렌즈·화면 가운데가 끝난 직후 |
| `calibrated` | `SetupResult` | 보정이 끝날 때(성공이든 아니든) |
| `decision` | `FrameDecision`(4분류, 방향, 좌우·상하, 확률, 신뢰도와 이유, 처음 위치에서 이동) | 실시간 프레임마다 |
| `reanchor` | 다시 맞추기 상태 | 상태가 바뀔 때 |
| `error` | `{message}` | Worker 오류 |

`SetupResult`는 보정 품질(`quality`), 저장할 모델(`model`, 순수 데이터라 `structuredClone`·IndexedDB 가능), 카메라 위치(`placement`),
판정할 수 있는 곳(`classes`), ①의 마지막 점검(`preconditions`), ②의 고개 원(`sweep`, `sweepSkipped`), ③의 정면 기준 확인(`baseline`),
경고를 무시하고 넘어갔는지(`forced`), 결과 카드 문구(`notes`)를 담습니다. 영상이나 프레임은 들어 있지 않습니다.

`decision`은 엔진의 프레임 판정 그대로입니다. 프론트엔드 1초 판정은 지금처럼 `aiAdapter`의 규칙(얼굴 없음은 표 없음)으로
`TemporalVoter`에 넣고, 그 결과를 `setZone()`으로 돌려주면 테두리가 맞춰집니다. 데모 페이지(`src/demo/main.ts`)가 이 모양 그대로입니다.

### 확인한 것

| 항목 | 상태 |
|---|---|
| Worker 안에서 동작 (DOM 없음) | ✅ 모듈 Worker에서 MediaPipe 로드·검출, OffscreenCanvas 밝기 |
| Vite 개발 서버 | ✅ 동적 import에 `?import`가 붙어 막히는 문제를 자산 경로를 절대 URL로 바꿔 해결 (엔진 안에서 처리) |
| 입력 | ✅ `ImageBitmap`. 엔진은 비트맵을 닫지 않습니다(FE 규칙대로 호출부가 닫음) |
| 보정 기준값 저장 | ✅ `model`은 순수 데이터라 `structuredClone`·IndexedDB 가능. 스키마가 다르면 `calibrate()`가 거절 |
| 출력 키 | ✅ Python `to_dict()`와 같은 이름이라 FE `aiAdapter.ts`가 그대로 변환 |
| FE 컴파일러 옵션 | ✅ `tsconfig.fe.json`으로 검사 |
| 격리 헤더 없이 | ✅ 운영 서버(Caddy)에 COOP/COEP가 없어도 동작 (`crossOriginIsolated=false`에서 측정) |
| 타임스탬프 | ✅ 배치 프레임과 실시간 프레임이 섞여도 MediaPipe VIDEO 모드용으로 단조 증가 |
| 버전 문자열 | `gaze_v1.1.0+head_pose+reference_anchor_v1` (FE가 Take에 고정, 엔진이 바뀌면 저장된 기준을 버림) |
| 카메라 화면 모듈 | ✅ FE 컴파일러 옵션으로 타입 검사(`src/camera`·`src/worker`), 헤드리스 Chrome에서 창 전체와 16:9 카드 둘 다에서 흐름 확인, 받은 상자를 정확히 채움 |

### FE 연동 시 확인할 것

계약은 바꾸지 않았고, 엔진이 FE의 현재 계약(2점 보정, 3구역)에 맞춰 값을 냅니다. 그 과정에서 FE와
정해야 할 것이 남습니다.

1. **OTHER(다른 곳)의 자리** — FE 구역은 청중/화면/판정 불가 셋뿐입니다. 기본값은 `OTHER → UNCERTAIN`입니다.
   이러면 딴 곳을 본 시간이 분모(`measuredMs`)에서 빠져 청중 비율이 실제보다 높게 나옵니다.
   `createClassifier({ otherAs: 'BOTTOM' })`로 "청중이 아님"으로 셀 수 있습니다(대신 화면 시간이 늘어남).
2. **화면 가운데 보기** — FE는 배치 확인 때 화면 가운데를 2초 봅니다. 엔진은 그 프레임을 SCREEN 기준점으로
   재사용하고, SCREEN은 FE의 BOTTOM(화면·대본)으로 나갑니다. 다만 고개로 판정하므로 렌즈와 화면 가운데를
   같은 자세로 보는 사람은 둘이 합쳐져(`SCREEN_MERGED`) **화면 보기도 청중으로** 셉니다. FE 문서가 우려한
   "카메라와 화면 사이 각도 차"가 고개 기준에서는 더 작을 수 있다는 뜻입니다.
3. **FE 목록에 없는 실패 사유** — 엔진의 `ANCHOR_AMBIGUOUS`는 FE `CalibrationFailReason`에 없어서
   FE 어댑터가 `ENGINE_ERROR` 안내로 바꿉니다(테스트로 고정). FE 목록에 추가할지 정해야 합니다.
4. **품질 등급** — FE는 안내문에서 `INVERTED_PITCH`만 찾아 FAIR로 내립니다. `SCREEN_MERGED` 경고는 GOOD으로 보입니다.
5. **준비 점검, 고개 원, 신뢰도** — FE 계약에는 자리가 없습니다. 엔진에는 이미 있습니다(`checkPreconditions(frame, tMs)`,
   `startSweep(tMs)` / `offerSweepFrame(frame, tMs)`, `classify()` 결과의 `condition.reliability`·`issues`).
   FE의 지금 Worker로 쓰려면 메시지를 늘려야 하고, 카메라 화면 모듈을 쓰면 그 Worker(`src/worker`)에 이미 있습니다. 고개 원은 판정에 쓰이지 않으므로 FE가 빼도 판정 값은 같습니다.
6. **보정 프레임의 첫 순간** — FE는 각 지점의 2초를 바로 모읍니다. 엔진은 시선 이동 중인 프레임을
   중앙값에서 크게 벗어난 것으로 보고 버립니다(게이지의 이상치 규칙과 같은 기준).
7. **카메라 배치** — 고개 기준에서는 `INCONCLUSIVE`가 자주 나옵니다(렌즈와 화면 가운데를 같은 자세로 봄).
   FE는 배치 결과를 경고로만 쓰므로 흐름은 막히지 않습니다.
8. **방향과 에이전트 입력** — FE의 1초 판정(`ZoneDecision`)은 3구역뿐이라 **방향과 화면/대본 구분이 사라집니다.**
   코치·리뷰 에이전트에 `GAZE_AWAY`의 방향이나 `GAZE_ON_SCRIPT`/`GAZE_ON_SCREEN`을 주려면 Worker가
   `GazeEvidenceRecorder`의 1초 기록을 함께 내보내는 메시지와 저장 자리가 필요합니다(계약 변경).
   기록 하나는 약 150바이트이고 영상은 들어가지 않습니다. 1초 다수결 기준(프레임 4개, 60%)은 FE `TemporalVoter`와 같지만,
   격자 시작점이 달라 경계의 1초는 서로 다르게 나올 수 있습니다.
