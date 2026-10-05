# v1 — 발표자 시선 판정 (CAMERA / SCREEN / BOTTOM / OTHER)

발표자가 **카메라를 보는가**, **화면을 보는가**, **화면 아래 대본을 보는가**,
**그 밖의 곳을 보는가**를 판정하는 모델입니다. 확신이 없으면 **기권(UNCERTAIN)** 합니다.

```
            ┌ 준비 점검: 한 명 · 가운데 · 거리 · 조명 · FPS ─ 미달이면 거절(strict)
            ├ 고개 원 확인: 고개를 천천히 돌려 원 채우기 ─ 방향별 추적 확인 (막지 않음)
            │
BGR 프레임 ──▶ preprocess ──▶ backbone ──▶ 기준점 분류기 ──▶ temporal ──▶ GAZE_STATE
   + t_ms      MediaPipe      고개 방향    사용자별 3점 보정   K클래스 평활   (7키 JSON)
               주 얼굴 추적    (head_pose)  (편하게 바라보기)   히스테리시스
                    │                        │
                    │                        └─ 프레임 판정(+ OTHER 방향) ──▶ 에이전트 증거
                    │                              1초 기록 → 코치 이슈 · 리뷰 요약 · 개입 효과
                    └────────▶ 조건 감시 (보정 때 장면과 비교) ──▶ SESSION_CONDITION
```

