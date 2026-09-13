# v1 — CAMERA / BOTTOM 2-클래스 시선 판정

발표자가 **카메라를 보는가**, **화면 아래 대본을 보는가**를 판정하는 첫 번째 모델입니다.
확신이 없으면 **기권(UNCERTAIN)** 합니다.

```
BGR 프레임 ──▶ preprocess ──▶ backbone ──▶ calibration ──▶ temporal ──▶ GAZE_STATE
   + t_ms      MediaPipe      시선 각도    사용자별 LR      평활화       (7키 JSON)
               478 랜드마크    (라디안)     2-포인트         히스테리시스
```

이 문서는 **v1 모델의 전체 기술 레퍼런스**입니다 — 알고리즘 유도, 설계 근거,
전체 CLI 레퍼런스, doc N 대응표. `local`과 `deploy`가 공유합니다.

> **코드를 돌려보려면 [local/README.md](local/README.md)** 로 가세요.
> 설치 절차, 자주 쓰는 명령, 모듈 지도 같은 작업용 내용은 거기에 있습니다.

---

## 목차

1. [v1의 범위](#1-v1의-범위)
2. [출력 계약](#2-출력-계약)
3. [환경과 현재 상태](#3-환경과-현재-상태)
4. [저장소 구조](#4-저장소-구조)
5. [아키텍처](#5-아키텍처)
6. [좌표계와 부호 규약](#6-좌표계와-부호-규약)
7. [파이프라인 상세](#7-파이프라인-상세)
8. [설정 (ai/configs)](#8-설정-aiconfigs)
9. [CLI 레퍼런스](#9-cli-레퍼런스)
10. [데이터셋 파이프라인](#10-데이터셋-파이프라인)
11. [평가와 릴리스 게이트](#11-평가와-릴리스-게이트)
12. [실험 (doc 23)](#12-실험-doc-23)
13. [테스트](#13-테스트)
14. [알려진 한계](#14-알려진-한계)
15. [트러블슈팅](#15-트러블슈팅)
16. [설계 문서(doc N) 대응표](#16-설계-문서doc-n-대응표)

---

## 1. v1의 범위

**하는 것**

- CPU 단독 · 분석 8 FPS · 프레임당 p95 지연 125 ms 예산
- **사용자별 캘리브레이션**: 카메라 2초 + 대본 2초로 그 사람 전용 분류기를 만들고 세션과 함께 버립니다
- **카메라 배치 확인**: 웹캠이 화면 위에 있다는 전제가 깨지면 모든 라벨이 뒤집히므로, 캘리브레이션 **전에** 감지합니다
- **교체 가능한 백본**: 기본은 학습 가중치가 없는 기하 기반(`mediapipe_geom`), 사전학습 모델(`l2cs`, `gazetr`)도 지원

**하지 않는 것**

- 좌/우나 청중 응시 같은 다른 방향 — 클래스는 CAMERA/BOTTOM 둘뿐입니다
- 다중 얼굴 — 항상 한 명만 봅니다
- 시선 좌표 추정 — "어디를 정확히 보는가"가 아니라 "둘 중 어느 쪽인가"입니다
- 전송 계층 — 이벤트를 밖으로 내보내는 서버 코드가 없습니다. 소비자가 `VisionSession`을 인프로세스로 임베드합니다

---

## 2. 출력 계약

`GazeStateEvent.to_dict()`가 내보내는 **정확히 7개 키**가 다운스트림과의 접점입니다.

```json
{
  "type": "GAZE_STATE",
  "model_version": "gaze_v1.0.0",
  "t_ms": 12480,
  "label": "BOTTOM",
  "confidence": 0.8134,
  "continuous_duration_ms": 1750,
  "face_valid": true
}
```

이 모양이 바뀌면 **v2**입니다.

---

## 3. 환경과 현재 상태

### 환경

| | 상태 | 설명 |
|---|---|---|
| [`local/`](local/) | ✅ 동작 | 웹캠을 붙여 실험·평가하는 리그. 지금 코드는 전부 여기 |
| `deploy/` | ⬜ 없음 | 서비스 투입용 패키징. 아직 만들지 않았습니다 |

두 환경은 같은 모델이므로 이 문서를 공유합니다.

---

이 저장소는 **PoC(Stage A) 단계의 로컬 테스트 리그**입니다. 아래는 실제로 실행해 확인한 현재 상태입니다.

| 항목 | 상태 |
|---|---|
| 온라인 경로 | ✅ **동작함** — 데모가 420프레임 완주. 전처리 29.5 ms + 백본 0.7 ms/frame (125 ms 예산 내) |
| 테스트 | ✅ `python -m pytest -q` → **1421 passed (~43 s)**, 10개 서브시스템 전체 커버 ([§13](#13-테스트)) |
| Git | ⚠️ 저장소는 초기화되어 있으나 **커밋이 아직 없습니다.** HEAD가 없어 `AiVersion.code_commit`은 계속 `"unknown"`입니다 — 첫 커밋 이후 실제 SHA가 들어갑니다 |
| 녹화 데이터셋 | ⚠️ `ai/datasets/{raw,features,labels,manifests}` 전부 **비어 있음** — 실제 참가자 녹화본이 아직 없습니다 |
| 릴리스 게이트(doc 7) | ⚠️ **실제 데이터로 평가된 적 없음.** 게이트 *메커니즘*은 검증됐지만(통과/실패 양쪽), 6개 임계값은 여전히 *설계 목표*이지 관측 결과가 아님 |
| 체크포인트 | `face_landmarker.task` ✅ (3,758,596 B) / `L2CSNet_gaze360.pkl` ✅ (95,849,977 B, sha256 검증됨) / `GazeTR-H-ETH.pt` ❌ 없음 |
| 실행 가능한 백본 | `mediapipe_geom`(기본, 체크포인트 불필요), `l2cs` — `gazetr`는 가중치 없어 생성 시 실패 |
| CI | 없음 |
| 설계 문서 | 이 저장소에 없음. 코드 전반의 `doc N` 참조는 **외부 설계 문서**의 섹션 번호 ([§16](#16-설계-문서doc-n-대응표)) |

> ### 검증된 것과 검증되지 않은 것을 구분하세요
>
> **코드 계약은 검증됐습니다.** pytest 스위트가 부호 규약, 좌표 변환, 유효성 게이트 순서,
> 캘리브레이션 품질 게이트, 평활화 상태 머신, 지표 정의, 릴리스 게이트 판정 규칙을 고정합니다
> ([§13](#13-테스트)). 픽셀이 필요한 곳은 실제 픽스처(`face.jpg`, `static_face_30fps.mp4`)를 씁니다.
>
> **모델 정확도는 검증되지 않았습니다.** 이 저장소에는 녹화 데이터셋이 없고,
> 정확도는 doc 4-1 프로토콜로 사람을 녹화해야만 알 수 있습니다.
> doc 7 게이트의 6개 임계값은 여전히 *설계 목표*이지 관측 결과가 아닙니다.
>
> 문서화된 정량 수치(각도, 지연 시간)는 `tests/fixtures/face.jpg` 단일 프레임 측정입니다.

### 알려진 함정: kiwisolver 1.5.1은 이 리그를 통째로 마비시킵니다

`requirements.txt`가 `kiwisolver>=1.4,<1.5`로 고정하는 이유입니다. 상한을 풀면 다음이 재현됩니다:

```
mediapipe 1.0.1
  └─ mediapipe/tasks/python/vision/drawing_utils.py
       └─ matplotlib.pyplot
            └─ kiwisolver 1.5.1  (cp311-win_amd64)
                 └─ ImportError: DLL load failed while importing _cext
                    (응용 프로그램 구성이 올바르지 않습니다 / side-by-side configuration error)
```

MSVC 2015–2022 x64 재배포 패키지가 최신으로 설치되어 있어도 발생하며(즉 vcredist 문제가 아닙니다),
`--force-reinstall`로도 낫지 않습니다. **1.4.9는 정상 로드됩니다.**

파급 범위가 큰 이유는 **`matplotlib`이 "선택적 플로팅 도구"가 아니라 mediapipe import 체인의 필수 요소**이기 때문입니다.
이것이 깨지면 `PreprocessPipeline`을 만드는 모든 경로가 즉시 죽어
(`landmarker.py`의 lazy import 지점) `pipeline_demo` · `collect` · `extract_features`가 `--help` 외에는 동작하지 않습니다.
평가 CLI(`gaze_eval`, `threshold_sweep`, `exp1`, `exp2`, `experiment_log`)만 영향을 받지 않습니다.

---

### v1을 "완료"라고 부르려면

1. `local/`의 `collect` 도구로 doc 4-1 프로토콜에 따라 참가자를 녹화
2. `label_review`로 라벨 검수 → `extract_features`로 특징 테이블 생성
3. `gaze_eval`로 릴리스 게이트 평가 — 여기서 처음으로 실제 정확도가 나옵니다
4. 필요하면 `threshold_sweep`으로 임계값 조정, `exp1`/`exp2`로 백본·특징 집합 결정

절차와 명령은 [local/README.md](local/README.md)에 있습니다.

---

## 4. 저장소 구조

```
gaze-tracking/
├── README.md                        # 프로젝트 개요
├── .gitattributes                   # LF 정규화 (JSONL이 LF를 요구)
└── v1/
    ├── README.md                    # 이 문서 — v1 전체 기술 레퍼런스 (local/deploy 공유)
    ├── deploy/                      # 아직 없음
    └── local/                       ← 아래가 그 내용, 모든 명령의 실행 위치
```

```
v1/local/
├── README.md                        # 작업용 개발 가이드 (먼저 읽는 문서)
├── pyproject.toml                   # setuptools, src-layout, pytest 설정
├── requirements.txt                 # 기본 의존성 (torch 없음, floors only)
├── requirements-torch.txt           # 선택: l2cs / gazetr 용 (CPU 인덱스에서)
├── run_demo.bat                     # Windows 더블클릭 런처
├── .gitignore                       # 체크포인트/원본 영상 배제 정책
│
├── ai/
│   ├── configs/                     # 7개 YAML 튜닝 파일 (§8)
│   ├── models/
│   │   ├── face_landmarker.task            # MediaPipe 번들 (필수, gitignored)
│   │   └── gaze/backbone/                  # L2CSNet_gaze360.pkl, GazeTR-H-ETH.pt (+ .json 출처 사이드카)
│   ├── datasets/                    # raw / labels / features / manifests (현재 전부 빔)
│   ├── reports/                     # 평가·실험 산출물 (현재 빔)
│   ├── model_cards/                 # 플레이스홀더 (비어 있음)
│   │
│   ├── src/vision/                  # ★ 배포되는 유일한 패키지
│   │   ├── schemas.py               # 모든 크로스-모듈 데이터 계약 + 부호 규약 (단일 진실 원천)
│   │   ├── config.py                # 타입드 설정 데이터클래스 + YAML 로딩 + config_hash
│   │   ├── preprocess/              # pipeline, landmarker, headpose, crops, sampler
│   │   ├── backbones/               # base, registry, mediapipe_geom, l2cs, gazetr
│   │   ├── calibration/             # features, classifier, quality
│   │   ├── temporal/                # smoother
│   │   ├── runtime/                 # session, placement, version
│   │   └── data/                    # manifest, labels, splits, features_table
│   │
│   ├── tools/                       # CLI 5종 (§9) — `python -m ai.tools.X`
│   └── evaluation/                  # 평가 CLI 5종 (§9) — `python ai/evaluation/X.py`
│
└── tests/                           # 1421개 테스트 (§13)
    ├── conftest.py                  # 공유 픽스처 + sys.path 부트스트랩
    └── fixtures/                    # face.jpg, static_face_30fps.mp4
```

### 패키징 메커니즘

- 배포 이름 `gaze-tracking` 1.0.0, **최상위 패키지는 `vision` 하나뿐**입니다
  (`[tool.setuptools.packages.find] where = ["ai/src"]`).
- editable 설치는 단순 경로 주입 방식입니다:
  `.venv/Lib/site-packages/__editable__.gaze_tracking-1.0.0.pth` 안에
  `…\gaze-tracking\v1\local\ai\src` 한 줄.
  → `import vision`은 라이브 소스로 바로 해석되며 리빌드가 필요 없습니다.
- **`ai.tools`와 `ai.evaluation`은 의도적으로 배포되지 않습니다.** `v1/local/`에서만 접근 가능하며,
  이것이 거기서 실행해야 하는 요구사항과 각 모듈의 `_bootstrap_import_path()`가 존재하는 구조적 이유입니다.
- 트리의 모든 `__init__.py`는 **0바이트**입니다. 패키지 레벨 재노출이 없으므로 항상 구체 모듈을 import하세요
  (`from vision.preprocess.pipeline import PreprocessPipeline`).

> 코드가 `REPO_ROOT`라고 부르는 것은 **`v1/local/`** 입니다
> (`config.py`의 `parents[3]`, `conftest.py`의 `parents[1]`). 모노레포 루트가 아닙니다.

### 평가 CLI를 `-m`으로 부를 때 ⚠️

| 위치 | 동작하는 호출 |
|---|---|
| `ai/tools/*` | `python -m ai.tools.pipeline_demo` |
| `ai/evaluation/*`, `ai/evaluation/experiments/*` | `python ai/evaluation/gaze_eval.py` **또는** `python -m ai.evaluation.gaze_eval` |

**`ai.` 접두사를 빼면 실패합니다** — `python -m evaluation.gaze_eval`은
`ModuleNotFoundError: No module named 'evaluation'`을 냅니다.
`ai/`를 `sys.path`에 넣는 것은 각 모듈의 `_bootstrap_import_path()`인데,
그 시점은 `-m`이 이미 이름을 해석해야 하는 시점보다 뒤이기 때문입니다.
`ai.evaluation...`으로 부르면 `ai/`가 네임스페이스 패키지로 잡혀 정상 해석됩니다.

---

## 5. 아키텍처

### 데이터 흐름 (타입 계약)

```
BGR 프레임 (np.ndarray uint8 HxWx3) + t_ms
        │
        │  FrameSampler.should_process(t_ms)      ← 타임스탬프 기반 데시메이션 (8 FPS)
        ▼
   PreprocessPipeline.process_bgr()               [doc 3-1]
        │   MediaPipe FaceLandmarker → 478 랜드마크 + 52 블렌드셰이프 + 4x4 변환행렬
        │   → 헤드포즈(행렬 우선, PnP 폴백) / roll 정렬 crop / 품질 신호 / 유효성 게이트
        ▼
   FrameObservation  ─ HeadPose, FrameQuality, face_crop(224²), eye_crops(60×36),
        │              landmarks(478,3), blendshapes, face_bbox, invalid_reason
        ▼
   GazeBackbone.predict_observation()             [doc 3-2]
        ▼
   GazeVector(gaze_yaw, gaze_pitch, confidence, backbone, inference_ms)   ← 라디안
        │
        ├──(캘리브레이션 중)→ CalibrationSample ──→ PerUserGazeClassifier.fit()  [doc 5]
        │                                              └→ CalibrationQuality
        ▼
   PerUserGazeClassifier.decide()                 [doc 5-4]
        ▼
   GazeDecision(label, p_camera, p_bottom, face_valid, uncertain_reason, latency_ms)
        ▼
   TemporalSmoother.update()                      [doc 6]
        ▼
   GazeStateEvent  ──.to_dict()──▶  GAZE_STATE JSON  (다운스트림 계약)
```

전 과정을 하나로 묶는 것이 **`VisionSession`** (`vision/runtime/session.py`)입니다.
프레임 ID 공간, 캘리브레이션 버퍼, 배치 확인 버퍼, 지연 시간 링버퍼, 이벤트 목록을 소유합니다.

### `GAZE_STATE` 이벤트 계약

`GazeStateEvent.to_dict()`가 내보내는 **정확히 7개 키**가 다운스트림과의 통합 계약입니다:

```json
{
  "type": "GAZE_STATE",
  "model_version": "gaze_v1.0.0",
  "t_ms": 12480,
  "label": "BOTTOM",
  "confidence": 0.8134,
  "continuous_duration_ms": 1750,
  "face_valid": true
}
```

`is_transition`, `smoothed_p_camera`, `smoothed_p_bottom`은 **계약에 포함되지 않습니다.**
`to_debug_dict()` / `dump_events(debug=True)`로만 노출되며, 실패 케이스 첨부용이지 소비자용이 아닙니다.

> ⚠️ **이 저장소에는 전송 계층이 없습니다.** websocket / fastapi / flask / grpc / zmq 등 어떤 서버 코드도 없습니다.
> `GAZE_STATE`가 프로세스 밖으로 나가는 유일한 경로는 `VisionSession.dump_events()`의 JSONL 파일뿐이며,
> 유일한 호출자는 데모의 `S` 키(`ai/reports/demo_events.jsonl`)입니다.
> 소비자는 `VisionSession`을 인프로세스로 임베드해야 합니다.

### 이벤트 방출 vs 갱신

- `smoother.update(decision)`은 **모든 프레임마다** 이벤트를 반환합니다 (UI가 매 프레임 렌더 가능).
- 실제로 와이어에 올릴 것은 `should_emit(event)`가 `True`인 것뿐입니다 (`VisionSession.events`).
- `should_emit`은 **상태를 변경**합니다(하트비트 시계). 발행하지 않을 이벤트에 투기적으로 호출하지 마세요.

---

## 6. 좌표계와 부호 규약

`vision/schemas.py`의 모듈 독스트링이 **단일 진실 원천**입니다. 필드명이 `_deg`로 끝나지 않으면 전부 **라디안**입니다.

카메라 프레임은 OpenCV 규약: `+x` 이미지 오른쪽, `+y` 이미지 **아래**, `+z` 카메라에서 장면 안쪽.
시선 단위벡터 `g`를 두 각도로 사영합니다:

```
gaze_pitch = -asin(g_y)        # > 0 이면 위를 봄,  < 0 이면 아래를 봄
gaze_yaw   =  atan2(g_x, g_z)  # > 0 이면 이미지 오른쪽을 봄
```

**모든 백본 어댑터가 지켜야 할 귀결**:
> **BOTTOM(대본을 봄)은 CAMERA보다 `gaze_pitch`가 더 음수여야 합니다.**

헤드포즈도 같은 규약입니다: `head_pitch > 0` 턱 올림, `head_yaw > 0` 얼굴이 이미지 오른쪽, `head_roll > 0` 시계방향 기울임.

### 이 규약은 "강제"되지 않습니다 — 세 곳에서 *경고*만 합니다

| 위치 | 동작 |
|---|---|
| `ai/evaluation/sanity.py` `pitch_ordering_report()` | (participant, backbone)별 `OK` / `WEAK` / `INVERTED` / `INSUFFICIENT` 판정. **자문(advisory)** — 릴리스 게이트 6행에 포함되지 않음 |
| `calibration/quality.py` (`check_pitch_ordering: true`) | `quality.hint`에 `"INVERTED_PITCH: …"` 문자열을 앞에 붙일 뿐, `status`/`reason`은 건드리지 않음 |
| `exp1_backbone --pitch-veto` | 이 플래그를 줄 때만 구속력 있는 veto가 됨 |

> ⚠️ **`CalibrationFailReason.INVERTED_PITCH`는 `quality.reason`에 절대 대입되지 않습니다.**
> `quality.reason`이 가질 수 있는 값은 `_fail()`이 내보내는 나머지 다섯 개뿐입니다.
> 역전 pitch의 유일한 런타임 신호는 `CalibrationQuality.hint`에 붙는 접두사이며,
> 이를 위해 `schemas.INVERTED_PITCH_HINT_PREFIX` 상수가 있습니다:
>
> ```python
> from vision.schemas import INVERTED_PITCH_HINT_PREFIX
> inverted = bool(quality.hint) and INVERTED_PITCH_HINT_PREFIX in quality.hint
> ```
>
> 리터럴 `"INVERTED_PITCH"`를 직접 쓰지 말고 이 상수로 매칭하세요 — 생산자(`calibration/quality.py`)와
> 소비자(`ai/tools/pipeline_demo.py`)가 모두 같은 상수를 참조하므로 문자열이 어긋날 수 없습니다.

---

## 7. 파이프라인 상세

### 7-1. 전처리 — `vision/preprocess` [doc 3-1]

**프레임 샘플링** (`sampler.py`): 프레임 카운터가 아니라 **타임스탬프**로 데시메이션합니다
(웹캠은 프레임을 드롭하고 컨테이너는 가변 프레임 길이를 가짐). `interval_ms = 1000/analysis_fps` = 125 ms.
데드라인은 이상적 그리드 위에서 전진(`deadline += interval`)해 반올림 드리프트를 막되,
스톨 시에는 `t + interval`로 재동기화해 캐치업 버스트를 막습니다. 타임스탬프가 역행하면 새 테이크로 보고 리셋합니다.

**MediaPipe** (`landmarker.py`): `mediapipe`는 `FaceLandmarkerWrapper.__init__` 내부에서 **lazy import** 됩니다
(순수 기하 모듈인 `headpose`/`crops`는 mediapipe 없이 import 가능).
검출기 confidence 3종은 0.5로 **하드코딩**되어 있고 config로 노출되지 않습니다.

`face_confidence`는 **MediaPipe 점수가 아닙니다.** `in_bounds_fraction(landmarks)` —
478개 랜드마크 중 `x`, `y`가 모두 `[0,1]`에 있는 비율입니다. IMAGE 모드에서 MediaPipe가 per-face 점수를 노출하지 않기 때문입니다.

**헤드포즈** (`headpose.py`): MediaPipe 4×4 변환행렬 경로를 우선하고, 없으면 9점 SQPNP로 폴백합니다.

- 좌표 변환: MediaPipe는 OpenGL 스타일(+y 위, +z 뷰어 쪽) → `GL_TO_CV = diag(1, -1, -1)`, `R_cv = GL_TO_CV @ R_gl @ GL_TO_CV`
- Euler: `R = Rz(c)·Ry(b)·Rx(a)` 분해 후 `(yaw, pitch, roll) = (-b, -a, +c)`
- PnP 9점: 표정에 안정적인 점만 사용 (`1, 168, 10, 33, 263, 133, 362, 234, 454`).
  말하는 동안 수 cm 움직이는 입·턱 점은 제외 — doc 4-1 프로토콜 대부분이 발화 구간이기 때문입니다.
- 3D 모델 점은 **MediaPipe 자체 기하**를 `tests/fixtures/face.jpg`에서 역투영한 값입니다.
  고전적인 OpenCV 6점 모델은 **시도했다가 기각**되었습니다 — 턱을 코 아래 7.0 cm에 두는데 MediaPipe는 9.1 cm이고,
  결과적으로 상수 −19° pitch 편향과 11 px 재투영 잔차가 발생합니다.
- **카메라 내부 파라미터는 측정하지 않고 가정합니다**: 수직 FOV 63°, 주점은 이미지 중앙, 왜곡 0.
  63°는 픽스처에서 0.3 px 이내로 맞고, 60°는 ~17 px 빗나갑니다.
- `reprojection_error`는 **행렬 경로에서 항상 0.0**입니다(투영을 풀지 않음). 0.0이 "완벽한 피팅"을 뜻하지 않습니다.

**크롭** (`crops.py`): 얼굴 224×224, 눈 60×36. `align_face_roll: true`면 두 눈 코너를 잇는 선이 수평이 되도록 회전합니다.
눈 중심은 **홍채가 아니라 눈꼬리 중점**입니다 — 측정하려는 시선을 크롭이 따라가면 안 되기 때문입니다.
경계는 `BORDER_REPLICATE`로 채웁니다(검은 테두리는 CNN에 가짜 휘도 계단으로 읽힘).

> ⚠️ 랜드마크는 x, y가 **각각** 정규화되어 있어 비정사각 프레임에서 거리·각도가 왜곡됩니다.
> 모든 함수가 `to_pixels()`로 먼저 변환하는 이유이며, 820×1024 픽스처에서 EAR이 20% 달라집니다.

**유효성 게이트** — `_invalid_reason`, 첫 매치가 이깁니다:

| # | 조건 | `InvalidReason` |
|---|---|---|
| 0 | 얼굴 미검출 | `NO_FACE` (유일하게 관측이 비어 있는 분기) |
| 1 | `presence < min_face_confidence` (0.50) | `LOW_FACE_CONFIDENCE` |
| 2 | `face_area_ratio < min_face_area_ratio` (0.010) | `FACE_TOO_SMALL` |
| 3 | `touches_border` | `OUT_OF_FRAME` |
| 4 | `min_eye_openness < min_eye_openness` (0.12) | `EYES_CLOSED` |
| 5 | face_crop 없음 **또는** 양쪽 눈 크롭 모두 없음 | `CROP_FAILED` |

`CROP_FAILED`가 비대칭인 것은 의도입니다 — face-crop 기반 백본(L2CS, GazeTR)은 눈 크롭을 보지 않으므로
한쪽 눈만 실패하는 것은 생존 가능합니다.

**`NO_FACE`를 제외하면 무효 프레임도 완전히 채워집니다.** 랜드마크·포즈·크롭·품질이 모두 남습니다 —
doc 19 실패 버킷과 doc 5-2 캘리브레이션 힌트가 사용자에게 실패 이유를 설명하는 데 필요한 것이 정확히 그것들이기 때문입니다.

**보고만 되고 게이트하지 않는 신호**: `backlight_ratio`(>~1.6이면 강한 역광), `face_brightness`, `face_contrast`, `landmark_visibility`.

### 7-2. Gaze Backbone — `vision/backbones` [doc 3-2]

이름 기반 레지스트리로 `BackboneConfig.name` → 클래스가 **순수 함수**로 결정됩니다
(doc 23 실험 1이 다른 모든 것을 고정한 채 백본만 교체할 수 있도록).

| 백본 | 입력 | 체크포인트 | torch | 픽스처 측정 (정답 ≈ (0°, 0°)) |
|---|---|---|---|---|
| **`mediapipe_geom`** (기본) | 랜드마크 + 블렌드셰이프 + 헤드포즈 | 불필요 | ❌ | yaw +2.74°, pitch −2.77°, conf 0.67, **0.21 ms** |
| `l2cs` | face crop만 | `L2CSNet_gaze360.pkl` (95.8 MB) | ✅ | yaw +2.20°, pitch −0.23°, conf 0.98, **565 ms** |
| `gazetr` | face crop만 | `GazeTR-H-ETH.pt` (**없음**) | ✅ | — |

> 측정된 L2CS 설정 3종(565 / 228 / 195 ms) **모두 doc 7의 125 ms 예산을 초과합니다.**
> `mediapipe_geom`이 폴백이 아니라 *기본값*인 실질적 이유입니다.

**`mediapipe_geom`** — 학습 가중치가 전혀 없는 순수 기하 + 블렌드셰이프 융합:

- **안구 구면 모델**: 눈을 반지름 `R`의 구로 보고 홍채 중심 `I`가 그 위에 있다고 두면
  `d = (I − C)/R`, 약한 원근 하에서 `d_x = offset_x / R_px`, `d_y = offset_y / R_px`, `d_z = √(1 − d_x² − d_y²)`.
- **스케일**: `R_px = (12.0 / 5.85) × r_iris_px`. 성인 안축장 ~24 mm(→ 반지름 12 mm),
  홍채 지름 11.7 ± 0.5 mm(인체에서 가장 일정한 치수 중 하나).
  홍채 링의 **수평** 반축을 씁니다 — 눈꺼풀이 수직축을 가리기 때문입니다.
- **기준점**: 안구 중심의 투영을 **두 눈꼬리의 중점**으로 근사합니다(33/133, 362/263).
  눈꼬리는 뼈에 붙어 있고 깜빡임에 불변인 반면, 눈꺼풀 중심은 눈꺼풀을 따라 내려가
  BOTTOM과 CAMERA를 가르는 하향 편위를 상당 부분 상쇄해 버립니다.
- **블렌드셰이프 분기**: ARKit `eyeLookIn/Out/Up/Down` 점수로 독립적인 두 번째 추정치를 만들고
  `iris_weight 0.60 / blendshape_weight 0.40`으로 융합합니다.
- **confidence**: `openness`가 하드 곱셈자이고, `agreement`(두 소스 불일치) · `pose` · `visibility`가
  `0.40/0.35/0.25` 가중으로 *변조*만 합니다 → `clip(openness × (0.30 + 0.70·modulation), 0, 1)`.

> ⚠️ MediaPipe의 "left iris"(468–472)는 **피험자의 오른쪽 눈**입니다 — MediaPipe 명명은 거울 기준,
> ARKit 블렌드셰이프 접미사는 피험자 기준입니다. 코드는 이름이 아니라 **인덱스**를 씁니다.

**네이티브 규약 → 프로젝트 규약 변환** (각 백본이 스스로 책임집니다. 다운스트림은 절대 부호를 뒤집지 않습니다):

- `l2cs`: GazeHub/MPII 규약 → `gaze_yaw = -yaw_model`, `gaze_pitch = +pitch_model`.
  예측은 소프트맥스 **기댓값**: `angle = Σ p_i · centre_i`, 빈 중심 `4i − 180`도, 90빈.
- `gazetr`: 출력 텐서 순서가 **`(pitch, yaw)`** 이고 라디안 직접 출력 →
  `gaze_yaw = -out[1]`, `gaze_pitch = out[0]`.

**전처리 함정** (둘 다 조용히 몇 도씩 틀어지므로 하드코딩이 아니라 config 파라미터입니다):
`l2cs`는 **RGB + ImageNet 정규화**, `gazetr`는 **BGR + 정규화 없음**(원 저자 리더가 `cv2.imread` 사용).

**체크포인트 검증**: 생성자에서 **파일 존재 확인이 `import torch`보다 먼저** 실행됩니다.
누락 시 `CheckpointMissingError`가 **생성 시점에** 발생합니다 — doc 3-2는 다른 백본으로의 조용한 폴백을 금지합니다
(모델이 몰래 바뀐 실행은 재현 불가능하기 때문). `load_state_dict(strict=False)`를 쓰므로
**`assert_required_keys_loaded()`가 무작위 가중치 추론을 막는 유일한 방벽**입니다.

> ⚠️ `gazetr`의 confidence는 **상수 1.0**입니다(불확실성 헤드 없음).
> 이 백본을 선택하면 `min_sample_confidence`, doc 5-4 규칙 등 모든 confidence 게이트가 사실상 무효화됩니다.

### 7-3. 사용자별 캘리브레이션 — `vision/calibration` [doc 5]

카메라 응시 2초 + 대본 응시 2초, 총 약 4초(≈32행)로 세션과 함께 폐기되는 **일회용 사용자별 분류기**를 만듭니다.

**특징 집합**

| 집합 | 차원 | 구성 |
|---|---|---|
| A | 2 | `gaze_yaw, gaze_pitch` |
| B | 5 | A + `head_yaw, head_pitch, head_roll` |
| **C** (기본) | 9 | B + `gaze_{yaw,pitch}_minus_camera`, `gaze_{yaw,pitch}_minus_bottom` |

C의 델타 특징은 캘리브레이션 중 계산된 클래스별 시선 중심(centroid)과의 차이입니다.
**누수 방어 규칙**: 앵커는 `fit()` 안에서 캘리브레이션 샘플로만 한 번 추정되고 **동결**됩니다.
`StandardScaler`가 각 열의 학습 평균을 빼기 때문에 앵커 *값*은 상쇄되지만, **움직인 앵커**는 결정 경계를 다시 씁니다
(학습된 모델 아래에서 앵커를 재적합하면 300개 프로브 프레임 중 87개의 클래스가 뒤집힘).
따라서 **추론 경로에서는 절대 `fit()`을 호출하면 안 됩니다.**

**분류기**: `Pipeline([StandardScaler(), LogisticRegression(C=1.0, max_iter=1000, class_weight='balanced', random_state=42)])`.
LOO 추정값과 배포 모델이 구조적으로 동일한 추정기가 되도록 `quality.py`에 정의되어 있습니다.

> ⚠️ **확률 열 순서 함정**: sklearn이 클래스 레이블을 정렬하므로 `pipeline.classes_ == ['BOTTOM', 'CAMERA']` 입니다.
> **BOTTOM이 `predict_proba` 열 0**입니다. 코드는 `_class_indices`로 적합된 추정기에서 위치를 읽어오지만,
> `predict_proba()[0]`을 `p_camera`로 인덱싱하는 코드는 뒤집힙니다.
> 참고로 `schemas.DECISION_CLASSES = ("CAMERA","BOTTOM")`는 캘리브레이션 경로에서 **전혀 참조되지 않습니다**
> (`metrics.py`, `buckets.py`의 리포트 순서만 고정).

**doc 5-4 판정 규칙** (`decide()`, 분기 순서 그대로):

1. `face_valid` 아님 / gaze 없음 / 비유한 → `UNCERTAIN`, `p=0.5/0.5`, `uncertain_reason = invalid_reason` (없으면 `BACKBONE_FAILED` 또는 `NO_FACE`)
2. `p_max < 0.70` → `UNCERTAIN`, reason `LOW_CONFIDENCE`
3. `margin < 0.20` → `UNCERTAIN`, reason `LOW_MARGIN`
4. 그 외 → `p_camera >= p_bottom` 이면 `CAMERA`, 아니면 `BOTTOM` (동점은 CAMERA)

> ⚠️ **기본값에서 3번 분기는 도달 불가능합니다.** 2-클래스 모델에서 `margin == 2·p_max − 1`이므로
> `p_max ≥ 0.70`이면 항상 `margin ≥ 0.40`입니다. `margin_threshold`를 0.40 위로 스윕할 때만 발동합니다.
> 두 분기를 모두 유지하는 이유는 스윕이 두 임계값을 독립적으로 움직이기 때문입니다.

> ⚠️ `GazeDecision.face_valid`는 "얼굴이 보였는가"가 아니라 **"이 프레임으로 판정할 수 있었는가"** 입니다.
> 얼굴은 멀쩡한데 백본이 예외를 던진 프레임은 `face_valid=False` + `uncertain_reason=BACKBONE_FAILED`가 됩니다.

**품질 게이트 (doc 5-2)** — 다섯 개, 근본 원인 순서대로 가장 구체적인 사유를 냅니다:

| 순서 | 조건 | `CalibrationFailReason` |
|---|---|---|
| 1 | `min(n_camera, n_bottom) < min_samples_per_class` (10) | `NOT_ENOUGH_SAMPLES` |
| 2 | `gaze_yaw` **와** `gaze_pitch` 둘 다 분산이 죽음 (σ ≤ 1e−8) | `DEGENERATE_FEATURES` |
| 3 | `separability < min_separability` (1.00) | `CLASS_NOT_SEPARABLE` |
| 4 | `centroid_distance < min_centroid_distance` (0.60) | `CENTROIDS_TOO_CLOSE` |
| 5 | `loo_accuracy < min_loo_accuracy` (0.85) | `LOW_LOO_ACCURACY` |

- `separability`(**주 기준**)는 두 중심을 잇는 축 **위에서만** 측정한 Fisher 유사 비율입니다:
  `centroid_distance / pooled_within_class_std_along_axis`.
  9차원 전체에서 산포를 재면 분류기가 무시해도 되는 방향(`head_roll`)의 노이즈가 멀쩡한 캘리브레이션을 거부할 수 있습니다.
- `centroid_distance`는 **표준화된 특징 단위**입니다. 특징 집합 간 비교 불가 —
  동일 합성 캘리브레이션에서 A 1.99 / B 2.79 / C 3.96 (C의 델타 열은 중심화 후 gaze 열과 동일해져 시선 분리를 3번 셈).
- LOO는 폴드마다 특징 추출기까지 새로 적합합니다(C에서는 중심이 모델 파라미터이므로).

> ⚠️ **`fit()`은 `quality.status == RETRY_REQUIRED`여도 모델을 학습합니다.**
> 재시도 결정은 호출자의 몫입니다. `is_calibrated`는 "이 세션이 프레임을 채점할 수 있는가"만 답하며,
> 더 강한 진술이 필요하면 `calibration_quality.ok`를 읽어야 합니다.
> `finish_calibration()`도 품질을 보고할 뿐 강제하지 않습니다.

> ⚠️ `PerUserGazeClassifier.save()` / `.load()`는 스키마 버전(`gaze_calib_v1`)까지 갖춰 구현되어 있지만
> **트리 전체에 호출자가 하나도 없습니다.** 모델을 쓰거나 읽는 도구가 없어 매 세션이 처음부터 재캘리브레이션하고,
> 모델은 프로세스와 함께 사라집니다.

### 7-4. 시간 평활화 — `vision/temporal/smoother.py` [doc 6]

프레임 단위의 흔들리는 판정을 안정적인 상태 스트림으로 바꿉니다. **2단계 + 히스테리시스** 구조입니다.

**1단계 — EMA**: 투표 프레임에서 `ema += 0.35 × (p_bottom − ema)`.
기권 프레임에서는 (`decay_on_invalid: true`) `ema += 0.35 × (0.5 − ema)` — 증거가 얼어붙지 않고 **낡아갑니다**.

**2단계 — 최신성 가중 투표**: 8프레임 deque. 가중치는 **랙(lag) 기준**으로 색인되어
`[2.0, 1.857, 1.714, 1.571, 1.429, 1.286, 1.143, 1.0]` — 최신이 2.0, 최고령이 1.0.
랙 기준이므로 윈도가 채워지는 동안에도 프레임 가중치가 일정하고, 첫 1초가 정상 상태와 동일하게 동작합니다.

**블렌드**: `score_bottom = 0.5 × (ema + vote)` — 캐스케이드가 아니라 **평균**입니다.
독스트링의 측정 결과(임계 0.60, 125 ms 프레임 기준):

| 방식 | →BOTTOM | →CAMERA | 4프레임 스파이크 피크 |
|---|---|---|---|
| 캐스케이드 (vote over EMA) | 1375 ms | 1250 ms | 0.13 / 0.25 / 0.36 / 0.47 |
| **평균 (채택)** | **1000 ms** | **875 ms** | 0.27 / 0.46 / 0.60 / 0.71 |
| 투표 단독 | 1125 ms | 1000 ms | 0.18 / 0.33 / 0.47 / 0.60 |

평균 방식이 양방향 375 ms를 벌어줍니다. 4프레임 스파이크는 전환을 **arm 할 뿐**이고
dwell이 다시 600 ms를 요구하므로 어떤 플립이든 최소 1초 이상의 지속 증거가 필요합니다.

**상태 머신** — `_advance_state(t_ms, abstains)`, 순서 그대로:

1. `abstains` **and** 연속 기권 경과 ≥ `uncertain_dwell_ms`(800) → `UNCERTAIN` 커밋. **이 분기가 점수보다 우선합니다.**
2. `candidate = _candidate()`; `None`이거나 현재 상태와 같으면 `_disarm()` — 점수가 데드밴드로 돌아오면 진행 중인 dwell이 취소됩니다.
3. `candidate != _pending` 이면 arm (경계를 넘은 **첫 프레임**에 arm — 실제로 관측한 시점부터 dwell을 잼)
4. 경과 ≥ dwell (`to_bottom 600 ms` / `to_camera 400 ms`) → 커밋

**비대칭 히스테리시스**가 의도적입니다: BOTTOM 진입이 이탈보다 비쌉니다.
**잘못 보고된 대본 응시가 놓친 것보다 나쁜 피드백**이기 때문입니다.
같은 이유로 `_candidate()`는 BOTTOM을 먼저 테스트합니다 (doc 7이 BOTTOM recall을 게이트).

**기권은 arm 하지도, 이미 arm 된 전환을 리셋하지도 않습니다.** 프레임 드롭은 정상이고 dwell을 재시작시켜선 안 되기 때문입니다.
따라서 **드롭아웃 직전에 arm된 전환이 기권 프레임에서 커밋될 수 있고, 그 이벤트는 `face_valid=False`를 실어 나릅니다.**

**하트비트**: `heartbeat_ms`(1000) 마다, 그리고 모든 전환에서 방출. 전환도 하트비트 시계를 재시작시킵니다.

> ⚠️ **이벤트의 `face_valid`는 판정의 `face_valid`가 아닙니다**: `decision.face_valid and state != UNCERTAIN`.
> 모든 `UNCERTAIN` 이벤트는 프레임에 멀쩡한 얼굴이 있어도(예: `LOW_MARGIN` 기권) `face_valid=False`로 보고됩니다.

> ⚠️ `_candidate()`는 절대 `UNCERTAIN`을 반환하지 않습니다. `UNCERTAIN`에 도달하는 경로는
> 연속 기권 dwell 분기(또는 초기 리셋 상태)뿐이며, 임계값 기반 경로는 없습니다.

### 7-5. 카메라 배치 확인 — `vision/runtime/placement.py`

전체 CAMERA/BOTTOM 정식화는 **노트북 웹캠이 화면 위에 있고 대본이 화면 아래**라는 기하를 전제합니다.
카메라가 아래나 옆에 있으면 **시끄럽게 실패하지 않습니다** — 캘리브레이션은 멀쩡히 경계를 학습하고,
단지 이후의 모든 CAMERA/BOTTOM 라벨이 **뒤집힐 뿐**이며 어떤 다운스트림 지표도 이를 감지할 수 없습니다.
doc 19는 이를 사후에 `webcam이 화면 아래/옆에 위치` 버킷으로만 잡습니다. 이 모듈은 **캘리브레이션 전에** 잡습니다.

**측정 원리**: 화면 상대 기하를 아는 두 큐 — (1) 렌즈를 보세요, (2) 화면 중앙을 보세요.
카메라가 각도의 원점이므로 큐 1이 영점을 고정하고 큐 2가 화면의 상대 위치를 말해줍니다.
`pitch(screen) < pitch(camera)` → 화면이 렌즈 아래 → **카메라가 화면 위**(지원되는 기하).

**learned 모드**(기본): doc 5-3과 **같은 LR 계열**을 원시 `(yaw, pitch)`에 적합하고 결정 경계에서 배치를 읽습니다.
경계 법선 `w`가 **표준화 공간**에 있으므로 `|w_yaw|` vs `|w_pitch|` 비교가 원시 각도가 아니라
**신호 대 잡음비**로 축을 순위 매깁니다. 원시 각도 변위로 축을 고르면 **더 많이 흔들리는 노이즈 축이 이겨버립니다** —
이것이 임계값 방식이 아닌 learned 방식을 쓰는 이유입니다. 방향 자체는 항상 중앙값(centroid)에서 옵니다.

**거부 순서**: `NOT_ENOUGH_SAMPLES`(8/큐) → `TARGETS_NOT_SEPARATED`(LOO < 0.85) →
`AMBIGUOUS_AXIS`(dominance < 1.30) → `DISPLACEMENT_TOO_SMALL`(|Δ| < 4.0°).

`geometric` 모드는 sklearn 없이 동작하는 폴백입니다(고정 각도 임계값).

> ⚠️ 판정은 **자문(advisory)** 이며 캘리브레이션을 차단하지 않습니다.
> `supported=True`가 되는 것은 `TOP` 하나뿐이고 `PlacementCheckResult.ok`는 `supported`의 별칭입니다.
> yaw 부호는 **미러링되지 않은 원본 프레임**을 전제합니다 — 셀피 뷰를 먹이면 `SIDE_LEFT`/`SIDE_RIGHT`가 조용히 뒤바뀝니다.

### 7-6. 버전 스탬핑 — `vision/runtime/version.py` [doc 15 / 18]

| 상수 | 값 | 의미 |
|---|---|---|
| `MODEL_VERSION` | `gaze_v1.0.0` | `GazeStateEvent.model_version`의 와이어 값. **vision 슬라이스 전체**를 지칭 |
| `CLASSIFIER_VERSION` | `per_user_lr_v1` | 추정기 계열 버전 (파일 포맷 버전 `SCHEMA_VERSION`과 **별개**) |
| `TEMPORAL_RULE_VERSION` | `gaze_temporal_v1.0` | 평활화 규칙 버전 |

`config_hash` = `sha256(json.dumps(config.to_dict(), sort_keys=True))[:12]`.
읽는 섹션만이 아니라 **병합된 전체 설정**을 커버하므로, `calibration.yaml`만 건드린 스윕도 해시를 움직입니다 —
doc 18의 "같은 해시 = 같은 숫자" 주장이 성립하는 근거입니다.

`code_commit` 해석 순서: `GAZE_TRACKING_GIT_COMMIT` 환경변수 → 캐시 → `git rev-parse --short=12 HEAD`
(+ `git status --porcelain --untracked-files=no`가 비어 있지 않으면 `-dirty` 접미사) → `unknown`.

> ⚠️ **아직 커밋이 없어 `git rev-parse HEAD`가 실패하므로 `code_commit`이 항상 `"unknown"`** 입니다.
> 모든 doc 18 실험 기록과 `AiVersion`이 `unknown`을 실어 나르며, 이는 재현성 주장을 실질적으로 약화시킵니다.
> 실용적 해결책은 `GAZE_TRACKING_GIT_COMMIT`를 빌드에서 설정하는 것입니다(수작업으로 설정하면 스탬프가 거짓말을 시작합니다).

---

## 8. 설정 (ai/configs)

`vision.config.load_config(config_dir=None, overrides=None)` → `VisionConfig`.
7개 YAML이 7개 데이터클래스 섹션으로 로드됩니다.

| 파일 | 섹션 키 | 데이터클래스 |
|---|---|---|
| `preprocess.yaml` | `preprocess` | `PreprocessConfig` |
| `gaze_backbone.yaml` | **`backbone`** ⚠️ | `BackboneConfig` |
| `placement.yaml` | `placement` | `PlacementConfig` |
| `calibration.yaml` | `calibration` | `CalibrationConfig` |
| `temporal.yaml` | `temporal` | `TemporalConfig` |
| `release_gate.yaml` | `release_gate` | `ReleaseGateConfig` |
| `collection.yaml` | `collection` | `CollectionConfig` (→ `List[ProtocolCondition]`) |

> ⚠️ **파일명과 섹션 키가 다른 유일한 경우**: `gaze_backbone.yaml` → 섹션 `backbone`.
> `overrides` 딕셔너리는 반드시 `{"backbone": {...}}`를 써야 합니다.

> ⚠️ `_read_yaml`은 없는 파일에 대해 `{}`를 반환하고 `_build`는 알 수 없는 키를 **조용히 버립니다.**
> 잘못된 `--config-dir`나 오타난 키는 에러 없이 **전부 기본값인 설정 + 멀쩡해 보이는 해시**를 만들어냅니다.

현재 7개 YAML은 모두 데이터클래스 기본값을 그대로 재기술하고 있어, `load_config()`와 `VisionConfig()`가
딱 한 곳에서만 다릅니다 — `collection.conditions` (YAML 7블록 vs 기본 빈 리스트).
검증: `load_config().hash() == '3cc1193e3238'` / `VisionConfig().hash() == '2d345c8771c6'`.

### 주요 키

<details>
<summary><b>preprocess.yaml</b> — 입력 표준화 [doc 3-1]</summary>

| 키 | 기본값 | 설명 |
|---|---|---|
| `analysis_fps` | `8.0` | 실제 분석 프레임률. `frame_interval_ms = 1000/fps = 125 ms`. ≤0이면 데시메이션 비활성 |
| `face_crop_size` | `224` | 정사각 얼굴 크롭 한 변 (px) |
| `eye_crop_size` | `[60, 36]` | 눈 크롭 [폭, 높이] (px) |
| `face_crop_margin` | `0.25` | 크롭 창에만 적용되는 여백. `face_bbox` / `face_area_ratio`에는 **미적용** |
| `eye_crop_scale` | `2.2` | 눈 크롭 폭 = 눈꼬리 간 거리 × 이 값 |
| `min_face_confidence` | `0.50` | 게이트 1. **MediaPipe에 전달되지 않고** 자체 presence 프록시만 게이트 |
| `min_face_area_ratio` | `0.010` | 게이트 2 |
| `min_eye_openness` | `0.12` | 게이트 4. 뜬 눈 ≈ 0.3, 깜빡임 < 0.1 |
| `border_tolerance_px` | `2` | 게이트 3. **음수면 경계 테스트와 `OUT_OF_FRAME` 규칙이 비활성화됩니다** |
| `align_face_roll` | `true` | 눈 선이 수평이 되도록 크롭 회전 |
| `num_faces` | `1` | 어차피 인덱스 0만 소비됨 |
| `landmarker_model_path` | `ai/models/face_landmarker.task` | `resolve_path()`로 repo root 기준 해석 |
| `running_mode` | `IMAGE` | `IMAGE`(결정적 오프라인) / `VIDEO`(t_ms 필수). `LIVE_STREAM`은 `NotImplementedError` |

</details>

<details>
<summary><b>gaze_backbone.yaml</b> — 백본 선택 [doc 3-2]</summary>

| 키 | 기본값 | 설명 |
|---|---|---|
| `name` | `mediapipe_geom` | `mediapipe_geom` \| `l2cs` \| `gazetr` |
| `checkpoint` | `null` | `null`이면 백본의 `DEFAULT_CHECKPOINT` 사용 |
| `device` | `cpu` | `torch.device()`에 전달 |
| `num_threads` | `0` | `>0`일 때만 `torch.set_num_threads()`. CPU 지연의 최대 레버 |
| `batch_size` | `1` | torch 백본 `predict_batch` 청크 크기 |
| `params` | `{}` | 백본별 추가 파라미터. **알 수 없는 키는 `ValueError`로 하드 실패** |

> 비대칭 주의: 최상위 키(`name`, `device`) 오타는 조용히 무시되지만, `params:` 안의 오타는 하드 에러입니다. 의도된 설계입니다.

주요 `params`: `l2cs` — `num_bins 90`, `bin_width_deg 4.0`, `bin_offset_deg -180.0`, `input_size 448`,
`center_crop_ratio 1.0`, `normalize imagenet`, `confidence_window_bins 3`.
`gazetr` — `maps 32`, `nhead 8`, `num_layers 6`, `dim_feedforward 512`, `dropout 0.1`, `input_size 224`(체크포인트에 고정),
`channel_order bgr`, `normalize none`, `fixed_confidence 1.0`.
`mediapipe_geom` — `eyeball_radius_mm 12.0`, `iris_radius_mm 5.85`, `iris_temporal_bias_r 0.30`,
`iris_superior_bias_r 0.30`, `blendshape_yaw_deg 30.0`, `blendshape_pitch_deg 25.0`, `iris_weight 0.60`,
`blendshape_weight 0.40`, `max_eye_angle_deg 45.0`, `ear_closed 0.12`, `ear_open 0.25`, `occlusion_yaw_deg 70.0`,
`agreement_tolerance_deg 25.0`, `single_source_confidence 0.70`.

</details>

<details>
<summary><b>calibration.yaml</b> — 2-포인트 캘리브레이션 [doc 5]</summary>

| 키 | 기본값 | 설명 |
|---|---|---|
| `camera_seconds` / `bottom_seconds` | `2.0` / `2.0` | 각 큐 응시 시간 |
| `min_samples_per_class` | `10` | 클래스별 사용 가능 샘플 하한 |
| `min_loo_accuracy` | `0.85` | LOO 정확도 하한 (마지막 게이트) |
| `min_centroid_distance` | `0.60` | 표준화 단위 중심 거리 하한. **특징 집합 간 비교 불가** |
| `min_separability` | `1.00` | Fisher 유사 비율 하한. **주 기준** |
| `feature_set` | `C` | `A`(2) / `B`(5) / `C`(9) |
| `C` / `max_iter` / `class_weight` / `random_seed` | `1.0` / `1000` / `balanced` / `42` | LogisticRegression 파라미터 |
| `p_max_threshold` | `0.70` | doc 5-4 신뢰도 하한 (먼저 검사) |
| `margin_threshold` | `0.20` | doc 5-4 마진 하한 (기본값에선 도달 불가) |
| `check_pitch_ordering` | `true` | 역전 pitch 경고 활성화 (**실패시키지 않음**) |

</details>

<details>
<summary><b>temporal.yaml</b> — 시간 평활화 [doc 6]</summary>

| 키 | 기본값 | 설명 |
|---|---|---|
| `window_frames` | `8` | 투표 deque 길이 |
| `ema_alpha` | `0.35` | EMA 계수. `(0, 1]` |
| `vote_recency_weight` | `2.0` | 최신 프레임 가중치(최고령 = 1.0) |
| `enter_bottom_threshold` / `enter_camera_threshold` | `0.60` / `0.60` | 전환 arm 임계값 |
| `to_bottom_dwell_ms` / `to_camera_dwell_ms` | `600` / `400` | 비대칭 히스테리시스 |
| `uncertain_dwell_ms` | `800` | 연속 기권 후 `UNCERTAIN` 폴백. **점수 분기보다 우선** |
| `heartbeat_ms` | `1000` | 최대 이벤트 간격 |
| `decay_on_invalid` | `true` | 기권 프레임이 EMA를 0.5로 붕괴 (false면 동결) |

> ⚠️ `enter_*_threshold`를 0.5 아래로 내리면 두 후보 테스트가 동시에 통과할 수 있고, BOTTOM이 먼저 검사되므로 조용히 BOTTOM 편향이 생깁니다.

</details>

<details>
<summary><b>placement.yaml</b> · <b>release_gate.yaml</b> · <b>collection.yaml</b></summary>

**placement**: `camera_seconds`/`screen_seconds` 2.0, `min_samples_per_target` 8, `min_sample_confidence` 0.30,
`mode` `learned`, `min_loo_accuracy` 0.85, `C` 1.0, `max_iter` 1000, `random_seed` 42,
`min_axis_dominance` 1.30, `min_delta_deg` 4.0, `min_separation` 1.00.

**release_gate** (doc 7, 첫 PoC 통과선 — 최종 KPI 아님): `min_macro_f1` 0.85, `min_bottom_recall` 0.90,
`min_per_user_f1` 0.75, `max_uncertain_ratio` 0.20, `max_calibration_failure_rate` 0.10, `max_p95_latency_ms` 125.0.

**collection** (doc 4-1): `record_fps` 30, `frame_width/height` 1280×720, `transition_guard_ms` 500,
`countdown_s` 3.0, `conditions`(7블록: `static_camera` 20s, `static_bottom` 20s, `alternating` 60s,
`speaking_camera` 60s, `speaking_bottom_reference` 60s, `head_motion` 60s, `lighting_variant` 40s).

</details>

### 환경 변수

| 변수 | 효과 |
|---|---|
| `GAZE_TRACKING_FORCE_CSV` | `1`/`true`/`yes`면 pyarrow가 있어도 gzip CSV 경로 강제. `parquet_available()`이 **첫 호출에서 캐시**하므로 테이블 조작 전에 설정해야 함 |
| `GAZE_TRACKING_GIT_COMMIT` | `AiVersion.code_commit` 오버라이드. 커밋을 물어볼 곳이 없는 환경(컨테이너, 체크아웃이 사라진 휠)용. **빌드에서 설정하세요** — 손으로 설정하면 스탬프가 거짓말을 시작합니다. git과 캐시를 모두 건너뜁니다 |

---

## 9. CLI 레퍼런스

**총 10개 진입점**입니다. 모두 `v1/local/` 에서 실행하세요.

| 도구 | 호출 | 역할 |
|---|---|---|
| `pipeline_demo` | `python -m ai.tools.pipeline_demo` | 3단계 대화형 데모 (**먼저 실행할 것**) |
| `collect` | `python -m ai.tools.collect` | 가이드 데이터셋 녹화 + 큐 타임라인/세그먼트 라벨 생성 |
| `label_review` | `python -m ai.tools.label_review` | 라벨 스크러빙 및 수정 |
| `extract_features` | `python -m ai.tools.extract_features` | 테이크 → 특징 테이블 + 매니페스트 |
| `download_checkpoints` | `python -m ai.tools.download_checkpoints` | 사전학습 가중치 다운로드/검증 |
| `gaze_eval` | `python ai/evaluation/gaze_eval.py` | 오프라인 평가 + doc 7 릴리스 게이트 |
| `threshold_sweep` | `python ai/evaluation/threshold_sweep.py` | doc 5-4 임계값 그리드 스윕 |
| `exp1_backbone` | `python ai/evaluation/experiments/exp1_backbone.py` | 백본 비교 실험 |
| `exp2_calibration_ablation` | `python ai/evaluation/experiments/exp2_calibration_ablation.py` | 특징 집합 A/B/C 절제 실험 |
| `experiment_log` | `python ai/evaluation/experiment_log.py` | doc 18 실험 로그 마크다운 재렌더 (읽기 전용) |

### 종료 코드는 문서화된 인터페이스입니다

| 도구 | `1` 반환 조건 | 기타 |
|---|---|---|
| `gaze_eval` | doc 7 릴리스 게이트 실패 (**리포트는 그래도 기록됨**) | — |
| `exp1` / `exp2` | 추천 가능한 arm이 하나도 없음 | — |
| `extract_features` | 소스가 0프레임 샘플링 **또는** 백본이 예외 발생 | — |
| `collect` | 큐가 한 번도 표시되지 않음 (**라벨 파일 미작성**) | — |
| `download_checkpoints` | 실패한 키가 있음 / **KEY 없이 `--list`도 없이 실행** ⚠️ | `2` = 인자 오용 |
| `pipeline_demo --check` | 카메라 열기 실패 / 프레임 0 / 얼굴 미추적 | 스테이지 플로우는 항상 `0` |
| `threshold_sweep` | 빈 split · 특징 테이블 미해결 (`SystemExit`) | 정상 실행은 `0` |
| `experiment_log` | — | 항상 `0` |

### `pipeline_demo` — 3단계 대화형 데모

```
INTRO → PLACE_CAMERA → PLACE_SCREEN → PLACE_RESULT → CALIB_CAMERA → CALIB_BOTTOM → CALIB_RESULT → LIVE
        └── STEP 1/3 배치 추정 ──┘        └── STEP 2/3 캘리브레이션 ──┘        └ STEP 3/3 실시간 ┘
```

각 수집 스테이지는 `--countdown` 초 카운트다운 후 2.0초의 큐 시간 동안 수집합니다.
프레임은 `analysis_fps`(8.0)로 데시메이션되며 **얼굴이 유효한 프레임만** 샘플 카운터에 반영됩니다.

**게이팅**: 배치 판정은 자문이라 캘리브레이션을 절대 막지 않습니다.
`CALIB_RESULT`에서는 `session.is_calibrated`(적합된 분류기 존재)일 때만 진행되며,
품질이 낮기만 한 캘리브레이션은 앰버 경고 카드와 함께 통과합니다. **분류기 부재만이 실제 차단 요인**입니다.

**키보드** (OpenCV 창에 포커스가 있어야 입력이 들어갑니다 — SPACE가 "안 먹는" 1순위 원인):

| 키 | 동작 |
|---|---|
| `SPACE` / `ENTER` | 진행 |
| `R` | 현재 스테이지 그룹 재시도 |
| `D` | 디버그 오버레이 토글 |
| `M` | 컨투어 메시 토글 |
| `S` | 세션 이벤트를 `ai/reports/demo_events.jsonl`로 덤프 |
| `C` | 실패 판정 강제 통과 (`overridden by operator` 기록). **범례에 표시되지 않음** |
| `Q` / `ESC` | 종료 |

**플래그**

| 플래그 | 기본값 | 설명 |
|---|---|---|
| `--camera-index` | `0` | 웹캠 인덱스 (`--video` 시 무시) |
| `--video PATH` | — | 녹화 파일 재생 (소스 타임스탬프 사용 → 결정적) |
| `--backbone NAME` | config | `cfg.backbone.name` 오버라이드. **`choices=` 제약이 없어** 잘못된 이름은 `build_backbone`에서 늦게 실패합니다 |
| `--width` / `--height` | `1280` / `720` | 요청 해상도. 15 fps 미만이면 640×480으로 자동 강등 |
| `--countdown` | `1.5` | 큐 수집 전 카운트다운 (초) |
| `--no-window` | off | 헤드리스. `INTRO`/`PLACE_RESULT`/`CALIB_RESULT`에서 동일 게이팅으로 자동 진행 |
| `--english` | off | 영어 강제 (기본은 한국어, CJK 폰트 없으면 자동 영어) |
| `--max-seconds` | `0.0` | N초 후 정지 (0 = 무제한) |
| `--check` | off | 카메라/얼굴 추적 진단 후 종료 |
| `--no-overlay` / `--no-mesh` | off | 오버레이 전체 / 메시만 숨김 |

> **미러링**: 분석은 항상 **원본(비미러) 프레임**에서 수행됩니다 — 배치 확인의 yaw 부호가 이에 의존합니다.
> 미러링되는 것은 미리보기 화면뿐입니다. `collect`도 동일합니다(녹화 mp4는 비미러, 참가자는 셀피 뷰를 봄).

### `collect` — 가이드 녹화 [doc 4-1]

참가자가 **무엇을 하라고 지시받았는지** 아는 유일한 컴포넌트이므로, 정답(ground truth)을 만들 수 있는 유일한 곳입니다.

**기본 프로토콜은 9블록 / 355초(약 6분)** 입니다. `--no-calibration-block`이 없으면
`calib_camera`(7 s) + `calib_bottom`(7 s)이 앞에 붙고, 이어 `collection.yaml`의 7블록이 옵니다
(각 수치는 녹화되지만 `IGNORE`로 큐잉되는 3초 카운트다운 포함).

**주요 플래그**: `--participant-id`(필수), `--session-id`(`S01`), `--glasses`/`--no-glasses`,
`--lighting {normal,dim,dark,bright,backlit,backlit_dim}`, `--camera-position {auto,top_center,bottom,side_left,side_right,unknown}`,
`--device-group`, `--notes`, `--conditions`(반복/콤마), `--calibration-block`/`--no-calibration-block`,
`--camera-index`, `--config-dir`, `--out-root`, `--labels-dir`, `--overwrite`,
`--dry-run`, `--dry-run-fps`(`6.0`), `--video`(dry-run 소스), `--save-overlay`, `--no-window`, `--english`, `--list-conditions`.

**출력물**

| 경로 | 내용 |
|---|---|
| `<out-root>/<pid>/<sid>.mp4` | 원본 비미러 프레임, mp4v, `record_fps` (dry-run 시 미작성) |
| `<out-root>/<pid>/<sid>.cues.json` | 스키마 `gaze_cues_v1` — 큐 타임라인, 배치 확인 결과, 프레임 통계 등 |
| `<out-root>/<pid>/participant.json` | 스키마 `participant_meta_v1` — 세션 목록 누적 |
| `<labels-dir>/<pid>_<sid>.segments.json` | doc 4-2 세그먼트 라벨 (`±transition_guard_ms` 가드 밴드 포함) |
| `<save-overlay>/<NN>_<block>_<CUE>.png` | 큐 단계별 HUD 스냅샷 |

> ⚠️ `--dry-run`은 **raw 트리만** `ai/datasets/raw/_dryrun`으로 리다이렉트합니다.
> 라벨 파일은 `--labels-dir`를 함께 주지 않으면 **실제 `ai/datasets/labels`에 기록됩니다.**

> `PacedWriter`가 컨테이너 프레임 인덱스를 캡처 wall clock에 고정하기 위해 프레임을 복제/드롭할 수 있습니다.
> 터미널 요약의 `duplicated`/`dropped` 값이 0이 아니면 **머신이 버거웠다는 뜻이지 라벨이 틀렸다는 뜻이 아닙니다.**

### `label_review` — 라벨 수정

두 가지 불변식: **프로토콜 라벨 파일은 절대 수정되지 않고**(수정본은 별도 `.segments.corrected.json`),
**세그먼트 전체 단위로만 재라벨**됩니다(경계가 정말 틀렸다면 `IGNORE`로 표시할 것).

**키**: `SPACE` 재생/정지, `,`/`.` 프레임 스텝, `a`/`d` 1초 점프, `[`/`]` 세그먼트 이동,
`1`/`2`/`3` = CAMERA/BOTTOM/IGNORE, `u` 실행 취소, `w` 저장, `q`/`ESC` 종료.

**플래그**: `--video`, `--labels`, `--labels-dir`, `--participant-id`, `--session-id`, `--out`,
`--set T_MS=LABEL`(반복 가능, `8000=IGNORE` 또는 `11.5s=CAMERA`), `--write`, `--start-ms`,
`--save-frame`, `--no-window`, `--english`, `--dry-run`.

```bash
# 헤드리스 스크립트 수정
python -m ai.tools.label_review --labels ai/datasets/labels/P01_S01.segments.json \
    --set 8000=IGNORE --set 11.5s=CAMERA --write --no-window
```

> ⚠️ `--dry-run`이 `--write`보다 **먼저** 평가되므로 둘을 같이 주면 표만 출력하고 아무것도 쓰지 않습니다.
> ⚠️ 저장하지 않고 창을 닫으면 수정 사항은 콘솔 경고만 남기고 **버려집니다** (자동 저장 없음).
> ⚠️ `--save-frame`은 PNG를 쓴 **뒤에도 창을 엽니다.** 진짜 헤드리스 확인에는 `--no-window`를 함께 주세요.
> `finalise()`는 편집된 것만이 아니라 **모든** 세그먼트에 `source='corrected'`를 찍고 인접 동일 세그먼트를 병합합니다.

### `extract_features` — 특징 테이블 생성 [doc 20]

이 단계 이후로는 **영상이 다시 필요하지 않습니다.**
프레임은 `analysis_fps`로 데시메이션되고, **전처리는 프레임당 한 번만** 실행되며,
요청된 모든 `--backbone`이 **동일한 `FrameObservation`** 을 채점합니다 —
이것이 doc 23 실험 1을 "샘플링 비교"가 아닌 진짜 백본 비교로 만드는 요소입니다.

`pred_label` / `p_camera` / `p_bottom` / `latency_ms` 열은 **의도적으로 비어 있습니다.**
추출 시점에는 분류기가 없습니다 — doc 5-3 모델은 사용자별이고 `gaze_eval`이 각자의 캘리브레이션 블록에서 적합합니다.

**주요 플래그**: `--video`(**필수·반복 가능**), `--labels`, `--protocol-labels`, `--backbone`(반복/콤마),
`--participant-id`, `--session-id`, `--glasses`/`--no-glasses`, `--lighting`, `--camera-position`, `--device-group`,
`--analysis-fps`, `--out-dir`, `--labels-dir`, `--manifest-dir`, `--config-dir`,
`--limit`, `--overwrite`, `--no-manifest`, `--no-progress`, `--dry-run`.

```bash
python -m ai.tools.extract_features --video ai/datasets/raw/P01/S01.mp4 \
    --backbone mediapipe_geom,l2cs --overwrite
```

> ⚠️ 기존 테이블이 있으면 **모델을 로드하기 전에** 중단합니다 (`--overwrite` 또는 `--dry-run` 필요).
> ⚠️ 라벨 파일 선택 우선순위는 `.segments.corrected.json` > `.segments.json`. `--protocol-labels`로 뒤집습니다.

### `download_checkpoints` — 가중치 설치/검증 [doc 3-2 / 16]

세 가지 일을 의도적으로 분리합니다: **출처를 복붙 가능한 형태로 말해주기**,
**안정적 직접 URL이 있을 때만 다운로드**, **무엇이든 디스크에 있는 것을 신뢰 전에 검증**.

검증이 핵심입니다 — Google Drive 원라이너는 결국 2 kB HTML 동의 페이지를 `.pkl` 이름으로 저장하고
몇 시간 뒤 `torch.load` 안에서 실패합니다. 후보는 **바이트 길이 + 매직 바이트**
(torch zip `PK\x03\x04` 또는 pickle `0x80`, HTML 접두사는 이름으로 거부) **+ 고정된 SHA-256**이 모두 맞아야 통과합니다.
거부된 파일은 남겨두지 않고 삭제합니다.

```bash
python -m ai.tools.download_checkpoints --list                    # 상태 확인 (exit 0)
python -m ai.tools.download_checkpoints l2cs                      # 다운로드 + 검증
python -m ai.tools.download_checkpoints gazetr --from-file "C:/…/GazeTR-H-ETH.pt"
python -m ai.tools.download_checkpoints all --verify-only
```

| 키 | 학습셋 | 크기 | URL |
|---|---|---|---|
| `l2cs` | Gaze360 | 95,849,977 B (sha256 고정) | 커밋 고정된 HuggingFace 미러 (원저자는 Google Drive 폴더로만 배포) |
| `gazetr` | ETH-XGaze | 미공개 | **없음 — `--from-file` 수동 설치만 가능** (Drive 스크레이핑 거부) |

플래그: `--list`, `--dest-root`, `--url`, `--from-file`, `--force`, `--verify-only`,
`--no-verify-hash`, `--no-verify-load`, `--timeout`(60.0).

각 체크포인트 옆에 `<checkpoint>.json` 출처 사이드카가 기록됩니다
(key, backbone, trained_on, size_bytes, 관측 sha256, 고정 sha256, source, installed_at_ms).

---

## 10. 데이터셋 파이프라인

녹화 데이터셋이 있을 때의 정규 경로입니다. 이 저장소에는 아직 녹화본이 없으므로
`collect`부터 시작해야 합니다.

```
collect  →  label_review  →  extract_features  →  gaze_eval  →  threshold_sweep / exp1 / exp2  →  experiment_log
```

코드 계약의 검증은 데이터셋이 아니라 **pytest 스위트**가 담당합니다 ([§13](#13-테스트)).

### 온디스크 레이아웃 (`ai/datasets/`)

```
raw/<pid>/<sid>.mp4                       원본 테이크 (비미러)
raw/<pid>/<sid>.cues.json                 큐 타임라인 (schema: gaze_cues_v1)
raw/<pid>/participant.json                참가자 메타 (schema: participant_meta_v1)
labels/<pid>_<sid>.segments.json          프로토콜 세그먼트 라벨 (schema: gaze_segments_v1)
labels/<pid>_<sid>.segments.corrected.json  검수된 세그먼트 라벨
features/<pid>_<sid>_<backbone>.parquet   특징 테이블 (엔진 없으면 .csv.gz)
manifests/<pid>_<sid>.jsonl               doc 17 매니페스트 (프레임당 1행)
```

**`sample_id`** = `f"{pid}_{sid}_{frame_id:06d}"` (예: `P07_S03_000194`).
매니페스트 · 특징 테이블 · 덤프된 프레임 사이의 조인 키이며, 카운터나 타임스탬프 없이 세 입력만으로 재현 가능해야 합니다.

### 큐 타임라인 → 세그먼트 라벨 [doc 4-2]

`segments_from_cue_timeline(cues, guard_ms, meta, session_id, …)`.

- 큐 목록은 반드시 **종료 마커로 끝나야** 하고, 중간에 종료 마커가 있으면 거부합니다
  (일시정지된 녹화는 두 개의 별도 타임라인으로 써야 합니다).
- 각 항목은 `[t_ms, 다음 t_ms)` 반개구간을 보유합니다.
- **가드 밴드**: `transition_guard_ms`(500) 이내 프레임은 `IGNORE`가 됩니다.
  단 **라벨이 바뀌는 경우에만** 가드가 걸립니다 — `static_camera → alternating`처럼 양쪽 다 CAMERA인 블록 경계는
  눈 움직임을 요구하지 않으므로 라벨을 유지합니다.
- 가드 조각은 블록의 `condition`/`lighting`을 상속하고 **라벨만** `IGNORE`가 됩니다.

> ⚠️ 가드가 블록보다 넓으면 그 블록을 통째로 삼킵니다(가드는 뺄셈 전에 병합됨).
> `transition_guard_ms`가 크고 블록이 짧으면 테이크 전체가 거의 `IGNORE`가 될 수 있습니다 —
> `segment_summary()['ignore_ratio']`를 확인하세요.

### 분할 전략 [doc 4-3]

**프레임 무작위가 아니라 참가자 단위 분리(participant-disjoint)** 입니다.
8 fps에서 연속 프레임은 거의 중복이므로 무작위 프레임 분할은 같은 얼굴·조명·안경을 양쪽에 넣어 **암기를 측정**하게 됩니다.

- `participant_split(ids, ratios=(0.6, 0.2, 0.2), seed=42)`. ID는 중복 제거 후 **정렬**하고 셔플하므로
  디렉터리 나열 순서에 의존하지 않습니다.
- 반올림 나머지는 소수부가 큰 순으로 배분하고, 비어버린 분할이 있으면 회원이 2명 이상인 분할에서 하나를 옮깁니다.
  참가자가 분할 수보다 적으면 **낮은 비율의 분할은 비어 있는 채로 보고**되지, 조용히 채워지지 않습니다.
- 겹침이나 누락이 발견되면 `RuntimeError`로 실패합니다.

**사용자 내부 프로토콜 (doc 4-3 규칙 2)**: 분류기가 사용자별이므로 참가자 내부에서 캘리브레이션 프레임은 학습 데이터입니다.
반드시 **첫 캘리브레이션 블록**에서 와야 하고, 평가는 **그 블록이 끝난 뒤**에 시작해야 합니다 —
마지막 캘리브레이션 *프레임* 이후가 아닙니다. 같은 정적 블록의 나머지는 동일한 자세를 유지한 것이기 때문입니다.

`per_participant_partition()`이 안전한 진입점입니다. 하나의 `cfg`를 두 호출에 전달하고,
`sample_id`(없으면 `(session_id, t_ms)`) 기준으로 **실제 교집합을 검사**해 누수 시 `RuntimeError`를 던집니다.

> ⚠️ `IGNORE` 프레임은 캘리브레이션 선택에서 **절대 뽑히지 않지만**(정답이 없음),
> **무효(invalid) 프레임은 의도적으로 유지됩니다** — 여기서 버리면 전처리 이유로 캘리브레이션이 실패한 참가자를 숨기게 되고,
> doc 5-2는 그것이 "누락 데이터"가 아니라 "캘리브레이션 실패"로 드러나기를 원합니다.

> ⚠️ 테이블에 쓸 만한 `condition` 열이 없으면 `calibration_boundary_ms`가 조용히 성능 저하합니다 —
> 블록 끝 대신 마지막 캘리브레이션 프레임으로 폴백하여 유지된 정적 자세의 나머지가 평가로 누수됩니다.
> 수정은 수집기 쪽에서 해야 합니다.

### 특징 테이블 — 41열

**열 순서는 온디스크 계약의 일부입니다. 새 열은 반드시 끝에 추가하세요.**
doc 18은 저장된 테이블에서 실험을 재현하는데, 재정렬된 CSV 헤더는 위치 기반 리더를 조용히 깨뜨립니다.

| 그룹 | 열 |
|---|---|
| 식별 (5) | `sample_id, participant_id, session_id, frame_id, t_ms` |
| 정답 (4) | `label, label_version, label_source, condition` |
| 버킷 키 (4) | `glasses, lighting, device_group, camera_position` |
| 백본 출력 (4) | `backbone, gaze_yaw, gaze_pitch, gaze_confidence` (라디안) |
| 헤드포즈 (5) | `head_yaw, head_pitch, head_roll, head_reprojection_error, head_depth_proxy` |
| 유효성 (3) | `face_valid, face_confidence, invalid_reason` |
| 품질 (9) | `q_face_area_ratio, q_face_brightness, q_background_brightness, q_face_contrast, q_left_eye_openness, q_right_eye_openness, q_landmark_visibility, q_touches_border, q_backlight_ratio` |
| 판정 (4) | `pred_label, p_camera, p_bottom, uncertain_reason` |
| 타이밍 (3) | `preprocess_ms, inference_ms, latency_ms` |

**결측값 정책**: 결측은 항상 `None`이고 **절대 센티넬 숫자가 아닙니다.**
doc 19 버킷이 이 열들로 필터링하므로 조작된 `0.0`은 프레임을 엉뚱한 버킷에 넣습니다.

**저장 포맷**: parquet 엔진이 있으면 parquet, 없으면 gzip CSV.
`parquet_available()`은 시험 쓰기가 아니라 **import**로 판단하고 `lru_cache`로 캐시합니다 —
답이 행이 하나라도 생기기 전에 **파일 이름**을 결정하기 때문입니다.

> ⚠️ **`save_table()`의 반환값을 반드시 사용하세요.** 엔진이 없거나 parquet 쓰기가 실패하면
> 요청한 `.parquet` 경로가 조용히 `<stem>.csv.gz`가 됩니다(예외 없이 `RuntimeWarning`만).
> ⚠️ CSV 읽기는 `float_precision='round_trip'`이 필수입니다. 기본 고속 리더는 최대 1 ULP 차이가 나
> doc 18 실험 재현이 마지막 자릿수에서 어긋납니다.
> ⚠️ 문자열 열은 의도적으로 object dtype입니다. `.astype('string')`을 하거나 pandas ≥3가 `str` dtype을 추론하게 두면
> 결측이 NaN이 되어 `invalid_reason` / `uncertain_reason` 필터가 조용히 바뀝니다.

---

## 11. 평가와 릴리스 게이트

`ai/evaluation/`은 **영상 없이** 저장된 특징 테이블만으로 파이프라인의 사용자별 절반을 재실행합니다
(캘리브레이션 적합 → doc 5-4 기권 규칙 → doc 6 평활화). 임계값·특징 집합·평활화 변경을 카메라 없이 측정할 수 있습니다.

**조직 원칙**: `UNCERTAIN`은 오답도 아니고 공짜 통과도 아닙니다.

- 정확도 지표는 **판정된(decided) 프레임에 대해서만** 계산합니다.
- 기권 비율은 **별도로** 게이트합니다.
- 모든 헤드라인 숫자 옆에 비관적 뷰(`*_uncertain_as_error`)를 함께 보고합니다 — 기권을 FN에만 더하고
  precision은 건드리지 않습니다(기권은 클래스를 주장하지 않으므로). 두 뷰의 큰 격차 = 기권으로 F1을 사고 있다는 뜻.

### 주요 지표

| 지표 | 정의 |
|---|---|
| `macro_f1` | 두 결정 클래스 F1의 비가중 평균, 판정 프레임 기준 |
| `bottom_recall` | `per_class['BOTTOM']['recall']` |
| `uncertain_ratio` | `n_uncertain / n_labelled` |
| `coverage` | `n_decided / n_labelled` |
| 세그먼트 지표 | 이벤트 스트림을 조각별 상수 타임라인으로 읽어 시간 가중 정확도, 검출률, onset 지연 산출 |
| 지연 시간 | `p50/p95/p99` (numpy 선형 보간). **빈 샘플은 0.0이 아니라 `None`** — 측정 안 한 지연이 게이트를 통과하면 안 되므로 |

**per-user**: 참가자별 행 + `attrs['summary']`에 `_mean/_min/_max/_std`.
doc 7은 **per-user 최솟값**을 게이트합니다 — 망가진 캘리브레이션 하나는 풀링된 평균에서 보이지 않기 때문입니다.

### ⚠️ `frame_smoothed`를 `frame`과 직접 비교하지 마세요

리포트의 프레임 지표 표에는 세 행이 나란히 있습니다:

| view | macro_f1 |
|---|---|
| per frame | **0.9979** |
| per frame, UNCERTAIN as error | 0.9716 |
| after temporal smoothing (doc 6) | **0.7941** |

"평활화가 macro F1을 20포인트 깎았다"로 읽히지만, **틀린 독해입니다.**

doc 6은 `to_bottom_dwell_ms`(600) / `to_camera_dwell_ms`(400)만큼 증거가 지속되어야 전환을 커밋합니다.
따라서 실제 큐 변경 시점과 커밋 시점 사이의 모든 프레임이 이 행에서 **오분류로 계상**됩니다.
그 지연은 스무더가 설계대로 동작한 결과이고, doc 4-2의 가드밴드(±500 ms)는 dwell보다 좁아 이를 흡수하지 못합니다.
데이터가 아무리 깨끗해도 이 효과는 사라지지 않습니다.

평활화된 스트림을 올바르게 채점하는 렌즈는 **세그먼트 지표**입니다. 같은 지연이 오답이 아니라
`onset_latency_ms`로 보고됩니다. 동일 실행에서:

| 지표 | 값 |
|---|---|
| 세그먼트 macro F1 | **1.0000** (48/48 세그먼트) |
| detection rate | CAMERA 1.0000 / BOTTOM 1.0000 |
| onset latency p50 | CAMERA **875 ms** / BOTTOM **1000 ms** |

이 onset 값은 스무더 독스트링이 설계 목표로 기록한 `875 / 1000 ms`와 **정확히 일치합니다.**
즉 프레임 지표가 20포인트 떨어져 보이는 그 순간, 스무더는 사양대로 동작하고 있었습니다.

`gaze_eval`은 이제 프레임 표 바로 아래에 이 주의사항을 렌더링하고,
세그먼트 라벨이 있으면 위 수치를 함께 출력합니다. `--segments`를 주지 않았다면 그렇게 하라고 안내합니다.

### 릴리스 게이트 [doc 7]

게이트는 **정확히 한 곳**에서만 강제됩니다: `metrics.evaluate_release_gate()`의 `_GATE_SPEC` 테이블.

| 행 | Config 필드 | 방향 | 기본값 |
|---|---|---|---|
| `macro_f1` | `min_macro_f1` | ≥ | 0.85 |
| `bottom_recall` | `min_bottom_recall` | ≥ | 0.90 |
| `per_user_f1_min` | `min_per_user_f1` | ≥ | 0.75 |
| `uncertain_ratio` | `max_uncertain_ratio` | ≤ | 0.20 |
| `calibration_failure_rate` | `max_calibration_failure_rate` | ≤ | 0.10 |
| `p95_latency_ms` | `max_p95_latency_ms` | ≤ | 125.0 |

> ⚠️ **측정되지 않은 게이트 행은 실패한 게이트 행입니다.** `value=None`, `pass=False`, `missing` 목록에 추가됩니다.
> `gaze_eval.main()`은 게이트 통과 시 0, 실패 시 1을 반환하며 **리포트는 어느 쪽이든 기록됩니다.**

### doc 19 실패 버킷

10개 불리언 술어. **상호 배타적이지 않습니다** — doc 19는 모델이 *어디서 깨지는지*를 묻지 프레임을 어떻게 분할할지 묻지 않습니다.

| # | 버킷 | 규칙 |
|---|---|---|
| 1 | glasses reflection | `glasses` 참 (근사: "안경 착용" 상위집합) |
| 2 | strong backlight | `q_backlight_ratio ≥ 1.6` |
| 3 | small face | `0 < q_face_area_ratio < 0.030` (무효 컷 0.010의 3배) |
| 4 | leaning back | `ctx_depth_ratio ≥ 1.15` (**참가자 내부** 중앙값 대비) |
| 5 | head down but eyes on camera | `head_pitch ≤ −0.20` **and** 정답 = `CAMERA` |
| 6 | eyes down but head straight | `\|head_pitch\| ≤ 0.10` **and** 정답 = `BOTTOM` |
| 7 | fast transition | `ctx_ms_to_label_change ≤ 1000 ms` |
| 8 | partial occlusion | `q_landmark_visibility < 0.995` **or** `q_touches_border` |
| 9 | webcam at bottom/side | `camera_position ∉ (top_center, top, center, unknown, '')` |
| 10 | low light | `q_face_brightness < 60` **or** `lighting ∈ (dim, dark, low, low_light, backlit_dim)` |

> ⚠️ 버킷 5·6은 **정답 라벨로 정의**되어 한 클래스만 담습니다. 없는 클래스의 recall이 구조적으로 0이라
> macro F1이 0.5 근처에 갇힙니다 — **실제 존재하는 클래스의 recall만** 읽어야 합니다.
> `exp1`은 이들을 별도 표로 랭킹합니다.
> ⚠️ 누락된 열은 NaN으로 읽히고 모든 NaN 비교는 False이므로, 오래된 테이블은 에러 대신 **어떤 버킷에도 들어가지 않습니다.**
> `bucket_coverage()`로 실제 기록 여부를 확인하세요.

### `gaze_eval` — 메인 평가 하네스

```bash
python ai/evaluation/gaze_eval.py --features-dir ai/datasets/features
```

플래그: `--table`(반복), `--features-dir`, **`--backbone`**, `--config-dir`, `--run-name`, `--out-dir`,
`--split {all,train,val,test}`, `--split-seed`(42), `--split-ratios`(`0.6,0.2,0.2`),
`--segments`(반복), `--no-temporal`, `--use-stored-predictions`, `--drop-failed-calibration`,
`--p-max`, `--margin`, `--dump-frames`.

출력: `ai/reports/<run>/gaze_eval.json`, `gaze_eval.md` (+ `--dump-frames` 시 `scored_frames.parquet`).

> ⚠️ **백본이 섞인 테이블은 거부됩니다.** `extract_features --backbone a --backbone b`는
> 백본마다 테이블을 하나씩 쓰고, `--features-dir`는 디렉터리를 통째로 globbing 합니다.
> 그 둘을 합치면 더 큰 데이터셋이 아니라 **같은 프레임이 두 번** 들어간 것입니다 —
> `sample_id`가 중복되고, 각 사용자의 doc 5-3 분류기가 두 모델의 각도로 함께 적합되며,
> 게이트는 어느 백본의 것도 아닌 p95 지연을 읽습니다.
> 그래서 경고가 아니라 에러입니다. `--backbone <name>`으로 하나를 고르거나,
> 비교가 목적이라면 `exp1_backbone.py`를 쓰세요.

> ⚠️ **평활화 플래그 비대칭**: `gaze_eval`은 **기본 평활화 ON**이고 `--no-temporal`로 끕니다.
> `exp1`/`exp2`는 **기본 OFF**이고 `--temporal`로 켭니다. 같은 개념의 스위치가 정반대로 철자되어 있습니다.

> `--features-dir`는 `*.parquet` → `*.csv.gz` → `*.csv` 순으로 글로빙하고 중복을 제거합니다.
> 아무것도 해결되지 않으면 `no feature table found; pass --table or --features-dir`로 종료합니다.
> 이는 `gaze_eval`·`threshold_sweep`·`exp1`·`exp2` 네 도구가 공유하는 동작입니다.

### `threshold_sweep` — doc 5-4 임계값 스윕

기본 격자 81점: `p_max ∈ {0.50 … 0.90}` × `margin ∈ {0.0 … 0.60}`.
`p_max`가 0.50에서 시작하는 이유는 그 아래에서는 2-클래스 규칙이 구속력을 가질 수 없기 때문이고,
`margin`이 0.0에서 시작하는 것은 마진 규칙 off를 뜻합니다.

각 참가자의 분류기는 **한 번만** 적합하고 확률을 캐시한 뒤, 격자 점마다 벡터화된 규칙 적용 한 번만 수행합니다.

**선택 규칙**: `uncertain_ratio ≤ max_uncertain_ratio` **and** `bottom_recall ≥ min_bottom_recall`을 만족하는 행 중
`macro_f1` 최대 → 기권 최소 → 임계값 최소 순으로 안정 정렬.

> ⚠️ 두 축은 독립적이지 않습니다. 2-클래스에서 `margin == 2·p_max − 1`이므로 **대각선 전체가 같은 규칙**을 인코딩하고,
> 마진 임계값은 `margin_threshold > 2·p_max_threshold − 1`인 좌상단 영역에서만 물립니다.
> ⚠️ 기본 `--split val`은 의도된 것입니다 — test에서 고른 임계값은 test-set fitting입니다.
> ⚠️ 여기의 `release_gate_at_best`는 **의도적으로 불완전**합니다(임계값으로 움직일 수 있는 4행만).
> `calibration_failure_rate`와 `p95_latency_ms`는 설계상 실패로 읽히므로, 같은 실행의 `gaze_eval`에서 가져와야 합니다.

### `sanity` — 부호 규약 점검

`(participant_id, backbone)`별로 그룹화합니다 — 풀링하면 한 참가자만 뒤집혀도 평균은 올바른 순서로 남을 수 있기 때문입니다.

`delta_gaze_pitch = mean(pitch | CAMERA) − mean(pitch | BOTTOM)` 은 규약상 **양수**여야 합니다.

| 판정 | 조건 |
|---|---|
| `INSUFFICIENT` | `min(n_camera, n_bottom) < 5` |
| `INVERTED` | delta가 비유한 또는 ≤ 0 |
| `WEAK` | `effect_size < 0.5` |
| `OK` | 그 외 |

`INSUFFICIENT`는 실패시키지 않지만(측정된 적 없음) 집계되므로, **아무것도 측정하지 않고 통과할 수는 없습니다.**

### `experiment_log` — doc 18 실험 로그

**append-only JSONL**이 진실의 원천이고, 옆의 `.md`는 매 append마다 **파일 전체에서 재렌더되는 뷰**입니다
(마크다운 수기 편집은 다음 실행에서 소실됩니다).

`experiment_id` = `<slug>-<YYYYMMDDThhmmss>-<config_hash[:8]>` (예: `exp1-backbone-l2cs-20260906T101112-ab12cd34`) — 결정적입니다.

`dataset_version` = 정렬된 `sample_id` + `label_version`의 sha256 지문.
**측정된 열을 의도적으로 무시**하므로 같은 프로토콜을 새 백본으로 재실행해도 **같은 데이터셋**입니다 —
이것이 exp1의 백본 비교가 하나의 `dataset_version`을 공유하게 만드는 요소입니다.
파일 경로가 버전이 아닌 이유는 `extract_features`가 제자리 덮어쓰기를 하기 때문입니다.

```bash
python ai/evaluation/experiment_log.py --tail 10       # 마크다운 재렌더 + 최근 arm 출력
```
플래그: `--log`(`ai/reports/experiment_log.jsonl`), `--markdown`, `--tail`(10), `--no-render`. 항상 0 반환.

---

## 12. 실험 (doc 23)

### 실험 1 — 백본 비교 (`exp1_backbone`)

gaze backbone을 **유일한 변수**로 고립시킵니다. 네 가지가 arm 간에 고정되고 리포트에 명시됩니다:
동일 참가자·동일 프레임(`sample_id` 교집합), 동일 캘리브레이션 프로토콜(doc 4-3 첫 블록만),
동일 doc 5-3 분류기(arm마다 사용자별 재적합), **기본적으로 평활화 없음**(doc 6의 dwell 타이머가 흔들리는 백본을 가려버리므로).

**doc 3-3 선택 규칙**: 먼저 veto로 arm을 걸러낸 뒤, **적격 집합 안에서만** 순위를 매깁니다.

| Veto 행 | 게이트 필드 | 방향 |
|---|---|---|
| `bottom_recall` | `min_bottom_recall` | ≥ |
| `p95_latency_ms` | `max_p95_latency_ms` | ≤ |
| `pitch_ordering == 'OK'` | — | `--pitch-veto` 시에만 |

> ⚠️ **`unmeasured`는 pass가 아닙니다.** veto 열이 None/NaN/비수치인 arm은 선택 대상에서 제외됩니다.

순위 키: `(-macro_f1, latency_p95_ms, model_size_mb, backbone)` — 완전 결정적.
`recommended`(최선 적격)와 `best_macro_f1`(전체 최선)을 **둘 다** 보고하므로 veto 오버라이드가 항상 드러납니다.

주요 플래그: `--backbone`(반복), `--split`, `--temporal`, `--no-align-frames`, `--pitch-veto`,
`--dump-frames`, `--log`/`--no-log`, `--timestamp`, `--run-name`, `--out-dir`.

출력: `exp1_backbone.{csv,json,md}` + (`--dump-frames`) `scored_frames_<backbone>.parquet`.

> 가중치 부재는 예상된 상황이지 치명적이지 않습니다. doc 3-2가 조용한 폴백을 금지하므로
> `l2cs`/`gazetr`는 가중치 없이 `CheckpointMissingError`를 던지고, `backbone_profile`은 이를 `note`에 기록합니다.
> arm은 저장된 테이블에서 파생되는 모든 것으로 계속 비교됩니다 — `weights: unavailable`은
> "이 머신에서 모델 크기를 읽지 못했다"는 뜻일 뿐입니다.

### 실험 2 — 캘리브레이션 절제 (`exp2_calibration_ablation`)

특징 집합 A/B/C가 각각 무엇을 벌어다 주는지 측정합니다. `calibration.feature_set`만 arm 간에 움직입니다.

**설정 격리**: arm마다 `cfg`를 깊은 복사하므로 **각 arm이 고유한 config hash**를 가집니다.
공유 설정을 변형하면 모든 arm의 기록된 해시가 "마지막으로 실행된 집합"의 해시가 되어버립니다(doc 18).

**서로 상충하는 두 게이트 숫자**를 함께 보고합니다: per-user F1 (평균 **그리고** 최솟값)과 doc 5-2 캘리브레이션 실패율.
약 32개 캘리브레이션 행으로 9차원 집합은 한 사람의 유지된 자세를 외울 여지가 실재하고,
**거부해 버린 사용자에게서만 좋은 점수를 받는 집합은 더 나은 집합이 아닙니다.**

**선택**: `calibration_failure_rate ≤ max_calibration_failure_rate`인 arm 중
`(-per_user_f1_min, -per_user_f1_mean, n_features 오름차순, 이름)` 순.
**작은 집합 우선 타이브레이크는 실질적**입니다 — 4초 캘리브레이션에서 동점이라면 가장 싼 집합이
유지된 자세를 가장 덜 외울 수 있는 집합입니다.

`per_user_matrix`가 풀링된 숫자가 감추는 사례를 드러냅니다: **집합 C가 평균으로는 이기면서
과적합한 그 한 명에게서 지는 경우.**

> ⚠️ 여러 백본이 섞인 테이블에 `--backbone`도 없고 설정된 백본도 매칭되지 않으면 **하드 `SystemExit`** 입니다.
> 그렇지 않으면 arm이 특징 집합 **과** 각도를 만든 모델 양쪽에서 달라져 비교가 교란됩니다.

---

## 13. 테스트

```bash
python -m pytest -q     # 1421 passed in ~43 s
```

pytest 설정은 전부 `pyproject.toml`에 있습니다: `testpaths=["tests"]`, `pythonpath=["ai/src"]`,
`filterwarnings=["ignore::DeprecationWarning"]`(mediapipe/protobuf, numpy 2.x가 import 시 시끄럽게 냄).

`tests/conftest.py`가 repo root를 `sys.path`에 넣으므로 **bare `pytest`도 동작합니다.**
(`pythonpath`는 `ai/src`만 넣기 때문에 이전에는 `ModuleNotFoundError: No module named 'ai'`로 실패했습니다.)

### 커버리지

| 파일 | 테스트 | 보호하는 계약 |
|---|---:|---|
| `test_schemas_config.py` | 324 | 부호 규약, 각도 왕복 변환, 7키 `GAZE_STATE` 계약, 설정 로딩·병합·해시 |
| `test_data.py` | 197 | `sample_id`, 가드밴드, 참가자 분할, doc 4-3 누수 검사, 테이블 왕복 |
| `test_evaluation.py` | 196 | 지표 정의, 기권 회계, 릴리스 게이트 판정, doc 19 버킷 10종, 부호 sanity |
| `test_preprocess_geometry.py` | 172 | 종횡비 함정, roll 부호, bbox 클램핑, PnP Euler, 샘플러 상태 머신 |
| `test_calibration.py` | 117 | 특징 집합 A/B/C, 앵커 동결(누수 방어), doc 5-4 분기 순서, 품질 게이트 5종 |
| `test_backbones.py` | 116 | 레지스트리, torch 지연 임포트, 체크포인트 검증, 백본별 규약 변환 |
| `test_temporal.py` | 107 | EMA·투표·dwell 상태 머신, 기권 처리, 하트비트 |
| `test_runtime.py` | 104 | 세션 수명주기, 지연 시간 회계, 배치 판정 순서, 버전 스탬핑 |
| `test_preprocess_pipeline.py` | 81 | 유효성 게이트 순서, `NO_FACE` 분기, 무효 프레임의 완전 보존 |
| `test_pipeline_demo.py` | 7 | 데모 스테이지 게이팅, 오버레이 기하 |
| **합계** | **1421** | ~43초, 웹캠·네트워크 불필요 |

픽셀이 필요한 곳은 실제 픽스처(`face.jpg`, `static_face_30fps.mp4`)를 씁니다.
mediapipe나 gitignore된 `.task` 모델이 없으면 실패가 아니라 **skip** 합니다 — 그 상태는 정상적인 체크아웃이기 때문입니다.

> ⚠️ **이 스위트는 코드 계약을 고정하지, 모델 정확도를 측정하지 않습니다.**
> 정확도는 doc 4-1 프로토콜로 사람을 녹화해야만 알 수 있습니다.

### 이 스위트가 찾아낸 것

작성 과정에서 **17개의 실제 버그**가 드러났고 전부 수정됐습니다. 가장 중요한 것은 세 모듈에 반복된 한 패턴입니다:

> **NaN이 clamp를 통과해 "확신에 찬 오답"이 되는 문제.**
> `max(-1.0, min(1.0, nan))`은 `nan < 1.0`이 False이므로 **1.0을 반환합니다.**
> 그 결과 `unit_vector_to_angles`는 NaN 방향에 `pitch = -π/2` — 가능한 가장 강한 거짓 BOTTOM — 을 돌려주고,
> `mediapipe_geom`은 같은 −90°를 confidence 0.65로 보고했으며,
> 스무더의 `min(1.0, max(0.0, nan))`은 0.0, 즉 **최대 CAMERA 증거**로 읽었습니다.
> CAMERA/BOTTOM이 시스템의 전체 출력인 만큼, 읽을 수 없는 값이 조용히 확신에 찬 클래스가 되는 것은 심각한 결함입니다.
>
> 수정: `schemas.clamp_unit()` 하나로 통일하고, 모든 가드를 **긍정형**으로 진술했습니다
> ("유한한가"를 검사하지 "유한하지 않은가"를 검사하지 않음 — NaN 비교는 항상 False이므로).
> 이제 읽을 수 없는 값은 기권합니다.

나머지 16개도 같은 성격입니다 — 조용히 잘못된 값을 만들어내되 예외는 던지지 않는 종류:

- 실패한 `solvePnP`가 **이상적인 정면 얼굴과 바이트 단위로 동일**한 값을 반환 (이제 `inf`/`nan`으로 구분)
- `NO_FACE` 프레임이 랜드마크가 0개인데 `landmark_visibility=1.0`을 보고 (doc 19 버킷이 읽는 열)
- CSV 백엔드가 `'07'` → `'7'`로 바꿔 매니페스트 조인 키를 깨뜨림 (parquet은 유지 → "bit-exact" 주장이 거짓)
- 백본 빌드 실패 시 MediaPipe 그래프 누수 (체크포인트 부재는 **문서화된 정상 경로**)
- NaN을 반환하는 백본이 `last_backbone_error`를 남기지 않아, 예외를 던지는 백본과 달리 진단 불가
- 레지스트리가 실패한 임포트를 latch해, 깨진 의존성을 "알 수 없는 백본 이름"으로 보고
- `load_config`가 override의 리스트를 참조 공유해, 한 arm의 수정이 다른 arm의 `config_hash`를 이동

`config_hash`는 수정 전후 **불변**입니다 (`3cc1193e3238`) — doc 18 프로버넌스가 보존됩니다.

> `tests/fixtures/face.jpg`는 여분의 자산이 아니라 **하중을 받는 문서**입니다.
> 9개 PnP 모델 점, 63° 초점거리 선택, 눈 좌우 명명, 홍채 편향 상수(0.30 홍채 반지름),
> 백본 간 부호 규약 교차 검증(`mediapipe_geom` +2.74/−2.77° vs `L2CS` +2.20/−0.23°)이
> **모두 이 176 KB 이미지 하나에서 유도되거나 검증되었습니다.**
> 이 픽스처를 교체하면 해당 상수들의 출처가 무효화되며, 어떤 테스트도 이를 잡아내지 못합니다.

---

## 14. 알려진 한계

1. **2 클래스뿐**: `CAMERA` vs `BOTTOM` + 기권 `UNCERTAIN`. 좌/우를 보거나 청중을 보는 개념이 없습니다.
2. **단일 얼굴**: `num_faces: 1`이고, 값을 올려도 인덱스 0만 소비됩니다.
3. **기하 전제**: 노트북 웹캠이 **화면 위**, 대본이 아래. 다른 배치는 모든 라벨을 뒤집으며
   어떤 다운스트림 지표도 감지할 수 없습니다. 배치 확인은 의도적으로 **자문**이며 캘리브레이션을 막지 않습니다
   (`test_placement_estimate_never_blocks_calibration`으로 고정된 동작).
4. **카메라 내부 파라미터를 측정하지 않음**: 63° 수직 FOV, 주점 = 이미지 중앙, 왜곡 0으로 가정.
5. **지연 시간**: CPU에서 doc 7의 125 ms 예산에 맞는 것은 `mediapipe_geom`(픽스처 0.21 ms)뿐입니다.
   측정된 L2CS 3종(565 / 228 / 195 ms) 모두 초과합니다. GazeTR은 confidence가 상수 1.0이라
   모든 다운스트림 confidence 게이트가 무효화됩니다.
6. **사용자별 모델이 영속화되지 않음**: `save()`/`load()`가 구현되어 있으나 호출자가 없습니다.
   매 세션 재캘리브레이션하고 모델은 프로세스와 함께 사라집니다.
7. **CI 없음**: 1421개 테스트가 있지만 자동 실행되지 않습니다. 커밋 전에 직접 돌려야 합니다.
8. **실제 데이터 성능 미측정**: 코드 계약은 검증됐지만 정확도는 아닙니다.
   doc 7 게이트의 6개 임계값은 실제 참가자 녹화본으로 평가된 적이 없으며,
   이것이 현재 가장 큰 공백입니다.
9. **재현성 주장 약화**: 아직 커밋이 없어 `code_commit`이 `unknown`입니다(첫 커밋 후 해소).
   `requirements.txt`는 `kiwisolver` 상한을 빼면 하한(`>=`)만 있고 락파일이 없어
   신선한 설치가 검증 환경을 재현하지 않습니다 — 헤더에 검증 시점 버전을 기록해 두었습니다.
10. **빈 플레이스홀더 트리**: `ai/model_cards/`, `ai/models/embeddings/`, `ai/models/gaze/adapters/`는
    `.gitkeep`으로 클론 후에도 살아남지만 내용은 비어 있습니다.
    이름이 시사하는 작업(모델 카드, 임베딩, 파인튜닝 어댑터)은 아직 없습니다.
11. **Windows 전용 런처**: `run_demo.bat`에 대응하는 셸 스크립트가 없고 Makefile이나 태스크 러너도 없습니다.
12. **`cv2` 섀도잉 위험**: `opencv-python`과 `opencv-contrib-python`이 같은 버전(5.0.0.93)으로 나란히 설치되어
    있습니다. 후자는 `requirements.txt`에 없습니다. 버전이 같아 현재는 무해하지만,
    한쪽만 업그레이드되면 모듈 섀도잉이 발생합니다.

---

## 15. 트러블슈팅

| 증상 | 원인 / 해결 |
|---|---|
| `ImportError: DLL load failed while importing _cext` | kiwisolver 1.5.1이 설치된 것입니다 → `matplotlib` → `mediapipe` 체인이 함께 붕괴합니다. `pip install "kiwisolver>=1.4,<1.5"`. 재설치나 vcredist로는 해결되지 않습니다 ([§3](#3-환경과-현재-상태)) |
| `ModuleNotFoundError: No module named 'ai'` | CWD가 `v1/local/` 이 아님 |
| `python -m evaluation.gaze_eval` 실패 | 평가 모듈은 **스크립트 경로**로 실행: `python ai/evaluation/gaze_eval.py` |
| `pip install -e .`만 했는데 의존성이 없음 | `pyproject.toml`에 `[project] dependencies`가 없습니다. `pip install -r requirements.txt`를 **먼저** 실행 |
| 데모에서 `SPACE`가 안 먹음 | OpenCV 창에 포커스가 없습니다. **창을 먼저 클릭**하세요 (도구가 시작 시 `CLICK IT FIRST`를 출력) |
| 온스크린 텍스트가 영어로 나옴 | Windows CJK 폰트(`malgun.ttf`/`malgunbd.ttf`/`gulim.ttc`) 또는 Pillow가 없습니다. 데모 시작 시 `korean_text=yes/no`로 확인할 수 있습니다 |
| `--backbone gazetr` 생성 실패 | `GazeTR-H-ETH.pt`가 없습니다. Drive/Baidu에서 수동 다운로드 후 `download_checkpoints gazetr --from-file <path>` |
| `FileNotFoundError: face_landmarker.task` | 에러 메시지의 `curl` 명령으로 다운로드 ([local/README.md](local/README.md)) |
| `CheckpointCorruptError` | Drive HTML 동의 페이지가 `.pkl` 이름으로 저장됨. 파일 삭제 후 `download_checkpoints` 재실행 |
| `download_checkpoints`가 exit 1인데 에러가 없어 보임 | KEY 없이 `--list`도 없이 실행하면 목록만 출력하고 **1을 반환**합니다. 안전한 no-op가 아닙니다 — `--list`를 쓰세요 |
| `cv2` 동작이 이상함 | `opencv-python`과 `opencv-contrib-python`이 **동시 설치**되어 있습니다(후자는 requirements에 없음). 현재는 버전이 같아 무해하지만, 어긋나면 `pip uninstall opencv-contrib-python` |
| `code_commit`이 `unknown` | 아직 커밋이 없어 HEAD가 존재하지 않습니다. 첫 커밋 후 실제 SHA가 들어갑니다. 커밋할 수 없는 환경이라면 `GAZE_TRACKING_GIT_COMMIT`를 빌드에서 설정하세요 |
| 요청한 `.parquet` 파일이 없음 | 엔진 부재 또는 쓰기 실패로 `.csv.gz`가 되었습니다. `save_table()`의 **반환 경로**를 쓰세요 (`load_table`은 네 접미사를 모두 탐색) |
| 모든 프레임이 `UNCERTAIN` | 백본이 죽었을 수 있습니다. `VisionSession._estimate_gaze`가 모든 예외를 삼키므로 **`session.last_backbone_error`를 확인**하세요. 외부에서는 "사용자가 자리를 비운 것"과 구분되지 않습니다 |
| 설정을 바꿨는데 반영이 안 됨 | `_build`가 알 수 없는 키를 조용히 버립니다. 키 철자와 **섹션 이름**(`gaze_backbone.yaml` → `backbone`)을 확인하세요 |
| `threshold_sweep`이 빈 split으로 종료 | 참가자가 적으면 `val`이 빌 수 있습니다. `--split all` 사용 |
| `plot_sweep`이 PNG를 만들지 않음 | matplotlib은 여기서 **선택 의존성**입니다. ImportError면 `None`을 반환하고 CSV/JSON은 정상 기록됩니다 |

---

## 16. 설계 문서(doc N) 대응표

이 저장소에는 `docs/` 디렉터리도, 설계 문서도 없습니다.
코드 독스트링·CLI 도움말·리포트 산문에 흩어진 약 380개의 `doc N` 인용은 **별도로 배포되는 외부 프로젝트 설계 문서**의
섹션 참조입니다(한국어 문서로 보입니다 — `buckets.py`가 doc 19 버킷 이름을 `webcam이 화면 아래/옆에 위치`로 그대로 인용).

| doc | 주제 | 주 구현 위치 |
|---|---|---|
| **3-1** | 입력 표준화 / 전처리 계약 | `vision/preprocess/*` |
| **3-2** | 어떤 gaze backbone인가 (조용한 폴백 금지 규칙) | `vision/backbones/*`, `ai/tools/download_checkpoints.py` |
| **3-3** | 지연 시간 예산 및 백본 선택 | `exp1_backbone.py` |
| **4-1** | Stage-A 녹화 프로토콜 | `ai/tools/collect.py`, `ai/configs/collection.yaml` |
| **4-2** | 라벨링과 전환 가드 밴드 | `vision/data/labels.py` |
| **4-3** | 참가자 분할 및 사용자별 누수 규칙 | `vision/data/splits.py` |
| **5** | 캘리브레이션 전반 | `vision/calibration/*` |
| **5-1** | 캘리브레이션 특징 집합 A/B/C | `calibration/features.py`, `exp2_*.py` |
| **5-2** | 캘리브레이션 품질 게이트 | `calibration/quality.py` |
| **5-3** | 사용자별 LR 분류기 | `calibration/classifier.py` |
| **5-4** | UNCERTAIN 규칙 | `classifier.decide()`, `gaze_eval.apply_uncertain_rule()` |
| **6 / 6-1 / 6-2** | 시간 평활화 / dwell·히스테리시스 / `GAZE_STATE` 계약 | `vision/temporal/smoother.py`, `schemas.GazeStateEvent` |
| **7** | PoC 릴리스 게이트 | `ai/configs/release_gate.yaml`, `metrics.evaluate_release_gate()` |
| **15** | 테이크별 버전 스탬핑 | `vision/runtime/version.py`, `schemas.AiVersion` |
| **16** | 체크포인트는 오브젝트 스토리지에 | `.gitignore`, `download_checkpoints.py` |
| **17** | 데이터셋 매니페스트 | `vision/data/manifest.py` |
| **18** | 실험 로그와 재현성 | `ai/evaluation/experiment_log.py` |
| **19** | 실패 버킷 | `ai/evaluation/buckets.py`, `runtime/placement.py` |
| **20** | 프라이버시 — 숫자는 남기고 픽셀은 절대 남기지 않음 | `vision/data/features_table.py`, `.gitignore` |
| **23** | 실험 1 (백본) / 실험 2 (캘리브레이션 절제) | `ai/evaluation/experiments/*` |

인용 빈도 상위(어떤 문서가 실제로 코드를 이끄는지의 신호):
doc 19 (53) · doc 7 (49) · doc 18 (33) · doc 5-2 (30) · doc 3-1 (29) · doc 5-4 (28) · doc 23 (27) · doc 3-2 (26) · doc 4-3 (25).

> doc 1, 2, 8–14, 21, 22에 대한 참조는 코드 어디에도 없습니다 — vision 슬라이스는 위 부분집합만 건드립니다.

---

## 라이선스 / 서드파티 가중치

- `L2CSNet_gaze360.pkl` — L2CS-Net, Gaze360 학습. 원저자는 Google Drive 폴더로만 배포하므로
  커밋 고정된 HuggingFace 미러에서 가져오고 SHA-256으로 검증합니다.
- `GazeTR-H-ETH.pt` — GazeTR-Hybrid, ETH-XGaze 학습. 안정적인 직접 URL이 없어 수동 설치만 지원합니다.
- `face_landmarker.task` — Google MediaPipe Face Landmarker (float16 v1 번들).

각 가중치의 원 라이선스를 따르며, 이 저장소는 어떤 가중치도 재배포하지 않습니다(`.gitignore` 정책, doc 16).
