# 배포 가이드 — 실시간 코치

판단 코어(`src/coach/`)를 AI 서버(`ai/service/`)의 `features/coach/`로 옮겨 BE가 호출하게 할 때 필요한 일을 정리합니다.
아직 서버는 없고, 코어를 코드 변경 없이 폴더째 옮기는 것을 전제로 합니다. 요청 · 응답 필드는 [INTERFACE.md](INTERFACE.md)에 있습니다.

## 배포 구성

```
Take 시작 전 (선택): BE ── POST /coach/plan ──▶ AI ── LLM 1회
                    BE ◀── 코칭 계획 ───────── AI

FE ── 시선 · 음량 1초 기록 · 장 ──▶ BE ── POST /coach/evaluate (1초마다) ──▶ AI (상태 없음)
                                     │     원자료 inputs + plan · missions · coaching_plan + 지난 coach_state
FE ◀── feedback · indicators ─────── BE ◀── action · feedback · indicators · events · coach_state · meta

Take 종료:  BE ── POST /coach/finalize (마지막 창 · events · coach_state [· replay]) ──▶ AI
            BE ◀── take_result · events · meta   (409 REPLAY_REQUIRED 면 replay 를 싣고 다시)
```

- **AI 서버는 아무것도 저장하지 않습니다.** 코치의 기억(`coach_state`)은 응답에 담겨 나가고 BE가 다음 요청에 붙입니다. 서버를 재시작하거나 worker를 늘려도 어느 worker가 받든 판단이 같습니다.
- **코치는 판정 모듈을 import하지 않습니다.** 라우터가 시선 · 속도 · 음량 · 군더더기 모듈(#152~#155)을 `Judges` 묶음으로 만들어 넘깁니다.
- **1초 판단은 LLM을 쓰지 않습니다.** Take 시작 전 코칭 계획(`/coach/plan`)만 LLM을 한 번 부릅니다. 계획을 안 부르거나 실패해도 코칭은 계획 없이 돕니다.
- **같은 요청에는 같은 응답이 나옵니다.** 시간은 요청의 `t_ms`뿐입니다. 코칭 계획만 LLM 답이라 부를 때마다 다를 수 있습니다.

## 옮기기

### 무엇을 가져가나

| 가져가는 것 | 가져가지 않는 것 |
|---|---|
| `src/coach/` 폴더 전부 (`examples/` · `prompts/` 포함) | `src/coach_lab/` (재생 · 실험 도구. 대역 판정 모듈 포함) |
| 의존성은 `pydantic` 하나 | `langchain-openai` · `python-dotenv` (실험용) |

- 코어 안은 **상대 import**뿐이라 폴더 이름이 바뀌어도 코드를 고치지 않습니다.
- 코어는 **파일 · DB · 네트워크 · 환경변수를 쓰지 않고**, 시계 · 난수도 쓰지 않습니다. 로그는 표준 `logging`으로 남기기만 합니다 (형식 · request_id는 service 공통 로깅이 정합니다).
- 코칭 계획의 LLM 클라이언트와 캐시는 코어 밖(service의 LLM 설정)에서 만들어 인자로 넘깁니다.
- 테스트(`tests/unit/`)도 옮깁니다. import의 패키지 이름만 새 이름으로 바꾸고, `test_core_boundary.py`는 코어 폴더를 찾는 상대 경로(`parents[2] / "src" / "coach"`)를 새 위치에 맞춥니다.

### 라우터가 할 일

| 할 일 | 설명 |
|---|---|
| `Judges` 묶음 만들기 | 앱 시작 때 한 번. `Judges(gaze=…, pace=…, volume=…, filler=…, baseline=…)` — 각 모듈은 `judge` · `summarize` · `criteria` 세 함수를 가진 모듈(객체)이고, `baseline`은 `(list[float]) -> float \| None`입니다. 모듈 사이에는 JSON(dict)만 오갑니다. `timing`은 코어 안에 있어 넘기지 않습니다 |
| 호출 | `/coach/evaluate` → `decide_safe(req, judges)`, `/coach/finalize` → `finalize(req, judges)`, `/coach/plan` → `plan_coaching(req, llm=…, model=…)` |
| 오류 매핑 | 요청 형식 오류(pydantic `ValidationError`)는 **422**. `finalize`의 `ReplayRequired`는 **409** `REPLAY_REQUIRED`로 바꾸고 빠진 구간(`e.missing`: `[시작, 끝)` 목록)을 본문에 싣습니다 |
| 예외를 올리지 않는 것 | `decide_safe`는 코치 안의 예외를 `WAIT` + `INTERNAL_ERROR`로 돌려주고, `plan_coaching`은 LLM 실패를 기본 계획 + `fallback_reason`으로 돌려줍니다. 판정 모듈 하나의 예외는 그 영역만 잴 수 없음으로 처리됩니다 |

```python
from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from ..features.coach import Judges, ReplayRequired, decide_safe, finalize, plan_coaching
from ..features.coach.schemas import (
    CoachRequest, CoachResponse, FinalizeRequest, FinalizeResponse, PlanRequest, PlanResponse,
)
from ..features import gaze, pace, volume, filler          # 판정 모듈 #152~#155
from ..llm import coach_plan_llm, settings                 # service 의 LLM 설정

router = APIRouter(prefix="/coach", tags=["coach"])
JUDGES = Judges(gaze=gaze, pace=pace, volume=volume, filler=filler, baseline=volume.baseline)


@router.post("/evaluate", response_model=CoachResponse)
def evaluate(req: CoachRequest) -> CoachResponse:            # 형식 오류는 FastAPI 가 422
    return decide_safe(req, JUDGES)


@router.post("/finalize", response_model=FinalizeResponse)
def finalize_take(req: FinalizeRequest) -> FinalizeResponse:
    try:
        return finalize(req, JUDGES)
    except ReplayRequired as e:
        raise HTTPException(409, {"code": "REPLAY_REQUIRED", "missing": e.missing, "message": str(e)})


@router.post("/plan", response_model=PlanResponse)
def plan(req: PlanRequest) -> PlanResponse:
    return plan_coaching(req, llm=coach_plan_llm, model=settings.llm_model)
```

- 판단은 CPU만 쓰는 짧은 계산이라 동기 함수(`def`)로 두면 FastAPI가 스레드 풀에서 돌립니다. `/plan`도 동기 함수이고 LLM을 기다리는 동안 스레드 하나를 씁니다 (Take 당 한 번).
- 판정 모듈의 `judge`는 `(inputs: dict, t_ms: int)`를 받아 `JudgmentResult` 모양(모델 또는 dict)의 목록을 냅니다. `since_ms`로 이미 센 곳을 건너뛰는 계약이라, 모듈은 상태를 갖지 않아야 합니다.
- 코칭 계획 LLM은 출력 모양을 `PlanDraft`로 고정한 클라이언트를 앱 시작 때 한 번 만듭니다 (research의 `coach_lab/llm.py`와 같은 모양: `ChatOpenAI(..., timeout=20, max_retries=0).with_structured_output(PlanDraft)`). SDK의 `timeout`은 전체 시간을 보장하지 않으니 전체 상한은 BE 타임아웃이 맡습니다. 캐시가 필요하면 `PlanCache`(`get` · `put`)를 구현해 `cache=`로 넘깁니다.

### 그 밖의 서버 작업

- 서버 뼈대 (별도 작업): FastAPI 앱, `/health` · `/ready`, Python 3.12.
- 버전: 응답 `meta`에 `schema_version` · `feature_version` · `criteria_versions`가 이미 실려 있습니다. service 공통 응답 메타 형식이 정해지면 맞춥니다.
- 계약 파일(OpenAPI)과 계약 테스트. 요청 · 응답 예시는 `src/coach/examples/`의 실제 출력을 씁니다 (`tests/unit/test_examples.py`가 예시를 코치에 넣어 맞는지 봅니다).
- CI (ruff · pytest).

## BE가 할 일 (BE와 협의)

### 호출

- **Take 시작 전 `/coach/plan` (선택)**: 계획 · 장별 대본 · 미션 · 기억 · 직전 리뷰 요약을 보내고, 응답의 `plan`을 Take 동안 보관했다가 매 `/coach/evaluate`의 `coaching_plan`에 싣습니다. 실패하면 `coaching_plan: null`로 진행합니다. 입출력은 후속 작업(#162)에서 Take 결과 · 리뷰 출력에 맞춰 바뀝니다.
- **1초마다 `/coach/evaluate`**: 타임아웃은 짧게 둡니다. 실패하면 그 1초는 건너뛰고 `coach_state`는 바꾸지 않습니다. 이전 `coach_state`로 다시 보내면 같은 판단이 나오고, 이미 처리한 `t_ms`는 `STALE_TICK`으로 아무것도 바꾸지 않습니다. 422는 그 1초를 건너뜁니다.
- **Take 종료 `/coach/finalize`**: 마지막 창 · 쌓은 이벤트 · 마지막 `coach_state`를 보냅니다. **409 `REPLAY_REQUIRED`**를 받으면 Take 전체 원자료를 `replay`에 실어 다시 부릅니다 (아래 저장 항목이 이 때문에 필요합니다).

### 요청 만들기

- 시선: FE가 보낸 1초 기록을 가공하지 않고 `inputs.gaze_records`에 넣습니다. 음량 `voice_records`도 같습니다. 두 기록은 최근 30초만 보내면 됩니다.
- STT: 확정 단어 `words`와 문장 끝 `utterance_ends`는 최근 60초, `stt_status`는 지금 상태입니다.
- 장: `inputs.slide = {number, started_ms}`.
- 계획 · 미션: 대본 분석의 장별 목표 시간 · 글자 수(`script_chars`), 전체 허용 범위(`min_ms` · `max_ms`), 직전 리뷰의 다음 미션과 "아직 남은 문제"(`recurring_issues`).
- 평소 목소리 기준 `calibration.base_level_db`: 없으면 코치가 Take 첫 발화로 잡습니다. 처음부터 작게 말하면 기준도 낮게 잡히므로 발표 전 점검에서 재 두면 더 정확합니다.

### 저장

| 저장하는 것 | 이유 |
|---|---|
| `coach_state` **최신 하나만** (Take 동안) | 다음 요청에 그대로 붙인다. 내용은 몰라도 된다. 지난 것은 지워도 된다 |
| 코치 이벤트 **전부** (받은 순서대로, 추가만) | `finalize`에 그대로 돌려주고, 문제 구간 · 개입 · 포기의 원천이다. `event_id`가 같으면 같은 이벤트라 두 번 저장해도 걸러진다 |
| **원자료** (Take 전체의 시선 · 음량 1초 기록, STT 단어와 확정된 시각 `final_at_ms`, STT 상태 변화, 문장 끝, 장 전환) | `REPLAY_REQUIRED`일 때 `replay`로 보내 처음부터 다시 판정하기 위해 |
| **Take 결과**(`take_result`)와 `meta` | 리뷰의 입력. `meta`의 기준 버전으로 어떤 판정 기준이었는지 추적한다 |
| 띄운 피드백 | `feedback`의 `instruction` · `priority` · `evidence`까지 담을 곳이 필요하다 |

## 결정해야 할 것

- 원자료를 BE 어디에 얼마나 보관할지 (`replay`가 필요한 드문 경우를 위해 Take 동안, 또는 종료 분석이 끝날 때까지).
- 평소 목소리 캘리브레이션을 둘지와 `calibrations.base_volume`을 `base_level_db`로 쓸지.
- 코칭 계획을 언제 부를지: Take 시작 버튼 뒤(몇 초 대기)인지, 이전 Take 종료 분석 직후에 미리 불러 둘지. 계획 모델과 비용.
- `indicators`를 FE에 띄울지와 모양.
- 기준값(대본 응시 · 말 속도 · 음량 등)은 판정 모듈이 갖습니다. 실제 연습 데이터로 다시 골라야 하는 값입니다.

## 옮긴 뒤

- [ ] 단위 테스트 통과 (코어 경계 테스트 포함)
- [ ] research가 service를 editable 의존성으로 설치하고 `coach_lab`의 `from coach…` import를 새 패키지 이름으로 바꿉니다. 대역 판정 모듈은 기능 모듈(#152~#155)로 바꿔 끼웁니다.
- [ ] 같은 결과인지 확인: `python -m coach_lab.replay`가 전부 통과하고, `python -m coach_lab.evaluate --sweep --out reports/results/take_result.json` · `python -m coach_lab.recovery_eval` 뒤 `git diff`가 비어 있는지 봅니다.
- [ ] `python -m pytest -m live`(실제 LLM 1회)와 `python -m coach_lab.plan_eval`이 커밋된 `reports/results/coaching_plan.json`과 비슷한지 봅니다 (LLM 답이라 바이트 단위로 같지는 않습니다).
- [ ] research의 `src/coach/`를 지웁니다. 이후 코어는 service에서만 고칩니다.