> **v1.1 (`gaze_v1.1.0`)** — 같은 v1 폴더에서 올라간 개정입니다. 2초+2초 2클래스 LR 보정을
> **3점 기준점 분류기**로 바꾸고, 준비 점검 · 조건 감시 · 다중 얼굴 처리를 더했습니다.
> 판정 신호는 **고개 방향**(`head_pose` 백본)입니다. 노트북 웹캠 실측에서 눈 기반 백본이 고개를 고정한 채
> 움직인 눈을 세로로 거의 읽지 못했기 때문입니다([§7-2](#7-2-gaze-backbone--visionbackbones-doc-3-2)).
> 눈 기반 백본은 `vision/eye/`로 옮겨 비교 실험용으로 남겼고, 기존 LR은 `calibration.method: logistic`으로 남아 있습니다.
> 준비 점검과 보정 사이에 **고개 원 확인**(고개를 천천히 돌려 얼굴 주위 원을 채우는 단계)이 있어,
> 고개를 어느 쪽으로 돌려도 얼굴을 따라가는지 촬영 전에 확인합니다([§7-12](#7-12-고개-원-확인--visionruntimesweeppy)).
> 화면 밖(OTHER)은 **어느 쪽인지 8방향**으로 붙고, 프레임 판정을 **1초 기록**으로 모아 코치·리뷰 에이전트가 읽는
> 형식(이슈 · 테이크 요약 · 개입 효과)으로 냅니다([§7-11](#7-11-에이전트-증거--visionevidencegazepy)).
> 같은 엔진의 **브라우저용 TypeScript 포팅과 로컬 웹 데모**가 `local/web/`에 있습니다
> (프론트엔드 Worker 계약에 맞춘 모양, [§7-10](#7-10-브라우저-포팅--localweb)).

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
- **준비 점검**: 한 명 · 화면 가운데 · 인식 가능한 얼굴 크기 · 정면 · 조명 · FPS를 1초 유지해야 통과. 3초 이상 미달이면 **거절**(strict) ([§7-7](#7-7-준비-점검--visionruntimepreconditionspy))
- **고개 원 확인**: 준비 점검을 통과하면 고개를 천천히 돌려 얼굴 주위 원(32칸)을 채웁니다. 방향마다 얼굴 추적이 유지되는지 확인하고, 놓친 방향을 기록합니다. 판정에는 쓰지 않고 막지도 않습니다 ([§7-12](#7-12-고개-원-확인--visionruntimesweeppy))
- **3점 보정**: 편한 자세로 **렌즈 → 화면 가운데 → 대본**을 차례로 봅니다. 시간이 아니라 **좋은 프레임**이 차야 넘어가는 게이지 방식 ([§7-3](#7-3-사용자별-캘리브레이션--visioncalibration-doc-5))
- **기준점 분류**: 세 지점의 시선 중앙값(기준점)과 화면 영역을 만들어 CAMERA / SCREEN / BOTTOM / OTHER를 판정. 학습 데이터가 필요 없고, 보정은 세션과 함께 버립니다
- **카메라 배치 확인**: 렌즈·화면 가운데 기준점으로 웹캠이 화면 위 가운데인지 판정. 아니면 대본 보정 **전에** 막습니다
- **조건 감시**: 촬영 중 거리·위치·고개·조명이 보정 때와 달라지거나 다른 사람이 보이면 `SESSION_CONDITION`의 신뢰도를 낮춥니다. 측정 대상이 사라지거나 바뀌면 판정을 UNCERTAIN으로 강제
- **고개 방향으로 판정**: 기본 백본 `head_pose`는 얼굴이 향한 방향을 시선으로 씁니다. 눈 기반 백본(`mediapipe_geom`, `l2cs`, `gazetr`)은 `vision/eye/`에 보관되어 이름으로 고르면 쓸 수 있습니다
- **OTHER 방향**: 화면 밖을 보면 보정한 화면 영역에서 어느 쪽으로 벗어났는지를 **발표자 기준 8방향**(오른쪽, 오른쪽 위, 위, …)과 각도로 붙입니다 ([§7-3](#7-3-사용자별-캘리브레이션--visioncalibration-doc-5))
- **판별 기준과 시나리오**: 잴 수 있는가 · 측정 불가 · 어디를 보는가(좌우·상하 값 포함) · 누가 화면에 있는가를 숫자로 정한 표와, 그것을 확인하는 시나리오 24개(Python 세션 전체 · 브라우저 엔진 전체) ([§7-13](#7-13-판별-기준과-시나리오))
- **에이전트 증거**: 프레임 판정을 1초 기록으로 모아, 코치에게는 지금의 시선 이슈를, 리뷰에는 테이크 통계·문제 구간·이전 테이크와의 차이를, 피드백 뒤에는 개입 효과를 공통 평가기 형식으로 냅니다 ([§7-11](#7-11-에이전트-증거--visionevidencegazepy))

**하지 않는 것**

- 좌/우를 별도 라벨로 두지 않습니다 — 화면 밖은 모두 OTHER이고, 어느 쪽인지는 `direction`으로 따로 붙습니다
- 코칭 결정 — 언제 무엇을 말할지는 코치 에이전트가 정합니다. 이 모델은 측정과 그 근거까지만 냅니다
- 눈만 움직이는 시선은 보지 않습니다(기본 `head_pose`) — 고개를 든 채 눈만 내려 대본을 읽으면 정면으로 판정됩니다
- 여러 사람을 동시에 판정하지 않습니다 — 얼굴은 최대 3개까지 검출해 **다른 사람의 존재는 감지**하지만, 판정은 주 얼굴 한 명만 합니다
- 시선 좌표 추정 — "화면의 정확히 어느 픽셀"이 아니라 "어느 영역"입니다
- 거리 변화 보정 — 보정 후 거리가 바뀌면 신뢰도만 낮춥니다 (기준점을 다시 계산하지 않음)
- 저장 — 영상 · 보정값 · 사용자 데이터를 남기지 않습니다
- 전송 계층 — 이벤트를 밖으로 내보내는 서버 코드가 없습니다. 소비자가 `VisionSession`을 인프로세스로 임베드합니다

---

## 2. 출력 계약

이벤트는 두 종류입니다. 둘 다 키 집합이 고정돼 있습니다.

**`GAZE_STATE`** — `GazeStateEvent.to_dict()`, **정확히 7개 키**:

```json
{
  "type": "GAZE_STATE",
  "model_version": "gaze_v1.1.0",
  "t_ms": 12480,
  "label": "BOTTOM",
  "confidence": 0.8134,
  "continuous_duration_ms": 1750,
  "face_valid": true
}
```

| `label` | 의미 |
|---|---|
| `CAMERA` | 렌즈(와 그 바로 아래 화면 상단 띠)를 봄 — 눈맞춤 |
| `SCREEN` | 화면의 나머지 영역(슬라이드 등)을 봄 |
| `BOTTOM` | 화면 아래쪽 대본 영역을 봄 |
| `OTHER` | 보정한 어느 영역도 아닌 곳을 봄 (화면 밖, 고개를 크게 돌림) |
| `UNCERTAIN` | 판단할 수 없음 (얼굴 없음, 애매한 확률, 측정 대상이 사라짐·바뀜) |

v1.0과 비교해 **키는 그대로이고 `label` 값이 둘(`SCREEN`, `OTHER`) 늘었습니다.**
`label`을 switch하는 소비자는 두 값을 처리해야 합니다.

**`SESSION_CONDITION`** — `SessionConditionEvent.to_dict()`, **정확히 4개 키**:

```json
{"type": "SESSION_CONDITION", "t_ms": 45200, "reliability": 0.62, "issues": ["TOO_FAR", "SECOND_FACE"]}
```

측정 환경이 보정 때와 얼마나 같은지(0~1)와 그 이유입니다. 상태가 바뀔 때와 2초마다 나갑니다.
GAZE_STATE의 `confidence`에 섞지 않는 이유는 "시선이 애매함"과 "환경이 나빠짐"을 소비자가
구분할 수 있어야 하기 때문입니다 ([§7-8](#7-8-조건-감시--visionruntimeconditionpy)).

**에이전트 증거** — 이벤트가 아니라 세션이 들고 있는 기록입니다(`VisionSession.gaze_evidence`).
코치·리뷰 에이전트는 프레임을 읽지 않고 이것을 읽습니다. 세 가지 모양이 있습니다
(자세한 규칙은 [§7-11](#7-11-에이전트-증거--visionevidencegazepy)).

1초 기록(`GazeSample.to_dict()`) — OTHER면 방향이 붙습니다:

```json
{"t_ms": 14000, "duration_ms": 1000, "state": "OTHER", "direction": "UP_LEFT",
 "confidence": 1.0, "reliability": 0.9475, "issues": [], "frames": 7}
```

코치 이슈(`evaluate_gaze()`) — 공통 평가기 형식:

```json
{
  "evaluator": "gaze", "issue_type": "GAZE_AWAY", "t_ms": 17000,
  "severity": 0.375, "confidence": 0.9472, "persistence_sec": 3.0,
  "evidence": {"state": "OTHER", "run_start_ms": 14000, "continuous_ms": 3000,
               "other_ratio_5s": 1.0, "other_ratio_30s": 0.2, "camera_ratio_30s": 0.4,
               "mean_reliability": 0.9472, "direction": "UP_LEFT"},
  "actionable": true
}
```

개입 효과(`intervention_outcome()`):

```json
{"intervention": {"type": "LOOK_AT_CAMERA", "issue_type": "GAZE_ON_SCRIPT", "t_ms": 12000},
 "before": {"bottom_ratio_5s": 1.0, "measured_ms": 5000},
 "after_5s": {"bottom_ratio_5s": 0.0, "measured_ms": 1000},
 "effective": true}
```

프레임 판정(`GazeDecision.to_dict()`)에도 `direction`(OTHER일 때)과 `offset_deg`(`[오른쪽°, 위°]`)가
값이 있을 때만 들어갑니다. GAZE_STATE의 7키는 바뀌지 않았습니다.

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
| 온라인 경로 | ✅ **동작함** — 픽스처 영상으로 준비 점검 → 고개 원 확인(헤드리스는 건너뜀) → 3점 보정 → LIVE 완주(`--advisory`). 프레임당 **p95 20 ms** (`head_pose`, 얼굴 3개 검출 포함, 125 ms 예산 내) |
| strict 흐름 | ✅ 픽스처 영상(정지 얼굴)은 고개가 움직이지 않아 카메라 위치는 "판정 불가"로 기록만 하고 넘어가고, **보정 결과에서 `CLASS_NOT_SEPARABLE`로 차단**됨 — 의도한 동작 |
| 테스트 | ✅ `python -m pytest -q` → **1752 passed, 38 skipped** (1790개 수집, [§13](#13-테스트)) |
| 브라우저 엔진 | ✅ `local/web`: vitest 197개(Python 기준 답과의 동치 136개, 판별 시나리오 28개 포함), FE 컴파일러 옵션 타입 검사(엔진·Worker·카메라 화면 모듈), 헤드리스 Chrome 실행 검사 23개 ([§7-10](#7-10-브라우저-포팅--localweb)) |
| 환경 의존 테스트 | ⚠️ Windows 응용 프로그램 제어 정책이 DLL 로드를 막는 PC에서는 실패합니다. 이 PC에서 pyarrow(`test_the_parquet_backend_round_trips_the_table_unchanged`)와 scikit-learn(`_target_encoder_fast`, `pairwise`)이 막힌 적이 있고, scikit-learn이 막히면 `test_calibration.py` 수집과 logistic·learned 배치 테스트 14개가 실패합니다. 코드와 무관하며, 같은 PC에서 막히지 않은 실행은 모두 통과했습니다 |
| Git | 커밋이 있어 `AiVersion.code_commit`에 실제 SHA가 기록됩니다 |
| 녹화 데이터셋 | ⚠️ `ai/datasets/{raw,features,labels,manifests}` 전부 **비어 있음** — 실제 참가자 녹화본이 아직 없습니다 |
| 릴리스 게이트(doc 7) | ⚠️ **실제 데이터로 평가된 적 없음.** 게이트 *메커니즘*은 검증됐지만(통과/실패 양쪽), 6개 임계값은 여전히 *설계 목표*이지 관측 결과가 아님 |
| 체크포인트 | `face_landmarker.task` ✅ (3,758,596 B) / `L2CSNet_gaze360.pkl` ✅ (95,849,977 B, sha256 검증됨) / `GazeTR-H-ETH.pt` ❌ 없음 |
| 실행 가능한 백본 | `head_pose`(기본), `mediapipe_geom`, `l2cs` — `gazetr`는 가중치 없어 생성 시 실패. 눈 기반 셋은 `vision/eye/` |
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
> v1.1에서 추가된 기준값(준비 점검 한계, 게이지 조건, 화면 비율, 조건 감시 경계)도
> 전부 **초기값**이며, 과잉 거절을 피하도록 느슨하게 잡았습니다.
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
│   ├── configs/                     # 11개 YAML 튜닝 파일 (§8)
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
│   │   ├── backbones/               # base, registry, head (기본 head_pose)
│   │   ├── eye/                     # 보관: mediapipe_geom, l2cs, gazetr, blink (눈 기반, 비교 실험용)
│   │   ├── calibration/             # references(기본), gauge, factory, classifier·features·quality(LR)
│   │   ├── temporal/                # smoother (K클래스)
│   │   ├── runtime/                 # session, preconditions, sweep, condition, placement, policy, version
│   │   ├── evidence/                # gaze — 에이전트 증거 (1초 기록 · 코치 이슈 · 리뷰 요약 · 개입 효과)
│   │   └── data/                    # manifest, labels, splits, features_table
│   │
│   ├── tools/                       # CLI 5종 (§9) — `python -m ai.tools.X`
│   └── evaluation/                  # 평가 CLI 5종 (§9) — `python ai/evaluation/X.py`
│
├── web/                             # 브라우저 엔진(TS) + 로컬 웹 데모 (§7-10, web/README.md)
│
└── tests/                           # 1790개 테스트 (§13)
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
   PreprocessPipeline.process_bgr(main_face_hint)  [doc 3-1]
        │   MediaPipe FaceLandmarker (얼굴 최대 3개) → 주 얼굴 선택(직전 위치에 가장 가까운 얼굴)
        │   → 478 랜드마크 + 52 블렌드셰이프 + 4x4 변환행렬
        │   → 헤드포즈(행렬 우선, PnP 폴백) / roll 정렬 crop / 품질 신호 / 유효성 게이트
        ▼
   FrameObservation  ─ HeadPose, FrameQuality, face_crop(224²), eye_crops(60×36),
        │              landmarks(478,3), blendshapes, face_bbox, invalid_reason,
        │              scene(FaceScene: 얼굴 수, 두 번째 얼굴 비율, 홍채 지름 px)
        │
        ├──(보정 전)→ PreconditionChecker.update()  → PreconditionReport (PASS / RETRY / REJECT)
        ├──(보정 전)→ HeadSweep.offer()              → SweepStatus (고개 원: 칸, 놓친 방향)
        ├──(보정 후)→ AdaptiveBlinkGate.apply()     → 깜빡임이면 EYES_CLOSED로 표시
        ▼
   GazeBackbone.predict_observation()             [doc 3-2]
        ▼
   GazeVector(gaze_yaw, gaze_pitch, confidence, backbone, inference_ms)   ← 라디안
        │
        ├──(보정 중)→ CalibrationGauge.offer() → CalibrationSample ──→ fit_gaze_classifier()  [doc 5]
        │                                                              └→ CalibrationQuality
        ▼
   ReferenceAnchorClassifier.decide()             [doc 5-4]   (method: logistic → PerUserGazeClassifier)
        ▼
   GazeDecision(label, probs{CAMERA,SCREEN,BOTTOM,OTHER}, p_camera, p_bottom, face_valid, uncertain_reason,
        │       direction(OTHER일 때), offset_deg)
        │                                            ┌─ ConditionMonitor.update() → SESSION_CONDITION
        │                                            │   (심각하면 판정을 UNCERTAIN으로 강제)
        ├──▶ GazeEvidenceRecorder.record()  → GazeSample(1초) → GazeTimeline → 코치 이슈 · 리뷰 요약
        ▼
   TemporalSmoother.update()  (분류기의 클래스 집합)
        ▼
   GazeStateEvent  ──.to_dict()──▶  GAZE_STATE JSON  (다운스트림 계약)
```

전 과정을 하나로 묶는 것이 **`VisionSession`** (`vision/runtime/session.py`)입니다.
프레임 ID 공간, 따라가는 얼굴 위치, 캘리브레이션 버퍼와 게이지, 보정 때 장면(조건 감시 기준),
지연 시간 링버퍼, 두 이벤트 목록을 소유합니다. **아무것도 디스크에 남기지 않습니다.**

한 테이크의 순서:

| 단계 | 세션 API | 결과 |
|---|---|---|
| 1. 준비 점검 | `check_preconditions(bgr, t_ms)` | `PreconditionReport` — 막을지는 호출자가 `runtime.policy`로 결정 |
| 1-1. 고개 원 확인 | `start_head_sweep(t_ms)` → `offer_sweep_frame(bgr, t_ms)` 반복 | `SweepStatus` (켜진 칸, 지금 방향, 힌트, 놓친 방향). 막지 않음 |
| 2. 보정 (큐마다) | `start_calibration_cue(cue, t_ms)` → `offer_calibration_frame(bgr, t_ms)` 반복 | `GaugeStatus` (진행률, 끝났는지, 지금 무엇이 문제인지) |
| 2-1. 배치 판정 | `estimate_placement()` (SCREEN 큐 직후) | `PlacementCheckResult` (mode `anchors`) |
| 2-2. 보정 종료 | `finish_calibration()` | `CalibrationQuality` (+ `placement`) — 조건 감시·깜빡임 게이트가 켜짐 |
| 3. 실시간 | `process_frame(bgr, t_ms)` | `(GazeStateEvent, GazeDecision)` + `last_condition` |
| 3-1. 다시 맞추기 | `begin_reanchor(t_ms)` | 렌즈를 다시 보는 동안 기준점 이동 (`reanchor_status`) |
| 3-2. 에이전트 증거 | `gaze_evidence.issues(t_ms)` / `.summary()` / `.timeline` | 코치 이슈, 테이크 요약, 1초 기록 ([§7-11](#7-11-에이전트-증거--visionevidencegazepy)) |

게이지 없는 기존 방식(`add_calibration_frame`)과 독립 배치 확인(`add_placement_frame` / `finish_placement`)은
녹화 도구와 기존 호출자를 위해 그대로 남아 있습니다.

### `GAZE_STATE` 이벤트 계약

`GazeStateEvent.to_dict()`가 내보내는 **정확히 7개 키**가 다운스트림과의 통합 계약입니다:

```json
{
  "type": "GAZE_STATE",
  "model_version": "gaze_v1.1.0",
  "t_ms": 12480,
  "label": "BOTTOM",
  "confidence": 0.8134,
  "continuous_duration_ms": 1750,
  "face_valid": true
}
```

`is_transition`, `smoothed_p_camera`, `smoothed_p_bottom`, `smoothed_probs`(클래스가 셋 이상일 때만)는
**계약에 포함되지 않습니다.** `to_debug_dict()` / `dump_events(debug=True)`로만 노출되며,
실패 케이스 첨부용이지 소비자용이 아닙니다.

`SESSION_CONDITION`은 `VisionSession.condition_events` / `dump_condition_events()`로 따로 나옵니다.
`events`와 `dump_events()`에는 GAZE_STATE만 들어 있습니다.

> ⚠️ **이 저장소에는 전송 계층이 없습니다.** websocket / fastapi / flask / grpc / zmq 등 어떤 서버 코드도 없습니다.
> 이벤트가 프로세스 밖으로 나가는 유일한 경로는 `VisionSession.dump_events()` /
> `dump_condition_events()`의 JSONL 파일뿐이며, 유일한 호출자는 데모의 `S` 키
> (`ai/reports/demo_events.jsonl`, `ai/reports/demo_conditions.jsonl`)입니다.
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
| `calibration/quality.py`, `calibration/references.py` (`check_pitch_ordering: true`) | `quality.hint`에 `"INVERTED_PITCH: …"` 문자열을 붙이고(기준점 방식은 `quality.warnings`에도 기록) `status`/`reason`은 건드리지 않음. 기준점 방식은 CAMERA > SCREEN > BOTTOM 순서를 봄 |
| `exp1_backbone --pitch-veto` | 이 플래그를 줄 때만 구속력 있는 veto가 됨 |

> 기준점 방식에서는 뒤집힌 순서가 **배치 판정**으로도 드러납니다: 화면 가운데가 렌즈보다 위로
> 측정되면 카메라가 화면 아래(`BOTTOM`)로 판정되고, strict 모드는 대본 보정 전에 막습니다.

> ⚠️ **`CalibrationFailReason.INVERTED_PITCH`는 `quality.reason`에 절대 대입되지 않습니다.**
> `quality.reason`이 가질 수 있는 값은 나머지 여섯 개뿐입니다 (`ANCHOR_AMBIGUOUS`는 기준점 방식만 냄).
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

**눈을 읽지 않는 백본에서는 눈 게이트(4·5번)를 되돌립니다.** 기본 `head_pose` 백본으로 돌 때 세션
(`VisionSession._observe`)과 브라우저 엔진(`GazeEngine.observe`)은 `EYES_CLOSED`·`CROP_FAILED` 프레임을 유효로
되돌립니다. 고개를 숙이거나 옆·위로 크게 돌리면 눈이 가려지거나 납작해져 EAR이 0.12 밑으로 떨어지고, 눈이 작거나
돌아가 크롭이 안 되기도 하는데, 그래도 얼굴이 향한 방향은 얼굴 전체 메시로 그대로 측정되기 때문입니다.
되돌리지 않으면 그동안 판정이 `UNCERTAIN`("눈을 뜬 채로 바라봐 주세요")으로 멈추고, 보정 게이지와 고개 원도
그 프레임을 버립니다. 1~3번(얼굴이 충분히 보이는가)은 그대로 적용합니다. 전처리 자체는 공유 파이프라인이라
그대로 `EYES_CLOSED`·`CROP_FAILED`를 기록합니다(눈 기반 백본은 이 판정이 필요합니다).

**`NO_FACE`를 제외하면 무효 프레임도 완전히 채워집니다.** 랜드마크·포즈·크롭·품질이 모두 남습니다 —
doc 19 실패 버킷과 doc 5-2 캘리브레이션 힌트가 사용자에게 실패 이유를 설명하는 데 필요한 것이 정확히 그것들이기 때문입니다.

**프레임 단위로는 게이트하지 않는 신호**: `backlight_ratio`(>~1.6이면 강한 역광), `face_brightness`, `face_contrast`, `landmark_visibility`.
역광·밝기는 프레임을 무효로 만들지 않고, **준비 점검**([§7-7](#7-7-준비-점검--visionruntimepreconditionspy))과
**조건 감시**([§7-8](#7-8-조건-감시--visionruntimeconditionpy))가 장면 단위로 판단합니다.

**여러 얼굴과 주 얼굴** (`landmarker.detect_all`, `pipeline.select_main_face`):
`num_faces: 3`으로 얼굴을 최대 3개까지 받고, 그중 **하나만** 분석합니다.

- 힌트가 없으면 **가장 큰 얼굴**(자기 노트북 앞에 앉은 발표자가 가장 가깝습니다).
- 힌트(직전 프레임의 주 얼굴 중심, 정규화 좌표)가 있으면 **그 위치에 가장 가까운 얼굴** —
  옆 사람이 몸을 숙여 순간적으로 더 커져도 측정 대상이 바뀌지 않습니다. 동점이면 더 큰 얼굴.
- `VisionSession`이 매 프레임 힌트를 갱신해 넘깁니다.
- MediaPipe 순서의 `face_landmarks[0]`은 "발표자"가 아닙니다. 예전처럼 index 0을 쓰면 안 됩니다.

`FrameObservation.scene`(`FaceScene`)에 얼굴 수, **다른 사람 얼굴의 면적 비**(주 얼굴 대비, `second_face_ratio`),
**홍채 지름(px)**, 모든 얼굴 bbox가 담깁니다. 특징 테이블(`to_record`)에는 들어가지 않습니다.

- 다른 사람 얼굴로는 **별개의 얼굴만** 셉니다. 주 얼굴이 두 번 잡힌 것(한 상자의 중심이 다른 상자 안 — MediaPipe가 작은 얼굴을
  두 번 돌려주기도 합니다)과 검출기 하한(`min_face_area_ratio`, 화면의 1%)보다 작은 검출(대개 배경 오검출)은 빼고, 남은 것 중
  가장 큰 얼굴의 면적 비입니다. 멀어져 주 얼굴이 작아지면 작은 오검출도 비율로는 커지기 때문입니다.
- 홍채 지름은 홍채 고리 4점 사이 최대 거리를 두 눈에서 평균합니다(`crops.iris_diameter_px`).
  성인 홍채는 약 11.7 mm로 거의 일정해서, **거리 추정**과 **정밀도 지표**를 겸합니다.
  카메라↔화면 가운데(눈 약 9° 회전)는 홍채 이동량이 지름의 약 16%뿐이라, 홍채가 몇 픽셀이냐가 곧 판정 해상도입니다.
- ⚠️ MediaPipe 얼굴 검출기는 근거리용이라 **작은 얼굴은 아예 검출하지 않습니다**
  (픽스처를 옆에 0.9배로 붙이면 검출, 0.8배면 미검출). 멀리 있는 사람은 "다른 사람"으로 잡히지 않을 수 있습니다.

### 7-2. Gaze Backbone — `vision/backbones` [doc 3-2]

이름 기반 레지스트리로 `BackboneConfig.name` → 클래스가 **순수 함수**로 결정됩니다
(doc 23 실험 1이 다른 모든 것을 고정한 채 백본만 교체할 수 있도록).

| 백본 | 위치 | 입력 | 체크포인트 | torch | 픽스처 측정 (정답 ≈ (0°, 0°)) |
|---|---|---|---|---|---|
| **`head_pose`** (기본) | `backbones/head.py` | 헤드포즈 | 불필요 | ❌ | 고개 각도 그대로 |
| `mediapipe_geom` | `eye/` (보관) | 랜드마크 + 블렌드셰이프 + 헤드포즈 | 불필요 | ❌ | yaw +2.74°, pitch −2.77°, conf 0.67, **0.21 ms** |
| `l2cs` | `eye/` (보관) | face crop만 | `L2CSNet_gaze360.pkl` (95.8 MB) | ✅ | yaw +2.20°, pitch −0.23°, conf 0.98, **565 ms** |
| `gazetr` | `eye/` (보관) | face crop만 | `GazeTR-H-ETH.pt` (**없음**) | ✅ | — |

> 측정된 L2CS 설정 3종(565 / 228 / 195 ms) **모두 doc 7의 125 ms 예산을 초과합니다.**

**`head_pose`** (기본) — 얼굴이 향한 방향을 시선으로 씁니다. `gaze_yaw = head.yaw`, `gaze_pitch = head.pitch`이고
부호 규약도 헤드포즈와 같습니다(눈 기반 백본에 눈 편위 0을 넣었을 때와 같은 값). `confidence`는 화면 안에 있는
랜드마크 비율입니다. 헤드포즈가 측정되지 않은 프레임(PnP 실패: 각도 0 + 재투영 오차 ∞)은 정면으로 읽지 않고
예외로 거절해 `BACKBONE_FAILED`가 됩니다. `uses_eyes = False`라서 보정 게이지가 고개 고정·깜빡임 규칙을 적용하지 않고,
세션도 전처리의 눈 게이트(`EYES_CLOSED`·`CROP_FAILED`)로 프레임을 버리지 않습니다([§7-1](#7-1-전처리--visionpreprocess-doc-3-1) 무효 게이트 표 아래).
깜빡이거나, 대본을 읽으며 눈꺼풀을 내리거나, 고개를 돌려 눈이 안 보여도 고개 방향은 그대로이기 때문입니다.
조건 감시의 거리·위치도 눈 대신 얼굴 메시 거리로 잽니다(§7-8).

#### 왜 고개인가 — 눈 기반 백본 실측

노트북 웹캠(1280×720, 약 30 fps)에서 한 사람이 **고개를 고정한 채 눈만** 렌즈 → 화면 가운데 → 화면 아래 →
왼쪽 → 오른쪽을 보고, 이어서 **눈은 렌즈에 둔 채 고개만** 끄덕이고 돌리는 측정을 했습니다(단계마다 약 100프레임).
"구분"은 두 단계의 중앙값 차이를 단계 안 흔들림(MAD 기반 σ)으로 나눈 값이고, 4 이상이면 깔끔하게 갈립니다.

| 신호 | 렌즈 ↔ 화면 아래 (세로) | 왼쪽 ↔ 오른쪽 (가로) |
|---|---|---|
| MediaPipe 홍채 점 (눈꼬리 기준) | 이동 약 0.1 홍채 반지름 (기하상 예상 0.7~1.0) | 반응함 |
| 블렌드셰이프 `eyeLookDown` / `eyeLookIn·Out` | 약 2σ | 반응함 (고개를 돌리며 렌즈를 볼 때 r = −0.91) |
| 픽셀에서 찾은 홍채(어두운 영역) 중심 · 윗눈꺼풀 위치 | 2σ 이하 | 반응함 (약 5σ) |
| `mediapipe_geom` 최종 시선 | 구분 안 됨 (가운데와 아래가 같은 값) | 약 1.7σ |
| **고개 각도** (눈만 움직이라고 했는데도 고개가 따라간 회차) | 렌즈 → 가운데 → 아래 단계마다 약 6.5°, 단계마다 약 4σ | 7.5°, 약 13σ |

- **세로 방향 눈 움직임은 이 환경의 랜드마크로 거의 보이지 않습니다.** 렌즈를 보며 고개를 끄덕일 때 홍채 점은
  눈이 반대로 도는 것을 따라가지 않고 얼굴과 함께 움직였습니다.
- **`mediapipe_geom`의 고개 보정은 MediaPipe 홍채 깊이(z)를 씁니다.** 이 값이 실시간에서 홍채 반지름의 약 3배로
  나왔고(정지 픽스처에서는 1.0~1.5배) 고개 pitch와 r = 0.94로 함께 움직여, 고개 1°당 눈 각도를 약 2° 반대로 돌렸습니다.
  그래서 고개를 움직이면 최종 시선이 오히려 상쇄됐습니다. z를 상수(홍채 반지름 1배)로 바꾸면 같은 데이터에서
  가로 방향은 고개와 무관해졌지만, 세로 방향은 그대로 구분되지 않았습니다. 이 수정은 `vision/eye/`에 반영하지 않았습니다.
- 대본을 읽는 발표자는 보통 고개를 숙이고, 고개를 든 채 눈만 내려 점수를 맞추려는 경우는 이 제품의 목적과 맞지 않으므로
  **고개 방향을 판정 신호로 씁니다.** 사람마다 고개를 얼마나 움직이는지는 3점 보정이 기준점으로 잡습니다.

**`mediapipe_geom`** (보관) — 학습 가중치가 전혀 없는 순수 기하 + 블렌드셰이프 융합:

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

세션마다 **일회용 사용자별 분류기**를 만들고 세션과 함께 버립니다. `calibration.method`가 계열을 고릅니다.

| `method` | 분류기 | 보정 | 클래스 |
|---|---|---|---|
| **`reference`** (기본) | `references.ReferenceAnchorClassifier` | 3점 (렌즈 · 화면 가운데 · 대본) | CAMERA / SCREEN / BOTTOM / OTHER |
| `logistic` | `classifier.PerUserGazeClassifier` | 카메라 · 대본 2점 | CAMERA / BOTTOM |

두 계열은 같은 표면(`fit → (분류기, quality)`, `is_fitted`, `classes`, `decide`)을 가지며
`factory.fit_gaze_classifier`가 고릅니다. 세션은 분류기의 `classes`로 평활기를 맞춥니다.

#### 보정 절차: 세 지점을 편하게 바라봅니다

기본 `head_pose` 백본에서는 **평소 화면과 대본을 볼 때처럼** 렌즈 → 화면 가운데 → 대본을 봅니다. 각 지점을 볼 때의
고개 방향이 그 사람의 기준점이 되므로, 고개를 얼마나 숙이는지는 사람마다 달라도 됩니다.
눈 기반 백본(`vision/eye/`)을 고르면 세션의 `eye_based`가 참이 되고, 그때만 **고개는 고정하고 눈만** 움직이도록
안내와 게이지 규칙(아래 5·6번)이 바뀝니다.

**좋은 프레임 게이지** (`gauge.CalibrationGauge`, 세션의 `start_calibration_cue` / `offer_calibration_frame`):
큐가 정해진 시간이 아니라 **좋은 프레임이 `target_good_frames`(16)개 모이면** 끝납니다. 프레임은 아래 순서로 검사합니다.

| 순서 | 검사 | 거절 사유 |
|---|---|---|
| 1 | 얼굴 유효 (전처리 게이트) | `NO_FACE`, `EYES_CLOSED`, `OUT_OF_FRAME` … |
| 2 | 시선 값이 유한함 | `BACKBONE_FAILED` |
| 3 | 시선 신뢰도 ≥ `min_sample_confidence` (0.30) | `LOW_GAZE_CONFIDENCE` |
| 4 | 큐 시작 후 `cue_settle_ms`(500) 경과 — 그 전은 아직 시선 이동 중 | `SETTLING` (사용자 탓으로 집계하지 않음) |
| 5 | (눈 기반 백본만) 고개가 첫 큐의 자세에서 `max_head_deviation_deg`(6°) 이내 | `HEAD_MOVED` |
| 6 | (눈 기반 백본만) 이 큐의 눈 뜬 정도 중앙값 × 0.7 이상 (깜빡임 아님) | `BLINK` |
| 7 | (고개 방향 엔진, 기준 자세가 있을 때) 고개가 기준 자세에서 **큐 방향으로** 돌아 있음 — 아래 표 | `LOOK_HIGHER` / `LOOK_LOWER` / `OFF_TARGET` |
| 8 | 이 큐의 시선 중앙값에서 `outlier_k`(4) × 흔들림 이내 (딴 데 안 봄) | `OUTLIER` |

**큐 방향 확인 (7번)**: 화면 가운데 큐는 **고개 원 확인의 중심**([§7-12](#7-12-고개-원-확인--visionruntimesweeppy))을,
렌즈·대본 큐는 **화면 가운데 큐에서 확인된 자세**를 기준으로 삼습니다(화면 가운데를 아직 안 쟀으면 고개 원의 중심,
둘 다 없으면 이 검사는 꺼짐). 기준에서 돌린 각도 `(오른쪽, 위)`로 판정합니다.

**②에서 잰 기준을 ③에서 확인하고 보정에 반영** (`VisionSession.baseline_check`): 고개 원의 중심은 화면 속 내 얼굴을
보는 자세라서, 화면 가운데 큐와 같은 자세입니다. 그래서 고개 원을 했으면 화면 가운데 큐는 처음부터 재지 않고 **확인**합니다.

| 단계 | 하는 일 |
|---|---|
| 확인 | 좋은 프레임 `cue_confirm_frames`(8)개를 모아 그 중앙값을 고개 원의 중심과 비교 |
| 같으면 (`cue_confirm_deg` 4° 이내) | 고개 원 중심의 프레임(8개)을 화면 가운데 표본에 **합쳐** 기준점을 만들고 큐를 끝냄 — 16개 대신 8개만 보면 됨 |
| 다르면 | 고개 원 이후 자세나 자리가 바뀐 것이므로, 같은 큐에서 **16개를 새로 잼**. 렌즈·대본 확인의 기준도 이 새 자세로 바뀜 |

예: 고개 원을 한 뒤 방석을 깔아 모든 자세가 5° 높아졌다면, 고개 원의 중심으로 대본을 확인할 경우 대본 자세가 중심보다 2°밖에
낮지 않아 영영 채워지지 않습니다(시나리오 S24). 화면 가운데에서 확인·보정한 자세를 기준으로 쓰면 정상적으로 채워집니다.

| 큐 | 진행도가 오르는 조건 | 아니면 |
|---|---|---|
| CAMERA (렌즈) | 위로 `cue_min_up_deg`(2°) 이상 | `LOOK_HIGHER` (고개를 조금 더 들어 주세요) |
| BOTTOM (대본) | 아래로 `cue_min_down_deg`(3°) 이상 | `LOOK_LOWER` (조금 더 숙여 주세요) |
| SCREEN (화면 가운데) | 기준에서 `cue_screen_radius_deg`(7°) 이내 | `OFF_TARGET` |
| 모든 큐 | 옆으로 `cue_max_side_deg`(10°) 이내 | `OFF_TARGET` |

그래서 렌즈를 보라는데 고개를 그대로 두거나 다른 곳을 보면 **진행도가 오르지 않습니다.** 고개를 거의 움직이지 않는
사람이 같은 큐에서 계속 막히지 않도록, 웹 데모는 한 큐가 두 번 시간 초과되면 세 번째는 이 검사 없이 받습니다
(`startCue(cue, tMs, false)`). 눈 기반 백본에는 적용하지 않습니다(고개를 고정하는 방식이라).

나쁜 프레임은 게이지를 **멈추기만 하고 되돌리지 않습니다.** `cue_timeout_ms`(8초)가 지나면
`min_samples_per_class`(10)개 이상이면 완료, 아니면 `TIMED_OUT`이고 **가장 많았던 사유**와 안내 문구를 돌려줍니다.
같은 큐를 다시 시작하면 그 큐의 표본만 교체됩니다. 게이지 없는 `add_calibration_frame`도 그대로 남아 있습니다.

#### `reference`: 기준점 분류기

**기준점(anchor)** = 큐별 시선의 중앙값 `(yaw°, pitch°)`. **흔들림 σ** = 축별로 모든 큐를 합친
견고 추정 `max(sigma_min_deg, sigma_scale × 1.4826 × MAD)` — 정지 응시보다 실시간 시선이 더 흔들리므로
`sigma_scale`(1.25)로 넓히고 `sigma_min_deg`(1.5°)로 바닥을 둡니다. 큐별 σ를 따로 쓰지 않는 이유는
표본 16개로는 σ가 우연히 2배까지 달라져, 영역 크기가 운에 좌우되기 때문입니다.

**영역(soft box)**: 각 클래스는 시선 공간의 상자를 측정 잡음으로 흐린 분포입니다(축별 `Uniform[lo,hi] * N(0,σ²)`).

```
f(x) = [Φ((hi − x)/σ) − Φ((lo − x)/σ)] / (hi − lo)      (상자 폭이 0이면 가우시안)
```

| 클래스 | 영역 |
|---|---|
| CAMERA | 렌즈 기준점 한 점 (`camera_halfwidth_deg` > 0이면 그 반경의 상자) |
| SCREEN | 화면 사각형. 중심 = 화면 가운데 기준점, 반높이 = `max(|Δpitch|, |Δyaw|/a)`, 반너비 = `max(|Δyaw|, min(|Δpitch|·a, screen_max_halfwidth_deg))` (Δ = SCREEN − CAMERA, a = `screen_aspect`). 렌즈가 화면 위쪽 가장자리에 놓입니다 |
| BOTTOM | 대본 기준점 중심, 화면 상자의 `script_width_fraction`(0.6) × `script_height_fraction`(0.25) |
| OTHER | 어디서나 같은 값 `1 / (other_field_yaw_deg × other_field_pitch_deg)` |

각 분포는 적분이 1인 진짜 밀도라서 크기가 다른 영역끼리 공정하게 경쟁합니다: 점인 렌즈는 날카로워
자기 근처에서 이기고, 큰 화면 상자는 묽어서 더 구체적인 영역이 없는 곳에서만 이깁니다.
사전확률(`prior_*`)을 곱해 사후확률을 구하며, 계산은 로그 공간(`log_ndtr`, `logsumexp`)에서 합니다.
`screen_aspect`는 시선 공간의 **실효** 비율입니다 — 기하 백본은 위아래 시선을 압축하는 경향이 있어,
화면 좌우가 OTHER로 읽히면 가장 먼저 조정할 값입니다.

**눈맞춤 반경**: 렌즈가 화면 안쪽보다 이기는 반경은 `σ·√(2·ln(렌즈 최대 밀도 / 화면 밀도))`이며
`quality.camera_capture_radius_deg`로 보고됩니다. σ가 클수록(보정이 흔들릴수록) 넓어집니다.

**고개 이탈**: 고개가 보정 자세(보정 프레임 전체의 중앙값)에서 `head_away_yaw_deg`(35°) / `head_away_pitch_deg`(30°)
넘게 돌아가면 화면을 보고 있지 않으므로 시선 값과 무관하게 **OTHER**입니다
(`prior_other: 0`으로 OTHER를 끄면 `UNCERTAIN` + `HEAD_AWAY`).

**OTHER는 확실히 벗어났을 때만**: 사후확률에서 OTHER는 시선이 아래 화면 영역에서 `other_margin_deg`(6°) 이상
벗어났을 때만 경쟁합니다. 그 안쪽 — 렌즈보다 고개를 조금 더 들었거나, 화면 가장자리보다 조금 더 돌렸거나, 대본보다 조금 더
숙인 경우 — 에서는 OTHER가 빠지고 **가장 가까운 대상**(청중·화면·대본)이 이깁니다. 발표자는 고개를 계속 움직이는데,
고개 방향 보정에서는 영역이 몇 도 폭밖에 안 되어 영역 가장자리에서 2σ(3~4°)만 벗어나도 OTHER가 되던 문제를 막습니다.
또 화면 영역은 좌우로 최소 `screen_min_halfwidth_deg`(10°)입니다. 렌즈·화면 가운데·대본을 볼 때 고개의 좌우 차이가
1~2°뿐이거나 화면이 렌즈로 합쳐지면(SCREEN_MERGED) 영역 폭이 흔들림 크기(±1.5°)로 쪼그라들어, 화면 옆을 보려고
고개를 조금만 돌려도 밖으로 읽혔기 때문입니다. 반대로 좌우 반폭은 최대 `screen_max_halfwidth_deg`(12°)입니다.
렌즈를 볼 때 고개를 많이 드는 사람은 세로 간격 × 화면 비율로 잡은 화면이 한쪽 20° 넘게 넓어져, 옆을 확실히 봐도
"화면"으로 읽혔기 때문입니다. 결과적으로 보통의 보정에서 렌즈 위로 약 8°, 옆으로 약 19~20° 넘게 벗어나야 OTHER이고,
더 크게 돌리면 고개 이탈 규칙(35°/30°)도 OTHER로 잡습니다. 전체 기준은 [§7-13](#7-13-판별-기준과-시나리오)에 표로 모았습니다.

**OTHER 방향** (`gaze_offset`, `direction_of`): 화면 영역 = 모든 클래스 상자를 감싸는 사각형을 σ만큼 넓힌 것
(좌우는 최소 `screen_min_halfwidth_deg`, `screen_region`). 시선이 그 밖에 있으면 벗어난 양을 `offset_deg = (오른쪽°, 위°)`로, 안에 있으면 `(0, 0)`으로 냅니다.
따로 `aim_deg = (오른쪽°, 위°)`는 화면 가운데 기준점(없으면 렌즈와 대본의 가운데)에서 고개가 향한 방향으로, 영역 안에서도 항상 나옵니다(`aim_offset`) — 실시간 화면의 좌우·상하 값입니다.
방향은 이 벡터의 각도를 45° 칸으로 나눈 8방향입니다.

```
각도 = atan2(위, 오른쪽)  →  GAZE_DIRECTIONS[floor((각도 + 22.5) / 45) mod 8]
GAZE_DIRECTIONS = RIGHT, UP_RIGHT, UP, UP_LEFT, LEFT, DOWN_LEFT, DOWN, DOWN_RIGHT
```

좌우는 **발표자 기준**입니다. 원본 프레임에서 yaw > 0은 이미지 오른쪽, 즉 발표자의 **왼쪽**이라
가로 부호를 뒤집습니다(§6). 그래서 `LEFT`는 발표자 자신의 왼쪽이고, 거울처럼 보이는 미리보기에서는 화면 왼쪽입니다.
방향은 OTHER 판정에만 붙습니다. 고개 이탈로 OTHER가 됐는데 시선 값은 화면 안에 있는 경우
(눈 기반 백본에서만 생김)나, 화면 영역 안의 빈 틈에서 OTHER가 이긴 경우에는 `direction`이 `None`입니다.

**일부 기준점만 있을 때**: CAMERA+BOTTOM만 있으면 CAMERA / BOTTOM / OTHER 3클래스로 동작합니다
(기존 2큐 데이터셋으로 평가할 때). CAMERA만 있으면 학습하지 않습니다.
SCREEN 표본이 일부만 있으면(1 ≤ n < 10) 조용히 빼지 않고 `NOT_ENOUGH_SAMPLES`로 실패합니다.

**품질 사다리** (`assess_reference_calibration`, 가장 구체적인 사유가 먼저):

| 순서 | 조건 | 사유 |
|---|---|---|
| 1 | CAMERA·BOTTOM < 10개, 또는 SCREEN이 1~9개 | `NOT_ENOUGH_SAMPLES` |
| 2 | 모든 시선 값의 분산이 0 (시선이 전혀 안 움직임) | `DEGENERATE_FEATURES` |
| 3 | 렌즈–대본 분리(σ 단위) < `min_anchor_separation`(4) | `CLASS_NOT_SEPARABLE` |
| 4 | 다른 기준점 쌍 분리 < 4 (힌트에 쌍 이름) | `CENTROIDS_TOO_CLOSE` |
| 5 | 어떤 기준점이 자기 자리에서 자기 클래스로 0.75 미만 판정 (영역이 너무 묽음) | `ANCHOR_AMBIGUOUS` |
| 6 | 한 표본씩 빼고 다시 만든 모델의 정확도 < 0.85 (UNCERTAIN은 오답) | `LOW_LOO_ACCURACY` |

세로 순서(CAMERA > SCREEN > BOTTOM)가 어긋나면 실패가 아니라 `INVERTED_PITCH:` 경고입니다.
`quality`에는 `anchors_deg`, `sigma_deg`, `pair_separation`, `camera_capture_radius_deg`, `placement`가 함께 담깁니다.

**화면을 정면으로 합치기** (`merge_inseparable_screen`, 기본 true): 고개로 판정하면 렌즈에서 화면 가운데로
고개를 거의 움직이지 않는 사람이 있습니다. 그러면 SCREEN 기준점이 렌즈와 겹쳐 4·5·6번 사유로 실패합니다.
이때 SCREEN 표본을 빼고 CAMERA / BOTTOM / OTHER로 다시 평가해 통과하면, 실패 대신 그 모델을 쓰고
화면을 보는 것도 **CAMERA(정면)** 로 판정합니다. `quality.warnings`와 안내 문구에 `SCREEN_MERGED`가 남고,
`pair_separation`에는 원래 쌍별 분리 값이 그대로 남아 이유를 보여줍니다. 렌즈와 대본이 구분되지 않는
경우(3번)는 합쳐도 고칠 수 없으므로 그대로 실패합니다.

**다시 맞추기** (`VisionSession.begin_reanchor`): 실시간 중 사용자가 렌즈를 약 1초(`reanchor_frames` 8개) 보면
그 중앙값을 새 CAMERA 기준점으로 삼아 **모든 기준점을 같은 만큼 이동**합니다(영역 모양은 유지).
이동이 `reanchor_max_shift_deg`(8°)를 넘으면 렌즈를 본 게 아니라고 보고 거절합니다.
고개 이탈 기준 자세와 조건 감시의 기준 장면도 지금 자세로 옮깁니다. 기준점 방식만 지원합니다.

#### `logistic`: doc 5-3 LR (비교 실험용)

카메라 응시 + 대본 응시 표본으로 로지스틱 회귀를 학습합니다. SCREEN 표본은 쓰지 않습니다
(`quality.split_by_label`이 CAMERA·BOTTOM만 남깁니다 — 예전에는 그 밖의 라벨을 전부 BOTTOM으로 넣었습니다).

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

**doc 5-4 판정 규칙** (두 계열 공통, `decide()` 분기 순서 그대로):

1. `face_valid` 아님 / gaze 없음 / 비유한 → `UNCERTAIN`, `p=0.5/0.5`, `uncertain_reason = invalid_reason` (없으면 `BACKBONE_FAILED` 또는 `NO_FACE`)
2. (기준점 방식만) 고개 이탈 → `OTHER`
3. `p_max < 0.70` → `UNCERTAIN`, reason `LOW_CONFIDENCE`
4. `margin`(1위 − 2위 확률) `< 0.20` → `UNCERTAIN`, reason `LOW_MARGIN`
5. 그 외 → 가장 높은 클래스 (동점이면 `STATE_CLASSES` 순서 CAMERA → SCREEN → BOTTOM → OTHER, 즉 CAMERA가 이김)

`GazeDecision.probs`에 클래스별 확률 전체가 담기고, `p_camera`/`p_bottom`은 그중 두 값입니다
(로지스틱은 `probs=None`이고 두 값이 분포 전체입니다).

> ⚠️ **기본값에서 LOW_MARGIN 분기는 도달 불가능합니다.** 클래스 수와 상관없이 `margin ≥ 2·p_max − 1`이므로
> `p_max ≥ 0.70`이면 항상 `margin ≥ 0.40`입니다. `margin_threshold`를 0.40 위로 스윕할 때만 발동합니다.
> 두 분기를 모두 유지하는 이유는 스윕이 두 임계값을 독립적으로 움직이기 때문입니다.

> ⚠️ `GazeDecision.face_valid`는 "얼굴이 보였는가"가 아니라 **"이 프레임으로 판정할 수 있었는가"** 입니다.
> 얼굴은 멀쩡한데 백본이 예외를 던진 프레임은 `face_valid=False` + `uncertain_reason=BACKBONE_FAILED`가 됩니다.

**logistic 품질 게이트 (doc 5-2)** — 다섯 개, 근본 원인 순서대로 가장 구체적인 사유를 냅니다
(기준점 방식의 사다리는 위 표):

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
> **트리 전체에 호출자가 하나도 없습니다.** 기준점 분류기에는 저장 기능 자체가 없습니다.
> 매 세션이 처음부터 다시 보정하고, 모델은 프로세스와 함께 사라집니다 — 아무것도 저장하지 않는다는 제품 원칙과 일치합니다.

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

**K클래스 일반화** (`TemporalSmoother(cfg, classes=...)`, `set_classes`): 위 설명은 2클래스 기준이며,
평활기는 분류기가 판정하는 클래스 집합을 그대로 다룹니다(세션이 `finish_calibration`에서 맞춤).

- **CAMERA는 나머지(complement)** 입니다: 다른 클래스만 EMA·투표로 평활하고 CAMERA 점수는 `1 − 나머지 합`.
  2클래스에서는 이것이 원래의 `1 − score_bottom`과 **비트 단위로 같습니다** — `tests/_legacy_smoother.py`
  (변경 전 평활기 사본)와 무작위 스트림 30종을 비교하는 골든 테스트가 이를 고정합니다.
- 기권 프레임의 중립값은 `1/K`(2클래스면 0.5). 모든 진입 임계값이 `1/K`보다 크므로 기권은 여전히 arm 하지 못합니다.
- 진입 후보 우선순위: BOTTOM → CAMERA → SCREEN → OTHER. 클래스별 임계값 `enter_*_threshold`(0.60),
  dwell `to_screen_dwell_ms`(400) · `to_other_dwell_ms`(800). 화면 밖은 가장 긴 증거를 요구합니다 —
  생각하느라 잠깐 시선을 돌리는 것은 정상이고 피드백할 일이 아니기 때문입니다.
- 클래스가 셋 이상이면 디버그 dict에 `smoothed_probs`가 추가됩니다.
- 2클래스 평활기에 다클래스 판정을 넣으면 SCREEN/OTHER 확률이 조용히 사라지므로, 클래스 집합은 반드시 분류기에서 가져옵니다.

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

**anchors 모드** (`placement_from_anchors`, 런타임 기본 흐름): 따로 배치 단계를 두지 않고
**보정의 렌즈·화면 가운데 기준점**으로 판정합니다(`VisionSession.estimate_placement`, SCREEN 큐 직후).

- 한 축만 보지 않고 **두 성분(Δpitch, Δyaw)을 모두** 봅니다. 우세 축이 다른 축의
  `anchor_min_axis_dominance`(2.5)배 이상이어야 판정하므로, TOP은 "위쪽이면서 가운데"
  (`|Δyaw| ≤ 0.4·|Δpitch|`)를 뜻합니다. 화면 위 구석의 카메라는 TOP으로 통과하지 않고 `AMBIGUOUS_AXIS`입니다.
- 거부 순서: `NOT_ENOUGH_SAMPLES` → `TARGETS_NOT_SEPARATED`(두 중앙값이 σ 단위 1 미만) →
  `AMBIGUOUS_AXIS` → `DISPLACEMENT_TOO_SMALL` → 판정. 부호 규칙은 세 모드가 같은 함수(`_verdict_from_deltas`)를 씁니다.

> ⚠️ 판정을 **막을지**는 `runtime.policy.gate_placement`가 정합니다: strict 모드(제품 기본)는 TOP이 아니면
> 대본 보정 전에 막고, advisory 모드(연구용·`collect`)는 기록만 합니다. 세션 자체는 보고만 합니다.
> 기본 `head_pose`에서는 **판정 불가(`INCONCLUSIVE`)는 막지 않습니다**(`block_inconclusive=False`): 렌즈와 화면 가운데를
> 같은 고개 자세로 보는 사람이 있고, 그것은 카메라 위치에 대해 아무것도 말해주지 않기 때문입니다.
> 아래·옆으로 **판정된** 배치는 그대로 막습니다.
> `supported=True`가 되는 것은 `TOP` 하나뿐이고 `PlacementCheckResult.ok`는 `supported`의 별칭입니다.
> yaw 부호는 **미러링되지 않은 원본 프레임**을 전제합니다 — 셀피 뷰를 먹이면 `SIDE_LEFT`/`SIDE_RIGHT`가 조용히 뒤바뀝니다.

### 7-6. 버전 스탬핑 — `vision/runtime/version.py` [doc 15 / 18]

| 상수 | 값 | 의미 |
|---|---|---|
| `MODEL_VERSION` | `gaze_v1.1.0` | `GazeStateEvent.model_version`의 와이어 값. **vision 슬라이스 전체**를 지칭 |
| `CLASSIFIER_VERSIONS` | `reference` → `reference_anchor_v1`, `logistic` → `per_user_lr_v1` | 추정기 계열 버전. `AiVersion.gaze_classifier`에는 **실제로 쓴 `method`**의 값이 기록됩니다 (파일 포맷 버전 `SCHEMA_VERSION`과 **별개**) |
| `CLASSIFIER_VERSION` | `reference_anchor_v1` | 기본 설정이 고르는 계열 |
| `TEMPORAL_RULE_VERSION` | `gaze_temporal_v1.1` | 평활화 규칙 버전 (v1.1에서 K클래스 추가, 2클래스 규칙은 그대로) |

`config_hash` = `sha256(json.dumps(config.to_dict(), sort_keys=True))[:12]`.
읽는 섹션만이 아니라 **병합된 전체 설정**을 커버하므로, `calibration.yaml`만 건드린 스윕도 해시를 움직입니다 —
doc 18의 "같은 해시 = 같은 숫자" 주장이 성립하는 근거입니다.

`code_commit` 해석 순서: `GAZE_TRACKING_GIT_COMMIT` 환경변수 → 캐시 → `git rev-parse --short=12 HEAD`
(+ `git status --porcelain --untracked-files=no`가 비어 있지 않으면 `-dirty` 접미사) → `unknown`.

> git이 없는 환경(컨테이너·휠)에서는 `code_commit`이 `"unknown"`이 됩니다.
> 그럴 때는 `GAZE_TRACKING_GIT_COMMIT`를 **빌드에서** 설정하세요(수작업으로 설정하면 스탬프가 거짓말을 시작합니다).

### 7-7. 준비 점검 — `vision/runtime/preconditions.py`

모델은 **한 명이 화면 위 가운데 웹캠을 마주 보는** 장면을 전제합니다. 이것이 깨지면 측정은 시끄럽게 실패하지
않고 조용히 틀립니다(다른 사람의 시선, 찾지 못하는 얼굴, 역광 얼굴). `PreconditionChecker`가 보정 전 미리보기
프레임에서 판정합니다(`VisionSession.check_preconditions`).

**모든 기준은 이상적인 자세가 아니라 "측정에 필요한 최소 조건"입니다** — 얼굴을 찾아 랜드마크를 따라갈 수 있으면 통과합니다.

| 검사 | 기준 (`preconditions.yaml`) | 사유 |
|---|---|---|
| 얼굴 | 얼굴이 검출됨 | `NO_FACE` |
| 한 명 | 별개의 두 번째 얼굴(§7-1)이 주 얼굴 면적의 0.25를 넘는 상태가 0.5초 이상이면 실패 (한두 프레임 오검출은 무시) | `MULTIPLE_FACES` |
| 가운데 | 얼굴 중심이 화면 중심에서 가로·세로 0.35 이내 (얼굴이 화면 안에 넉넉히 있으면 됨) | `OFF_CENTER` |
| 너무 멀지 않음 | 얼굴 면적 ≥ 화면의 1.2% — 전처리가 얼굴을 버리는 인식 한계(`preprocess.min_face_area_ratio` 1%) 바로 위 | `TOO_FAR` |
| 너무 가깝지 않음 | 얼굴 높이 ≤ 화면의 70% — 고개를 조금만 움직여도 화면 밖으로 나가는 크기 | `TOO_CLOSE` |
| 정면 | 고개 yaw ≤ 30°, pitch ≤ 40° | `FACING_AWAY` |
| 밝기 | 얼굴 평균 휘도 ≥ 40 | `TOO_DARK` |
| 역광 | 배경/얼굴 휘도 ≤ 2.5 | `BACKLIT` |
| 속도 | 분석 FPS ≥ 5 (1초 판정에 필요한 4프레임 + 여유) | `LOW_FPS` |

- **홍채 크기 하한(7 px)은 눈 기반 백본에만** 적용합니다(`PreconditionChecker(..., eye_based=True)`, 세션이 백본에 따라 정함).
  눈동자의 작은 회전을 읽으려면 홍채에 픽셀이 필요하지만, 기본 `head_pose`는 홍채를 읽지 않으므로 얼굴만 찾으면 됩니다.
- 거리(cm)는 판정에 쓰지 않는 **표시용 측정값**입니다.

- **PASS**: 모든 검사가 `hold_ms`(1초) 연속 유지. **REJECT**: 한 검사가 `reject_after_ms`(3초) 연속 실패. 그 외 **RETRY**.
- 깜빡임(`EYES_CLOSED`)처럼 장면과 무관한 무효 프레임은 유지 시간을 끊지도 늘리지도 않습니다.
- 거리는 MediaPipe가 얼굴 전체 메시를 맞춰 구한 카메라↔얼굴 거리(`HeadPose.depth_proxy` × 초점거리,
  `head_distance_cm`)입니다. 눈이 필요 없고 고개를 돌려도 변하지 않습니다. 그 값이 없는 프레임만
  `초점거리(px) × 1.17 cm / 홍채(px)`로 대신합니다. 둘 다 수직 FOV 63° 가정에서 나오므로,
  시야각이 다른 웹캠에서는 거리 값이 비례해 어긋납니다.
- 얼굴 크기로 거리를 보는 이유: MediaPipe 얼굴 검출은 화면 전체를 작게 줄여서 얼굴을 찾으므로, 찾을 수 있는지는 얼굴이
  화면에서 차지하는 비율로 정해집니다. 1.2%는 16:9 화면에서 얼굴 높이 약 16%, 63° FOV에서 대략 1 m입니다.
- FPS 하한이 8이 아닌 이유: 30 · 15 FPS 카메라를 8 FPS로 샘플링하면 133 ms 격자(7.5 FPS)에 놓이는데, 이는 정상입니다.
- pitch 한계가 yaw보다 넓은 이유: 노트북 웹캠은 눈높이보다 아래에 있어, 렌즈를 보고 있어도 고개 pitch가
  16~24°(턱을 든 방향)로 측정됐습니다.
- REJECT가 실제로 막는지는 `preconditions.strict`(기본 true)와 `runtime.policy`가 정합니다.
- 모든 한계는 실측 전 **초기값**이며, 측정할 수 있는 사용자를 막지 않도록 인식에 필요한 최소 조건으로 잡았습니다.

### 7-8. 조건 감시 — `vision/runtime/condition.py`

보정이 끝나면 수락된 보정 프레임의 **장면 중앙값**(얼굴 중심 · 면적 · 얼굴 메시 거리 · 홍채 px · 밝기)과 **큐별 고개 자세**가 기준이 되고,
`ConditionMonitor`가 실시간 프레임마다 그 기준과 비교합니다. 벗어나도 촬영을 멈추지 않고 `reliability`를 낮춥니다.

> **신뢰도는 "지금 판정값이 틀렸거나 비어 있을 가능성"만 나타냅니다.** 그래서 신뢰도 항목은 모두 **판정 각도가
> 틀어지거나 비는 경로**가 있어야 합니다. 원인이 아니라 결과를 잽니다.
> 기본 `head_pose` 백본에서는 고개 방향이 곧 시선이므로, 위나 옆을 크게 보는 것은 판정할 **방향**(OTHER + `direction`)이지
> 측정 이상이 아닙니다. 그래서 이 엔진에서는 아래 표의 **고개 신호를 쓰지 않습니다**(`ConditionMonitor(..., head_is_gaze=True)`).
> 고개 신호는 고개를 고정하고 눈만 움직이는 **눈 기반 백본**에서만 켜집니다(그때는 고개가 돌면 눈 시선 추정이 틀어지므로).

| 신호 | 경고 → 실패 경계 (`condition.yaml`) | 이슈 |
|---|---|---|
| 고개 (눈 기반 백본만) | **가장 가까운** 보정 자세(렌즈·화면·대본 큐별)에서 12° → 30° | `HEAD_TURNED` |
| 위치 (옆·위아래) | 보정 자리에서 움직인 만큼의 **판정 오차 각도** `atan(이동 / 거리)` — 경고 = 한계의 절반, 한계 = 아래 기준선 | `OFF_CENTER` |
| 거리 | 가까워지거나 멀어져서 바뀐 **가장 먼 큐의 각도** `\|atan(tan(폭) × D0/D1) − 폭\|` — 같은 경계 | `TOO_CLOSE` / `TOO_FAR` |
| 얼굴 크기 (인식 한계) | 멀어져 얼굴 면적이 1.6% → 1.1%로 줄거나(인식 한계 1% 직전), 가까워져 얼굴 높이가 70% → 85%로 커질 때 | `TOO_FAR` / `TOO_CLOSE` |
| 고개 방향 흔들림 | 최근 2초 동안 고개 각도의 **프레임 간 흔들림**(아래). 한계 = 보정 지점 사이 가장 좁은 간격의 절반(3~6°), 경고 = 그 절반 | `NOISY_TRACKING` |
| 판정 공백 | 최근 3초 중 판정한 프레임 비율 90% → 50% | `LOW_VALID_RATIO` |

**고개 방향 흔들림** (`head_jitter_deg`): 고개 각도의 2차 차분 `x[i] − 2·x[i−1] + x[i−2]`은 일정한 속도로 돌리는 움직임을
지우고 프레임마다 튀는 값만 남깁니다. 흔들림 σ는 `1.4826 × 중앙값|차분| / √6`(축별, 큰 쪽)이고, 중앙값이라 돌리기 시작하거나
멈추는 몇 프레임에는 흔들리지 않습니다. 250 ms 넘게 벌어진 프레임은 잇지 않습니다. 실제 MediaPipe로 정지 얼굴을 재면 0.06°였고
(헤드리스 Chrome 실행 검사), 보통 보정의 경고 기준은 1.75°입니다.

**신뢰도에 넣지 않는 것** (원인이지 결과가 아님):

- **조명 변화** — 밝기가 바뀌어도 랜드마크가 그대로면 판정값은 그대로입니다. 어두워져 랜드마크가 흔들리면 **흔들림**으로,
  얼굴을 놓치면 **판정 공백**으로 이미 잡힙니다. 준비 점검의 "너무 어두움 / 역광"(얼굴을 찾을 수 있는가)은 그대로 둡니다.
- **다른 사람** — 화면에 다른 사람이 있어도 측정하는 것은 계속 발표자의 얼굴입니다(주 얼굴 추적, §7-1). 다른 사람이 측정을
  가로채면 **얼굴 바뀜(FACE_REPLACED)** 으로 잡힙니다. 별개의 두 번째 얼굴(주 얼굴의 0.35 이상, 1초 이상)은
  **참고 신호**(`ConditionState.notices`의 `SECOND_FACE`)로만 알리고 신뢰도와 `SESSION_CONDITION` 이벤트에는 넣지 않습니다.

- 신호마다 경고 경계에서 1.0, 실패 경계에서 `fail_reliability`(0.3)로 **선형** 하락하고,
  **reliability = 최솟값**입니다. 평균이 아니라 최솟값이라 `issues`가 항상 범인을 지목합니다.
  측정할 수 없는 신호(거리를 모르는 프레임 등)는 1.0으로 칩니다.
- **심각(severe)**: 얼굴이 `face_lost_ms`(1.5초) 이상 사라짐(`FACE_LOST`), 측정 중인 얼굴이 **두 프레임 사이에**
  화면의 30% 넘게 튀거나 면적이 2배 넘게(또는 절반 미만으로) 바뀐 뒤 0.5초 동안 보정 자리로 돌아오지 않음(`FACE_REPLACED` —
  다른 검출이 끼어든 것이지 사람이 움직인 것이 아님. 천천히 멀어지거나 옆으로 가는 것은 아무리 멀어도 해당 없고, 그것은 아래 위치 기준선이
  잽니다. 보정 직후 첫 프레임은 보정 때 얼굴과 비교),
  또는 **처음 위치에서 한계를 넘게 움직인 상태가 `drift_confirm_ms`(2초) 지속(`MOVED_TOO_FAR`)**.
  이때 reliability는 0이고, 세션이 판정을 `UNCERTAIN`(`face_valid=False`, 사유 = 이슈)으로 강제합니다 — **측정 불가**.

**위치 기준선 (처음 위치에서 얼마나 달라졌나)**: 기준점은 보정할 때 앉은 자리에서 잰 고개 각도입니다. 고개 방향으로
판정하므로 머리가 옆·위로 Δ cm 움직이면 같은 렌즈를 봐도 고개 각도가 `atan(Δ / 거리)`만큼 달라지고,
가까워지거나 멀어지면 렌즈·대본까지의 각도가 바뀝니다. 그래서 움직임을 **판정 오차 각도**로 바꿔 잽니다.

| 항목 | 계산 |
|---|---|
| 거리 | **얼굴 메시 거리**(MediaPipe가 얼굴 전체 모델을 맞춰 구한 값, 63° FOV) — 눈이 가려지거나 고개를 돌려도 변하지 않음. 보정 때 `D0`, 지금 `D1`. 지금 프레임이나 보정에 그 값이 없을 때만 둘 다 홍채 지름 1.17 cm로 계산(한쪽만 바꾸면 두 추정치의 차이가 이동으로 읽히므로) |
| 이동(cm) | 얼굴 중심의 픽셀 이동 × `D1` / 초점거리, 그중 **고개 회전으로 생긴 부분은 뺌**(회전 중심이 얼굴 뒤 `head_radius_cm` 8 cm라 고개만 돌려도 얼굴이 화면에서 `8 × sin(각)` cm 밀림) |
| 오차 | `max(atan(이동 / D1), \|atan(tan(폭) × D0/D1) − 폭\|)`, 폭 = 화면 가운데에서 가장 먼 큐까지의 각도 |
| 한계 | **보정 지점 사이 가장 좁은 간격**(`drift_fail_share` 1.0) — 그만큼 어긋나면 렌즈를 봐도 옆 대상으로 읽혀 측정값의 뜻이 바뀜. 6°~12°로 제한 |
| 경고 | 한계의 60%(`drift_warn_share` 0.6)부터 신뢰도가 선형으로 낮아짐 |

일부러 느슨하게 잡았습니다. 발표자는 자리에서 자세를 바꾸고, 분류기도 영역 밖 몇 도는 가장 가까운 대상으로 받아 주므로
(`other_margin_deg`), 측정값의 뜻이 바뀔 만큼 움직였을 때만 멈춥니다.
예: 렌즈·화면·대본이 7° 간격이면 한계 7°(55 cm에서 옆으로 약 6.8 cm), 경고는 4.2°(약 4 cm)부터입니다. `ConditionState.drift`가 이동(cm, 발표자 기준
오른쪽·위·가까워짐)과 오차·경고·한계 각도를 담아, 데모가 "처음 위치에서 오른쪽 3.2 cm · 판정 오차 1.8° / 한계 3.5°"처럼 보여 줍니다
(`SESSION_CONDITION` 이벤트의 4키는 그대로). 거리를 모르는 프레임은 재지 않습니다(벌점 없음).

**왜 홍채가 아니라 얼굴 메시인가**: 홍채 크기로 거리를 재면, 대본을 보려고 고개를 숙이거나 옆·위를 볼 때 눈꺼풀이
홍채를 가리거나 눈이 비스듬해져 홍채가 작게(또는 추정치로) 읽힙니다. 그러면 자리에 그대로 있어도 "멀어졌다"로
읽혀 신뢰도가 떨어지고, 심하면 측정 불가가 됩니다. 고개 방향 엔진은 눈을 읽지 않으니 거리도 눈 없이 재는 것이 맞습니다.
얼굴 메시 거리는 얼굴 전체를 강체로 맞춘 결과라 고개를 돌려도 그대로입니다.
- 방출: 처음, 이슈·심각 여부가 바뀔 때, 신뢰도가 0.1 이상 움직일 때, 그리고 2초마다(`SESSION_CONDITION`).
- 눈 기반 백본에서 고개를 가장 가까운 큐 자세와 비교하는 이유: 큐 자세들이 거의 같아 한 자세와 비교하는 것과 같고,
  어느 큐를 보던 중이든 같은 기준이 됩니다.
- 다시 맞추기([§7-3](#7-3-사용자별-캘리브레이션--visioncalibration-doc-5))가 성공하면 기준 장면을 지금 자세로 옮기고,
  큐별 고개 자세도 렌즈 자세가 움직인 만큼 함께 옮깁니다.
- **움직임을 판정에서 되돌려 보정하지는 않습니다** — 이동량을 알기 때문에 고개 각도를 되돌릴 수도 있지만,
  실측으로 검증한 뒤에 넣기로 하고 지금은 알리고(신뢰도·이동 표시) 한계를 넘으면 측정 불가로 멈춥니다.
  원래 자리로 돌아오거나 다시 맞추기를 하면 풀립니다.

### 7-9. 적응형 깜빡임 — `vision/eye/blink.py`

> **눈 기반 백본에서만 동작합니다.** 기본 `head_pose`는 눈을 읽지 않으므로 세션이 이 게이트를 보정하지 않고,
> 보정되지 않은 게이트는 아무 프레임도 건드리지 않습니다. 전처리의 고정 기준(EAR 0.12) 깜빡임 판정은 그대로입니다.

전처리의 깜빡임 기준(EAR 0.12)은 모든 사람에게 같은 값입니다. 눈이 가는 사람은 반쯤 감아도 통과하고,
그 프레임은 눈꺼풀에 가린 홍채 때문에 **확신에 찬 아래 시선**으로 읽힙니다.
`AdaptiveBlinkGate`는 보정 후 실시간 프레임에서 **이 사람의 눈 뜬 정도**를 기준으로 판단합니다.

```
기준 = min(최근 정상 프레임 중앙값, 대본(BOTTOM) 큐의 중앙값)
깜빡임 ⇔ EAR < max(0.12, blink_ratio(0.7) × 기준)
```

**대본 큐 값을 함께 쓰는 이유**: 아래를 볼 때는 위 눈꺼풀이 따라 내려와 EAR이 작아집니다.
카메라를 볼 때의 값만 기준으로 삼으면 대본을 읽는 프레임을 전부 깜빡임으로 지워,
제품이 잡아야 할 BOTTOM을 놓칩니다. 깜빡임 프레임은 `EYES_CLOSED`로 표시되어 분류기에 가지 않습니다.

---

### 7-10. 브라우저 포팅 — `local/web`

프론트엔드는 시선 판정을 **브라우저 Web Worker 안에서만** 하고, 카메라 영상을 서버로 보내지 않습니다.
그래서 같은 엔진을 TypeScript로 옮겼습니다(`web/src/engine/`). 범위는 기본 경로 전체입니다.
고개 방향 백본, 준비 점검, 고개 원 확인(`sweep.ts`), 게이지, 기준점 분류기(SCREEN 합치기 포함), OTHER 방향, 배치 판정,
조건 감시, 다시 맞추기, 에이전트 증거(`evidence.ts`)가 들어갑니다.

- **값의 출처**: 임계값은 Python 설정에서 생성한 `web/src/engine/defaults.ts`를 씁니다.
  `tests/test_web_engine_sources.py`가 이 파일과 기준 답이 낡았는지 검사합니다.
- **동치**: Python 코드가 만든 기준 답(`web/test/fixtures/parity.json`)과 TS 결과를 1e-9~1e-12 안에서 비교합니다(136개).
  에이전트 증거는 1초 기록, 창 통계, 코치 이슈, 테이크 요약, 이전 테이크 차이, 개입 효과까지 **키 이름까지 같게** 비교합니다.
  고개 원 확인은 빠른 움직임·얼굴 손실·자세 미측정·멈춤·시간 초과가 섞인 프레임 열로 매 프레임 상태를 비교합니다.
  실제 브라우저에서 테스트 영상의 고개 각도가 yaw 6.9°·pitch −4.4°로, Python(6.8°·−4.8°)과 같게 나왔습니다.
- **프론트엔드 계약**: 출력 키가 Python `to_dict()`와 같아서 프론트엔드의 `aiAdapter.ts`가 그대로 변환합니다.
  4분류는 프론트엔드의 3구역으로 바뀝니다. CAMERA → CAMERA, SCREEN·BOTTOM → BOTTOM, OTHER → UNCERTAIN(설정 가능)입니다.
  프론트엔드 파일을 읽기 전용으로 불러와 타입 검사와 실행 검사를 합니다.
- **카메라 화면 모듈**(`web/src/camera/`, `GazeCameraView`): AI 파트가 맡는 것은 카메라와 인식이고, 전체 화면과 그 바깥 페이지는
  프론트엔드가 맡습니다. 그래서 카메라 화면 안쪽만 모듈 하나로 만들었습니다. 프론트엔드가 준 상자를 채우고(상자 크기를 따라 배치가
  바뀌고, 스타일은 `.gzc` 아래로만 적용), 카메라 스트림을 받아 영상과 그 안에 뜨는 것을 모두 그립니다. 얼굴을 따라다니는 원,
  3D 십자선과 코끝 3D 화살표, 보정 과녁(렌즈·대본 띠), 안내 카드와 게이지, 결과 카드, 실시간의 "지금 보는 곳"과 1초 판정 테두리입니다.
  Worker를 직접 띄우고, 준비 점검 → 고개 원 → 3점 보정 → 실시간을 이 상자 안에서 이어 갑니다.
  결과는 이벤트로 나갑니다. `calibrated`는 보정 품질, 저장할 모델, 카메라 위치, 각 단계의 측정값을 담고, `decision`은 실시간 프레임 판정입니다.
  저장해 둔 모델로 바로 실시간을 시작할 수도 있습니다(`useCalibration`). 쓰는 법과 이벤트 목록은 `local/web/README.md`에 있습니다.
- **로컬 데모**: `cd local/web && npm install && npm run dev` → `http://localhost:5180`. 데모 페이지는 **프론트엔드 페이지의 대역**입니다.
  카메라 화면 모듈을 창 전체 상자에 띄우고(`?box`면 프론트엔드 미리보기처럼 16:9 카드), 시작 버튼에서 카메라 권한(프론트엔드와 같은 640×480)과
  전체 화면을 켭니다. 실시간에서는 모듈 이벤트만으로 오른쪽 개발 패널을 그립니다. 판정 보류의 **이유**, FE 1초 판정에서 '다른 곳'이 판정 불가로
  세어진다는 안내, **처음 위치에서 얼마나 움직였는지**, 화면 가운데 기준 **좌우·상하 고개 방향 값과 판별 기준 지도**, 클래스별 확률,
  신뢰도와 **고개 방향 흔들림(°)**, 신뢰도를 깎지 않는 **참고 신호**(예: 다른 사람이 보임), 코치에게 가는 시선 신호와 테이크 요약,
  `calibrated` 이벤트와 에이전트 입력 JSON이 나옵니다. 1초 판정은 프론트엔드의 `TemporalVoter`로 내고 `setZone()`으로 테두리에 돌려줍니다.
  보정 중 안내는 앞과 같습니다. 화면 가운데 단계는 고개 원에서 잰 정면 기준을 확인하고("정면 기준을 확인하고 있어요",
  다르면 "자세가 N° 달라 다시 재고 있어요"), 통과하고 알릴 것이 없으면 결과 카드 없이 실시간으로 넘어갑니다.

붙이는 절차, 확인한 것, 프론트엔드 팀과 정할 것(OTHER의 자리, 목록에 없는 실패 사유, 에이전트용 1초 기록 등)은
[`local/web/README.md`](local/web/README.md)에 있습니다.

### 7-11. 에이전트 증거 — `vision/evidence/gaze.py`

코치 에이전트와 리뷰 에이전트는 프레임을 읽지 않습니다. 이 모듈이 프레임 판정을 작고 오래가는 기록으로 바꾸고,
에이전트가 다른 평가기(말·대본·시간)와 나란히 순위를 매길 수 있도록 **공통 평가기 형식**으로 냅니다.
측정은 여기서 끝나고, 끼어들지 말지는 코치가 정합니다.

```
프레임 판정 ──GazeSlicer──▶ GazeSample (1초) ──GazeTimeline──▶ evaluate_gaze()        코치: 지금의 이슈
                                                               take_summary()         리뷰: 테이크 통계 · 구간
                                                               compare_summaries()    리뷰: 이전 테이크와의 차이
                                                               intervention_outcome() 피드백 전후 비교
```

**1초 기록** (`GazeSlicer`, `decide_slice`)

- 첫 프레임에 맞춘 1초(`slice_ms`) 격자로 자릅니다. 프레임이 없는 구간도 `UNMEASURED` 기록으로 남깁니다
  — 재지 못한 1초는 건너뛰지 않고 기록합니다.
- 상태 = 쓸 수 있는 프레임(얼굴이 유효한 프레임)의 다수결입니다. 가장 많은 상태가 `slice_vote_threshold`(60%) 미만이면
  `UNCERTAIN`, 쓸 수 있는 프레임이 `min_frames_per_slice`(4개) 미만이면 `UNMEASURED`입니다.
  동점은 `CAMERA → SCREEN → BOTTOM → OTHER → UNCERTAIN` 순서로 정합니다.
- `confidence` = 이긴 상태의 프레임 비율, `reliability` = 프레임별 조건 감시 신뢰도의 평균,
  `issues` = 프레임의 절반 이상에 나온 조건 감시 이슈, OTHER면 `direction` = 프레임 방향의 최빈값입니다.
- 상태는 6가지입니다. 판정된 4개(CAMERA / SCREEN / BOTTOM / OTHER)와 `UNCERTAIN`(얼굴은 있으나 판정 보류), `UNMEASURED`(얼굴 없음)입니다.

**비율은 측정된 시간으로 나눕니다.** 판정된 4개 상태의 시간만 분모에 넣습니다. 그래서 재지 못한 시간이
"청중을 안 봤다"로 읽히지 않습니다. 측정된 시간이 0이면 비율은 `None`입니다.
`coverage` = 측정된 시간 / 기록된 시간입니다.

**코치 이슈** (`evaluate_gaze(timeline, t_ms, cfg)`) — 그 시각까지의 기록만 봅니다. 심각도 순으로 정렬합니다.

| `issue_type` | 조건 | `severity` | `confidence` | `persistence_sec` | `actionable` |
|---|---|---|---|---|---|
| `GAZE_ON_SCRIPT` | 마지막 연속 구간이 BOTTOM이고 `script_min_ms`(3초) 이상 | 길이 / `script_full_ms`(10초) | 구간 평균 확신 × 평균 신뢰도 | 구간 길이 | ✅ |
| `GAZE_ON_SCREEN` | 마지막 연속 구간이 SCREEN이고 `screen_min_ms`(5초) 이상 | 길이 / `screen_full_ms`(15초) | 〃 | 〃 | ✅ |
| `GAZE_AWAY` | 마지막 연속 구간이 OTHER이고 `away_min_ms`(2초) 이상. 방향이 달라도 한 구간 | 길이 / `away_full_ms`(8초) | 〃 | 〃 | ✅ |
| `GAZE_LOW_EYE_CONTACT` | 최근 30초의 측정 시간 ≥ 15초이고 청중(CAMERA) 비율 < 0.30 | (0.30 − 비율) / 0.30 | 창 평균 확신 × 평균 신뢰도 | 측정된 시간 | ✅ |
| `GAZE_UNMEASURABLE` | 최근 5초의 `coverage` < 0.5 또는 평균 신뢰도 < 0.5 | 1 − min(coverage/0.5, 신뢰도/0.5) | 1.0 | 재지 못한 시간 | ❌ |

- 값은 모두 0~1로 자르고 소수 4자리로 반올림합니다(`r4`: `floor(x·10⁴ + 0.5) / 10⁴`, TS 포팅과 같은 계산).
- `evidence`의 키에는 창 길이가 들어갑니다(`bottom_ratio_5s`, `camera_ratio_30s`). 창 길이를 바꾸면 키 이름도 바뀌므로
  에이전트가 어느 창의 값인지 헷갈리지 않습니다. 연속 구간 이슈의 `evidence`에는 `run_start_ms`, `continuous_ms`,
  그 상태의 5초·30초 비율, 30초 청중 비율, 구간 평균 신뢰도가 들어가고, `GAZE_AWAY`에는 `direction`이 더 붙습니다.
- **`GAZE_UNMEASURABLE`은 "시선 피드백을 하지 말라"는 신호입니다.** `actionable: false`이고 코치 행동이 없습니다.
  측정할 수 없을 때 시선을 지적하면 틀린 피드백이 되기 때문입니다.
  `evidence`에 `coverage_5s`, `mean_reliability_5s`, 재지 못한·보류한 시간, 조건 이슈별 시간이 들어갑니다.
- 행동이 있는 네 이슈는 모두 코치 행동 `LOOK_AT_CAMERA`로 이어집니다(`GAZE_COACH_ACTION`). 실제로 말할지는 코치가 정합니다.

**리뷰 요약** (`take_summary(timeline, cfg)`) — 테이크 전체:
기록·측정·보류·미측정 시간, `coverage`, 상태별 시간과 비율, `eye_contact_ratio`(= CAMERA 비율),
`other_direction_ms`(OTHER를 본 방향별 시간), 평균 신뢰도, 조건 이슈별 시간, 상태별 가장 긴 구간(`longest_run_ms`)과
1초 이상 구간 수(`episodes`), 1초 이상 구간 목록(`segments`), 이슈 조건을 넘긴 구간 목록(`problem_segments`, `issue_type` 포함).

**이전 테이크 차이** (`compare_summaries(previous, current)`) — `eye_contact_ratio`, `coverage`, `mean_reliability`와
상태별 `state_ratio` · `episodes` · `longest_run_ms`를 `{"previous", "current", "delta"}`로 나란히 둡니다.
한쪽이 없으면 `delta`는 `None`입니다.

**개입 효과** (`intervention_outcome(timeline, t_ms, issue_type, cfg)`) — 피드백 시각 전 5초(`outcome_before_ms`)와
5초 뒤(`outcome_delay_ms`)부터 5초(`outcome_after_ms`)의 대상 비율을 비교합니다.
대상은 이슈마다 다릅니다(대본 → BOTTOM 감소, 화면 → SCREEN 감소, 다른 곳 → OTHER 감소, 눈맞춤 부족 → CAMERA 증가).
변화가 `outcome_min_change`(0.2) 이상이면 `effective: true`이고, 어느 창이든 측정된 시간이 없으면 `None`(아직 판단 불가)입니다.

**세션 연결**: `VisionSession`이 실시간 프레임마다 판정과 조건 감시 상태를 `GazeEvidenceRecorder`에 넣습니다.
보정을 새로 하면 기록을 비웁니다. 기록은 메모리에만 있고 디스크에 남지 않습니다.
값은 모두 `evidence.yaml`의 **초기값**입니다(§8).

브라우저 포팅(`web/src/engine/evidence.ts`)이 같은 동작을 하며, Python이 만든 기준 답과 키 이름까지 비교합니다.
프론트엔드의 지금 계약(1초 3구역 `ZoneDecision`)에는 방향과 SCREEN/BOTTOM 구분이 없어, 에이전트에 이 값을 주려면
1초 기록을 함께 넘기는 계약 변경이 필요합니다([`local/web/README.md`](local/web/README.md)).

### 7-12. 고개 원 확인 — `vision/runtime/sweep.py`

준비 점검을 통과한 뒤, 보정 전에 하는 확인입니다. 잠깐 정면을 보면 그 자세가 원의 중심이 되고,
고개를 천천히 돌려 원을 그리면 얼굴 주위 원(32칸)이 고개가 간 방향부터 켜집니다.

**무엇을 확인하나**

- **어느 방향으로 고개를 돌려도 얼굴을 따라가는가.** 안경 반사, 한쪽이 어두운 조명, 화면 가장자리에 걸린 얼굴은
  여기서 채워지지 않는 방향(`missing`)이나 얼굴을 놓친 방향(`lost`)으로 드러납니다. 촬영 중에 "측정 불가" 시간으로
  나타나기 전에 알 수 있습니다.
- **방향을 발표자 기준으로 읽는가.** 자기 오른쪽으로 돌리면 RIGHT 칸이 켜집니다. OTHER 방향과 같은 규약입니다.

분류기에는 아무것도 바꾸지 않고, 스스로 막지도 않습니다. 결과를 어떻게 쓸지는 호출자가 정합니다(데모는 기록하고 진행).

**기하**

```
offset = (오른쪽, 위) = (-(yaw - yaw0), pitch - pitch0)          중심 자세에서 돌린 각도(°)
reach  = hypot(오른쪽 / reach_yaw_deg, 위 / reach_pitch_deg)       1.0 = 칸을 켤 만큼 돌림
angle  = atan2(위 / reach_pitch_deg, 오른쪽 / reach_yaw_deg)       0 = 발표자 오른쪽, 반시계
```

- 중심 = 처음 `neutral_frames`(8)개, 약 1초 동안의 고개 자세 중앙값이고, 그 흔들림(`neutral_sigma_deg`, 1.4826 × MAD)도 함께 잽니다.
  노트북 웹캠에서 화면을 보는 자세는 이미 턱이 들려 있으므로(§7-7) 0°가 아니라 이 사람의 정면을 씁니다.
- 칸은 `reach ≥ 1`일 때 켜집니다. 사람은 고개를 좌우로 더 크게 돌리므로 타원(`reach_yaw_deg` 14°, `reach_pitch_deg` 9°)입니다.
- 0번 칸이 RIGHT 가운데이고 반시계로 번호가 붙습니다. 32칸이면 8방향이 4칸씩 갖습니다(`ticks`는 8의 배수여야 함).
- 8 FPS에서 원을 그리면 한 프레임에 약 한 칸씩 움직이므로, **연속한 두 프레임이 모두 칸을 켤 만큼 돌아 있으면 그 사이 칸도 켭니다**
  (`max_gap_ms` 400 ms, `max_fill_arc_deg` 90° 이내).
- 프레임 사이 움직임이 `max_speed_deg_s`(150°/s)보다 빠르면 아무 칸도 켜지 않고 `TOO_FAST`(천천히)로 알립니다.
  그 빠른 이동을 사이에 둔 칸 채우기도 하지 않습니다.

**진행과 안내**

| 상황 | 상태 |
|---|---|
| 얼굴 없음 / 고개 자세 미측정 | `last_reason` = 무효 사유 / `NO_HEAD_POSE`. 원은 **멈추기만** 하고 켜진 칸은 그대로 |
| 돌린 채로 얼굴을 놓침 | 그 방향의 `lost`가 1 늘어남(놓칠 때마다 한 번) |
| `hint_after_ms`(3초) 동안 새 칸이 없음 | `hint` = 아직 안 켜진 가장 긴 구간의 가운데 방향 |
| 모든 칸이 켜짐 | `DONE` |
| `timeout_ms`(25초) 경과 | `TIMED_OUT`. `missing`과 `hint`가 남음 |

세션 API는 `start_head_sweep(t_ms)` → `offer_sweep_frame(bgr, t_ms)`이고 마지막 상태는 `head_sweep`입니다.
**여기서 잰 중심이 보정의 출발점입니다.** 중심은 화면 속 내 얼굴을 보는 자세이므로, 이어지는 보정의 화면 가운데 큐가
이 값을 확인하고 맞으면 중심의 프레임을 그대로 보정에 씁니다. 렌즈·대본 큐는 확인된 자세를 기준으로 방향을 봅니다
([§7-3](#7-3-사용자별-캘리브레이션--visioncalibration-doc-5) 게이지 7번). 고개 원 자체는 평활기에 손대지 않습니다. 값은 모두 `sweep.yaml`의 **초기값**입니다(§8).

데모에서의 동작:

- **방향별 게이지:** 칸마다 그쪽으로 고개를 얼마나 돌렸는지(`tick_reach`, 0~1, 1 = 켜짐)를 기억하고, 8방향마다 그 평균(`direction_progress`)을 냅니다.
  칸이 켜지기 전에도 "그쪽으로 조금 더" 가 보이는 방향별 게이지입니다.
- **웹 데모:** 카메라 화면 전체(자르지 않음, 거울상) 위에서 원이 얼굴을 따라다니고, 원 바깥에 8방향 게이지 호가 있습니다. 아직 고개를 돌리지 않았으면 주황 칸이 원을 한 바퀴 돌아 할 일을 보여 줍니다.
  고개가 향한 방향은 원 중심 기준 **코끝 3D 화살표**(원근이 있어 정면이면 나를 향한 원뿔 끝, 돌릴수록 그쪽으로 길게)와 주황 칸으로, 켜진 칸은 초록으로 표시합니다. 멈추면 빈 쪽으로 화살표가 나옵니다.
  다 채우면 초록 원과 체크가 나오고 보정으로 넘어갑니다. 고개를 돌리기 어려운 사람은 `건너뛰기`를 누를 수 있습니다.
- **Python 데모:** `SWEEP` 단계로 들어가며 `SPACE`로 건너뜁니다. 헤드리스 실행은 돌릴 사람이 없으므로 건너뜁니다.

### 7-13. 판별 기준과 시나리오

기본 `head_pose` 백본으로 돌 때 한 프레임이 어떻게 판정되는지를 한 곳에 모은 표입니다. 각도는 모두 **고개 각도**이고
보정에서 잰 자세가 기준입니다. 좌우는 발표자 기준입니다(카메라 화면의 왼쪽 = 발표자의 오른쪽).
숫자는 보통의 노트북 보정(렌즈 18°, 화면 가운데 11°, 대본 4° — 고개 pitch)에서 나오는 값입니다.

**1) 이 프레임을 잴 수 있는가** (아니면 `판정 보류` + 이유)

| 상황 | 결과 |
|---|---|
| 얼굴 없음 / 일부만 보임 / 너무 작음(화면의 1% 미만) / 화면 밖에 걸침 | 판정 보류 (`NO_FACE` / `LOW_FACE_CONFIDENCE` / `FACE_TOO_SMALL` / `OUT_OF_FRAME`) |
| 눈이 감겼거나 가려짐, 눈을 잘라낼 수 없음 | **그대로 판정** — 고개 방향은 눈 없이 잽니다(§7-1) |
| 헤드포즈 측정 실패 | 판정 보류 (`BACKBONE_FAILED`) |

**2) 측정 불가** (신뢰도 0, 판정을 `UNCERTAIN`으로 강제, §7-8)

| 상황 | 기준 |
|---|---|
| 얼굴이 사라짐 | 1.5초 (`FACE_LOST`) |
| 다른 사람으로 바뀜 | 얼굴이 두 프레임 사이에 화면의 30% 넘게 **튀거나** 면적이 2배 넘게 바뀐 뒤 0.5초 동안 보정 자리로 돌아오지 않음 (`FACE_REPLACED`). 천천히 멀어지거나 옆으로 가는 것은 해당 없음. 보정 직후 첫 프레임은 보정 때 얼굴과 비교 |
| 자리를 크게 옮김 | 판정 오차가 보정 지점 간격(6~12°)을 2초 넘게 넘음 (`MOVED_TOO_FAR`) |

**3) 어디를 보는가** (화면 가운데를 볼 때가 좌우 0°·상하 0°)

| 판정 | 기준 |
|---|---|
| 청중(렌즈) `CAMERA` | 렌즈 볼 때 자세에 가장 가까움. 렌즈보다 조금(약 7°까지) 더 들어도 청중 |
| 화면 `SCREEN` | 화면 사각형 안: 위아래는 렌즈↔화면 가운데 간격, 좌우 반폭은 그 간격 × 화면 비율(1.78)을 **10~12°로 제한** |
| 대본 `BOTTOM` | 대본 자세 둘레의 상자(화면의 0.6 × 0.25) |
| 판정 보류 (`LOW_CONFIDENCE`/`LOW_MARGIN`) | 두 영역 사이라 1위 확률 < 0.70이거나 1·2위 차 < 0.20 |
| 다른 곳 `OTHER` + 8방향 | 보정 영역(위 셋 + σ)에서 **6° 넘게** 벗어남 → 보통 보정에서 좌우 약 19~20°, 렌즈보다 약 8° 위, 대본보다 약 8° 아래부터. 또는 고개가 보정 자세에서 좌우 35°·상하 30° 넘게 돌아감 |

화면이 가로로 몇 도인지는 보정에서 직접 재지 않습니다(렌즈·화면 가운데·대본은 세로로만 떨어져 있음). 그래서 렌즈를 볼 때 고개를 많이 드는
사람은 세로 간격 × 1.78로 잡은 화면이 한쪽 20° 넘게 넓어져, 옆을 확실히 봐도 "화면"으로 읽혔습니다(시나리오 S11).
노트북 화면 가장자리를 볼 때 고개는 10° 안팎만 돌아가므로 좌우 반폭을 12°에서 자릅니다(`screen_max_halfwidth_deg`).

**4) 누가 화면에 있는가**

| 상황 | 결과 |
|---|---|
| 같은 얼굴이 두 번 잡힘(한 상자의 중심이 다른 상자 안) | 한 사람 |
| 화면의 1% 미만인 작은 검출(배경 오검출) | 무시 — 검출기가 얼굴로 찾을 수 있는 크기 미만 |
| 별개의 얼굴이 주 얼굴 면적의 0.35 이상, **1초 이상** | 참고 신호 `SECOND_FACE` — **신뢰도는 그대로**, 판정 계속. 준비 점검은 0.25 이상, 0.5초 이상이면 `MULTIPLE_FACES` |
| 다른 사람이 측정을 가로챔 | 얼굴 바뀜(`FACE_REPLACED`) → 측정 불가 |

**측정 신뢰도 항목** (§7-8): 자리 이동(위치·거리) · 고개 방향 흔들림 · 얼굴 크기(인식 한계) · 판정 공백. 모두 판정 각도가
틀어지거나 비는 경로가 있는 것만 넣습니다. 조명 변화와 다른 사람은 원인일 뿐이라 넣지 않습니다(결과는 흔들림·공백·얼굴 바뀜으로 잡힘).

**6) 사전에 잰 값이 하는 일**

보정의 목적은 **사람마다 다른 책상 높이·노트북 거치대 때문에 달라지는 카메라 위치를 기준선으로 맞추는 것**입니다.
같은 렌즈를 봐도 카메라가 높으면 고개를 더 들고, 낮으면 덜 듭니다. 그래서 렌즈·화면 가운데·대본을 볼 때의 고개 자세를
이 사람, 이 자리에서 한 번 재어 기준선으로 쓰고, 실시간 판정은 이 기준선과의 비교일 뿐입니다. 세 지점을 재는 이유는
카메라 높이(렌즈 자세)와 함께, 이 사람이 화면·대본을 볼 때 고개를 얼마나 움직이는지(사람마다 다름)도 기준선에 담기 위해서입니다.

| 사전에 잰 값 | 어디에 쓰나 | 한계 |
|---|---|---|
| 준비 점검 | 촬영을 시작해도 되는지 판정만 | 이후 판정에는 쓰지 않음 |
| 고개 원(정면 기준, 1초) | 화면 가운데 큐가 확인하는 기준. 맞으면 이 프레임이 화면 가운데 기준점에 들어감 | 원 둘레의 범위는 판정에 쓰지 않음 |
| 화면 가운데 큐(확인된 기준) | 렌즈·대본 큐의 방향 확인 기준(렌즈는 위로, 대본은 아래로) | — |
| 3점 보정 기준점 | 렌즈·화면·대본 영역의 중심, 흔들림 σ, 고개 이탈 기준 | 화면의 **가로** 폭은 재지 않음(세로 간격 × 1.78, 10~12°로 제한) |
| 장면 기준값(얼굴 위치·크기·거리) | 자리 이동·얼굴 바뀜·인식 한계 판단 | — |

렌즈와 화면 가운데를 볼 때 고개 차이가 흔들림의 4배(최소 6°)보다 작으면 둘을 구분할 수 없어 화면이 렌즈로 합쳐지고,
그때는 화면을 보는 것도 청중으로 셉니다(결과 화면에 알림). 고개 방향만으로 판정하는 이 엔진의 근본 한계입니다.
보정 때만 취한 자세(예: 렌즈 큐에서 고개를 평소보다 많이 듦)는 따로 검사하지 않습니다. 기준선이 최소한의 역할만 하도록
두었고, 실시간에 렌즈가 화면으로 읽히면 `렌즈 다시 맞추기`(렌즈를 1초 봄)로 렌즈 기준만 다시 잡습니다.

**5) 화면에 보이는 값**

- 실시간 화면의 **좌우·상하 값**(`aim_deg`)은 화면 가운데를 볼 때를 0°로 한 고개 방향이고, 영역 안에서도 항상 나옵니다.
  옆의 작은 지도에 보정한 영역(화면·대본·렌즈 점)과 '다른 곳' 경계(점선), 지금 고개 방향(점)을 같은 좌표로 그립니다.
- 보정 화면에는 세 단계(화면 가운데 · 렌즈 · 대본)와 지금 단계의 진행 막대, 퍼센트, 좋은 프레임 수가 크게 나옵니다.
  고개 방향이 맞지 않아 멈추면 막대가 회색이 되고 "멈춤"과 이유가 나옵니다.

**시나리오** — 같은 시나리오를 Python 세션 전체(`tests/test_scenarios.py`)와 브라우저 엔진 전체(`web/test/scenarios.test.ts`)로
검사합니다. Python 쪽은 전처리 대역이 고개 회전·거리·눈 감음·여러 얼굴을 실제 전처리처럼 보고하고, 두 번째 얼굴은 실제
`second_face_ratio`로 판정합니다. 브라우저 쪽은 가짜 얼굴 검출기의 랜드마크로 `observe`부터 지납니다.

| # | 하는 일 | 기대 결과 |
|---|---|---|
| S01 | 렌즈를 본다 | 청중 |
| S02 | 화면 가운데를 본다 | 화면, 좌우·상하 0° |
| S03 | 대본을 읽는다(고개 숙임, 눈꺼풀 내려감) | 대본, 판정 계속 |
| S04 | 화면 가장자리를 본다(고개 좌·우 8°) | 화면, 좌우 값 8° |
| S05 | 옆을 확실히 본다(고개 25°) | 다른 곳 · 오른쪽/왼쪽, 신뢰도 그대로 |
| S06 | 렌즈보다 12° 위를 본다 | 다른 곳 · 위 |
| S07 | 렌즈보다 3° 위 | 청중 |
| S08 | 대본보다 15° 아래(책상) | 다른 곳 · 아래 |
| S09 | 고개를 40° 돌림, 눈이 안 보임 | 다른 곳 · 왼쪽, 판정 계속 |
| S10 | 화면과 대본 사이 | 다른 곳은 아님(화면·대본·판정 보류 중 하나, FE 구역은 화면/대본) |
| S11 | 렌즈 볼 때 고개를 많이 드는 사람이 옆을 봄(22°) | 다른 곳 · 오른쪽 (예전 기준에서는 화면) |
| S12 | 혼자 천천히 뒤로 물러남(크기 60%까지) | 다른 사람·얼굴 바뀜 없음 |
| S13 | 같은 얼굴이 두 번 잡힘 | 다른 사람 아님 |
| S14 | 배경에 아주 작은 오검출 | 다른 사람 아님 |
| S15 | 다른 사람이 0.4초 지나감 | 무시 |
| S16 | 다른 사람이 옆에 1초 넘게 있음 | `SECOND_FACE`, 신뢰도 0.7, 판정 계속 |
| S17 | 자리에 다른 사람이 앉음(얼굴이 튐) | 0.5초 뒤 측정 불가 `FACE_REPLACED` |
| S17b | 보정 직후 다른 사람이 앉음 | 측정 불가 `FACE_REPLACED` |
| S18 | 옆으로 12 cm 옮겨 2초 넘게 | 측정 불가 `MOVED_TOO_FAR` |
| S19 | 얼굴이 1.5초 사라짐 | 측정 불가 `FACE_LOST`, 돌아오면 다시 판정 |
| S20 | 렌즈를 보며 깜빡임 | 청중, 판정 계속 |
| S21 | 조명이 절반 아래로 어두워짐 | 신뢰도 그대로 |
| S22 | 고개 각도가 프레임마다 ±3° 흔들림(흐린 조명·흔들린 영상) | `NOISY_TRACKING`, 신뢰도 하락(측정 불가는 아님) |
| S23 | 고개 원 뒤 같은 자세로 화면 가운데를 봄 | 고개 원의 기준이 확인되어 화면 가운데는 8프레임만에 끝나고, 고개 원 프레임 8개가 기준점에 합쳐짐 |
| S24 | 고개 원 뒤 방석을 깔아 모든 자세가 5° 높아짐 | 화면 가운데를 16프레임 새로 재고, 렌즈·대본 확인도 새 자세 기준 → 보정 완료, 판정 정상 |
| P1 | 멀지만 찾을 수 있는 얼굴 + 중복 검출로 준비 점검 | 통과 |
| P2 | 준비 점검 중 다른 사람이 0.5초 넘게 | `MULTIPLE_FACES` |

---

## 8. 설정 (ai/configs)

`vision.config.load_config(config_dir=None, overrides=None)` → `VisionConfig`.
11개 YAML이 11개 데이터클래스 섹션으로 로드됩니다.

| 파일 | 섹션 키 | 데이터클래스 |
|---|---|---|
| `preprocess.yaml` | `preprocess` | `PreprocessConfig` |
| `gaze_backbone.yaml` | **`backbone`** ⚠️ | `BackboneConfig` |
| `placement.yaml` | `placement` | `PlacementConfig` |
| `calibration.yaml` | `calibration` | `CalibrationConfig` |
| `temporal.yaml` | `temporal` | `TemporalConfig` |
| `release_gate.yaml` | `release_gate` | `ReleaseGateConfig` |
| `collection.yaml` | `collection` | `CollectionConfig` (→ `List[ProtocolCondition]`) |
| `preconditions.yaml` | `preconditions` | `PreconditionConfig` ([§7-7](#7-7-준비-점검--visionruntimepreconditionspy)) |
| `condition.yaml` | `condition` | `ConditionConfig` ([§7-8](#7-8-조건-감시--visionruntimeconditionpy)) |
| `evidence.yaml` | `evidence` | `EvidenceConfig` ([§7-11](#7-11-에이전트-증거--visionevidencegazepy)) |
| `sweep.yaml` | `sweep` | `SweepConfig` ([§7-12](#7-12-고개-원-확인--visionruntimesweeppy)) |

> 오타 방지: `test_every_shipped_yaml_key_is_a_real_dataclass_field`가 출하 YAML의 모든 키가
> 실제 필드인지 검사합니다(`_build`가 모르는 키를 조용히 버리기 때문).

> ⚠️ **파일명과 섹션 키가 다른 유일한 경우**: `gaze_backbone.yaml` → 섹션 `backbone`.
> `overrides` 딕셔너리는 반드시 `{"backbone": {...}}`를 써야 합니다.

> ⚠️ `_read_yaml`은 없는 파일에 대해 `{}`를 반환하고 `_build`는 알 수 없는 키를 **조용히 버립니다.**
> 잘못된 `--config-dir`나 오타난 키는 에러 없이 **전부 기본값인 설정 + 멀쩡해 보이는 해시**를 만들어냅니다.

현재 11개 YAML은 모두 데이터클래스 기본값을 그대로 재기술하고 있어, `load_config()`와 `VisionConfig()`가
딱 한 곳에서만 다릅니다 — `collection.conditions` (YAML 7블록 vs 기본 빈 리스트).
검증: `load_config().hash() == '958414c647b7'` / `VisionConfig().hash() == '2d74786c3f86'`
(v1.1에서 필드가 늘어 v1.0의 해시와는 다릅니다).

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
| `num_faces` | `3` | 검출할 얼굴 수. 분석은 주 얼굴 하나, 나머지는 "다른 사람" 감지용 |
| `landmarker_model_path` | `ai/models/face_landmarker.task` | `resolve_path()`로 repo root 기준 해석 |
| `running_mode` | `IMAGE` | `IMAGE`(결정적 오프라인) / `VIDEO`(t_ms 필수). `LIVE_STREAM`은 `NotImplementedError` |
| `adaptive_blink` / `blink_ratio` | `true` / `0.70` | 보정 후 적응형 깜빡임 ([§7-9](#7-9-적응형-깜빡임--visioneyeblinkpy)) |
| `blink_window_frames` / `blink_min_reference_frames` | `24` / `8` | 기준 중앙값 창 / 작동 시작에 필요한 프레임 |

</details>

<details>
<summary><b>gaze_backbone.yaml</b> — 백본 선택 [doc 3-2]</summary>

| 키 | 기본값 | 설명 |
|---|---|---|
| `name` | `head_pose` | `head_pose` \| 보관된 눈 기반: `mediapipe_geom` \| `l2cs` \| `gazetr` |
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
<summary><b>calibration.yaml</b> — 사용자별 캘리브레이션 [doc 5]</summary>

| 키 | 기본값 | 설명 |
|---|---|---|
| `method` | `reference` | `reference`(기준점) / `logistic`(doc 5-3 LR, 비교용). 오타는 `ValueError` |
| `camera_seconds` / `bottom_seconds` / `screen_seconds` | `2.0` | 녹화 블록 길이(collect)와 오프라인 보정 창(splits). 런타임은 게이지로 끝냄 |
| `min_samples_per_class` | `10` | 큐별 사용 가능 샘플 하한 (게이지 시간 초과 시 통과선) |
| `min_loo_accuracy` | `0.85` | LOO 정확도 하한 (마지막 게이트) |
| `p_max_threshold` / `margin_threshold` | `0.70` / `0.20` | doc 5-4 신뢰도 · 마진 하한 (마진은 기본값에선 도달 불가) |
| `check_pitch_ordering` | `true` | 역전 순서 경고 활성화 (**실패시키지 않음**) |
| **게이지** | | |
| `target_good_frames` | `16` | 큐가 끝나는 좋은 프레임 수 |
| `cue_settle_ms` / `cue_timeout_ms` | `500` / `8000` | 시선 이동 대기 / 큐 제한 시간 |
| `min_sample_confidence` | `0.30` | 이 미만 시선 신뢰도는 표본이 아님 |
| `max_head_deviation_deg` | `6.0` | (눈 기반 백본만) 첫 큐 자세에서 고개가 벗어나도 되는 한계 |
| `outlier_k` | `4.0` | 큐 중앙값에서 흔들림의 몇 배 넘게 떨어지면 딴 데 본 것으로 봄 |
| `cue_direction_gate` | `true` | 고개 방향 엔진: 기준 자세(고개 원 중심, 없으면 화면 가운데 큐)에서 큐 방향으로 고개가 향할 때만 게이지가 참 |
| `cue_min_up_deg` / `cue_min_down_deg` | `2.0` / `3.0` | 렌즈 큐는 위로, 대본 큐는 아래로 이만큼 이상 |
| `cue_screen_radius_deg` / `cue_max_side_deg` | `7.0` / `10.0` | 화면 가운데 큐는 기준에서 이 반경 안, 모든 큐는 옆으로 이 이내 |
| `cue_confirm_frames` / `cue_confirm_deg` | `8` / `4.0` | 고개 원을 했으면 화면 가운데 큐는 이만큼 모아 원의 중심과 비교. 이 안이면 확인(원의 프레임을 합침), 밖이면 16개를 새로 잼 |
| **기준점 분류기** | | |
| `sigma_min_deg` / `sigma_scale` | `1.5` / `1.25` | 흔들림 σ 바닥 / 실시간용 확대 |
| `screen_aspect` | `1.7778` | 시선 공간의 실효 화면 비율 (화면 좌우가 OTHER로 읽히면 먼저 조정) |
| `script_width_fraction` / `script_height_fraction` | `0.60` / `0.25` | 대본 영역 = 화면 상자의 비율 |
| `camera_halfwidth_deg` | `0.0` | >0이면 눈맞춤 영역을 그 반경으로 고정 |
| `other_field_yaw_deg` / `other_field_pitch_deg` | `120` / `90` | OTHER 균등 밀도의 범위 |
| `prior_camera` / `prior_screen` / `prior_bottom` / `prior_other` | `0.25` 각각 | 사전확률 (0이면 그 클래스 끔; CAMERA·BOTTOM은 0 불가) |
| `head_away_yaw_deg` / `head_away_pitch_deg` | `35` / `30` | 이 이상 고개를 돌리면 OTHER |
| `other_margin_deg` | `6.0` | 화면 영역에서 이만큼 이상 벗어나야 OTHER가 경쟁함 (안쪽은 가장 가까운 대상) |
| `screen_min_halfwidth_deg` | `10.0` | 화면 영역의 최소 좌우 반폭 (보정 지점이 좌우로 붙어 있어도) |
| `screen_max_halfwidth_deg` | `12.0` | 화면 상자의 최대 좌우 반폭 (렌즈를 볼 때 고개를 많이 들어도) |
| `min_anchor_separation` | `4.0` | 기준점 쌍의 최소 분리(σ 단위) |
| `merge_inseparable_screen` | `true` | SCREEN만 문제일 때 실패 대신 CAMERA로 합침 ([§7-3](#7-3-사용자별-캘리브레이션--visioncalibration-doc-5)) |
| `reanchor_frames` / `reanchor_max_shift_deg` / `reanchor_timeout_ms` | `8` / `8.0` / `3000` | 다시 맞추기 |
| **logistic 전용** | | |
| `min_centroid_distance` / `min_separability` | `0.60` / `1.00` | 표준화 단위 중심 거리 · Fisher 유사 비율 하한 |
| `feature_set` | `C` | `A`(2) / `B`(5) / `C`(9) |
| `C` / `max_iter` / `class_weight` / `random_seed` | `1.0` / `1000` / `balanced` / `42` | LogisticRegression 파라미터 |

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
| `decay_on_invalid` | `true` | 기권 프레임이 EMA를 중립값(1/K)으로 붕괴 (false면 동결) |
| `enter_screen_threshold` / `enter_other_threshold` | `0.60` / `0.60` | 다클래스 전환 arm 임계값 |
| `to_screen_dwell_ms` / `to_other_dwell_ms` | `400` / `800` | 다클래스 dwell (화면 밖은 가장 긺) |

> ⚠️ `enter_*_threshold`를 0.5 아래로 내리면 여러 후보 테스트가 동시에 통과할 수 있고, BOTTOM이 먼저 검사되므로 조용히 BOTTOM 편향이 생깁니다.

</details>

<details>
<summary><b>placement.yaml</b> · <b>preconditions.yaml</b> · <b>condition.yaml</b> · <b>evidence.yaml</b> · <b>sweep.yaml</b> · <b>release_gate.yaml</b> · <b>collection.yaml</b></summary>

**placement**: `camera_seconds`/`screen_seconds` 2.0, `min_samples_per_target` 8, `min_sample_confidence` 0.30,
`mode` `learned`, `min_loo_accuracy` 0.85, `C` 1.0, `max_iter` 1000, `random_seed` 42,
`min_axis_dominance` 1.30, `min_delta_deg` 4.0, `min_separation` 1.00,
`anchor_min_axis_dominance` 2.5 (anchors 모드: TOP은 `|Δyaw| ≤ 0.4·|Δpitch|`).

**preconditions** ([§7-7](#7-7-준비-점검--visionruntimepreconditionspy)): `strict` true, `hold_ms` 1000, `reject_after_ms` 3000,
`max_second_face_area_ratio` 0.25 · `second_face_confirm_ms` 500, `max_center_offset_x/y` 0.35 / 0.35, `min_face_area_ratio` 0.012,
`min_iris_px` 7.0 (눈 기반 백본만), `max_face_height_ratio` 0.70, `max_head_yaw_deg`/`max_head_pitch_deg` 30 / 40,
`min_face_brightness` 40, `max_backlight_ratio` 2.5, `min_analysis_fps` 5.0.

**condition** ([§7-8](#7-8-조건-감시--visionruntimeconditionpy)): `window_ms` 3000, `heartbeat_ms` 2000, `emit_delta` 0.10,
`fail_reliability` 0.30, `head_warn/fail_deg` 12 / 30 (눈 기반 백본만), `drift_fail_share` 1.0, `drift_fail_min/max_deg` 6 / 12,
`drift_warn_share` 0.6, `drift_confirm_ms` 2000, `drift_default_span_deg` 7, `head_radius_cm` 8,
`second_face_area_ratio` 0.35 · `second_face_confirm_ms` 1000 (참고 신호), `valid_warn/fail_ratio` 0.90 / 0.50,
`jitter_window_ms` 2000, `jitter_max_gap_ms` 300, `jitter_min_samples` 6, `jitter_fail_share` 0.5,
`jitter_fail_min/max_deg` 3 / 6, `jitter_warn_share` 0.5,
`small_face_warn/fail_area` 0.016 / 0.011, `large_face_warn/fail_height` 0.70 / 0.85,
`replace_center_offset` 0.30, `replace_area_ratio` 2.0,
`replace_confirm_ms` 500, `face_lost_ms` 1500.

**evidence** ([§7-11](#7-11-에이전트-증거--visionevidencegazepy)): `slice_ms` 1000, `min_frames_per_slice` 4, `slice_vote_threshold` 0.6,
`short_window_ms`/`long_window_ms` 5000 / 30000, `script_min/full_ms` 3000 / 10000, `screen_min/full_ms` 5000 / 15000,
`away_min/full_ms` 2000 / 8000, `low_eye_contact_ratio` 0.30, `low_eye_contact_min_measured_ms` 15000,
`unmeasurable_coverage` 0.5, `unmeasurable_reliability` 0.5, `segment_min_ms` 1000,
`outcome_before_ms`/`outcome_delay_ms`/`outcome_after_ms` 5000 / 5000 / 5000, `outcome_min_change` 0.2.

**sweep** ([§7-12](#7-12-고개-원-확인--visionruntimesweeppy)): `ticks` 32, `neutral_frames` 8 (1초),
`reach_yaw_deg`/`reach_pitch_deg` 14 / 9, `max_speed_deg_s` 150, `max_gap_ms` 400, `max_fill_arc_deg` 90,
`hint_after_ms` 3000, `timeout_ms` 25000.

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
| `pipeline_demo` | `python -m ai.tools.pipeline_demo` | 준비 점검 → 보정 → 실시간 대화형 데모 (**먼저 실행할 것**) |
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
| `pipeline_demo --check` | 카메라 열기 실패 / 프레임 0 / 얼굴 미추적 | 준비 점검 결과는 출력만 함. 스테이지 플로우는 엄격 판정에 막혀도 `0` |
| `threshold_sweep` | 빈 split · 특징 테이블 미해결 (`SystemExit`) | 정상 실행은 `0` |
| `experiment_log` | — | 항상 `0` |

### `pipeline_demo` — 준비 점검 → 고개 원 → 보정 → 실시간

```
INTRO → CHECK → SWEEP → CALIB_CAMERA → CALIB_SCREEN ─(배치 판정)─┬─ 지원 ──→ CALIB_BOTTOM → CALIB_RESULT → LIVE
        └ STEP 1 ┘      └──────── STEP 2 보정 (편하게 바라보기) ─┴─ 미지원 → PLACE_RESULT ┘        STEP 3
```

- **CHECK**: 준비 점검 체크리스트를 실시간으로 보여주고, PASS(모든 조건 1초 유지)면 자동으로 넘어갑니다 ([§7-7](#7-7-준비-점검--visionruntimepreconditionspy)).
- **SWEEP**: 얼굴 주위에 32칸 원을 그리고, 고개를 돌린 방향부터 켭니다. 다 채우거나 시간이 다 되면 기록하고 넘어갑니다.
  막지 않으며 `SPACE`로 건너뛸 수 있고, 헤드리스 실행은 건너뜁니다 ([§7-12](#7-12-고개-원-확인--visionruntimesweeppy)).
  `collect`의 배치 측정 흐름(`flow="placement"`)에는 들어가지 않습니다.
- **보정 큐 3개**: `--countdown` 초 동안 목표를 찾을 시간을 준 뒤, 게이지가 좋은 프레임 16개를 채우면 끝납니다.
  화면에는 게이지 막대와 지금 프레임이 버려지는 이유(얼굴 안 보임, 딴 곳 봄 등)가 나옵니다 ([§7-3](#7-3-사용자별-캘리브레이션--visioncalibration-doc-5)).
  눈 기반 백본을 고르면 안내가 "고개는 그대로, 눈만"으로 바뀝니다.
- **배치 판정**: 렌즈와 화면 중앙 두 큐가 끝난 직후 기준점으로 판정합니다(`anchors` 모드).
  지원하는 배치면 바로 대본 큐로 가고, 아니면 `PLACE_RESULT` 카드에서 멈춥니다. 대본 큐를 보기 전에 잡아야 그 큐가 의미 있기 때문입니다.
  기본 `head_pose`에서 판정 불가(`INCONCLUSIVE`)면 카드 없이 로그만 남기고 대본 큐로 갑니다.
- **LIVE**: 5개 라벨, 클래스별 확률 막대, 신뢰도 패널(이유 포함)을 그립니다. 다른 곳이면 방향(`다른 곳 응시 · 왼쪽 위`)이
  붙고, 코치 이슈가 있으면 가장 심한 것 하나를 `코치 신호:` 줄로 보여줍니다([§7-11](#7-11-에이전트-증거--visionevidencegazepy)).
  `A`로 다시 맞추기를 합니다.

**게이팅** (`vision/runtime/policy.py`): 기본은 엄격 모드(`preconditions.strict: true`)입니다.

| 지점 | 엄격 모드 | `--advisory` |
|---|---|---|
| `CHECK` | PASS가 될 때까지 대기. REJECT면 그 자리에서 멈춤 | `SPACE`로 건너뛸 수 있음 |
| 큐 시간 초과 (`TIMED_OUT`) | 멈춤 (`R` 재시도 / `C` 강제) | 기록하고 진행 |
| `PLACE_RESULT` | 지원 배치만 통과. 판정 불가(`INCONCLUSIVE`)는 눈 기반 백본에서만 차단 | 항상 통과 |
| `CALIB_RESULT` | 품질 `OK`만 통과 | 분류기가 있으면 경고 카드와 함께 통과 |

두 모드 모두 **분류기가 없으면 LIVE로 갈 수 없습니다**(`C`로도 불가).
`C`로 강제 통과하면 스테이지 로그에 `overridden by operator`가 남습니다.

**키보드** (OpenCV 창에 포커스가 있어야 입력이 들어갑니다 — SPACE가 "안 먹는" 1순위 원인):

| 키 | 동작 |
|---|---|
| `SPACE` / `ENTER` | 진행 (위 게이트 적용). SWEEP에서는 건너뛰기 |
| `R` | 재시도: CHECK는 점검 다시, SWEEP은 원 처음부터, 큐는 그 큐만, 결과 카드·LIVE는 보정 처음부터 |
| `C` | 막힌 판정 강제 통과 |
| `A` | LIVE에서 렌즈 다시 맞추기 |
| `D` | 디버그 오버레이 토글 |
| `M` | 컨투어 메시 토글 |
| `S` | `ai/reports/demo_events.jsonl`(GAZE_STATE)과 `demo_conditions.jsonl`(SESSION_CONDITION) 덤프 |
| `Q` / `ESC` | 종료 |

**플래그**

| 플래그 | 기본값 | 설명 |
|---|---|---|
| `--camera-index` | `0` | 웹캠 인덱스 (`--video` 시 무시) |
| `--video PATH` | — | 녹화 파일 재생 (소스 타임스탬프 사용 → 결정적) |
| `--backbone NAME` | config | `cfg.backbone.name` 오버라이드. **`choices=` 제약이 없어** 잘못된 이름은 `build_backbone`에서 늦게 실패합니다 |
| `--method` | config | `reference` / `logistic`. `calibration.method` 오버라이드 |
| `--advisory` | off | 판정을 기록만 하고 막지 않음 (연구용) |
| `--width` / `--height` | `1280` / `720` | 요청 해상도. 15 fps 미만이면 640×480으로 자동 강등 |
| `--countdown` | `1.5` | 큐 수집 전 카운트다운 (초) |
| `--no-window` | off | 헤드리스. `auto_advance()`가 키와 같은 게이트로 자동 진행하고, 엄격 판정에 막히면 `blocked at <STAGE>`를 출력하고 멈춤 |
| `--english` | off | 영어 강제 (기본은 한국어, CJK 폰트 없으면 자동 영어) |
| `--max-seconds` | `0.0` | N초 후 정지 (0 = 무제한) |
| `--check` | off | 1) 카메라 2) 얼굴 추적 3) 준비 점검을 진단하고 종료 |
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

**`--camera-position auto`(기본)**: 녹화 전에 `pipeline_demo`의 `placement` 흐름을 그대로 씁니다 —
준비 점검 → 렌즈 큐 → 화면 중앙 큐 → 배치 판정에서 멈춤. 자문 모드(`strict=False`)라 판정이 나빠도
녹화는 진행하고, 판정 결과를 메타데이터(`camera_position`, `cues.json`)에 남깁니다.

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

### SCREEN / OTHER 예측의 채점

정답 라벨은 여전히 CAMERA / BOTTOM 두 개입니다(`DECISION_CLASSES`). 기준점 분류기는 SCREEN과 OTHER도
내놓으므로, 정답이 CAMERA나 BOTTOM인 프레임에 이 둘이 나오면 이렇게 셉니다.

- **기권이 아니라 오답**입니다. 정답 클래스의 FN에 더해지고, `n_decided`·`coverage`에도 들어갑니다.
  "카메라를 보라"는 큐에 SCREEN이라고 답했다면 판정을 내리고 틀린 것이기 때문입니다.
- 기존 혼동표 3열(CAMERA / BOTTOM / UNCERTAIN)은 그대로 두고, 따로 집계합니다:
  `n_off_target`, `off_target_ratio`, `confusion_off_target[정답][SCREEN|OTHER]`.
- 세그먼트 지표에서는 `off_target_ms`(세그먼트별, 그리고 `time_weighted`)로 따로 보고하고,
  세그먼트의 `pred_label` 경쟁에도 참여합니다.
- UNCERTAIN 사유 집계에는 들어가지 않습니다.

`gaze_eval`은 프레임 행에 `p_screen` / `p_other` 열을 함께 남깁니다(2클래스 분류기는 NaN).
`apply_uncertain_rule(..., p_screen=, p_other=)`은 이 열이 있으면 K클래스 규칙(1위 확률 / 1·2위 차이,
동점은 `STATE_CLASSES` 순서)을 재현하고, 없으면 예전 2클래스 계산을 그대로 씁니다.
평활화 재생에는 그 사용자의 분류기가 실제로 가진 클래스를 그대로 넘깁니다.

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
> 기준점 분류기(K클래스)에서는 이 항등식이 성립하지 않아 마진 축이 실제로 구속력을 가집니다.
> 스윕은 `p_screen` / `p_other` 열을 규칙 재현에 함께 넘깁니다.
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
python -m pytest -q     # 1790개 수집 → 1752 passed, 38 skipped
```

skip 38개는 torch가 없을 때 건너뛰는 L2CS·GazeTR 테스트입니다.
`test_the_parquet_backend_round_trips_the_table_unchanged`는 PC의 애플리케이션 제어 정책이
pyarrow DLL 로드를 막으면 실패합니다(§3). 그때는 환경 문제이고, 코드 문제가 아닙니다.

pytest 설정은 전부 `pyproject.toml`에 있습니다: `testpaths=["tests"]`, `pythonpath=["ai/src"]`,
`filterwarnings=["ignore::DeprecationWarning"]`(mediapipe/protobuf, numpy 2.x가 import 시 시끄럽게 냄).

`tests/conftest.py`가 repo root를 `sys.path`에 넣으므로 **bare `pytest`도 동작합니다.**
(`pythonpath`는 `ai/src`만 넣기 때문에 이전에는 `ModuleNotFoundError: No module named 'ai'`로 실패했습니다.)

### 커버리지

| 파일 | 테스트 | 보호하는 계약 |
|---|---:|---|
| `test_schemas_config.py` | 343 | 부호 규약, 각도 왕복 변환, 7키 `GAZE_STATE` · 4키 `SESSION_CONDITION` 계약, 설정 로딩·병합·해시, YAML 키 오타 검사 |
| `test_data.py` | 199 | `sample_id`, 가드밴드, 참가자 분할, doc 4-3 누수 검사, 테이블 왕복, SCREEN 보정 창 |
| `test_evaluation.py` | 196 | 지표 정의, 기권 회계, 릴리스 게이트 판정, doc 19 버킷 10종, 부호 sanity |
| `test_preprocess_geometry.py` | 178 | 종횡비 함정, roll 부호, bbox 클램핑, PnP Euler, 샘플러 상태 머신, 홍채 지름 |
| `test_temporal.py` | 157 | EMA·투표·dwell 상태 머신, 기권 처리, 하트비트, **2클래스 골든 동등성**, K클래스 전환 |
| `test_runtime.py` | 123 | 세션 수명주기, 지연 시간 회계, 배치 판정, 버전 스탬핑, 게이지 보정 흐름, 조건 감시·다시 맞추기, 고개 방향 백본은 눈 게이트(눈 감음·눈 크롭 실패)로 프레임을 버리지 않음 |
| `test_calibration.py` | 118 | 특징 집합 A/B/C, 앵커 동결(누수 방어), doc 5-4 분기 순서, 품질 게이트, SCREEN 분리 |
| `test_backbones.py` | 117 | 레지스트리(기본 `head_pose` 먼저, 눈 기반은 `vision.eye`), torch 지연 임포트, 체크포인트 검증, 백본별 규약 변환 |
| `test_preprocess_pipeline.py` | 97 | 유효성 게이트 순서, `NO_FACE` 분기, 무효 프레임의 완전 보존, 여러 얼굴·주 얼굴 선택, 별개의 얼굴만 다른 사람(중복 검출·작은 오검출 제외) |
| `test_references.py` | 52 | 소프트 박스 밀도(적분 1, 극단값 유한), 영역 기하(화면 좌우 반폭 상한), 판정·기권, 품질 사유 순서, 다시 맞추기 이동 |
| `test_condition.py` | 35 | 신뢰도 램프, 심각 상황(얼굴 손실·교체는 튄 경우만·너무 멀리 이동), 다른 사람은 1초 이상의 참고 신호(신뢰도 그대로), 조명 변화만으로는 신뢰도 그대로, 고개 방향 흔들림(돌리는 움직임은 흔들림 아님), 천천히 물러나기는 교체 아님, 발행 주기, 인식 한계 기준 얼굴 크기, 위치 기준선(보정 간격으로 정한 한계, 고개만 돌린 건 이동 아님, cm·각도 보고, 거리 변화), 얼굴 메시 거리(눈이 가려져도 이동 아님, 홍채는 양쪽 모두 없을 때만) |
| `test_preconditions.py` | 22 | PASS / RETRY / REJECT 시간 규칙, 사유별 판정(인식에 필요한 최소 조건), 다른 사람은 0.5초 이상, 홍채 하한은 눈 기반 백본만, 거리 표시(얼굴 메시 우선, 홍채 대체) |
| `test_web_engine_sources.py` | 2 | 브라우저 엔진의 `defaults.ts`와 기준 답(`parity.json`)이 지금 Python 설정·엔진에서 생성한 것과 같은가 |
| `test_gaze_evidence.py` | 27 | OTHER 8방향(발표자 기준 좌우), 1초 다수결·보류·미측정, 빈 구간 기록, 측정 시간 기준 비율, 코치 이슈 5종과 키 이름, 리뷰 요약·이전 테이크 차이, 개입 효과 |
| `test_scenarios.py` | 28 | 판별 시나리오 S01~S24·P1~P2를 세션 전체로([§7-13](#7-13-판별-기준과-시나리오)) |
| `test_head_pose.py` | 31 | `head_pose` 백본(각도 그대로, 미측정 헤드포즈 거절), 고개 움직임을 허용하는 게이지, 가장 가까운 보정 자세 비교, SCREEN 합치기, 판정 불가 배치 통과, 세션 끝까지, 세션의 에이전트 증거, 원 중심 기준 큐 방향 확인, 화면 가운데가 원의 중심을 확인·합침 / 자세가 바뀌었으면 새로 재고 기준 갱신 / 원이 없으면 그대로 잼, 고개를 돌려도 신뢰도 유지, 너무 멀리 움직이면 판정 멈춤, 조금 들거나 돌린 고개는 가장 가까운 대상·확실히 벗어나야 OTHER |
| `test_gauge.py` | 19 | 게이지 거절 순서, 멈춤(리셋 없음), 시간 초과, 큐별 목표와 늘리기 |
| `test_pipeline_demo.py` | 19 | 엄격·자문 게이팅, 헤드리스 진행, 고개 원 단계(전체 흐름에만, 헤드리스·SPACE 건너뛰기, 그리기), 고개 기반의 배치 판정 불가 진행, 5라벨 렌더링, 오버레이 기하 |
| `test_sweep.py` | 17 | 고개 원: 원 채우기, 방향별 게이지, 중심 자세(1초, 흔들림), 발표자 기준 방향, 8 FPS 사이 칸 채우기, 빠른 움직임, 얼굴 손실의 방향 기록, 멈춤 힌트, 시간 초과, 세션 |
| `test_eval_multiclass.py` | 10 | SCREEN/OTHER 예측의 오답 회계, K클래스 규칙 재현 |
| **합계** | **1790** | 웹캠·네트워크 불필요 |

브라우저 엔진은 따로 `local/web`에서 `npm test`(vitest 197개), `npm run typecheck`, `npm run smoke`로 검사합니다([§7-10](#7-10-브라우저-포팅--localweb)).

**골든 테스트**: `tests/_legacy_smoother.py`는 K클래스로 바꾸기 전 smoother를 그대로 얼려 둔 사본입니다.
무작위 입력 스트림에서 새 smoother(2클래스)와 이벤트·점수가 완전히 같은지 비교해, 일반화가 기존 동작을
바꾸지 않았음을 고정합니다. `pytest -k legacy`로 따로 돌릴 수 있습니다.

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

`config_hash`는 이 버그 수정 전후 **불변**이었습니다 (`3cc1193e3238`) — doc 18 프로버넌스가 보존됐습니다.
(v1.1에서 설정 필드가 늘어 현재 해시는 `958414c647b7`입니다. §8 참고.)

> `tests/fixtures/face.jpg`는 여분의 자산이 아니라 **하중을 받는 문서**입니다.
> 9개 PnP 모델 점, 63° 초점거리 선택, 눈 좌우 명명, 홍채 편향 상수(0.30 홍채 반지름),
> 백본 간 부호 규약 교차 검증(`mediapipe_geom` +2.74/−2.77° vs `L2CS` +2.20/−0.23°)이
> **모두 이 176 KB 이미지 하나에서 유도되거나 검증되었습니다.**
> 이 픽스처를 교체하면 해당 상수들의 출처가 무효화되며, 어떤 테스트도 이를 잡아내지 못합니다.

---

## 14. 알려진 한계

1. **라벨 해상도**: `CAMERA` / `SCREEN` / `BOTTOM` / `OTHER` + 기권 `UNCERTAIN`. 좌·우는 별도 라벨이 아니라
   `OTHER`에 붙는 `direction`(8방향)입니다. 방향은 보정한 화면 영역에서 벗어난 쪽일 뿐이라, 그쪽에 청중이 있는지,
   창문이 있는지는 모릅니다. 대본 라벨 이름은 호환을 위해 `BOTTOM` 그대로입니다.
2. **고개 방향으로 판정**(기본 `head_pose`): 고개를 든 채 눈만 내려 대본을 읽는 사람은 CAMERA로 판정됩니다.
   렌즈와 화면 가운데를 같은 고개 자세로 보는 사람은 둘이 합쳐져 화면 보기도 CAMERA가 됩니다(`SCREEN_MERGED`).
   눈 기반 백본은 이 환경에서 세로 눈 움직임을 읽지 못해 보관 중입니다([§7-2](#7-2-gaze-backbone--visionbackbones-doc-3-2)).
3. **SCREEN 정답이 없음**: 녹화 프로토콜(`collect`)에는 아직 화면 중앙 보정 블록과 SCREEN 정답 구간이 없습니다.
   그래서 오프라인 평가는 CAMERA/BOTTOM 정답만 채점하고, SCREEN·OTHER 예측은 오답으로만 셉니다(§11).
   SCREEN·OTHER가 실제로 맞는지는 사람이 직접 확인해야 합니다.
4. **기하 전제**: 노트북 웹캠이 **화면 위 가운데**, 대본이 아래. 엄격 모드는 보정 중 배치 판정으로 이를 확인하고
   맞지 않으면 막습니다. `--advisory`와 `collect`는 기록만 합니다
   (`test_advisory_placement_estimate_never_blocks_calibration`으로 고정된 동작).
5. **카메라 내부 파라미터를 측정하지 않음**: 63° 수직 FOV, 주점 = 이미지 중앙, 왜곡 0으로 가정.
   준비 점검·조건 감시의 거리(cm)도 이 초점거리와 MediaPipe 평균 얼굴 모델(없으면 평균 홍채 지름 1.17 cm)로 계산한 **추정치**입니다.
6. **움직임은 알리기만 하고 되돌려 보정하지 않음**: 보정 자리에서 움직이면 그 판정 오차를 각도로 재서 신뢰도를 낮추고,
   보정 지점 간격(6~12°)을 2초 넘게 넘으면 측정 불가로 멈춥니다(§7-8). 고개 각도를 되돌려 보정하지는 않습니다. 필요하면 `A`(다시 맞추기)를 씁니다.
   이동 계산의 회전 중심(얼굴 뒤 8 cm)과 거리(평균 얼굴 모델, 63° FOV)는 가정이라 실측으로 확인해야 합니다.
7. **여러 얼굴 감지의 한계**: MediaPipe는 작은 얼굴을 잘 놓칩니다. 테스트에서 주 얼굴의 0.9배 크기 얼굴은 잡혔지만
   0.5~0.8배는 잡히지 않았습니다. 멀리 뒤에 있는 사람은 "다른 사람"으로 감지되지 않을 수 있습니다.
8. **임계값은 초기값**: 준비 점검·조건 감시·게이지·기준점 분류기의 수치(중앙 이탈 0.35, 얼굴 면적 1.2%~높이 70%,
   고개 이탈 35°/30°, σ 바닥 1.5° 등), 고개 원 확인의 수치(칸을 켜는 회전 14°/9°, 150°/s, 25초)와 에이전트 증거의 수치(대본 3초·화면 5초·다른 곳 2초부터 이슈, 눈맞춤 부족 30%,
   개입 효과 0.2)는 실측 전의 설계값입니다. 처음엔 느슨하게 두고 실제 사용자로 조이는 것을 전제로 합니다.
9. **지연 시간**: CPU에서 doc 7의 125 ms 예산에 맞는 것은 `head_pose`와 `mediapipe_geom`(픽스처 0.21 ms)입니다.
   측정된 L2CS 3종(565 / 228 / 195 ms) 모두 초과합니다. GazeTR은 confidence가 상수 1.0이라
   모든 다운스트림 confidence 게이트가 무효화됩니다.
10. **사용자별 모델은 저장하지 않음 (의도된 동작)**: 기준점·표본·기준 장면은 세션과 함께 사라지고 매 세션 다시 보정합니다.
   logistic 분류기의 `save()`/`load()`는 구현되어 있지만 호출자가 없습니다.
11. **CI 없음**: 1790개 Python 테스트와 웹 테스트 197개가 있지만 자동 실행되지 않습니다. 커밋 전에 직접 돌려야 합니다.
12. **실제 데이터 성능 미측정**: 코드 계약은 검증됐지만 정확도는 아닙니다.
   doc 7 게이트의 6개 임계값은 실제 참가자 녹화본으로 평가된 적이 없으며,
   이것이 현재 가장 큰 공백입니다.
13. **재현성 주장 약화**: 아직 커밋이 없어 `code_commit`이 `unknown`입니다(첫 커밋 후 해소).
   `requirements.txt`는 `kiwisolver` 상한을 빼면 하한(`>=`)만 있고 락파일이 없어
   신선한 설치가 검증 환경을 재현하지 않습니다 — 헤더에 검증 시점 버전을 기록해 두었습니다.
14. **빈 플레이스홀더 트리**: `ai/model_cards/`, `ai/models/embeddings/`, `ai/models/gaze/adapters/`는
    `.gitkeep`으로 클론 후에도 살아남지만 내용은 비어 있습니다.
    이름이 시사하는 작업(모델 카드, 임베딩, 파인튜닝 어댑터)은 아직 없습니다.
15. **Windows 전용 런처**: `run_demo.bat`에 대응하는 셸 스크립트가 없고 Makefile이나 태스크 러너도 없습니다.
16. **`cv2` 섀도잉 위험**: `opencv-python`과 `opencv-contrib-python`이 같은 버전(5.0.0.93)으로 나란히 설치되어
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
| 모든 프레임이 `UNCERTAIN` | ① 백본이 죽었을 수 있습니다. `VisionSession._estimate_gaze`가 모든 예외를 삼키므로 **`session.last_backbone_error`를 확인**하세요. 외부에서는 "사용자가 자리를 비운 것"과 구분되지 않습니다. ② 조건 감시가 얼굴 손실·교체를 보고 판정을 막고 있을 수 있습니다 — `session.last_condition.issues`에 `FACE_LOST` / `FACE_REPLACED`가 있는지 보세요. ③ 보정 품질이 `RETRY_REQUIRED`인데 강제로 넘어왔다면 기준점이 분리되지 않아 대부분 기권합니다 |
| 데모가 `CHECK`에서 넘어가지 않음 | 준비 점검이 통과하지 않았습니다. 화면 체크리스트의 빨간 항목(안내 문구 포함)을 고치세요. 어떤 조건이 실패하는지는 `--check`의 3번 항목에도 나옵니다. 연구용으로 그냥 진행하려면 `C` 또는 `--advisory` |
| 보정 게이지가 차지 않음 | 게이지 아래에 버려지는 이유가 나옵니다. `OUTLIER`면 한 지점을 계속 보세요. `HEAD_MOVED`(눈 기반 백본만)면 고개를 첫 큐 자세로 고정하고 **눈만** 움직이세요. `BLINK`·`LOW_GAZE_CONFIDENCE`면 조명과 안경 반사를 확인하세요. 8초 안에 못 채우면 `TIMED_OUT`이고 엄격 모드에서는 `R`로 그 큐만 다시 합니다 |
| 화면 좌우 끝을 보면 `OTHER`로 나옴 | 실효 화면 너비를 작게 잡은 것입니다. `calibration.screen_aspect`를 키우세요(와이드 모니터일수록 큼). 좌우 반폭은 `screen_max_halfwidth_deg`(12°)에서 잘리므로 그것도 함께 봅니다 |
| 옆을 확실히 봐도 `다른 곳`이 안 나옴 | 실시간 화면의 좌우 값과 지도에서 점이 점선(‘다른 곳’ 경계)을 넘는지 보세요. 보통 보정에서 좌우 약 19~20°부터입니다. 더 일찍 잡으려면 `other_margin_deg`나 `screen_max_halfwidth_deg`를 줄입니다 |
| 혼자인데 '다른 사람'이 나옴 | 별개의 얼굴(겹치지 않고 화면의 1% 이상)이 1초 넘게 잡힌 것입니다. 포스터·사진 속 얼굴이면 화면에서 치우세요. 참고 신호라 신뢰도는 깎지 않습니다 |
| 렌즈를 보는데 '화면'으로 나옴 | 보정 때 렌즈 쪽으로 고개를 평소보다 많이 든 경우입니다. 실시간 화면의 `렌즈 다시 맞추기`(렌즈를 1초 봄)로 렌즈 기준을 다시 잡으세요 |
| 헤드리스 실행이 `blocked at PLACE_RESULT`로 끝남 | 엄격 모드에서 배치를 판정하지 못한 것입니다. 정지 영상(`static_face_30fps.mp4`)처럼 시선이 움직이지 않는 입력에서는 정상입니다. 흐름 끝까지 보려면 `--advisory` |
| 설정을 바꿨는데 반영이 안 됨 | `_build`가 알 수 없는 키를 조용히 버립니다. 키 철자와 **섹션 이름**(`gaze_backbone.yaml` → `backbone`)을 확인하세요. 출하 YAML은 테스트가 오타를 잡지만, `--config-dir`로 넘긴 YAML이나 `overrides`는 검사하지 않습니다 |
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
| **3-2** | 어떤 gaze backbone인가 (조용한 폴백 금지 규칙) | `vision/backbones/*`, `vision/eye/*`, `ai/tools/download_checkpoints.py` |
| **3-3** | 지연 시간 예산 및 백본 선택 | `exp1_backbone.py` |
| **4-1** | Stage-A 녹화 프로토콜 | `ai/tools/collect.py`, `ai/configs/collection.yaml` |
| **4-2** | 라벨링과 전환 가드 밴드 | `vision/data/labels.py` |
| **4-3** | 참가자 분할 및 사용자별 누수 규칙 | `vision/data/splits.py` |
| **5** | 캘리브레이션 전반 (v1.1: 게이지 · 기준점 분류기) | `vision/calibration/*` (`gauge.py`, `references.py`, `factory.py`) |
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
