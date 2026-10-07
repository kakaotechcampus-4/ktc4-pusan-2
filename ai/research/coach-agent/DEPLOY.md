# 배포 가이드 — 실시간 코치

이 기능을 AI 서버(`ai/service`)에 올려 BE가 호출하게 하려면 무엇이 필요한지 정리합니다.
아직 서버는 없고, 판단 코어(`src/coach/`)를 그대로 옮겨 쓰는 것을 전제로 합니다. 요청 · 응답 필드는 [INTERFACE.md](INTERFACE.md)에 있습니다.

## 배포 구성

```
Take 시작 전 (선택): BE ── POST /coach/plan (계획 · 장별 대본 · missions · memory · 직전 리뷰 근거) ──▶ AI ── LLM 1회
                    BE ◀── 계획이 든 첫 coach_state ── AI

FE ── 시선 기록 · 음량 · 슬라이드 ──▶ BE ── POST /coach/evaluate (1초마다) ──▶ AI 서버 (상태 없음)
                                     │       지금 측정값 + plan · missions · memory + 지난 coach_state
FE ◀── feedback · indicators ─────── BE ◀── action · feedback · indicators · events · coach_state
                                     │
                                     ├─ coach_state: Take 동안 보관했다가 다음 요청에 그대로 붙임
                                     └─ events: Take 별로 그대로 쌓음 (추가만)

Take 종료: BE ── POST /coach/finalize ──▶ AI   남은 문제 구간 · 재지 못한 효과를 events 로
          BE ── 종료 분석 (/takes/analyze, 쌓인 events + plan · missions · memory) ──▶ AI
                 └─ 안에서 build_review_evidence() → 리뷰 에이전트 입력
```

- **AI 서버는 아무것도 저장하지 않습니다.** 코치의 기억(`coach_state`)은 응답에 담겨 나가고, BE가 다음 요청에 그대로 붙입니다.
  서버를 재시작하거나 worker를 늘려도, 같은 Take의 요청이 어느 worker로 가든 판단이 같습니다.
- **1초 판단은 LLM을 쓰지 않습니다.** Take 시작 전 코칭 계획(`/coach/plan`)만 LLM을 한 번 부릅니다. 그래서 LLM 키 · 모델 설정이 필요하고 Take 당 한 번 비용이 듭니다.
  계획을 부르지 않거나 실패해도 코칭은 계획 없이 그대로 돕니다.
- **같은 요청에는 같은 응답이 나옵니다.** 시간은 요청의 `t_ms`뿐이라, research의 재생 결과가 배포 결과와 같습니다.
  코칭 계획만 LLM 답이라 부를 때마다 다를 수 있습니다. 계획이 든 `coach_state`를 받은 뒤의 판단은 같습니다.

## 필요한 작업

### AI 서버 (`ai/service/`)

- [ ] 서버 뼈대 (별도 작업): FastAPI 앱, `/health` · `/ready`, Python 3.12
- [ ] 코어 옮기기: `src/coach/` → `src/pitch_coach_ai/features/coach/`
  - 코어 안은 상대 import라 코드 변경이 없습니다. 의존성은 `pydantic` 하나입니다. 코칭 계획의 LLM 클라이언트는 코어 밖(service의 LLM 설정)에서 만들어 넘깁니다.
- [ ] 테스트 옮기기: `tests/unit/` → `tests/unit/coach/`
  - 테스트의 import에서 패키지 이름만 `coach` → `pitch_coach_ai.features.coach`로 바꿉니다 (`from coach import …` · `from coach.config import …` 모두). `from .conftest import …`는 그대로입니다.
  - `test_core_boundary.py`는 코어 폴더를 이 파일 기준 상대 경로(`parents[2] / "src" / "coach"`)로 찾으므로 그 경로 계산을 새 위치에 맞춥니다.
