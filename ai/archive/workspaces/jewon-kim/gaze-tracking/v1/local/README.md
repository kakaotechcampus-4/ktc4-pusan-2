# v1 / local — 개발·실험 리그

gaze-tracking **v1 모델을 로컬에서 돌리고 평가하는 리그**입니다.
웹캠 프레임에서 발표자의 시선이 **카메라(CAMERA)**, **화면(SCREEN)**, **화면 아래 대본(BOTTOM)**,
**그 밖(OTHER)** 중 어디를 향하는지 판정해 `GAZE_STATE` 이벤트로 내보내고,
촬영 조건이 얼마나 믿을 만한지는 `SESSION_CONDITION` 이벤트로 따로 내보냅니다 (v1.1, `gaze_v1.1.0`).

한 테이크는 세 단계입니다.

1. **준비 점검** — 얼굴을 찾아 따라갈 수 있는지(한 명, 화면 안, 인식 가능한 크기, 너무 어둡지 않음)만 확인합니다.
   이상적인 자세가 아니라 측정에 필요한 최소 조건이고, 이것도 안 되면 거절합니다.
   통과하면 **고개 원 확인**으로 이어집니다. 고개를 천천히 돌려 얼굴 주위 원을 채우며, 어느 방향으로 돌려도
   얼굴을 따라가는지 확인합니다(막지 않음, `vision/runtime/sweep.py`).
2. **보정** — 편한 자세로 렌즈 → 화면 중앙 → 대본을 차례로 봅니다.
   좋은 프레임이 16개 차야 다음 큐로 넘어갑니다(게이지). **고개 원에서 잰 정면 기준(1초)을 화면 중앙 큐가 확인**합니다.
   같으면 고개 원의 프레임을 기준점에 합쳐 8개만에 끝나고, 다르면(그 사이 자세가 바뀜) 새로 16개를 잽니다.
   렌즈·대본은 확인된 자세를 기준으로 **그 지점 쪽으로 고개가 향했을 때만** 진행도가 오릅니다(렌즈 = 위, 대본 = 아래).
   두 번째 큐가 끝나면 카메라 배치도 판정합니다.
3. 고개 방향 엔진에서는 고개를 돌리는 것이 곧 시선이므로 **신뢰도를 깎지 않습니다.** 신뢰도는 판정값이 틀어지거나 비는
   경우만 — **보정 자리에서 움직인 만큼**, 고개 방향 값의 흔들림, 인식 한계에 가까운 얼굴 크기, 판정 공백 — 낮아집니다.
   조명 변화와 다른 사람은 원인일 뿐이라 넣지 않습니다(다른 사람은 참고 신호로만 알림). 움직임은 판정 오차 각도로 재고,
   보정 지점 간격(6~12°, 55cm에서 약 6~7cm)을 2초 넘게 넘으면 **측정 불가**입니다(../README.md §7-8).
4. **실시간** — **고개 방향**을 큐별 기준점과 비교해 라벨을 정하고, 조건이 보정 때와 달라지면 신뢰도를 낮춥니다.
   화면 밖(OTHER)을 보면 어느 쪽인지(발표자 기준 8방향)도 붙입니다. OTHER는 보정 영역에서 8° 넘게 확실히 벗어났을 때만이고,
   고개를 조금 들거나 돌린 정도는 가장 가까운 대상(청중·화면·대본)으로 봅니다.

실시간 판정은 1초 기록으로 모여 **코치·리뷰 에이전트가 읽는 값**이 됩니다. 지금의 시선 이슈(대본·화면·다른 곳을 오래 봄,
눈맞춤 부족, 측정 불가), 테이크 요약과 문제 구간, 이전 테이크와의 차이, 피드백 전후 효과입니다
(`vision/evidence/gaze.py`, [../README.md](../README.md) §7-11).

판정 신호는 고개 방향입니다(기본 백본 `head_pose`). 노트북 웹캠 실측에서 눈 기반 백본이 고개를 고정한 채 움직인
눈을 세로로 거의 읽지 못해, 눈 기반 백본(`mediapipe_geom`, `l2cs`, `gazetr`)과 적응형 깜빡임은 `ai/src/vision/eye/`로
옮겨 비교 실험용으로 보관했습니다. 측정 내용은 [../README.md](../README.md) §7-2에 있습니다.

