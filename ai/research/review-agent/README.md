# review-agent (리뷰)

Take가 끝난 뒤 코치의 **Take 결과**와 **대본 전달도 채점**을 읽어 Take를 평가합니다.
**결론**(차원 상태 · 점수 · 미션 판정)과 **다음 목표**(다음 미션 · 반복 문제)를 정하고, 리포트에 쓸 문장을 만듭니다.

| | |
|---|---|
| 담당 | seojin-lee |
| 판단 방식 | 규칙 단계가 숫자 · 상태 · 점수를 정합니다. 1차는 LLM 없이 기본 문장을 냅니다. 2차에 LLM 1번(문장 · 다음 미션 고르기)을 더할 예정입니다 |
| 판정 | 지표를 다시 계산하지 않습니다. 코치 Take 결과 · 대본 전달도 값을 읽기만 합니다 |
| 기능 버전 | `review-1.0`, 요청 · 응답 스키마 `1.0` (`src/review/version.py`) |
| 상태 | 패키지 + 테스트. **명세 예시를 바탕으로 만든 입력으로만 확인했습니다.** 기준값은 실제 발표 데이터로 검증하지 않았습니다 |
| 출처 | `ai/archive/workspaces/seojin-lee/review-agent/v1/local/review.ipynb`를 옮겼습니다 (같은 입력에 같은 결과인지 비교함) |
| 배포 | 리뷰 코어(`src/review/`)는 나중에 AI 서버로 그대로 옮깁니다 → [DEPLOY.md](DEPLOY.md) |
| 입출력 | BE ↔ 리뷰 계약 → [INTERFACE.md](INTERFACE.md) |

## 1. 이 기능이 하는 일

```
BE ── POST /takes/review ──▶ review_take()
      take_result (코치)              ├─ 규칙 단계: 판단 가능 → 미션 판정 → 부담 → 차원 상태
      script_coverage (대본 전달도)    │            → 사용자 기준 → 다음 미션 → 반복 문제 → 점수
      missions · user_criteria         └─ 기본 문장 (2차: LLM 1번)
      previous_recurring_issues
BE ◀── 보고서 · 점수 · 차원 상태 · 미션 결과 · 다음 미션 · 반복 문제 · meta
```

- **다음 Take로 이어지는 값:** `next_missions`는 다음 Take의 `missions`가 되고, `recurring_issues`는 다음 Take의 `recurring_issues`(코칭 계획 · 코치)와 다음 리뷰의 `previous_recurring_issues`가 됩니다.
- **AI는 아무것도 기억하지 않습니다.** 이전 Take의 정보는 BE가 요청에 실어 줍니다.

### 1차 범위

| 명세 3-2 단계 | 1차 | 비고 |
|---|---|---|
| 1 판단 가능 여부 | 함 | |
| 2 문제 부담 | 함 | 반복 문제 가중 1.2, 포기 · 미션 실패 가중은 1.0(가중 없음) |
| 3 차원 상태 | 함 | |
| 4 미션 판정 | 함 | |
| 5 이전 Take 비교 | **안 함** | `comparison: []`. `previous_takes`는 받기만 합니다 |
| 6 사용자 기준 | 함 | |
| 7 다음 미션 후보 | 함 | `pacing_rhythm`은 다음 미션을 만들지 않고 반복 문제로만 넘깁니다 |
| 8 반복 문제 | 함 | `dimension` 필드를 더했습니다 (5절) |
| 9 점수 | 함 | |
| LLM 단계 · 숫자 검사 | **안 함** | 기본 문장 + 후보 상위 미션, `fallback_reason: "LLM_UNAVAILABLE"` |

## 2. 코드 구조

코어 하나뿐입니다. 시나리오 재생이나 LLM 실험 도구(`review_lab`)는 2차에서 필요해지면 만듭니다.

```
src/review/        리뷰 코어 → 나중에 AI 서버의 features/review/ 로 폴더째 복사
```

코어의 규칙은 coach-agent와 같습니다 (`tests/unit/test_core_boundary.py`가 코어 파일을 읽어 검사합니다).

| 규칙 | 이유 |
|---|---|
| 패키지 안에서는 **상대 import만** | 폴더 이름이 바뀌어도 고칠 import가 없다 |
| **파일 · DB · 네트워크 · 환경변수 · 시계 · 난수를 쓰지 않음** | 같은 요청에 같은 응답. 저장은 BE가 맡는다 |
| 의존성은 **pydantic 하나** | 서버로 옮길 때 가져갈 라이브러리가 하나뿐이다 |
| **기준값은 `config.py`에만** | 바꾸면 응답 `meta.criteria_versions.review`의 해시가 바뀐다 |
| **결과 순서를 `set`으로 정하지 않음** | 문자열 `set`의 순서는 실행마다 달라진다. 순서는 차원 표 순서나 명시적 정렬로 정한다 |

## 3. 폴더와 파일

```
review-agent/
├─ README.md · INTERFACE.md · DEPLOY.md
├─ pyproject.toml
├─ src/review/
│   └─ examples/review/   BE 개발자용 요청 · 응답 예시 (계약 테스트가 응답과 맞는지 본다)
└─ tests/unit/           코어 경계 · 단계별 규칙 · 예시 계약
```