- [ ] 라우터 `api/coach.py`

  ```python
  from fastapi import APIRouter

  from ..features.coach import decide_safe, finalize, plan_coaching
  from ..features.coach.schemas import (
      CoachRequest,
      CoachResponse,
      FinalizeRequest,
      FinalizeResponse,
      PlanRequest,
      PlanResponse,
  )
  from ..llm import coach_plan_llm, settings  # service 의 LLM 설정 (앱 시작 때 한 번 만든다)

  router = APIRouter(prefix="/coach", tags=["coach"])


  @router.post("/evaluate", response_model=CoachResponse)
  def evaluate(req: CoachRequest) -> CoachResponse:  # 형식 오류는 FastAPI 가 422 로 돌려준다
      return decide_safe(req)  # 코치 안의 예외는 WAIT + 이전 coach_state 로 돌려준다


  @router.post("/finalize", response_model=FinalizeResponse)
  def finalize_take(req: FinalizeRequest) -> FinalizeResponse:
      return finalize(req)


  @router.post("/plan", response_model=PlanResponse)
  def plan(req: PlanRequest) -> PlanResponse:
      # LLM 실패 · 시간 초과는 기본 계획 + fallback_reason 으로 돌려준다 (예외를 올리지 않는다)
      return plan_coaching(req, llm=coach_plan_llm, model=settings.llm_model)
  ```

  - 판단은 CPU만 1ms 안쪽으로 쓰므로 동기 함수(`def`)로 둡니다. FastAPI가 스레드 풀에서 돌립니다.
  - `/plan`도 동기 함수입니다. LLM을 기다리는 2.5~8초 동안 스레드 하나를 씁니다 (Take 당 한 번).
- [ ] 코칭 계획 LLM: service의 LLM 설정(키 · 모델 · base URL)으로 출력 모양을 `PlanDraft`로 고정한 클라이언트를 앱 시작 때 한 번 만듭니다.
  research의 `src/coach_lab/llm.py`와 같은 모양입니다: `ChatOpenAI(..., timeout=20, max_retries=0).with_structured_output(PlanDraft)` (재시도 없이 한 번. 실험 최대 8.1초).
  SDK의 `timeout`은 연결 · 읽기마다 걸리는 값이라 전체 시간을 보장하지 않습니다. 전체 상한은 BE 타임아웃이 맡고, 서버에서도 막으려면 호출 전체에 시간 제한을 둡니다.
  캐시는 서버에 두지 않아도 됩니다(같은 Take를 다시 계획하지 않음). 두려면 `PlanCache`(`get` · `put`)를 구현해 `cache=`로 넘깁니다.
- [ ] 리뷰 근거는 API로 따로 열지 않습니다. 종료 분석(`/takes/analyze`) 안에서 `build_review_evidence(take_id, events, plan=, missions=, memory=)`를 부르고, 그 결과를 리뷰 에이전트에 넘깁니다.
- [ ] 버전: 1초 응답과 리뷰 근거에 이미 `schema_version` · `policy_version` · `config_hash`가 실려 있습니다 (이벤트 하나하나에는 없습니다). service 공통 응답 메타 형식이 정해지면 `version.py`의 값을 거기에 싣습니다.
- [ ] 로그: 코어는 `logging.getLogger(__name__)`로 남기기만 합니다(`decide_safe`의 예외). 형식 · request_id · 출력 위치는 service 공통 로깅이 정합니다.
- [ ] 계약 파일(OpenAPI · 요청/응답 예시)과 계약 테스트. 예시는 INTERFACE.md의 실제 출력에서 가져옵니다.
- [ ] CI (ruff · pytest)

### BE (BE와 협의)

- [ ] **Take 시작 전 `/coach/plan` (선택).** 이번 Take의 `plan` · 장별 대본(`scripts`, 대본 분석의 장별 원문) · `missions` · `memory`와
  직전 Take의 리뷰 근거(`build_review_evidence` 결과 그대로, 없으면 `null`)를 보내고, 응답의 `coach_state`를 첫 `/coach/evaluate`에 붙입니다.
  타임아웃은 약 25초(서버 LLM 요청 제한 20초 + 여유)이고, 실패하면 `coach_state=null`로 시작합니다. 실전 모드는 부르지 않아도 됩니다.
  응답의 `plan` · `planner_hash`를 Take와 함께 남기면 리뷰가 "이번에는 3번 장 시선을 봐줬다"를 말할 수 있습니다.
  직전 리뷰 근거를 넘기려면 종료 분석 때 만든 리뷰 근거를 Take별로 보관해야 합니다.