카메라 영상만 쓰고, 아무것도 저장하지 않습니다. 보정 결과는 세션과 함께 사라집니다.

같은 엔진의 **브라우저 버전**, 프론트엔드 페이지에 그대로 넣는 **카메라 화면 모듈**(카메라 영상과 그 안의 안내·보정 UI, `web/src/camera`),
그리고 그 모듈을 띄워 보는 **로컬 웹 데모**가 [`web/`](web/README.md)에 있습니다.
`cd web && npm install && npm run dev` → `http://localhost:5180`.

v1의 범위와 출력 계약은 [../README.md](../README.md)를 보세요.

> 이 문서는 **작업용 README**입니다 — 개발할 때 필요한 것만 담았습니다.
> 알고리즘 유도, 설계 근거, 전체 CLI 레퍼런스, doc N 대응표는
> **[../README.md](../README.md)** 에 있습니다.

> ⚠️ **모든 명령은 이 디렉터리(`v1/local/`)에서 실행하세요.**
> `ai/`에 `__init__.py`가 없어(PEP 420 네임스페이스 패키지) `python -m ai.tools.*`는
> CWD가 여기일 때만 해석됩니다. `run_demo.bat`이 `cd /d "%~dp0"`로 시작하는 이유입니다.

---

## 한눈에 보기

```
                     ┌─ ai/configs/*.yaml ──────────────┐
                     │  11개 YAML → VisionConfig        │
                     │  config_hash = 재현성 키(doc 18) │
                     └──────────────┬───────────────────┘
                                    ▼
 BGR 프레임 ──▶ preprocess ──▶ backbone ──▶ calibration ──▶ temporal ──▶ GAZE_STATE
   + t_ms       [doc 3-1]     [doc 3-2]     [doc 5]        [doc 6]      (7키 JSON)
                    │             │             │              │
              FrameObservation  GazeVector  GazeDecision   GazeStateEvent
              주 얼굴 478점     고개 방향    probs(K클래스)  label +
              헤드포즈·크롭     (head_pose)  CAMERA/SCREEN/  continuous_duration_ms
              scene(얼굴 수,                 BOTTOM/OTHER
              홍채 픽셀)                     /UNCERTAIN
                    │                           │
                    │                           └──▶ evidence: 1초 기록 → 코치 이슈 · 리뷰 요약
                    │                                (OTHER 방향 포함, 에이전트 입력)
                    └──▶ condition (보정 장면과 비교) ──▶ SESSION_CONDITION (4키 JSON)

                     └──────── VisionSession이 전부 소유 ────────┘
```

| 항목 | 값 |
|---|---|
| 분석 프레임률 | 8 FPS (프레임당 예산 125 ms) |
| 실측 비용 | 헤드리스 데모 p95 20 ms (`static_face_30fps.mp4`, 전체 파이프라인) |
| 기본 백본 | `head_pose` — 얼굴이 향한 방향. 학습 가중치·torch 불필요 |
| 기본 분류 | `reference` — 큐별 기준점 + 수식(소프트 박스 우도). `logistic`은 비교용 |
| 테스트 | Python 1790개 (1752 passed, 38 skipped) · 웹 197개 |
| 소스 | `ai/src/vision/` 약 11,600 LOC |

---

## 셋업

```bash
# 0) 가상환경 (v1/local 에서)
python -m venv .venv

# 1) 의존성 — pyproject.toml에 [project] dependencies가 없으므로 이 단계가 유일한 소스입니다
./.venv/Scripts/python.exe -m pip install -r requirements.txt

# 2) 패키지 (editable, src-layout)
./.venv/Scripts/python.exe -m pip install -e .

# 3) 확인
./.venv/Scripts/python.exe -m pytest -q                       # 1752 passed, 38 skipped
./.venv/Scripts/python.exe -m ai.tools.pipeline_demo --check   # 카메라·얼굴 추적·준비 점검 진단
```

> macOS/Linux에서는 `.venv/bin/python`입니다. `run_demo.bat`은 Windows 전용이라
> 다른 OS에서는 `python -m ai.tools.pipeline_demo`를 직접 쓰세요.
>
> **`.venv`는 저장소에 포함되지 않습니다** — editable 설치가 `.pth` 파일에 절대 경로를 박기 때문에
> 다른 머신으로 옮겨오면 동작하지 않습니다. 각자 만들어야 합니다.

