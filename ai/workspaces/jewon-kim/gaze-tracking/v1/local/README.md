# v1 / local — 개발·실험 리그

gaze-tracking **v1 모델을 로컬에서 돌리고 평가하는 리그**입니다.
웹캠 프레임에서 발표자의 시선이 **카메라(CAMERA)** 를 향하는지
**화면 아래 대본(BOTTOM)** 을 향하는지 판정해 `GAZE_STATE` 이벤트로 내보냅니다.

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
                     │  7개 YAML → VisionConfig         │
                     │  config_hash = 재현성 키(doc 18) │
                     └──────────────┬───────────────────┘
                                    ▼
 BGR 프레임 ──▶ preprocess ──▶ backbone ──▶ calibration ──▶ temporal ──▶ GAZE_STATE
   + t_ms       [doc 3-1]     [doc 3-2]     [doc 5]        [doc 6]      (7키 JSON)
                    │             │             │              │
              FrameObservation  GazeVector  GazeDecision   GazeStateEvent
              478 랜드마크      yaw/pitch    p_camera       label +
              헤드포즈·크롭     (라디안)     p_bottom       continuous_duration_ms
              유효성 게이트                  UNCERTAIN

                     └──────── VisionSession이 전부 소유 ────────┘
```

| 항목 | 값 |
|---|---|
| 분석 프레임률 | 8 FPS (프레임당 예산 125 ms) |
| 실측 비용 | 전처리 29.5 ms + `mediapipe_geom` 0.7 ms (데모 420프레임 실측; 픽스처 단일 프레임은 0.21 ms) |
| 기본 백본 | `mediapipe_geom` — 학습 가중치 없음, torch 불필요 |
| 테스트 | 1421개, ~43초 |
| 소스 | `ai/src/vision/` 약 7,400 LOC |

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
./.venv/Scripts/python.exe -m pytest -q                       # 1421 passed
./.venv/Scripts/python.exe -m ai.tools.pipeline_demo --check   # 카메라·얼굴 추적 진단
```

> macOS/Linux에서는 `.venv/bin/python`입니다. `run_demo.bat`은 Windows 전용이라
> 다른 OS에서는 `python -m ai.tools.pipeline_demo`를 직접 쓰세요.
>
> **`.venv`는 저장소에 포함되지 않습니다** — editable 설치가 `.pth` 파일에 절대 경로를 박기 때문에
> 다른 머신으로 옮겨오면 동작하지 않습니다. 각자 만들어야 합니다.

`--check`는 카메라를 열어 전달 fps를 재고, 마지막 20프레임에서 얼굴 추적을 시도한 뒤
거부 사유별 집계를 출력합니다. **카메라 앞에 얼굴이 없으면 `NO_FACE`로 exit 1이 나오는 것이 정상입니다** —
셋업이 깨진 것이 아니라 진단이 제대로 동작한 것입니다.

**필요한 것**: Python 3.10–3.12 (검증 환경: CPython 3.11.9 / Windows 11 / CPU only) · 웹캠(데모·수집용) · `ai/models/face_landmarker.task` · **MSVC 2015–2022 x64 재배포 패키지**(mediapipe import 체인이 요구)

모델 파일이 없으면 `FileNotFoundError`가 정확한 명령과 함께 납니다:

```bash
curl -L -o ai/models/face_landmarker.task \
  https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task
```

`torch`는 **선택**이며 기본 설치에 포함되지 않습니다 (`l2cs`, `gazetr` 백본 전용).
기본 백본 `mediapipe_geom`은 torch도 체크포인트도 필요 없습니다.
필요하면 **반드시 CPU 인덱스로** 설치하세요 — 그냥 설치하면 PyPI가 수 GB짜리 CUDA 빌드를 가져오고,
Windows에서는 그 휠이 DLL 로드에 실패하기도 합니다(`Error loading shm.dll`). torch가 아예 없는 것보다 나쁜 상태입니다:

```bash
./.venv/Scripts/python.exe -m pip install -r requirements-torch.txt \
    --index-url https://download.pytorch.org/whl/cpu
```

torch 없이 돌리면 관련 테스트 38개는 **skip** 됩니다 (`1383 passed, 38 skipped`).

---

## 자주 쓰는 명령