- [ ] **1초마다 `/coach/evaluate` 호출.** 타임아웃은 짧게(약 300ms) 둡니다. 실패하면 그 1초는 건너뛰고 `coach_state`는 바꾸지 않습니다.
  응답을 받지 못해 이전 `coach_state`로 다시 보내면 같은 판단 · 같은 이벤트가 다시 나오므로 그 응답 하나만 쓰면 됩니다. 이미 받은 응답의 `coach_state`로 같은 `t_ms`를 보내면 `STALE_TICK`으로 아무것도 바뀌지 않습니다.
- [ ] **응답 나눠 담기**
  - `feedback`(INTERVENE일 때만) → FE로 전달
  - `events` → Take별로 그대로 쌓기 (추가만). 그 응답의 `policy_version` · `config_hash`도 함께 남기면 어느 규칙으로 낸 기록인지 알 수 있습니다
  - `coach_state` → Take 동안 보관했다가 다음 요청에 그대로 붙이기. Take마다 한 개씩 있는 `TakeStream`이 들고 있기 좋습니다. 내용은 몰라도 됩니다
- [ ] **Take 종료**: `/coach/finalize`의 events까지 쌓은 뒤, 종료 분석 요청에 코치 이벤트 전체와 이 Take의 plan · missions · memory를 넣습니다.
- [ ] **요청 만들기**
  - 시선: FE가 보낸 1초 기록을 Take마다 최근 10초만 들고 있다가 `gaze.records`에 넣습니다. 가공하지 않고 그대로 넣으면 됩니다
  - STT 단어: 최근 15초. `w` ← Deepgram `word`, `final` ← 그 transcript 의 `is_final`. `filler` 표시는 선택이다 — 없으면 코치가 소리뿐인 간투사(음 · 어 …)만 센다. '그' · '이제' 같은 말까지 세려면 BE 가 문맥으로 판단해 `filler: true`를 붙인다. 문장 끝 신호 `utterance_end_ms`(마지막 `speech_final`의 `end_ms`)
  - 계획: 대본 분석의 장별 목표 시간 · 글자 수 · 필수 키워드, 전체 허용 범위(`min_ms` · `max_ms`)
  - 미션 · 기억: 직전 리뷰의 다음 미션과 '아직 남은 문제'
- [ ] **저장 공간**
  - 이벤트: `takes.event_logs`(JSONB)에 `kind`별로 그대로 넣을 수 있습니다.
  - 띄운 피드백: `live_feedbacks`에는 `type` · `message` · `triggered_at_ms` · `confidence`만 있어서, `instruction` · `priority` · `evidence`를 담을 곳이 필요합니다.

### FE (제안)

- [ ] 1초마다 BE로 보냅니다.
  - 시선: FE가 이미 만드는 1초 판정(`ZoneDecision`)을 `{t_ms, duration_ms, state}`로. `state` = `zone`, `t_ms` = `tMs` − 1000 (Take 시작 기준 ms로 바꾼 값). `tMs`는 지난 1초의 프레임을 모아 낸 판정 시각이라 그 판정이 덮는 시간은 앞 1초입니다. 사후 구간을 만드는 `gazeSegments`는 `tMs`를 구간 시작으로 쓰니 그 값을 그대로 넣지 않습니다. `tMs`는 1초 격자에 맞춰 올라가므로 이렇게 바꾼 기록은 빈틈없이 이어집니다. 3구역(CAMERA · BOTTOM · UNCERTAIN) 그대로 보내도 되고, 6상태(SCREEN · OTHER · UNMEASURED)를 보내도 코치가 받습니다. 최근 창 비율을 FE가 계산할 필요는 없습니다
  - 음량: 지난 1초 동안 말한 소리의 레벨 `level_db`(A 가중 dBFS. 말하지 않았으면 null) · `silence_ms` · `audio_live`. 평소 목소리 캘리브레이션이 있으면 `baseline_db`도 보내고, 없으면 코치가 첫 발화로 기준을 잡는다.
    `useMicLevel`에는 이 값이 아직 없어 1초 집계를 더해야 합니다 — `db`는 순간 레벨(Fast 가중)이고 `speechLeqDb`는 Take 처음부터 말한 구간 전체의 평균입니다. `speechLeqDb` · `speechMs`를 1초마다 읽어 두면 지난 1초의 발화 레벨은 10·log10((E₁ − E₀) / (ms₁ − ms₀)), E = 10^(Leq/10) × ms 로 구할 수 있습니다 (ms₁ = ms₀ 이면 null)
  - 슬라이드 번호 · 체류 시간