`--check`는 카메라를 열어 전달 fps를 재고, 마지막 20프레임에서 얼굴 추적을 시도한 뒤
거부 사유별 집계를 출력합니다. 얼굴이 잡히면 같은 프레임으로 준비 점검을 돌려 상태(PASS / RETRY / REJECT),
측정값, 실패한 조건과 고칠 방법을 출력합니다(이 결과는 종료 코드에 영향을 주지 않습니다).
**카메라 앞에 얼굴이 없으면 `NO_FACE`로 exit 1이 나오는 것이 정상입니다** —
셋업이 깨진 것이 아니라 진단이 제대로 동작한 것입니다.

**필요한 것**: Python 3.10–3.12 (검증 환경: CPython 3.11.9 / Windows 11 / CPU only) · 웹캠(데모·수집용) · `ai/models/face_landmarker.task` · **MSVC 2015–2022 x64 재배포 패키지**(mediapipe import 체인이 요구)

모델 파일이 없으면 `FileNotFoundError`가 정확한 명령과 함께 납니다:

```bash
curl -L -o ai/models/face_landmarker.task \
  https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task
```

`torch`는 **선택**이며 기본 설치에 포함되지 않습니다 (보관된 `l2cs`, `gazetr` 백본 전용).
기본 백본 `head_pose`와 보관된 `mediapipe_geom`은 torch도 체크포인트도 필요 없습니다.
필요하면 **반드시 CPU 인덱스로** 설치하세요 — 그냥 설치하면 PyPI가 수 GB짜리 CUDA 빌드를 가져오고,
Windows에서는 그 휠이 DLL 로드에 실패하기도 합니다(`Error loading shm.dll`). torch가 아예 없는 것보다 나쁜 상태입니다:

```bash
./.venv/Scripts/python.exe -m pip install -r requirements-torch.txt \
    --index-url https://download.pytorch.org/whl/cpu
```

torch 없이 돌리면 관련 테스트 38개는 **skip** 됩니다 (`1752 passed, 38 skipped`).

---

## 자주 쓰는 명령

```bash
PY=./.venv/Scripts/python.exe

# ── 실행 ─────────────────────────────────────────────────────────────
$PY -m ai.tools.pipeline_demo                    # 준비 점검 → 3점 보정 → 실시간 (엄격 모드)
$PY -m ai.tools.pipeline_demo --advisory         # 판정을 기록만 하고 막지 않음 (연구용)
$PY -m ai.tools.pipeline_demo --method logistic  # 예전 LR 분류기로 비교
run_demo.bat --english                           # 더블클릭 래퍼 (플래그 그대로 전달)
$PY -m ai.tools.pipeline_demo --video tests/fixtures/static_face_30fps.mp4 --no-window
#   ↑ 정지 영상이라 배치를 판정하지 못해 PLACE_RESULT에서 멈추는 것이 정상
$PY -m ai.tools.pipeline_demo --video tests/fixtures/static_face_30fps.mp4 --no-window --advisory --countdown 0
#   ↑ 끝(LIVE)까지 진행하고 이벤트 수, 마지막 조건, p95 지연을 출력
$PY -m ai.tools.pipeline_demo --check            # 카메라·얼굴·준비 점검 진단만

# ── 테스트 ───────────────────────────────────────────────────────────
$PY -m pytest -q                                 # 전체
$PY -m pytest tests/test_temporal.py -q          # 한 서브시스템
$PY -m pytest -q -k "sign or convention"         # 부호 규약 관련만
$PY -m pytest -q -x --lf                         # 마지막 실패부터, 첫 실패에서 중단

# ── 데이터셋 (녹화본 필요) ──────────────────────────────────────────
$PY -m ai.tools.collect --participant-id P01     # 9블록 355초 프로토콜
$PY -m ai.tools.collect --list-conditions        # 계획만 출력 (카메라 안 씀)
$PY -m ai.tools.label_review --participant-id P01 --session-id S01 --video ai/datasets/raw/P01/S01.mp4
$PY -m ai.tools.extract_features --video ai/datasets/raw/P01/S01.mp4 --backbone mediapipe_geom

# ── 평가 (`ai.` 접두사가 있으면 -m 도 가능) ─────────────────────────
$PY ai/evaluation/gaze_eval.py --features-dir ai/datasets/features --backbone mediapipe_geom
$PY ai/evaluation/threshold_sweep.py --features-dir ai/datasets/features
$PY ai/evaluation/experiments/exp1_backbone.py --features-dir ai/datasets/features
$PY ai/evaluation/experiment_log.py --tail 10

# ── 체크포인트 ───────────────────────────────────────────────────────
$PY -m ai.tools.download_checkpoints --list
$PY -m ai.tools.download_checkpoints l2cs
```