| 파일 | 하는 일 | 명세 3-2 |
|---|---|---|
| `core.py` | 유일한 진입점 `review_take` · `review_take_traced`. 단계 실행 순서를 정한다 | 전체 |
| `schemas.py` | 요청 · 응답 모양의 단일 진실 원천 (pydantic). 입력은 모르는 필드 무시, 출력은 정해진 필드만 | 3-1 · 3-3 |
| `config.py` | 모든 기준값 · 가중치 · 다음 미션 지표. `load_config()`로 덮어쓰기, `criteria_version()` | — |
| `vocab.py` | 고정 어휘와 **차원 표**(차원 → 영역 · 지표 · 부담 문제) | 3-3 표 |
| `version.py` | 스키마 · 기능 버전 | — |
| `metrics.py` | 지표 이름으로 값 찾기(`find_metric`), 비교(`compare`) | — |
| `evaluable.py` | 측정이 부족한 차원을 `NOT_EVALUABLE`로 | 1 |
| `burden.py` | 차원별 부담(합계 · 장별 · 영역별), 반복 문제 차원, 부담이 몰린 장(`focus_slide`) | 2 |
| `dimensions.py` | 차원 상태 | 3 |
| `missions.py` | 미션 · 사용자 기준 판정 (같은 규칙 `judge_target`) | 4 · 6 |
| `next_missions.py` | 다음 미션 후보와 최종 선택 | 7 |
| `recurring.py` | 반복 문제 | 8 |
| `score.py` | 점수 | 9 |
| `text.py` | 기본 문장 (요약 · 강점 · 개선점)과 표시 이름 | LLM 실패 시 |

## 4. 판단 규칙

실행 순서는 명세 단계 번호와 다릅니다. 부담 · 차원 상태가 미션 결과를 쓰므로 **미션 판정을 먼저** 합니다.

```
판단 가능 → 미션 판정 → 부담 → 차원 상태 → 사용자 기준 → 다음 미션 → 반복 문제 → 점수 → 기본 문장
```

### 4-1. 판단 가능 여부
- 차원에 속한 영역 중 **하나라도** `measured_ratio`가 기준 미만이면 `NOT_EVALUABLE`입니다 (`pacing_rhythm`은 SPEED · PAUSE · TIME 셋).
- `content_delivery`는 대본 전달도가 없거나 `take_scores.scored_ratio`가 기준 미만이면 `NOT_EVALUABLE`입니다.

### 4-2. 문제 부담
- **문제 구간:** `reliable`인 구간마다 `mean_severity × 초`를 차원별로 더합니다. `GAZE_ON_SCREEN` · `GAZE_UNMEASURABLE` · `FINAL_MINUTE`, 판단 불가 차원의 문제는 세지 않습니다. `script_used`가 true면 `GAZE_ON_SCRIPT`를 세지 않습니다.
- **대본 전달도:** `key_points` 중 `missing` · `contradicted`를 `importance`별 부담(critical 9 · high 6 · normal 3, `content_coverage` 가중치와 같은 3:2:1)으로 더합니다. `importance`가 없거나 모르는 값이면 normal로 봅니다. `critical_facts`는 세지 않습니다. 틀린 수치는 이미 그 핵심 내용의 `contradicted`에 반영되어 있어 두 번 세게 되기 때문입니다.
- **가중:** 직전 반복 문제 · 코치가 포기한 문제 · 이루지 못한(`FAILED` · `PARTIAL`) 미션의 차원은 이유마다 배수를 한 번씩 곱합니다.

### 4-3. 차원 상태
위에서부터 처음 맞는 상태입니다.

| 상태 | 조건 |
|---|---|
| `NOT_EVALUABLE` | 판단 불가 |
| `PRIORITY` | 부담 ≥ 기준(10) 중 부담 큰 순(같으면 이름순) 3개 |
| `IMPROVED` | 직전 반복 문제였는데 이번 부담이 기준 **미만**, 또는 미션을 이루고 부담 0 |
| `STRENGTH` | 부담 0 |
| `STABLE` | 그 밖 (부담은 있지만 PRIORITY는 아님. 개수 상한에서 밀린 반복 문제 포함) |

### 4-4. 미션 · 사용자 기준 판정
지표 이름으로 값을 찾아(Take 결과 → 대본 전달도, 장 미션이면 장 값) 비교합니다. 달성하면 `ACHIEVED`, 목표 쪽으로 모자란 정도가 허용치 이내면 `PARTIAL`, 그 밖은 `FAILED`입니다. 개수 지표에는 허용치가 없습니다. 값이 없거나 측정 비율이 기준 미만이면(대본 전달도는 채점 실패한 장) `NOT_EVALUABLE`입니다.