```bash
PY=./.venv/Scripts/python.exe

# ── 실행 ─────────────────────────────────────────────────────────────
$PY -m ai.tools.pipeline_demo                    # 배치확인 → 캘리브레이션 → 실시간
run_demo.bat --english                           # 더블클릭 래퍼 (플래그 그대로 전달)
$PY -m ai.tools.pipeline_demo --video tests/fixtures/static_face_30fps.mp4 --no-window
$PY -m ai.tools.pipeline_demo --check            # 웹캠 없이 진단만

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
| `schemas.py` | 585 | **모든 크로스-모듈 데이터 계약 + 부호 규약.** 단일 진실 원천 |
| `config.py` | 371 | 타입드 설정 데이터클래스, YAML 로딩·병합, `config_hash` |
| **preprocess/** | | 프레임 → `FrameObservation` |
| `crops.py` | 391 | 랜드마크 기하: 픽셀 변환, bbox, roll, 얼굴/눈 크롭, EAR, 품질 |
| `headpose.py` | 244 | 헤드포즈 (MediaPipe 행렬 우선, 9점 SQPNP 폴백), 카메라 내부 파라미터 |
| `landmarker.py` | 199 | MediaPipe 래퍼 — **이 트리에서 mediapipe를 아는 유일한 모듈** |
| `pipeline.py` | 157 | 오케스트레이션 + 유효성 게이트 순서 |
| `sampler.py` | 128 | 타임스탬프 기반 데시메이션, 영상 리더 |
| **backbones/** | | `FrameObservation` → `GazeVector` |
| `mediapipe_geom.py` | 635 | **기본.** 안구 구면 기하 + 블렌드셰이프 융합. 가중치 없음 |
| `gazetr.py` / `l2cs.py` | 384/331 | 사전학습 모델 어댑터. torch **지연 임포트** |
| `base.py` | 246 | 인터페이스, 체크포인트 로딩·검증, warmup |
| `registry.py` | 100 | 이름 → 클래스. `BackboneConfig.name`의 순수 함수 |
| **calibration/** | | 사용자별 2-포인트 캘리브레이션 |
| `quality.py` | 310 | doc 5-2 품질 게이트 5종, LOO, 분리도 |
| `classifier.py` | 275 | LR 파이프라인, doc 5-4 UNCERTAIN 규칙, 영속화 |
| `features.py` | 234 | 특징 집합 A/B/C, 중심점 동결(누수 방어) |
| **temporal/** | | |
| `smoother.py` | 405 | EMA + 최신성 가중 투표 + 비대칭 히스테리시스 상태 머신 |
| **runtime/** | | |
| `session.py` | 571 | `VisionSession` — 한 테이크 전체를 묶음 |
| `placement.py` | 434 | 카메라 배치 확인 (캘리브레이션 **전에** 실행) |
| `version.py` | 132 | `AiVersion` 스탬프, config hash, git commit |
| **data/** | | 오프라인 기질 |
| `features_table.py` | 506 | 41열 특징 테이블, parquet/csv.gz |
| `splits.py` | 351 | 참가자 분리 분할, doc 4-3 누수 검사 |
| `labels.py` | 312 | 큐 타임라인 → 세그먼트, 가드밴드 |
| `manifest.py` | 122 | JSONL 매니페스트, `sample_id` |

### 그 외

| 경로 | 역할 |
|---|---|
| `ai/tools/` | CLI 5종 — `python -m ai.tools.X` |
| `ai/evaluation/` | 평가 CLI 5종 — **배포되지 않음**, `v1/local/`에서만 접근 가능 |
| `ai/configs/` | 7개 YAML (아래 참조) |
| `ai/models/` | 체크포인트 (gitignore, 출처 `.json` 사이드카만 추적) |
| `ai/datasets/` | 녹화본·라벨·특징 테이블 (gitignore, `.gitkeep`만 추적) |
| `ai/reports/` | 평가·실험 산출물 (gitignore) |
| `tests/` | 1421개 테스트 + 실제 픽스처 2개 |
| `../README.md` | v1 전체 기술 레퍼런스 |

> 모든 `__init__.py`가 **0바이트**입니다. 패키지 레벨 재노출이 없으니 항상 구체 모듈을 임포트하세요:
> `from vision.preprocess.pipeline import PreprocessPipeline`

---

## 설정

| 파일 | → 섹션 | 튜닝 대상 |
|---|---|---|
| `preprocess.yaml` | `preprocess` | 분석 fps, 크롭 크기, 유효성 임계값 |
| `gaze_backbone.yaml` | **`backbone`** ⚠️ | 백본 선택, 체크포인트, `params` |
| `placement.yaml` | `placement` | 배치 확인 |
| `calibration.yaml` | `calibration` | 특징 집합, LR, doc 5-4 임계값 |
| `temporal.yaml` | `temporal` | EMA, 투표, dwell, 하트비트 |
| `release_gate.yaml` | `release_gate` | doc 7 통과선 6개 |
| `collection.yaml` | `collection` | 녹화 프로토콜 블록 |

```python
from vision.config import load_config
cfg = load_config()                                   # ai/configs 전체
cfg = load_config(overrides={"backbone": {"name": "l2cs"}})   # 섹션 키로!
cfg.hash()          # '3cc1193e3238' — 병합된 전체 설정의 12자리 해시
```

**함정 3가지**

1. `gaze_backbone.yaml` → 섹션 이름은 **`backbone`**. 유일하게 파일명과 다릅니다.
2. 알 수 없는 키는 **조용히 버려집니다.** 오타는 에러가 아니라 무효과입니다.
   단 `backbone.params` 안의 오타는 **하드 에러**입니다 (비대칭은 의도적).
3. 없는 파일은 `{}`로 읽힙니다 → 잘못된 `--config-dir`는 "전부 기본값 + 멀쩡해 보이는 해시"를 만듭니다.

---

## 개발 워크플로

### 테스트

```bash
$PY -m pytest -q                      # 1421 passed, ~43초
$PY -m pytest tests/test_backbones.py -q -k iris
```

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
| 확률 열 순서 | sklearn이 클래스를 정렬해 `classes_ == ['BOTTOM','CAMERA']` — **BOTTOM이 열 0** |
| `LOW_MARGIN` | 기본 임계값에서 도달 불가 (2클래스에선 `margin == 2·p_max−1`) |
| `INVERTED_PITCH` | `quality.reason`에 **절대** 들어가지 않음. `hint`의 `INVERTED_PITCH_HINT_PREFIX`로 감지 |
| 이벤트의 `face_valid` | 판정의 `face_valid`와 다름 — `decision.face_valid and state != UNCERTAIN` |
| `update()` vs `should_emit()` | `update()`는 매 프레임 반환. 와이어에 올릴 건 `should_emit()`이 True인 것뿐 |
| `should_emit()` | **상태를 바꿉니다.** 발행하지 않을 이벤트에 투기적으로 호출 금지 |
| 미러링 | 분석은 항상 **원본** 프레임. 배치 확인의 yaw 부호가 여기 의존. 미리보기만 뒤집힘 |
| 캘리브레이션 앵커 | 추론 경로에서 `CalibrationFeatureExtractor.fit()` 호출 **금지** — 결정 경계가 다시 써짐 |
| `save_table()` | **반환 경로를 쓰세요.** 엔진 없으면 `.parquet` 요청이 조용히 `.csv.gz`가 됩니다 |
| 전부 UNCERTAIN | `session.last_backbone_error` 확인 — 죽은 백본은 자리 비운 사용자와 구분 불가 |

---

## 문제 해결

셋업 단계에서 자주 걸리는 것만 담았습니다. 전체 목록은 [../README.md](../README.md) §15에 있습니다.

| 증상 | 해결 |
|---|---|
| `ImportError: DLL load failed ... _cext` | kiwisolver 1.5.1 문제. `pip install "kiwisolver>=1.4,<1.5"` |
| `ModuleNotFoundError: No module named 'ai'` | CWD가 `v1/local/`이 아님 |
| `python -m evaluation.X` 실패 | 평가 모듈은 스크립트 경로로: `python ai/evaluation/X.py` |
| 데모에서 SPACE가 안 먹음 | OpenCV 창에 포커스가 없음 — **창을 먼저 클릭** |
| 온스크린 텍스트가 영어 | CJK 폰트 없음. 시작 로그의 `korean_text=yes/no` 확인 |
| `--backbone gazetr` 실패 | 가중치 없음. `download_checkpoints gazetr --from-file <path>` |
| `gaze_eval`이 백본 혼재로 거부 | 의도된 동작. `--backbone <name>` 지정 또는 `exp1_backbone.py` 사용 |
| 설정 변경이 반영 안 됨 | 키 철자와 **섹션 이름** 확인 (`gaze_backbone.yaml` → `backbone`) |

---

## 현재 상태

| | |
|---|---|
| ✅ | 온라인 경로 동작 · 1421개 테스트 통과 · 10개 진입점 전부 정상 |
| ⚠️ | **녹화 데이터셋 없음** — `ai/datasets/*`가 비어 있습니다 |
| ⚠️ | **doc 7 릴리스 게이트가 실제 데이터로 평가된 적 없음** — 6개 임계값은 설계 목표입니다 |
| ⚠️ | CI 없음 · 커밋 전 `pytest` 직접 실행 필요 |
| ⚠️ | `gazetr` 가중치 없음 (수동 설치만 가능) |

가장 큰 공백은 **doc 4-1 프로토콜에 따른 실제 녹화**입니다. 그전까지 모든 정확도 수치는 미측정입니다.

자세한 기술 문서 · 알고리즘 유도 · 전체 CLI 레퍼런스 · doc N 대응표 → **[../README.md](../README.md)**