### 호출 방식이 두 계열로 갈립니다 ⚠️

| 위치 | 방식 |
|---|---|
| `ai/tools/*` | `python -m ai.tools.X` |
| `ai/evaluation/*` | `python ai/evaluation/X.py` 또는 `python -m ai.evaluation.X` |

**`ai.` 접두사를 빼면** 실패합니다 — `python -m evaluation.gaze_eval`은
`ModuleNotFoundError: No module named 'evaluation'`. `ai/`를 `sys.path`에 넣는 것은 모듈 내부의
`_bootstrap_import_path()`인데, `-m`이 이미 이름을 해석해야 하는 시점보다 뒤이기 때문입니다.
자세한 내용은 [../README.md](../README.md) §4.

---

## 모듈 지도

**"어디를 고쳐야 하나"** 기준으로 정리했습니다.

### `ai/src/vision/` — 배포되는 유일한 패키지

| 파일 | LOC | 무엇을 소유하는가 |
|---|---:|---|
| `schemas.py` | 774 | **모든 크로스-모듈 데이터 계약 + 부호 규약.** 단일 진실 원천. 라벨 상수(`STATE_CLASSES`, `GAZE_DIRECTIONS` 등), `SessionConditionEvent` |
| `config.py` | 650 | 타입드 설정 데이터클래스, YAML 로딩·병합, `config_hash` |
| **preprocess/** | | 프레임 → `FrameObservation` |
| `crops.py` | 421 | 랜드마크 기하: 픽셀 변환, bbox, roll, 얼굴/눈 크롭, EAR, 품질, 홍채 지름(px) |
| `headpose.py` | 244 | 헤드포즈 (MediaPipe 행렬 우선, 9점 SQPNP 폴백), 카메라 내부 파라미터 |
| `landmarker.py` | 227 | MediaPipe 래퍼 — **이 트리에서 mediapipe를 아는 유일한 모듈**. 얼굴 최대 3개 |
| `pipeline.py` | 256 | 오케스트레이션 + 유효성 게이트 순서, 주 얼굴 선택(힌트 → 가장 가까운 얼굴), `scene` |
| `sampler.py` | 128 | 타임스탬프 기반 데시메이션, 영상 리더 |
| **backbones/** | | `FrameObservation` → `GazeVector` |
| `head.py` | 83 | **기본 `head_pose`.** 고개 방향 = 시선. `uses_eyes = False` |
| `base.py` | 251 | 인터페이스, `uses_eyes` 표시, 체크포인트 로딩·검증, warmup |
| `registry.py` | 103 | 이름 → 클래스. `BackboneConfig.name`의 순수 함수 (눈 기반은 `vision.eye`에서 등록) |
| **eye/** | | **보관 — 눈 기반, 비교 실험용** (이름으로 고르면 동작) |
| `mediapipe_geom.py` | 656 | 안구 구면 기하 + 블렌드셰이프 융합. 실측 한계는 모듈 설명 참고 |
| `gazetr.py` / `l2cs.py` | 384/331 | 사전학습 모델 어댑터. torch **지연 임포트** |
| `blink.py` | 88 | 보정 후 적응형 깜빡임 판정. 눈 기반 백본에서만 켜짐 |
| **calibration/** | | 사용자별 3점 캘리브레이션 (렌즈 / 화면 중앙 / 대본) |
| `gauge.py` | 239 | 큐별 좋은 프레임 게이지 — 무엇을 왜 버리는지 (고개 고정·깜빡임은 눈 기반만) |
| `references.py` | 770 | **기본 분류기.** 기준점 + 흔들림 σ + 소프트 박스 우도, 품질 판정, SCREEN 합치기, 다시 맞추기, OTHER 방향 |
| `factory.py` | 58 | `calibration.method` → 분류기 (`reference` / `logistic`) |
| `quality.py` | 314 | doc 5-2 품질 게이트, LOO, 분리도 (logistic용) |
| `classifier.py` | 284 | LR 파이프라인, doc 5-4 UNCERTAIN 규칙, 영속화 (비교용) |
| `features.py` | 234 | 특징 집합 A/B/C, 중심점 동결(누수 방어) |
| **temporal/** | | |
| `smoother.py` | 561 | EMA + 최신성 가중 투표 + 비대칭 히스테리시스. K클래스 (2클래스는 예전과 동일) |
| **runtime/** | | |
| `session.py` | 963 | `VisionSession` — 한 테이크 전체를 묶음. `eye_based`로 보정 규칙을 고름. `gaze_evidence` |
| `preconditions.py` | 297 | 준비 점검: 한 명·가운데·거리·정면·조명·fps → PASS / RETRY / REJECT |
| `sweep.py` | 340 | 고개 원 확인: 고개를 돌린 방향부터 원(32칸)을 켬, 놓친 방향·힌트·시간 초과. 막지 않음 |
| `condition.py` | 295 | 촬영 중 조건 감시 → 신뢰도, `SESSION_CONDITION` |
| `placement.py` | 504 | 카메라 배치 판정 (기본은 보정 기준점으로 = `anchors` 모드) |
| `policy.py` | 53 | 엄격 / 자문 게이트 — 판정을 막을지 결정하는 유일한 곳 |
| `version.py` | 141 | `AiVersion` 스탬프, config hash, git commit |
| **evidence/** | | 코치·리뷰 에이전트가 읽는 값 |
| `gaze.py` | 612 | 프레임 판정 → 1초 기록 → 코치 이슈(공통 평가기 형식), 테이크 요약, 이전 테이크 차이, 개입 효과 |
| **data/** | | 오프라인 기질 |
| `features_table.py` | 506 | 41열 특징 테이블, parquet/csv.gz |
| `splits.py` | 358 | 참가자 분리 분할, doc 4-3 누수 검사, 보정 창 |
| `labels.py` | 312 | 큐 타임라인 → 세그먼트, 가드밴드 |
| `manifest.py` | 122 | JSONL 매니페스트, `sample_id` |

### 그 외

| 경로 | 역할 |
|---|---|
| `ai/tools/` | CLI 5종 — `python -m ai.tools.X` |
| `ai/evaluation/` | 평가 CLI 5종 — **배포되지 않음**, `v1/local/`에서만 접근 가능 |
| `ai/configs/` | 11개 YAML (아래 참조) |
| `ai/models/` | 체크포인트 (gitignore, 출처 `.json` 사이드카만 추적) |
| `ai/datasets/` | 녹화본·라벨·특징 테이블 (gitignore, `.gitkeep`만 추적) |
| `ai/reports/` | 평가·실험 산출물 (gitignore) |
| `web/` | 브라우저 엔진(TS, `src/engine`) + 카메라 화면 모듈(`src/camera`) + 로컬 웹 데모 + 웹 테스트 — [web/README.md](web/README.md) |
| `tests/` | 1790개 테스트 + 실제 픽스처 2개 + 골든 테스트용 예전 smoother 사본 |
| `../README.md` | v1 전체 기술 레퍼런스 |

> 모든 `__init__.py`가 **0바이트**입니다. 패키지 레벨 재노출이 없으니 항상 구체 모듈을 임포트하세요:
> `from vision.preprocess.pipeline import PreprocessPipeline`

---

## 설정

| 파일 | → 섹션 | 튜닝 대상 |
|---|---|---|
| `preprocess.yaml` | `preprocess` | 분석 fps, 크롭 크기, 유효성 임계값, 얼굴 수, 적응형 깜빡임 |
| `gaze_backbone.yaml` | **`backbone`** ⚠️ | 백본 선택, 체크포인트, `params` |
| `placement.yaml` | `placement` | 배치 판정 |
| `calibration.yaml` | `calibration` | `method`, 게이지, 기준점 분류기(σ·화면 기하·사전확률·고개 이탈), 다시 맞추기, doc 5-4 임계값, LR |
| `temporal.yaml` | `temporal` | EMA, 투표, 클래스별 진입 임계값·dwell, 하트비트 |
| `preconditions.yaml` | `preconditions` | 엄격 모드, 준비 점검 한계(가운데·거리·정면·조명·fps) |
| `condition.yaml` | `condition` | 촬영 중 신뢰도 경계, 얼굴 손실·교체 판정, 발행 주기 |
| `evidence.yaml` | `evidence` | 1초 기록 규칙, 코치 이슈 시작·최대 길이, 눈맞춤 부족·측정 불가 기준, 개입 효과 창 |
| `sweep.yaml` | `sweep` | 고개 원 칸 수, 칸을 켜는 회전 각도, 빠른 움직임 한계, 힌트·시간 초과 |
| `release_gate.yaml` | `release_gate` | doc 7 통과선 6개 |
| `collection.yaml` | `collection` | 녹화 프로토콜 블록 |

```python
from vision.config import load_config
cfg = load_config()                                   # ai/configs 전체
cfg = load_config(overrides={"backbone": {"name": "l2cs"}})   # 섹션 키로!
cfg.hash()          # '958414c647b7' — 병합된 전체 설정의 12자리 해시
```

> 준비 점검·고개 원·조건 감시·게이지·에이전트 증거의 수치는 실측 전 **초기값**입니다. 처음엔 느슨하게 두고 실제 사용자로 조입니다.
> 화면 좌우 끝이 `OTHER`로 읽히면 `calibration.screen_aspect`를 먼저 키워 보세요.

**함정 3가지**

1. `gaze_backbone.yaml` → 섹션 이름은 **`backbone`**. 유일하게 파일명과 다릅니다.
2. 알 수 없는 키는 **조용히 버려집니다.** 오타는 에러가 아니라 무효과입니다.
   단 `backbone.params` 안의 오타는 **하드 에러**입니다 (비대칭은 의도적).
   출하 YAML의 오타는 `test_every_shipped_yaml_key_is_a_real_dataclass_field`가 잡습니다.
3. 없는 파일은 `{}`로 읽힙니다 → 잘못된 `--config-dir`는 "전부 기본값 + 멀쩡해 보이는 해시"를 만듭니다.

---

## 개발 워크플로

### 테스트

```bash
$PY -m pytest -q                      # 1752 passed, 38 skipped
$PY -m pytest tests/test_backbones.py -q -k iris
$PY -m pytest tests/test_temporal.py -q -k legacy   # smoother 골든 테스트만
```

> 애플리케이션 제어 정책이 DLL 로드를 막는 PC에서는 실패가 납니다. pyarrow가 막히면
> `test_the_parquet_backend_round_trips_the_table_unchanged` 1건이, scikit-learn이 막히면 `test_calibration.py` 수집 오류와
> logistic·learned 배치 테스트 14건이 실패합니다. 환경 문제이고, 코드 문제가 아닙니다.

`tests/conftest.py`가 repo root를 `sys.path`에 넣으므로 bare `pytest`도 동작합니다.
픽셀이 필요한 테스트는 실제 픽스처를 쓰고, mediapipe나 `.task` 모델이 없으면 **skip**합니다.

공유 픽스처: `cfg`, `fresh_cfg`, `face_rgb`, `face_landmarks`, `face_observation`,
`face_jpg`, `face_video`, `face_image_size`, `repo_root`

> **합성 데이터셋을 만들어 정확도 수치를 뽑지 마세요.** 지어낸 각도에서 측정한 F1은 아무 의미가 없습니다.
> 특정 코드 경로를 겨냥한 손수 만든 입력(알려진 벡터, 명시적 판정 시퀀스)은 정상이고 권장됩니다 —
> **동작**(순서, 임계값, 전환, 예외)을 검증하되 모델 점수는 검증하지 마세요.

### 백본 추가하기

```python
# ai/src/vision/backbones/my_backbone.py
from vision.backbones.base import GazeBackbone
from vision.backbones.registry import register
from vision.schemas import GazeVector

@register("my_backbone")          # cls.name을 자동으로 설정합니다
class MyBackbone(GazeBackbone):
    def predict(self, face_crop, left_eye_crop, right_eye_crop, head_pose,
                *, landmarks=None, blendshapes=None, image_size=None) -> GazeVector:
        ...
```

1. `registry._BUILTIN_MODULES`에 모듈을 추가
2. **네이티브 규약 → 프로젝트 규약 변환을 어댑터 안에서 끝내세요.**
   다운스트림은 절대 부호를 뒤집지 않습니다
3. 필요한 입력이 없으면 그럴듯한 0이 아니라 `ValueError`를 던지세요
4. `tests/test_backbones.py`에 부호 규약 테스트 추가 (BOTTOM은 pitch가 더 음수)
5. 눈을 읽는 백본이면 `uses_eyes = True`(기본값) 그대로 두세요. 보정이 고개를 고정시키고 깜빡임을 걸러냅니다.
   고개처럼 눈을 읽지 않는 신호면 `uses_eyes = False`로 두면 고개 움직임을 허용합니다

### 설정 값 바꾸기

`config_hash`가 움직이면 기록된 모든 실험의 프로버넌스가 무효화됩니다.
스윕 중이 아니라면 새 키를 하나씩 추가하지 말고 **한 커밋에 묶으세요**.

---

## 반드시 알아야 할 함정

### 부호 규약 — 이것만은

```
gaze_pitch = -asin(g_y)     # > 0 위,  < 0 아래
gaze_yaw   =  atan2(g_x, g_z)   # > 0 이미지 오른쪽
```

> **BOTTOM은 CAMERA보다 `gaze_pitch`가 더 음수여야 합니다.**

`ai/evaluation/sanity.py`가 (참가자, 백본)별로 확인하지만 **자문일 뿐 강제하지 않습니다.**
릴리스 게이트 6행에 포함되지 않고, `exp1_backbone --pitch-veto`를 줄 때만 구속력이 생깁니다.

### 유효하지 않은 값은 반드시 기권시키세요

이 코드베이스에서 가장 많이 발견된 버그 클래스입니다:

```python
max(-1.0, min(1.0, nan))   # → 1.0  (nan < 1.0 이 False 이므로!)
```

이 한 줄이 NaN 방향을 `pitch = -π/2`, 즉 **가능한 가장 강한 거짓 BOTTOM**으로 바꿨습니다.
같은 패턴이 백본과 스무더에도 있었습니다.

```python
from vision.schemas import clamp_unit
s = clamp_unit(value)        # 유한하지 않으면 None
if s is None:
    return None              # 기권 — 절대 클래스를 지어내지 말 것
```

**가드는 긍정형으로 쓰세요.** `if not (total > 1e-9)`이지 `if total <= 1e-9`가 아닙니다 —
NaN 비교는 항상 False라 후자는 통과시킵니다.

### 그 외

| 함정 | 내용 |
|---|---|
| 확률 열 순서 | (logistic) sklearn이 클래스를 정렬해 `classes_ == ['BOTTOM','CAMERA']` — **BOTTOM이 열 0** |
| `LOW_MARGIN` | logistic(2클래스)에선 기본 임계값으로 도달 불가 (`margin == 2·p_max−1`). reference(K클래스)에선 실제로 걸림 |
| 클래스 목록 | 분류기마다 다릅니다(`classifier.classes`). 세션은 보정 후 smoother를 그 목록으로 다시 설정합니다 |
| 평가의 SCREEN/OTHER | 정답이 CAMERA/BOTTOM뿐이라 이 둘은 **기권이 아니라 오답**으로 셉니다(`off_target_*`) |
| `SESSION_CONDITION` | `GAZE_STATE`와 별개 스트림. 신뢰도는 신호별 점수의 **최소값**이고 `issues`가 이유입니다 |
| `INVERTED_PITCH` | `quality.reason`에 **절대** 들어가지 않음. `hint`의 `INVERTED_PITCH_HINT_PREFIX`로 감지 |
| 이벤트의 `face_valid` | 판정의 `face_valid`와 다름 — `decision.face_valid and state != UNCERTAIN` |
| `update()` vs `should_emit()` | `update()`는 매 프레임 반환. 와이어에 올릴 건 `should_emit()`이 True인 것뿐 |
| `should_emit()` | **상태를 바꿉니다.** 발행하지 않을 이벤트에 투기적으로 호출 금지 |
| 미러링 | 분석은 항상 **원본** 프레임. 배치 확인의 yaw 부호가 여기 의존. 미리보기만 뒤집힘 |
| 캘리브레이션 앵커 | 추론 경로에서 `CalibrationFeatureExtractor.fit()` 호출 **금지** — 결정 경계가 다시 써짐 |
| `save_table()` | **반환 경로를 쓰세요.** 엔진 없으면 `.parquet` 요청이 조용히 `.csv.gz`가 됩니다 |
| 전부 UNCERTAIN | `session.last_backbone_error` 확인 — 죽은 백본은 자리 비운 사용자와 구분 불가. `session.last_condition.issues`의 `FACE_LOST`/`FACE_REPLACED`도 판정을 막습니다 |
| `process_frame` 시간 측정 | `perf_counter`를 정확히 두 번 부르는 구조를 테스트가 고정합니다. 측정 구간 안에 호출을 더하지 마세요 |

---

## 문제 해결

셋업 단계에서 자주 걸리는 것만 담았습니다. 전체 목록은 [../README.md](../README.md) §15에 있습니다.

| 증상 | 해결 |
|---|---|
| `ImportError: DLL load failed ... _cext` | kiwisolver 1.5.1 문제. `pip install "kiwisolver>=1.4,<1.5"` |
| `ModuleNotFoundError: No module named 'ai'` | CWD가 `v1/local/`이 아님 |
| `python -m evaluation.X` 실패 | 평가 모듈은 스크립트 경로로: `python ai/evaluation/X.py` |
| 데모에서 SPACE가 안 먹음 | OpenCV 창에 포커스가 없음 — **창을 먼저 클릭**. 엄격 모드에서 판정이 막고 있다면 화면 안내를 따르거나 `R`(재시도) / `C`(강제) |
| 데모가 CHECK에서 안 넘어감 | 준비 점검 미통과. 빨간 항목을 고치거나 `--check`로 원인 확인. 연구용이면 `--advisory` |
| 보정 게이지가 안 참 | 게이지 아래 이유를 보세요. `OUTLIER`면 한 지점을 계속 보기. `HEAD_MOVED`(눈 기반 백본만)면 고개를 고정하고 **눈만** 움직이기 |
| 온스크린 텍스트가 영어 | CJK 폰트 없음. 시작 로그의 `korean_text=yes/no` 확인 |
| `--backbone gazetr` 실패 | 가중치 없음. `download_checkpoints gazetr --from-file <path>` |
| `gaze_eval`이 백본 혼재로 거부 | 의도된 동작. `--backbone <name>` 지정 또는 `exp1_backbone.py` 사용 |
| 설정 변경이 반영 안 됨 | 키 철자와 **섹션 이름** 확인 (`gaze_backbone.yaml` → `backbone`) |

---

## 현재 상태

| | |
|---|---|
| ✅ | v1.1 온라인 경로 동작 (준비 점검 → 고개 원 확인 → 3점 게이지 보정 → 고개 방향으로 5라벨 + 신뢰도) · 1752 passed · 헤드리스 p95 20 ms |
| ✅ | OTHER 방향(발표자 기준 8방향)과 에이전트 증거(1초 기록 · 코치 이슈 · 리뷰 요약 · 개입 효과) — Python과 브라우저가 같은 값 |
| ✅ | 브라우저 엔진(`web/`) — Python과 동치 검사, 프론트엔드 계약·컴파일러 옵션 검사, 헤드리스 Chrome 실행 검사 통과 |
| ⚠️ | 고개를 든 채 눈만 내려 읽으면 CAMERA로 판정됩니다(눈 기반 백본은 보관 중) |
| ⚠️ | **녹화 데이터셋 없음** — `ai/datasets/*`가 비어 있습니다 |
| ⚠️ | **doc 7 릴리스 게이트가 실제 데이터로 평가된 적 없음** — 6개 임계값은 설계 목표입니다 |
| ⚠️ | **SCREEN 정답 없음** — 녹화 프로토콜에 화면 중앙 블록이 없어, SCREEN/OTHER 정확도는 웹캠으로 직접 확인해야 합니다 |
| ⚠️ | 준비 점검·고개 원·조건 감시·게이지·기준점 분류기·에이전트 증거의 수치는 초기값입니다 |
| ⚠️ | CI 없음 · 커밋 전 `pytest` 직접 실행 필요 |
| ⚠️ | `gazetr` 가중치 없음 (수동 설치만 가능) |

가장 큰 공백은 **doc 4-1 프로토콜에 따른 실제 녹화**입니다. 그전까지 모든 정확도 수치는 미측정입니다.

자세한 기술 문서 · 알고리즘 유도 · 전체 CLI 레퍼런스 · doc N 대응표 → **[../README.md](../README.md)**