- [ ] `feedback.message`를 화면에 띄웁니다. 한 번에 하나만 옵니다. `indicators`(시간 진행 · 속도 · 시선 · 음량 상태)는 띄울지 FE가 정합니다.

## 비용과 시간

| 항목 | 값 |
|---|---|
| LLM 호출 | 1초 판단 0회. 코칭 계획 Take 당 1회 (계획 실험 25번: 중앙값 2.6초 · 평균 3.1초 · 최대 8.1초, `openai/gpt-5.6-luna`) |
| 호출 수 | Take 시작 전 계획 1회(선택) + Take 1분에 약 60회 + 종료 1회 |
| 판단 1회 | 시나리오별 p50 0.4ms · p95 1ms 이하 (재생 16개, 개발 PC · Python 3.12. PC 부하에 따라 달라진다) |
| `coach_state` 크기 | 재생 시나리오 16개에서 응답마다 잰 최대 약 15KB. 1초마다의 기록은 60초만 남기고, 10분짜리 재생의 끝 상태가 20KB 미만인 것을 테스트로 확인한다. 이 크기가 요청 · 응답에 매번 실린다 |

## 결정해야 할 것

- **시선 1초 기록을 FE → BE로 보내는 메시지**: 지금 FE는 시선을 종료 때 구간 요약으로만 보냅니다. 1초마다 보낼 WS 메시지 이름과 묶음 단위(1초마다 하나 · 몇 초씩 묶어서)를 FE · BE가 정합니다. 코치는 `gaze.records`에 최근 10초가 들어오기만 하면 됩니다.
- **평소 목소리 캘리브레이션을 둘지**: 없어도 코치가 첫 발화 15초로 기준을 잡습니다. 다만 처음부터 작게 말하면 기준도 낮게 잡혀 '작음'을 놓치므로, 발표 전 점검 화면에서 평소 목소리를 재 `baseline_db`로 보내면 더 정확합니다. BE `calibrations.base_volume`(지금은 '조용한 환경의 기준 음량')을 이 값으로 쓸지 FE · BE와 정합니다.
- **코칭 계획을 언제 부를지**: Take 시작 버튼을 누른 뒤 2.5~8초를 기다리게 할지, 입력(대본 · 미션 · 직전 리뷰)이 다 정해지는 이전 Take 종료 분석 직후에 미리 불러 둘지.
  계획 모델과 비용도 정합니다.
- `coach_state` · 이벤트를 BE 어디에 둘지, `live_feedbacks`를 넓힐지
- `indicators`를 FE에 띄울지와 모양
- 기준값(대본 응시 70% · 350 CPM · −6dB 등): 실제 연습 데이터로 다시 고를 값입니다. 바꿔도 버전은 그대로이고 `config_hash`가 달라집니다.

## 옮긴 뒤

- [ ] 단위 테스트 통과 (코어 경계 테스트 포함)
- [ ] research가 service를 editable 의존성으로 설치하고, `coach_lab`의 `from coach…` import를 `pitch_coach_ai.features.coach…`로 바꿉니다.
- [ ] 같은 결과인지 확인: `python -m coach_lab.replay`가 16/16이고 결과 JSON이 옮기기 전과 같은지(판단 지연 값 제외),
  `python -m coach_lab.evaluate --sweep --out reports/results/review_evidence.json` 뒤 `git diff`가 비어 있는지 봅니다.
- [ ] 코칭 계획: `python -m pytest -m live`(실제 LLM 1회)가 통과하는지, `python -m coach_lab.plan_eval`의 유효 · 기대 통과가 커밋된 `reports/results/coaching_plan.json`과 비슷한지 봅니다.
  LLM 답이라 바이트 단위로 같지는 않습니다.
- [ ] research의 `src/coach/`를 지웁니다. 이후 코어는 service에서만 고칩니다.