### 4-5. 다음 미션
1. **사용자 기준 `FAILED`:** 사용자가 정한 목표 그대로 먼저 둡니다. 이것끼리는 지표가 속한 차원의 부담 큰 순, 같으면 `targets` 순서입니다. 2차의 LLM도 빼거나 순서를 바꾸지 못합니다.
2. **PRIORITY 차원마다 하나:** 부담 큰 순입니다. 목표는 지금 값 + `step`이고 `final`을 넘지 않습니다. 이미 `final`에 닿았으면 만들지 않습니다.
   - 부담이 가장 큰 장이 전체의 50%를 넘으면 장 미션입니다(내용은 늘 장 미션). 그 장 값이 없으면 Take 미션으로 바꿉니다.
   - 같은 (지표, 장)은 하나만 둡니다.
3. 후보는 5개까지 만들고, 최종 3개를 고릅니다(1차: 후보 상위). 둘 다 Take 미션과 장 미션을 합친 개수입니다.

### 4-6. 반복 문제 · 점수 · 문장
- **반복 문제:** PRIORITY 차원마다 `{area, slide_number, dimension}`입니다. 영역이 여럿인 `pacing_rhythm`은 부담이 가장 큰 영역을, 장은 다음 미션과 같은 규칙으로 고릅니다.
- **점수:** STRENGTH 100 · IMPROVED 85 · STABLE 70 · PRIORITY 40의 평균입니다(`NOT_EVALUABLE` 제외). 평가할 차원이 없으면 `null`입니다.
- **기본 문장:** 문장 안 숫자는 장 번호뿐이고, 지표 값은 `evidence`에 입력 그대로 싣습니다.

## 5. 명세와 다른 점 · 팀 논의

| 항목 | 내용 | 논의할 곳 |
|---|---|---|
| **`recurring_issues.dimension`** | 명세 형식 `{area, slide_number}`에 `dimension`을 더했습니다. GAZE 영역은 `audience_gaze` · `script_independence` 두 차원이라, area만으로는 다음 리뷰가 어느 차원이 반복 문제였는지 모릅니다 (문제가 없던 차원을 IMPROVED로 잘못 판정). `dimension`이 없는 예전 리뷰는 area의 차원 전부로 봅니다 | 명세 3-1 · 3-3, BE(다음 리뷰 요청에 그대로 싣는지), 코치(모르는 필드 무시 확인) |
| **사용자 기준 FAILED끼리의 순서** | 명세에 없어 "차원 부담 큰 순, 같으면 targets 순서"로 정했습니다. targets가 사용자 입력 순서로 오도록 평가 기준 분석과 맞춰야 합니다 | 평가 기준 분석 |
| **다음 미션 지표 (`mission_metric`)** | 명세에 없어 정했습니다. `script_ratio`는 명세 예시와 같습니다. `pacing_rhythm`은 문제마다 방향이 달라 1차에서 만들지 않습니다 | 명세 3-2 7단계 |
| **약한 강점** | 문제 구간이 없으면 지표 값이 낮아도 STRENGTH가 됩니다 (예시의 `audience_ratio` 0.58) | 6절 "거짓 강점" 지표와 함께 |
| **같은 지표 미션** | 같은 지표로 Take 미션(사용자 기준)과 장 미션(PRIORITY)이 함께 나올 수 있습니다 | — |
| **대본 전달도 `failed` 모양** | 장 번호 또는 `{slide_number, ...}`를 모두 받습니다 | 대본 전달도 |
| **지표 표시 이름** | 미션 설명의 지표 이름(`text.METRIC_LABELS`)은 명세에 없어 정했습니다 | FE |

## 6. 실행법

```bash
cd ai/research/review-agent
uv sync                                   # Python 3.12, pydantic + 개발 도구 설치
```

LLM을 쓰지 않아 `ai/.env`가 필요 없습니다. Windows에서는 `uv run python -m …` 형태로 부르고, 콘솔 한글이 깨지면 `PYTHONIOENCODING=utf-8`을 붙입니다.

| 하는 일 | 명령 |
|---|---|
| 테스트 | `uv run python -m pytest` |
| 린트 | `uv run ruff check . && uv run ruff format --check .` |

파이썬에서 직접

```python
from review import load_config, review_take, review_take_traced

resp = review_take(request_dict)               # ReviewResponse. dict 로: resp.model_dump(mode="json")
resp, trace = review_take_traced(request_dict) # trace.evaluable · trace.burden · trace.next_mission_candidates
resp = review_take(request_dict, load_config(dimension={"priority_max_count": 2}))  # 설정 덮어쓰기
```

설정은 `config.py`에만 있습니다. 코드를 고치지 말고 바꿀 값만 `load_config(...)`로 덮어쓰세요.

## 7. 알려진 한계

- **모든 기준값이 잠정입니다.** 공통 기준 Take(명세 6절)로 "차원 상태 · 1순위 · 미션 판정 정답 일치", "거짓 강점", "반복 PRIORITY 비율"을 재며 조정해야 합니다.
- **LLM 단계 · 숫자 검사 · 이전 Take 비교가 없습니다.** 2차 작업입니다.
- 대본 전달도 입력은 대본 전달도 문서의 예시 모양을 따랐습니다. 실제 `/takes/analyze` 출력으로 다시 확인해야 합니다.
