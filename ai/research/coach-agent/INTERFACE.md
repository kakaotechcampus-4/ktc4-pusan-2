# 입출력 — 실시간 코치 코어

코어(`src/coach/`)가 무엇을 받아 무엇을 돌려주는지 정리합니다. 나중에 AI 서버의 API 요청 · 응답을 정할 때 이 문서를 기준으로 삼습니다. 판단 규칙(문제와 행동 표, 평가기, 우선순위)은 [README.md](README.md)에 있고, 이 문서는 **주고받는 모양**만 다룹니다.

- **필드의 원본은 코드의 pydantic 모델입니다.** 요청 · 응답 · 이벤트 · 리뷰 근거는 `src/coach/schemas.py`, 고정 어휘(enum)는 `src/coach/vocab.py`, 버전 값은 `src/coach/version.py`에 있습니다. 문서와 코드가 다르면 코드가 맞습니다.
- 아래 필드 표와 enum 표는 그 모델에서 뽑아 만들었습니다. 모델을 바꾸면 이 문서도 함께 고칩니다.
- 예시 JSON은 코드를 실제로 돌려 얻은 결과입니다. 요청 · 응답은 `scenarios/05_time_behind.json`을 재생해 처음 `INTERVENE`가 나온 틱(`t_ms` 52000)이고, 이벤트는 시나리오 재생에서 처음 나온 것이며, 리뷰 근거는 `scenarios/14_recurring_persists.json` 한 판 전체의 이벤트로 만든 것입니다. 길어서 목록은 앞의 몇 개만 남기고 그 사실을 적었습니다. `null` 필드는 보내지 않는 형태(`exclude_none`)로 실었고, 필수 필드의 `null`만 남겼습니다. 모든 예시는 모델 검증을 통과합니다.

## 목차

1. [진입점](#1-진입점)
2. [요청 `CoachRequest`](#2-요청-coachrequest)
3. [응답 `CoachResponse`](#3-응답-coachresponse)
4. [이벤트](#4-이벤트)
5. [리뷰 근거 `CoachReviewEvidence`](#5-리뷰-근거-coachreviewevidence)
6. [공통 지표 이름](#6-공통-지표-이름)
7. [`coach_state`](#7-coach_state)
8. [enum 값](#8-enum-값)
9. [버전과 호환](#9-버전과-호환)

---

## 1. 진입점

`from coach import decide, decide_safe, finalize, build_review_evidence, initial_state` 로 가져옵니다. 다섯 개 모두 파일 · DB · 네트워크 · 시계를 쓰지 않고 LLM도 없습니다. 시간은 요청의 `t_ms`뿐이라서 같은 요청에는 언제나 같은 응답이 나옵니다. 기억은 `coach_state`로 요청과 응답에 실려 오가고, 저장은 BE가 합니다 ([7절](#7-coach_state)).

| 진입점 | 언제 | 입력 | 출력 |
|---|---|---|---|
| `decide` | 연습 중 1초마다 | `CoachRequest` (dict도 됨) | `CoachResponse` |
| `decide_safe` | 연습 중 1초마다 (API가 부르는 판) | `CoachRequest` (dict도 됨) | `CoachResponse` |
| `finalize` | 연습 종료 때 한 번 | `FinalizeRequest` (dict도 됨) | `FinalizeResponse` |
| `build_review_evidence` | 연습 종료 분석 | Take의 이벤트 전체 + 계획 · 미션 · 기억 | `CoachReviewEvidence` |
| `initial_state` | Take의 첫 기억이 필요할 때 | (선택) `CoachingPlan` | `CoachState` |

**AI 서버에 붙일 때** (아직 없는 계획입니다. 경로에는 버전을 붙이지 않습니다)

| 경로 | 부르는 함수 |
|---|---|
| `POST /coach/evaluate` | `decide_safe` |
| `POST /coach/finalize` | `finalize` |
| `/takes/analyze` (연습 종료 분석) | 그 안에서 `build_review_evidence`를 부릅니다. 리뷰 근거만을 위한 API는 따로 두지 않습니다 |
| `POST /coach/plan` | v1.1 계획입니다. LLM이 Take 시작 전에 코칭 계획을 만들어 `initial_state(plan)`의 `coach_state`를 돌려줍니다. v1은 기본 계획이라 첫 요청의 `coach_state`를 `null`로 보내면 됩니다 |

```
FE  시선 · 음량 · 슬라이드 요약 ─┐
BE  최근 15초 STT 단어 · 모드 · 미션 ─┼─▶ decide_safe() ─▶ WAIT / IGNORE / INTERVENE
지난 응답의 coach_state ──────────┘        + 이벤트 + 새 coach_state + 상태 표시

Take 종료:  finalize() ─▶ 남은 구간 닫기 ─▶ build_review_evidence(+ plan · missions · memory) ─▶ 리뷰 에이전트
```

### 1-1. `decide` · `decide_safe`

```python
decide(
    request: CoachRequest | dict[str, Any],
    config: CoachConfig | None = None,
    policy: Policy | None = None,
) -> CoachResponse

decide_safe(
    request: CoachRequest | dict[str, Any],
    config: CoachConfig | None = None,
    policy: Policy | None = None,
) -> CoachResponse
```

`config`는 판단 기준값(`src/coach/config.py`)이고 `None`이면 기본값입니다. `policy`는 행동을 고르는 규칙이고 `None`이면 규칙 기반(`RULE_POLICY`)입니다. API는 둘 다 `None`으로 부릅니다.

**실패 처리** (코드에서 확인한 동작입니다)

| 상황 | 동작 |
|---|---|
| 요청 형식 오류 (필수 필드 없음, `t_ms` 음수 …) | `pydantic.ValidationError`를 그대로 올립니다. 두 함수 모두 같습니다. API는 이것을 **422**로 바꾸고, BE는 그 1초를 건너뜁니다 |
| 코치 안에서 예외 (`decide_safe`만) | 예외를 삼키고 `action=WAIT`, `reason_codes=["INTERNAL_ERROR"]`, **이전 `coach_state` 그대로**를 돌려줍니다. 요청의 `coach_state`가 `null`이었으면 새 상태를 돌려줍니다. 발표를 방해하지 않기 위한 것이라 BE는 이 응답도 정상으로 처리하고 다음 틱에 이어가면 됩니다 |
| 이미 처리한 `t_ms` (`t_ms <= coach_state.last_t_ms`; 재시도 · 순서 뒤바뀜) | 아무것도 바꾸지 않고 `WAIT` + `STALE_TICK`, 받은 상태 그대로, `events=[]`. 이벤트가 중복되지 않습니다 |
| `coach_state`의 버전이 다르거나 모양이 깨짐 | 버리고 새 상태로 시작하며 `reason_codes`에 `STATE_RESET`을 덧붙입니다. 새로 시작하므로 쿨다운 · 전략 단계가 풀리고, 열려 있던 문제 구간과 아직 재지 못한 개입 효과가 사라지며, 이벤트 번호도 `ev-00001`부터 다시 매겨집니다. 그래서 BE는 받은 `coach_state`를 고치지 말고 그대로 돌려줘야 합니다 |

`decide`는 위 두 번째(내부 예외)를 잡지 않고 올립니다. 연구 도구와 테스트는 `decide`, API는 `decide_safe`를 씁니다. 요청의 `schema_version`은 코드가 검사하지 않습니다.

### 1-2. `finalize`

```python
finalize(
    request: FinalizeRequest | dict[str, Any],
    config: CoachConfig | None = None,
) -> FinalizeResponse
```

Take 종료 때 한 번 부릅니다. 마지막 응답의 `coach_state`와 종료 시각 `t_ms`를 받아 다음을 이벤트로 돌려줍니다.

- 아직 효과를 재지 못한 개입마다 `OUTCOME`(`NOT_MEASURED`)
- 열려 있던 문제 구간마다 `EPISODE`(`closed_by=TAKE_END`). 개입도 없고 2초(`min_episode_ms`)도 안 된 깜빡임은 버립니다
- 지금 머무는 장의 `SLIDE`

이 이벤트를 `decide` 응답의 이벤트와 함께 쌓아야 리뷰 근거가 완성됩니다. 요청 형식이 틀리면 `ValidationError`를 올리고, 안전판은 없습니다. `coach_state`가 `null`이거나 버전이 다르거나 깨졌으면 새 상태에서 시작하므로 `events`가 빈 목록이 되고 `STATE_RESET` 표시도 없습니다. BE는 마지막 응답의 `coach_state`를 반드시 넘겨야 합니다.

### 1-3. `build_review_evidence`

```python
build_review_evidence(
    take_id: str,
    events: list[Any],
    *,
    plan: Plan | dict[str, Any] | None = None,
    missions: list[Mission] | list[dict[str, Any]] | None = None,
    memory: Memory | dict[str, Any] | None = None,
    config: CoachConfig | None = None,
) -> CoachReviewEvidence
```

`events`는 이 Take의 모든 `decide` 응답과 `finalize` 응답의 `events`를 이어 붙인 것입니다 (모델 객체도, `model_dump`한 dict도 됩니다). `plan` · `missions` · `memory`는 이 Take의 요청에 넣었던 것과 같은 것을 넘깁니다. 미션 판정과 이전 Take 비교에 쓰입니다. 이벤트 dict에 모르는 필드가 있거나 `kind`가 틀리면 `ValidationError`입니다(이벤트는 출력 모델이라 `extra="forbid"`). 자세한 내용은 [5절](#5-리뷰-근거-coachreviewevidence)입니다.

### 1-4. `initial_state`

```python
initial_state(plan: CoachingPlan | None = None) -> CoachState
```

Take의 첫 기억을 만듭니다. v1에서 BE는 부를 필요가 없습니다 — 첫 요청의 `coach_state`를 `null`로 보내면 `decide`가 알아서 `initial_state()`로 시작합니다. v1.1의 `/coach/plan`이 LLM 계획을 담아 이것을 돌려줄 예정입니다. 쓰려면 `dump_state`(`src/coach/state.py`)로 dict로 바꿔 요청에 실어야 합니다.

---

## 2. 요청 `CoachRequest`

`decide` · `decide_safe`에 매초 보냅니다. **입력 모델은 모르는 필드를 무시합니다**(`extra="ignore"`). BE가 필드를 먼저 추가해도 깨지지 않습니다. `current` 안의 영역이 빠지면 **그 영역 판단만** 건너뜁니다. `timing`이 없으면 장은 지난 상태 그대로 보고, 시간 판단은 `t_ms`와 계획으로 계속합니다. `speech`가 없으면 STT가 없는 것으로 보고 말 속도 판단을 끄며, 장 진행도는 시간으로 잽니다.

### 2-1. 입력이 어디서 오나

| 요청 필드 | 출처 | 비고 |
|---|---|---|
| `current.timing` · `gaze` · `voice` | FE의 1초 신호 | 슬라이드 번호와 체류 시간, 시선 1초 기록(또는 FE가 만든 최근 창 요약), 음량 · 침묵 · 오디오 상태 |
| `current.speech` | BE의 STT | 최근 15초 단어. 군더더기(`filler`) 표시는 BE가 하면 그대로 쓰고, 없으면 코치가 소리뿐인 간투사(음 · 어 · 으 · 엄 · 흠 · 아 · 에)만 셉니다 |
| `plan` | 대본 분석 계획 | 목표 시간 · 허용 범위, 장별 목표 시간 · 글자 수 · 필수 키워드. Take 동안 바뀌지 않습니다 |
| `missions` | 직전 리뷰의 `next_missions` | 그대로 넘깁니다 |
| `memory` | 직전 리뷰가 '아직 남은 문제'로 꼽은 것 | 영역 · 장 |
| `mode` | 사용자 선택 | `PRACTICE` · `EXAM`. 실전에서는 말하지 않고 기록만 남깁니다 (`EXAM_MODE`) |
| `coach_state` | 지난 응답의 `coach_state` | 첫 요청이면 `null`. 코치가 최근 60초 기록 · 개입 이력 · 전략 단계를 여기에 직접 쌓으므로 BE가 따로 만들 것이 없습니다 |

### 2-2. 필드

**`CoachRequest`**

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `schema_version` | str | `"1.1"` | 스키마 버전 (`version.py`의 `SCHEMA_VERSION`) |
| `take_id` | str | 필수 | Take ID. 응답 · 리뷰 근거에 그대로 돌아옵니다 |
| `t_ms` | int (>=0) | 필수 | Take 시작 기준 경과 ms. 모든 시각이 이 시간축이다 |
| `mode` | Mode | `"PRACTICE"` | `PRACTICE`(연습) 또는 `EXAM`(실전). 실전에서는 개입하지 않고 기록만 남긴다 |
| `plan` | `Plan` | `Plan()` | 발표 계획 |
| `missions` | list[`Mission`] | `[]` | 직전 리뷰의 다음 미션 |
| `memory` | `Memory` | `Memory()` | 이전 Take 기억 |
| `current` | `Current` | `Current()` | 지금 1초의 측정값 |
| `coach_state` | dict[str, Any] \| null | `null` | 지난 응답의 coach_state 그대로. 첫 요청이면 null. BE 는 내용을 몰라도 된다 |

**`Plan`**

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `target_ms` | int \| null (>0) | `null` | 발표 목표 시간 (ms) |
| `min_ms` | int \| null | `null` | 허용 범위. 예상 종료가 min_ms 보다 이르면 '너무 빨리 끝남', max_ms 를 넘으면 시간 초과 |
| `max_ms` | int \| null | `null` | 허용 상한 (ms). 예상 종료가 넘으면 시간 초과 |
| `slides` | list[`SlidePlan`] | `[]` | 장별 계획 |

**`SlidePlan`**

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `slide_number` | int | 필수 | 장 번호 |
| `target_ms` | int (>0) | 필수 | 이 장의 목표 시간 (ms) |
| `script_chars` | int (>=0) | `0` | 이 장 대본 글자 수 (공백 제외). 진행도 = 이 장에서 말한 글자 수 / script_chars |
| `required_keywords` | list[str] | `[]` | 이 장에서 꼭 말해야 할 키워드 |

**`Mission`** — 직전 리뷰의 `next_missions` 그대로

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `mission_id` | str | 필수 | 미션 ID. 리뷰 근거의 판정과 이어진다 |
| `type` | FeedbackType | 필수 | 원인 영역 (`FeedbackType`) |
| `slide_number` | int \| null | `null` | 장 번호. `null`이면 Take 전체 |
| `priority` | int | `1` | 우선순위 |
| `description` | str \| null | `null` | 사용자에게 보인 문장. 판정에는 쓰지 않는다 |
| `target` | `MissionTarget` \| null | `null` | 기계로 판정할 목표. `null`이면 판정하지 않는다 |

**`MissionTarget`**

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `metric` | str | 필수 | 지표 이름. 응답 evidence · 리뷰 evidence 와 같은 이름을 쓴다 ([6절](#6-공통-지표-이름)) |
| `operator` | "LT" \| "LTE" \| "GT" \| "GTE" \| "EQ" | 필수 | 비교 연산자 |
| `value` | float | 필수 | 목표값 |

**`Memory`**

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `recurring_issues` | list[`RecurringIssue`] | `[]` | 아직 남은 문제 목록 |

**`RecurringIssue`**

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `type` | FeedbackType | 필수 | 아직 남은 문제의 영역 |
| `slide_number` | int \| null | `null` | 장 번호. `null`이면 Take 전체 |

**`Current`** — 지금 1초의 측정값. 영역이 빠지면 그 영역 판단만 건너뜁니다

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `timing` | `TimingInput` \| null | `null` | 장 번호 · 체류 시간 |
| `gaze` | `GazeInput` \| null | `null` | 시선 최근 창 요약 |
| `voice` | `VoiceInput` \| null | `null` | 음량 · 침묵 · 오디오 상태 |
| `speech` | `SpeechInput` \| null | `null` | STT 단어 |

**`TimingInput`**

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `slide_number` | int \| null | `null` | 장 번호. `null`이면 장 정보가 없는 경우 |
| `slide_elapsed_ms` | int \| null (>=0) | `null` | 지금 장에 머문 시간 (ms) |

**`GazeInput`** — FE 시선 모듈의 최근 창. 1초 기록(`records`)이나 FE가 만든 요약(`ratios` · `current_label` · `current_label_ms`) 중 하나를 보냅니다. `records`가 있으면 요약은 쓰지 않습니다

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `window_ms` | int | `10000` | 창 길이 (ms) |
| `ratios` | dict[str, float] | `{}` | 시선 라벨 → 비율. `UNCERTAIN` 포함 합 1 |
| `current_label` | str \| null | `null` | 지금 시선 라벨 |
| `current_label_ms` | int \| null (>=0) | `null` | current_label 이 이어진 시간 |
| `records` | list[`GazeRecord`] \| null | `null` | 최근 window_ms 의 1초 기록. 기록이 없는 시간은 측정하지 못한 것으로 본다 (1.1) |

**`GazeRecord`** — 시선 1초 기록 하나. 시선 모듈의 1초 기록과 같은 이름을 쓰고, 모르는 필드(`confidence` · `reliability` · `direction` …)는 무시합니다

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `t_ms` | int (>=0) | 필수 | 이 기록이 덮는 시간의 시작 (Take 시작 기준 ms) |
| `duration_ms` | int (>0) | `1000` | 덮는 시간 길이 (ms) |
| `state` | str | 필수 | `CAMERA` · `BOTTOM` · `UNCERTAIN` (FE 3구역) 또는 `SCREEN` · `OTHER` · `UNMEASURED`를 더한 6상태 |

`records`로 받으면 코치가 최근 창의 요약을 직접 계산합니다 (`evaluators/gaze.py`의 `window_summary`).

- 창은 `[max(0, t_ms − window_ms), t_ms)`이고, 기록마다 창과 겹친 시간을 상태별로 더합니다. 겹치는 기록은 한 번만 세고, 창 밖의 기록(아직 오지 않은 시간 포함)은 버립니다.
- 기록이 덮지 못한 시간과 `UNMEASURED`는 `UNCERTAIN`처럼 **측정하지 못한 시간**으로 셉니다. 대본 응시 비율은 측정한 시간 중 `BOTTOM`의 비율입니다 (시선 모듈의 비율과 같은 분모).
- `SCREEN` · `OTHER`는 측정한 시간이지만 대본이 아닌 곳으로 셉니다.
- 지금 라벨 · 이어진 시간도 창 안으로 자르고 겹침을 뺀 기록으로 계산합니다. 지금 라벨은 마지막 기록의 상태이고, 이어진 시간은 같은 상태로 빈틈없이 이어진 기록의 길이입니다. 마지막 기록이 지금보다 2초(`record_stale_ms`) 넘게 오래됐으면 지금 라벨은 측정하지 못한 것으로 봅니다.
- 두 입력 모두 Take 시작 뒤 5초(`min_window_ms`)까지는 시선 비율로 지적하지 않고 상태 표시는 `UNKNOWN`입니다. 몇 초의 표본은 한두 번의 판정에 크게 흔들립니다. 창 길이가 아니라 Take 경과 시간으로 보므로, FE가 짧은 창을 보내도 그 뒤에는 지적합니다.

1초 기록 예시 (FE 3구역):

```json
{"window_ms": 10000, "records": [{"t_ms": 41000, "duration_ms": 1000, "state": "CAMERA"}, {"t_ms": 42000, "duration_ms": 1000, "state": "BOTTOM"}]}
```

**`VoiceInput`** — 음량은 기준 대비 값(`relative_db`)이나 측정한 레벨(`level_db`) 중 하나를 보냅니다

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `relative_db` | float \| null | `null` | 캘리브레이션(평소 목소리) 대비 dB. 음수가 작은 소리. 말하지 않는 중이면 null |
| `level_db` | float \| null | `null` | 지난 1초 동안 말한 소리의 레벨 (A 가중 dBFS). 말하지 않았으면 null. `relative_db`가 없을 때 쓴다 (1.1) |
| `baseline_db` | float \| null | `null` | 이 발표자의 평소 목소리 레벨 (dBFS, 캘리브레이션). 없으면 코치가 Take 첫 발화로 잡는다 (1.1) |
| `silence_ms` | int (>=0) | `0` | 지금 몇 ms 째 조용한가 |
| `audio_live` | bool | `true` | 오디오가 실제로 흐르는가. False 면 소리 판단을 전부 끈다 |

'작은 목소리'는 평소 목소리 대비로 판단합니다. `relative_db`가 오면 그대로 쓰고, `level_db`가 오면 `level_db − baseline_db`를 씁니다. `baseline_db`도 없으면 이번 Take에서 **말한 1초 15개(`baseline_samples`)의 레벨 중앙값**을 평소 목소리로 잡아 `coach_state`에 두고, 그 전까지는 음량을 판단하지 않습니다. 개인 캘리브레이션이 없어도 '평소보다 작아짐'을 잴 수 있습니다.

**`SpeechInput`**

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `stt_status` | str | `"ok"` | BE 의 stt_status 값. "ok" 가 아니면 STT 에 기대는 판단을 끈다 |
| `words` | list[`Word`] | `[]` | 최근 window_ms(15초) 의 단어. 확정과 중간 결과가 겹치지 않게 보낸다 |
| `utterance_end_ms` | int \| null | `null` | Deepgram 이 마지막으로 알려준 문장 끝 시각 (speech_final / UtteranceEnd) |

**`Word`**

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `w` | str | 필수 | 단어 (텍스트) |
| `start_ms` | int | 필수 | 단어 시작 (ms) |
| `end_ms` | int | 필수 | 단어 끝 (ms) |
| `final` | bool | `true` | 확정 단어만 누적(진행도 · 군더더기)에 쓴다. 중간 결과는 CPM 에만 쓴다 |
| `filler` | bool \| null | `null` | 군더더기인가. BE 가 표시하면 그대로 쓰고, 없으면(null) 코치가 소리뿐인 간투사(음 · 어 · 으 · 엄 · 흠 · 아 · 에, 길게 끈 것)만 군더더기로 센다. '그' · '이제'처럼 문맥이 필요한 말은 BE 표시가 있어야 센다 (1.1) |

### 2-3. 예시

`05_time_behind`의 `t_ms` 52000 요청입니다. `speech.words`는 15개 중 2개만, `coach_state`는 줄여 실었습니다 (실제로는 지난 응답의 `coach_state` 전체). 시뮬레이터가 만든 요청이라 단어는 자리표시(`가나다`)이고 `schema_version`은 생략(기본값 `"1.1"`)되어 있습니다.

```json
{
  "take_id": "sim-05_time_behind",
  "t_ms": 52000,
  "mode": "PRACTICE",
  "plan": {
    "target_ms": 180000,
    "min_ms": 165000,
    "max_ms": 195000,
    "slides": [
      {"slide_number": 1, "target_ms": 30000, "script_chars": 105},
      {"slide_number": 2, "target_ms": 60000, "script_chars": 210},
      {"slide_number": 3, "target_ms": 60000, "script_chars": 210, "required_keywords": ["로컬 처리"]},
      {"slide_number": 4, "target_ms": 30000, "script_chars": 105}
    ]
  },
  "missions": [{"mission_id": "mission-9", "type": "TIME", "slide_number": 3, "priority": 1, "target": {"metric": "slide_duration_ms", "operator": "LTE", "value": 60000}}],
  "memory": {"recurring_issues": [{"type": "TIME", "slide_number": 3}]},
  "current": {
    "timing": {"slide_number": 2, "slide_elapsed_ms": 19270},
    "gaze": {"window_ms": 10000, "ratios": {"CAMERA": 0.7, "BOTTOM": 0.3, "UNCERTAIN": 0.0}, "current_label": "CAMERA", "current_label_ms": 1000},
    "voice": {"relative_db": 0.0, "silence_ms": 0, "audio_live": true},
    "speech": {
      "stt_status": "ok",
      "utterance_end_ms": 51170,
      "words": [{"w": "가나다", "start_ms": 37280, "end_ms": 38030, "final": true, "filler": false}, {"w": "가나다", "start_ms": 38150, "end_ms": 38900, "final": true, "filler": false}]
    }
  },
  "coach_state": {"v": 1, "…": "지난 응답의 coach_state 그대로"}
}
```

---

## 3. 응답 `CoachResponse`

### 3-1. 필드

**`CoachResponse`** — 출력 모델(`extra="forbid"`)

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `schema_version` | str | `"1.1"` | 스키마 버전 (`version.py`의 `SCHEMA_VERSION`) |
| `policy_version` | str | 필수 | 판단 규칙 버전 (`POLICY_VERSION`) |
| `config_hash` | str | 필수 | 판단에 쓴 설정의 해시. 재현용 |
| `take_id` | str | 필수 | Take ID. 응답 · 리뷰 근거에 그대로 돌아옵니다 |
| `t_ms` | int | 필수 | Take 시작 기준 경과 ms |
| `action` | Action | 필수 | 이번 초의 행동 (`WAIT` `IGNORE` `INTERVENE`) |
| `candidate_id` | str \| null | `null` | 이번 판단의 대상이 된 문제. "문제코드-시작시각" 이라 문제가 이어지는 동안 같다 |
| `reason_codes` | list[str] | `[]` | 이유 코드. 개입한 것이면 맨 앞이 문제 코드(`Issue`)이고 이어서 개입 이유(`Reason`) |
| `feedback` | `Feedback` \| null | `null` | INTERVENE 일 때만 |
| `candidates` | list[`CandidateOut`] | `[]` | 이번 초에 걸린 모든 후보와 상태 |
| `indicators` | `Indicators` | `Indicators()` | 상태 표시 |
| `events` | list[`CoachEvent`] | `[]` | 이벤트 목록 (4절) |
| `coach_state` | dict[str, Any] | 필수 | 다음 요청에 그대로 붙인다 |

**`Feedback`** — `action`이 `INTERVENE`일 때만 있습니다

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `type` | FeedbackType | 필수 | 원인 영역 (`FeedbackType`) |
| `instruction` | Instruction | 필수 | 발표자에게 하라는 행동 (`Instruction`) |
| `message` | str | 필수 | 화면에 띄울 문장 |
| `priority` | int (>=0 <=100) | 필수 | 우선순위 |
| `confidence` | float (>=0.0 <=1.0) | 필수 | 신뢰도 (0~1) |
| `evidence` | dict[str, Any] | 필수 | start_ms · end_ms · slide_number + 모듈이 준 값 |

**`CandidateOut`** — 이번 초에 걸린 후보 하나. 내부 점수 순(같으면 문제 순서)으로 정렬됩니다

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `candidate_id` | str | 필수 | 문제 ID ("문제코드-시작시각") |
| `issue` | Issue | 필수 | 평가기가 찾은 문제 (`Issue`) |
| `type` | FeedbackType | 필수 | 원인 영역 (`FeedbackType`) |
| `instruction` | Instruction | 필수 | 발표자에게 하라는 행동 (`Instruction`) |
| `priority` | int (>=0 <=100) | 필수 | 우선순위 |
| `confidence` | float (>=0.0 <=1.0) | 필수 | 신뢰도 (0~1) |
| `status` | CandidateStatus | 필수 | 상태 |
| `reasons` | list[str] | `[]` | 이유 코드 (`Reason`) |

**`Indicators`** — 읽지 않아도 되는 상태 표시. 지시(`feedback`)는 한 번에 1개지만 이건 여러 개를 함께 띄워도 됩니다. 띄울지는 FE가 정합니다

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `schedule` | Schedule | `"UNKNOWN"` | 진행 상태 (`Schedule`) |
| `required_ratio` | float \| null | `null` | 남은 내용을 남은 시간에 끝내려면 필요한 속도 배율 (r) |
| `pace` | PaceLevel | `"UNKNOWN"` | 말 속도 단계 (`PaceLevel`) |
| `cpm` | float \| null | `null` | 말 속도 (글자/분) |
| `gaze` | GazeLevel | `"UNKNOWN"` | 시선 상태 (`GazeLevel`) |
| `volume` | VolumeLevel | `"UNKNOWN"` | 음량 상태 (`VolumeLevel`) |

`pace`는 `cpm` < 275 이면 `SLOW`, > 350 이면 `FAST`, 그 사이는 `NORMAL`입니다. `gaze`는 측정하지 못한 비율(지금 값과 최근 10초 평균 중 큰 쪽)이 50%를 넘으면 `UNCERTAIN`, Take 시작 뒤 5초 안이면 `UNKNOWN`, 보인 시간 중 대본 응시가 50% 이상이면 `SCRIPT`, 아니면 `AUDIENCE`(대본을 보지 않음)입니다. `volume`은 최근 5초 동안 말하며 잰 음량의 평균(평소 목소리 대비)이 -6 dB 미만이면 `LOW`, 그 표본이 3개 미만이면(한동안 말하지 않음 · 음량 기준을 잡는 중) `UNKNOWN`입니다. 말을 멈춘 직후에는 5초 안의 표본으로 앞 상태가 이어집니다 (기준값은 `config.py`의 `fast_cpm` · `slow_cpm` · `max_uncertain_ratio` · `indicator_script_ratio` · `low_relative_db`).

### 3-2. `action`

| 값 | 뜻 | `feedback` |
|---|---|---|
| `WAIT` | 지금은 말하지 않고 계속 봅니다. 문제가 없거나 타이밍을 기다리는 중 | 없음 |
| `IGNORE` | 문제는 있지만 지금 말할 가치가 낮아 이번엔 버립니다 | 없음 |
| `INTERVENE` | 지금 말합니다 | 있음 (화면에 띄울 1개) |

`candidate_id`는 이번 판단의 대상이 된 문제입니다. `문제코드-시작시각` 꼴이라 **문제가 이어지는 동안 같은 ID**입니다 (유지 격려 후보만 `IMPROVED_AFTER_FEEDBACK-<개입 event_id>` 꼴). `WAIT` · `IGNORE`일 때도 가장 앞선 후보의 ID가 들어갈 수 있고, 후보가 없으면 `null`입니다. `reason_codes`는 그 action을 고른 이유입니다. `INTERVENE`면 맨 앞이 문제 코드(`Issue`, 예 `BEHIND_SCHEDULE`)이고 이어서 개입 이유(`Reason`)가 오며, `WAIT` · `IGNORE`면 `Reason` 코드만 옵니다. 상태가 초기화됐으면 `STATE_RESET`이 맨 뒤에 덧붙습니다 ([8절](#8-enum-값)의 이유 코드).

**`type`과 `instruction`이 따로인 이유** — `type`은 원인 영역, `instruction`은 하라는 행동이고 1:1이 아닙니다. 같은 `TIME`이라도 상황에 따라 `SPEED_UP` · `CONDENSE` · `MOVE_ON` · `WRAP_UP`이 나오고, 같은 `SLOW_DOWN`이라도 원인이 말이 빨라서(`SPEED`)일 수도, 시간이 남아서(`TIME`)일 수도 있습니다. `type`은 리뷰의 `ReviewPoint.type` · `Mission.type`과 같은 7개 enum이라 실시간 지시와 리뷰가 같은 축으로 이어집니다. 화면에 무엇을 띄울지는 `instruction`(과 `message`)으로, 어떤 영역의 문제인지는 `type`으로 봅니다.

### 3-3. 후보 상태 `candidates[].status`

| 값 | 뜻 |
|---|---|
| `SELECTED` | 이번에 말한 후보. `reasons`에 말한 이유가 들어갑니다 |
| `OUTRANKED` | 말할 수 있었지만 점수에서 밀림. 다음 기회에 다시 경쟁합니다 |
| `WAITING` | 타이밍 때문에 보류 (지속시간 미달, 간격, 쿨다운, 문장 끝 대기) |
| `IGNORED` | 가치가 없어 버림 (실전 모드, 센서 불량, 낮은 신뢰도 …) |

`SELECTED`가 아닌 후보의 `reasons`에는 말하지 못한 이유가 들어갑니다. 이유 하나라도 `WAIT_REASONS`에 있으면 `WAITING`, `IGNORE_REASONS`에 있으면 `IGNORED`이고 `IGNORED`가 우선합니다 ([8절](#8-enum-값)).

### 3-4. 예시

위 요청의 응답입니다. `feedback`이 있는 `INTERVENE` 틱이고, `candidates`는 1개뿐이어서 그대로 실었습니다. `events`는 이 틱에서 나온 `INTERVENTION` 하나이고(전체 모양은 [4절](#4-이벤트)), `coach_state`는 줄여 실었습니다.

```json
{
  "schema_version": "1.1",
  "policy_version": "coach-v1.1",
  "config_hash": "dbe162df2a38",
  "take_id": "sim-05_time_behind",
  "t_ms": 52000,
  "action": "INTERVENE",
  "candidate_id": "BEHIND_SCHEDULE-52000",
  "reason_codes": ["BEHIND_SCHEDULE"],
  "feedback": {
    "type": "TIME",
    "instruction": "SPEED_UP",
    "message": "조금만 빠르게 — 남은 3장, 2분 8초",
    "priority": 52,
    "confidence": 0.9,
    "evidence": {"start_ms": 52000, "end_ms": 52000, "slide_number": 2, "required_ratio": 1.0513, "remaining_content_ms": 134571, "remaining_ms": 128000, "remaining_slides": 3, "cpm": 240.0, "required_cpm": 252.3}
  },
  "candidates": [{"candidate_id": "BEHIND_SCHEDULE-52000", "issue": "BEHIND_SCHEDULE", "type": "TIME", "instruction": "SPEED_UP", "priority": 52, "confidence": 0.9, "status": "SELECTED", "reasons": []}],
  "indicators": {"schedule": "BEHIND", "required_ratio": 1.0513, "pace": "SLOW", "cpm": 240.0, "gaze": "AUDIENCE", "volume": "OK"},
  "events": [
    {
      "event_id": "ev-00003",
      "t_ms": 52000,
      "kind": "INTERVENTION",
      "candidate_id": "BEHIND_SCHEDULE-52000",
      "issue": "BEHIND_SCHEDULE",
      "type": "TIME",
      "instruction": "SPEED_UP",
      "variant": "default",
      "message": "조금만 빠르게 — 남은 3장, 2분 8초",
      "priority": 52,
      "confidence": 0.9,
      "reason_codes": ["BEHIND_SCHEDULE"],
      "slide_number": 2,
      "evidence": {"start_ms": 52000, "end_ms": 52000, "slide_number": 2, "required_ratio": 1.0513, "remaining_content_ms": 134571, "remaining_ms": 128000, "remaining_slides": 3, "cpm": 240.0, "required_cpm": 252.3}
    }
  ],
  "coach_state": {"v": 1, "…": "다음 요청에 그대로 붙일 coach_state"}
}
```

### 3-5. `FinalizeRequest` · `FinalizeResponse`

**`FinalizeRequest`**

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `schema_version` | str | `"1.1"` | 스키마 버전 (`version.py`의 `SCHEMA_VERSION`) |
| `take_id` | str | 필수 | Take ID. 응답 · 리뷰 근거에 그대로 돌아옵니다 |
| `t_ms` | int (>=0) | 필수 | Take 시작 기준 경과 ms |
| `coach_state` | dict[str, Any] \| null | `null` | 마지막 응답의 `coach_state` 그대로 |

**`FinalizeResponse`**

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `schema_version` | str | `"1.1"` | 스키마 버전 (`version.py`의 `SCHEMA_VERSION`) |
| `policy_version` | str | 필수 | 판단 규칙 버전 |
| `take_id` | str | 필수 | Take ID. 응답 · 리뷰 근거에 그대로 돌아옵니다 |
| `events` | list[`CoachEvent`] | `[]` | 이벤트 목록 (4절) |

---

## 4. 이벤트

BE가 `decide` · `finalize` 응답의 `events`를 Take별로 **그대로 쌓아 둡니다 (추가만)**. Take가 끝나면 이 이벤트가 리뷰 근거([5절](#5-리뷰-근거-coachreviewevidence))의 원천이 됩니다. 리뷰 에이전트는 코치를 직접 부르지 않고, 이벤트 수백 개를 직접 읽지도 않습니다.

```
decide() 응답의 events  ─┐  BE가 Take별로 그대로 쌓음 (추가만)
finalize() 응답의 events ─┘
              │   + 이 Take의 plan · missions · memory (요청에 넣었던 것과 같은 것)
              ▼  연습 종료 분석 (/takes/analyze 안에서)
   build_review_evidence(take_id, events, plan=, missions=, memory=) → CoachReviewEvidence → 리뷰 에이전트
```

| `kind` | 언제 | 리뷰에서 쓰는 곳 |
|---|---|---|
| `INTERVENTION` | 화면에 띄움 (`CONTINUE` 격려 포함) | 실시간 코칭 이력 |
| `OUTCOME` | 개입의 효과를 쟀거나, 종료 때 못 쟀음 | "실시간 코칭이 효과가 있었나" |
| `EPISODE` | 문제 구간 하나가 끝남 (또는 Take 종료). 개입했든 안 했든 남김 | 문제 구간 · 부담 · 순위 |
| `SLIDE` | 장을 떠남 (또는 Take 종료). 다시 돌아오면 또 하나 | 장별 표 · 미션 판정 · 시간 문제 |
| `STRATEGY` | 효과가 없어 방법을 바꿨거나 그만둠 | "여러 번 말해도 안 바뀐 곳" |
| `SUPPRESSED` | 걸렸지만 말하지 않음 | 기준값 조정 · "왜 안 떴나" |

모든 이벤트에 공통으로 `event_id`와 `t_ms`가 있고, `kind`로 모델을 구분합니다 (`CoachEvent`는 `kind`를 판별자로 쓰는 union). 이벤트 모델도 `extra="forbid"`입니다.

**`event_id` 번호와 중복 방지** — `ev-00042` 꼴이고, 번호는 `coach_state.seq`로 매깁니다. 받은 `coach_state`를 그대로 이어 붙이는 동안에는 Take 안에서 겹치지 않습니다. 이미 받은 응답의 `coach_state`로 같은 `t_ms`를 다시 보내면 `STALE_TICK`으로 아무것도 바뀌지 않습니다. 응답을 받지 못해 이전 `coach_state`로 같은 요청을 다시 보내면 같은 이벤트가 같은 `event_id`로 다시 나오므로(코어는 시계 · 난수를 쓰지 않습니다) 그 응답 하나만 쓰면 됩니다. `STATE_RESET`이 나면 번호가 `ev-00001`부터 다시 매겨지므로, 저장한 이벤트를 구분할 때는 `event_id`와 `t_ms`를 함께 봅니다.

### 4-1. `INTERVENTION`

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `event_id` | str | 필수 | Take 안에서 겹치지 않는 번호 (`ev-00042`) |
| `t_ms` | int | 필수 | Take 시작 기준 경과 ms |
| `kind` | "INTERVENTION" | `"INTERVENTION"` | 이벤트 종류. 이 값으로 모델을 구분합니다 |
| `candidate_id` | str | 필수 | 문제 ID ("문제코드-시작시각") |
| `issue` | Issue | 필수 | 평가기가 찾은 문제 (`Issue`) |
| `type` | FeedbackType | 필수 | 원인 영역 (`FeedbackType`) |
| `instruction` | Instruction | 필수 | 발표자에게 하라는 행동 (`Instruction`) |
| `variant` | str | 필수 | 문구 변형 이름 (`SLOW_DOWN.pause_at_end` 의 `pause_at_end`) |
| `message` | str | 필수 | 화면에 띄운 문장 |
| `priority` | int | 필수 | 개입 시점의 우선순위 점수 |
| `confidence` | float | 필수 | 신뢰도 (0~1) |
| `reason_codes` | list[str] | 필수 | 이유 코드. 개입한 것이면 맨 앞이 문제 코드(`Issue`)이고 이어서 개입 이유(`Reason`) |
| `slide_number` | int \| null | `null` | 장 번호. `null`이면 장 정보가 없는 경우 |
| `evidence` | dict[str, Any] | 필수 | 근거 지표 (6절의 지표 이름) |

`02_gaze_effective` 시나리오 t_ms 48000:

```json
{
  "event_id": "ev-00003",
  "t_ms": 48000,
  "kind": "INTERVENTION",
  "candidate_id": "GAZE_SCRIPT-42000",
  "issue": "GAZE_SCRIPT",
  "type": "GAZE",
  "instruction": "LOOK_AT_CAMERA",
  "variant": "default",
  "message": "대본보다 청중을 조금 더 바라보세요",
  "priority": 100,
  "confidence": 1.0,
  "reason_codes": ["GAZE_SCRIPT", "WORSENING"],
  "slide_number": 2,
  "evidence": {"start_ms": 42000, "end_ms": 48000, "slide_number": 2, "script_ratio": 0.9, "continuous_script_gaze_ms": 7000, "gaze_uncertain_ratio": 0.0, "window_ms": 10000}
}
```

### 4-2. `OUTCOME`

개입 몇 초 뒤 실제로 행동이 바뀌었는지 `metric`의 `before` → `after`로 재서 남깁니다. 종료 때 못 쟀으면 `finalize`가 `NOT_MEASURED`로 남깁니다.

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `event_id` | str | 필수 | Take 안에서 겹치지 않는 번호 (`ev-00042`) |
| `t_ms` | int | 필수 | Take 시작 기준 경과 ms |
| `kind` | "OUTCOME" | `"OUTCOME"` | 이벤트 종류. 이 값으로 모델을 구분합니다 |
| `intervention_id` | str | 필수 | 개입(`INTERVENTION`) 이벤트의 `event_id` |
| `candidate_id` | str | 필수 | 문제 ID ("문제코드-시작시각") |
| `issue` | Issue | 필수 | 평가기가 찾은 문제 (`Issue`) |
| `type` | FeedbackType | 필수 | 원인 영역 (`FeedbackType`) |
| `instruction` | Instruction | 필수 | 발표자에게 하라는 행동 (`Instruction`) |
| `slide_number` | int \| null | `null` | 장 번호. `null`이면 장 정보가 없는 경우 |
| `outcome` | Outcome | 필수 | 효과 판정 (`Outcome`) |
| `metric` | str \| null | `null` | 효과를 잰 지표 이름 |
| `before` | float \| null | `null` | 개입 시점 지표값 |
| `after` | float \| null | `null` | 효과를 잰 시점 지표값 (재지 못했으면 `null`) |

`02_gaze_effective` 시나리오 t_ms 60000:

```json
{
  "event_id": "ev-00006",
  "t_ms": 60000,
  "kind": "OUTCOME",
  "intervention_id": "ev-00003",
  "candidate_id": "GAZE_SCRIPT-42000",
  "issue": "GAZE_SCRIPT",
  "type": "GAZE",
  "instruction": "LOOK_AT_CAMERA",
  "slide_number": 2,
  "outcome": "EFFECTIVE",
  "metric": "script_ratio",
  "before": 0.9,
  "after": 0.1852
}
```

### 4-3. `EPISODE`

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `event_id` | str | 필수 | Take 안에서 겹치지 않는 번호 (`ev-00042`) |
| `t_ms` | int | 필수 | Take 시작 기준 경과 ms |
| `kind` | "EPISODE" | `"EPISODE"` | 이벤트 종류. 이 값으로 모델을 구분합니다 |
| `candidate_id` | str | 필수 | 문제 ID ("문제코드-시작시각") |
| `issue` | Issue | 필수 | 평가기가 찾은 문제 (`Issue`) |
| `type` | FeedbackType | 필수 | 원인 영역 (`FeedbackType`) |
| `slide_number` | int \| null | `null` | 장 번호. `null`이면 장 정보가 없는 경우 |
| `start_ms` | int | 필수 | 문제 구간 시작 (ms) |
| `end_ms` | int | 필수 | 문제 구간 끝 (ms) |
| `peak_severity` | float | 필수 | 구간 중 가장 심했을 때의 심각도 |
| `intervention_ids` | list[str] | `[]` | 이 구간에서 말한 개입의 `event_id` |
| `suppressed_reasons` | list[str] | `[]` | 이 구간에서 말하지 못한 이유들 (EXAM_MODE, MIN_GAP …) |
| `closed_by` | "RESOLVED" \| "TAKE_END" | 필수 | `RESOLVED` 문제가 사라져 닫힘, `TAKE_END` 종료로 닫힘 |
| `peak_evidence` | dict[str, Any] | `{}` | 구간 동안 가장 나빴을 때의 지표 |
| `reliable_ms` | int | `0` | 센서를 믿을 수 있던 / 없던 시간. 대부분 믿을 수 없었다면 리뷰는 이 구간을 문제로 말하지 않는다 |
| `unreliable_ms` | int | `0` | 센서를 믿을 수 없던 시간 (ms). `reliable_ms` 참고 |
| `mean_severity` | float | `0.0` | 구간 평균 심각도. 부담(심각도 × 초)은 이걸로 잰다 — 최고값은 잡음에 크게 흔들린다 |

`02_gaze_effective` 시나리오 t_ms 56000:

```json
{
  "event_id": "ev-00005",
  "t_ms": 56000,
  "kind": "EPISODE",
  "candidate_id": "GAZE_SCRIPT-42000",
  "issue": "GAZE_SCRIPT",
  "type": "GAZE",
  "slide_number": 2,
  "start_ms": 42000,
  "end_ms": 52000,
  "peak_severity": 0.9,
  "intervention_ids": ["ev-00003"],
  "suppressed_reasons": ["NOT_PERSISTENT", "WAITING_FOR_PAUSE", "MIN_GAP", "COOLDOWN"],
  "closed_by": "RESOLVED",
  "peak_evidence": {"script_ratio": 0.9, "continuous_script_gaze_ms": 7000, "gaze_uncertain_ratio": 0.0, "window_ms": 10000},
  "reliable_ms": 11000,
  "unreliable_ms": 0,
  "mean_severity": 0.6818
}
```

### 4-4. `SLIDE`

평균은 `*_weighted / *_ms`로 냅니다 (예: CPM = `cpm_weighted / cpm_ms`). 요청 간격이 흔들려도 시간 가중 평균이 됩니다. 장 정보가 없는 발표면 `slide_number`가 `null`인 누적 하나가 Take 전체를 덮습니다.

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `event_id` | str | 필수 | Take 안에서 겹치지 않는 번호 (`ev-00042`) |
| `t_ms` | int | 필수 | Take 시작 기준 경과 ms |
| `kind` | "SLIDE" | `"SLIDE"` | 이벤트 종류. 이 값으로 모델을 구분합니다 |
| `slide_number` | int \| null | 필수 | null 이면 장 정보가 없는 발표 (Take 전체 누적에만 들어간다) |
| `start_ms` | int | 필수 | 이 장에 들어온 시각 (ms) |
| `end_ms` | int | 필수 | 이 장을 떠난 시각 (ms) |
| `target_ms` | int \| null | `null` | 목표 시간 (ms) |
| `script_chars` | int \| null | `null` | 이 장 대본 글자 수 |
| `total_ms` | int | 필수 | 이 장에 머문 시간 (ms) |
| `gaze_valid_ms` | int | 필수 | 시선을 판단할 수 있던 시간 (ms) |
| `gaze_script_ms` | float | 필수 | 그중 대본을 본 시간 (ms) |
| `gaze_unusable_ms` | int | 필수 | 시선을 믿을 수 없던 시간 (ms) |
| `speech_ok_ms` | int | 필수 | STT를 믿을 수 있던 시간 (ms) |
| `cpm_ms` | int | 필수 | CPM을 잰 시간 (ms) |
| `cpm_weighted` | float | 필수 | CPM × 시간의 합 |
| `filler_count` | int | 필수 | 군더더기 수 |
| `audio_live_ms` | int | 필수 | 오디오가 살아 있던 시간 (ms) |
| `speaking_ms` | int | 필수 | 말한 시간 (ms) |
| `db_ms` | int | 필수 | 음량을 잰 시간 (ms) |
| `db_weighted` | float | 필수 | dB × 시간의 합 |
| `long_silence_ms` | int | 필수 | 긴 침묵 시간 (ms) |
| `chars_total` | int | 필수 | 이 장에서 지금까지 말한 글자 수 (다시 돌아온 장이면 누적) |
| `keywords_required` | list[str] | `[]` | 이 장의 필수 키워드 |
| `keywords_found` | list[str] | `[]` | 그중 말한 키워드 |

`01_baseline_good` 시나리오 t_ms 28000:

```json
{
  "event_id": "ev-00001",
  "t_ms": 28000,
  "kind": "SLIDE",
  "slide_number": 1,
  "start_ms": 0,
  "end_ms": 28000,
  "target_ms": 30000,
  "script_chars": 105,
  "total_ms": 28000,
  "gaze_valid_ms": 28000,
  "gaze_script_ms": 5020.6,
  "gaze_unusable_ms": 0,
  "speech_ok_ms": 28000,
  "cpm_ms": 22000,
  "cpm_weighted": 6600000.0,
  "filler_count": 0,
  "audio_live_ms": 28000,
  "speaking_ms": 27000,
  "db_ms": 27000,
  "db_weighted": 0.0,
  "long_silence_ms": 0,
  "chars_total": 96,
  "keywords_required": [],
  "keywords_found": []
}
```

### 4-5. `STRATEGY`

같은 말이 안 통하면 사다리에서 한 칸 내려가 다른 방법으로 말하고(`ESCALATED`), 사다리 끝에서도 안 통하면 그 범위에서 그만둡니다(`GAVE_UP`). 슬라이드 단위 문제(시선 · 장 시간 초과 · 키워드)는 전략도 장마다 새로 시작합니다. 4번 장에서 시선 지적을 포기했어도 5번 장에서는 다시 시도합니다.

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `event_id` | str | 필수 | Take 안에서 겹치지 않는 번호 (`ev-00042`) |
| `t_ms` | int | 필수 | Take 시작 기준 경과 ms |
| `kind` | "STRATEGY" | `"STRATEGY"` | 이벤트 종류. 이 값으로 모델을 구분합니다 |
| `issue` | Issue | 필수 | 평가기가 찾은 문제 (`Issue`) |
| `type` | FeedbackType | 필수 | 원인 영역 (`FeedbackType`) |
| `slide_number` | int \| null | `null` | 장 번호. `null`이면 장 정보가 없는 경우 |
| `change` | StrategyChange | 필수 | 방법을 바꿨는지(`ESCALATED`) 그만뒀는지(`GAVE_UP`) |
| `from_instruction` | Instruction | 필수 | 바꾸기 전 행동 |
| `from_variant` | str | 필수 | 바꾸기 전 문구 변형 |
| `to_instruction` | Instruction \| null | `null` | 바꾼 뒤 행동 (`GAVE_UP`이면 `null`) |
| `to_variant` | str \| null | `null` | 바꾼 뒤 문구 변형 (`GAVE_UP`이면 `null`) |
| `failures` | int | 필수 | 그때까지 효과 없던 횟수 |
| `intervention_id` | str | 필수 | 방법을 바꾸게 한 개입의 `event_id` |

`03_gaze_gave_up` 시나리오 t_ms 51000:

```json
{
  "event_id": "ev-00006",
  "t_ms": 51000,
  "kind": "STRATEGY",
  "issue": "GAZE_SCRIPT",
  "type": "GAZE",
  "slide_number": 2,
  "change": "ESCALATED",
  "from_instruction": "LOOK_AT_CAMERA",
  "from_variant": "default",
  "to_instruction": "LOOK_AT_CAMERA",
  "to_variant": "sentence_start",
  "failures": 1,
  "intervention_id": "ev-00003"
}
```

### 4-6. `SUPPRESSED`

같은 문제는 `suppress_log_gap_ms`(기본 10초)에 한 번만 남깁니다. 침묵처럼 매초 걸리는 문제가 기록을 덮어 다른 것이 묻히지 않게 하려는 것이라, 이 기록의 개수는 대략값입니다.

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `event_id` | str | 필수 | Take 안에서 겹치지 않는 번호 (`ev-00042`) |
| `t_ms` | int | 필수 | Take 시작 기준 경과 ms |
| `kind` | "SUPPRESSED" | `"SUPPRESSED"` | 이벤트 종류. 이 값으로 모델을 구분합니다 |
| `candidate_id` | str | 필수 | 문제 ID ("문제코드-시작시각") |
| `issue` | Issue | 필수 | 평가기가 찾은 문제 (`Issue`) |
| `type` | FeedbackType | 필수 | 원인 영역 (`FeedbackType`) |
| `instruction` | Instruction | 필수 | 발표자에게 하라는 행동 (`Instruction`) |
| `status` | CandidateStatus | 필수 | `WAITING` 또는 `IGNORED` |
| `priority` | int | 필수 | 그때의 우선순위 점수 |
| `reasons` | list[str] | 필수 | 이유 코드 (`Reason`) |

`02_gaze_effective` 시나리오 t_ms 42000:

```json
{
  "event_id": "ev-00002",
  "t_ms": 42000,
  "kind": "SUPPRESSED",
  "candidate_id": "GAZE_SCRIPT-42000",
  "issue": "GAZE_SCRIPT",
  "type": "GAZE",
  "instruction": "LOOK_AT_CAMERA",
  "status": "WAITING",
  "priority": 55,
  "reasons": ["NOT_PERSISTENT"]
}
```

---

## 5. 리뷰 근거 `CoachReviewEvidence`

`build_review_evidence`가 Take의 이벤트를 요약한 구조입니다 ([1-3](#1-3-build_review_evidence)의 호출 형태). **숫자는 전부 코드가 이벤트에서 계산합니다.** 리뷰 에이전트(LLM)는 이 숫자를 인용만 하고 새로 만들지 않습니다. 문장은 만들지 않고, 꼬리표 · 상태 · 순위 · 목표값까지만 주며 문장은 리뷰가 씁니다. 필드 순서는 "리뷰가 먼저 볼 것"에서 "세부 근거" 순입니다.

### 5-1. 최상위 필드

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `schema_version` | str | `"1.1"` | 스키마 버전 (`version.py`의 `SCHEMA_VERSION`) |
| `policy_version` | str | 필수 | 판단 규칙 버전 |
| `config_hash` | str | 필수 | 판단에 쓴 설정의 해시 |
| `take_id` | str | 필수 | Take ID. 응답 · 리뷰 근거에 그대로 돌아옵니다 |
| `summary` | `CoachingSummary` | 필수 | 실시간 코칭 요약 |
| `data_quality` | `DataQuality` | 필수 | 센서 데이터 품질 |
| `type_status` | list[`TypeStatusReview`] | 필수 | 영역별 상태 → dimension 상태 |
| `issues` | list[`IssueReview`] | 필수 | 순위 매긴 문제 → improvements / remaining_issues / new_issues |
| `next_missions` | list[`NextMission`] | 필수 | 다음 Mission 후보 (목표값 포함) → next_missions |
| `mission_results` | list[`MissionReview`] | 필수 | 이전 Mission 판정 → mission_results / previous_mission_result |
| `memory_check` | list[`MemoryReview`] | 필수 | 이전 Take 기억 비교 → improved / remaining_issues |
| `strengths` | list[`StrengthReview`] | 필수 | 강점 → strengths |
| `slides` | list[`SlideReview`] | 필수 | 장별 표 |
| `segments` | list[`SegmentReview`] | 필수 | 문제 구간 (창 지연을 되돌린 추정 구간 포함) |
| `interventions` | list[`InterventionReview`] | 필수 | 개입마다 효과 |
| `strategy_changes` | list[`StrategyReview`] | 필수 | 방법 변경 · 포기 기록 |
| `by_type` | list[`TypeSummary`] | 필수 | 영역별 집계 |

| 필드 | 내용 | 리뷰 출력에서 |
|---|---|---|
| `type_status[]` | 영역 7개마다 상태 · 부담 · 이전 Take 비교 · Take 지표 | dimension 상태 |
| `issues[]` | 순위 매긴 문제(영역 × 장) | improvements · remaining_issues(`RECURRING`) · new_issues(`NEW`) |
| `next_missions[]` | 다음 미션 후보 (설정 `max_next_missions`, 기본 3개까지). `target`을 그대로 쓰면 다음 Take에서 기계로 판정됩니다 | next_missions |
| `mission_results[]` | 이전 미션마다 판정 | mission_results / previous_mission_result |
| `memory_check[]` | 이전 Take 기억 한 줄마다 이번 Take와 비교 | improved · remaining_issues |
| `strengths[]` | 강점 | strengths |
| `data_quality` | 센서 데이터 품질. 모자란 영역은 리뷰가 '판단 불가'라고 말해야 합니다 | '판단할 수 없었다'는 안내 |
| `summary` | 개입 · 격려 · 효과 · 포기 · 문제 구간 · 참은 이유 | 실시간 코칭 요약 |

### 5-2. 리뷰가 먼저 볼 것

**`CoachingSummary`** — `summary`

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `interventions` | int | 필수 | 화면에 띄운 전체 (교정 + 유지 격려) |
| `praises` | int | 필수 | 그중 유지 격려(CONTINUE) |
| `effective` | int | 필수 | 효과 있던 개입 수 |
| `ineffective` | int | 필수 | 효과 없던 개입 수 |
| `not_measured` | int | 필수 | 효과를 재지 못한 개입 수 |
| `effective_rate` | float \| null | 필수 | 효과를 잰 개입 중 효과 있던 비율. 잰 개입이 없으면 null |
| `gave_up` | int | 필수 | 방법을 다 써서 그만둔 문제 수 |
| `episodes` | int | 필수 | 문제 구간 수 |
| `episodes_unaddressed` | int | 필수 | 개입하지 못한(또는 안 한) 문제 구간 수 (믿을 수 없던 구간은 빼고) |
| `suppressed_by_reason` | dict[str, int] | 필수 | 참은 기록 수 (suppress_log_gap_ms 간격으로 남긴 것이라 대략값) |

**`DataQuality`** — `data_quality`

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `duration_ms` | int | 필수 | Take 전체 시간 (ms) |
| `gaze_coverage` | float \| null | 필수 | 시선 판단을 믿을 수 있던 시간 비율 |
| `speech_coverage` | float \| null | 필수 | STT 를 믿을 수 있던 시간 비율 |
| `audio_coverage` | float \| null | 필수 | 오디오가 살아 있던 시간 비율 |
| `unreliable_segments` | int | 필수 | 센서를 믿을 수 없어 문제에서 뺀 구간 수 |
| `evaluable` | dict[str, bool] | 필수 | 영역별 판단 가능 여부 |

**`TypeStatusReview`** — `type_status[]`. 영역 7개가 모두 한 줄씩 있습니다

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `type` | FeedbackType | 필수 | 원인 영역 (`FeedbackType`) |
| `status` | TypeStatus | 필수 | 이번 Take 상태 (`TypeStatus`) |
| `burden_s` | float | 필수 | 영역 전체 부담 (심각도 × 초) |
| `memory` | MemoryLabel \| null | `null` | 이전 Take 기억에 이 영역이 있었으면 그 결과 |
| `evidence` | dict[str, Any] | 필수 | 그 영역의 Take 지표 |

**`IssueReview`** — `issues[]`. `rank` 1이 다음에 먼저 고칠 것입니다

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `rank` | int | 필수 | 순위. 1이 다음에 먼저 고칠 것 |
| `type` | FeedbackType | 필수 | 원인 영역 (`FeedbackType`) |
| `slide_number` | int \| null | 필수 | null 이면 Take 전체에 걸친 문제 |
| `issues` | list[Issue] | 필수 | 합쳐진 문제 코드 |
| `burden_s` | float | 필수 | 심각도 × 초. 얼마나 크게 · 오래 문제였나 |
| `score` | float | 필수 | 순위 점수 = 부담 × 반복 · 포기 · 미션 실패 가중치 |
| `duration_ms` | int | 필수 | 문제였던 총 시간 (ms) |
| `segments` | int | 필수 | 문제 구간 수 |
| `peak_severity` | float | 필수 | 최고 심각도 |
| `interventions` | int | 필수 | 개입 수 |
| `effective` | int | 필수 | 효과 있던 개입 수 |
| `ineffective` | int | 필수 | 효과 없던 개입 수 |
| `gave_up` | bool | 필수 | 방법을 다 써서 포기했는가 |
| `memory` | MemoryLabel | 필수 | 이전 Take 기억 |
| `mission_failed` | bool | 필수 | 이 문제와 관련된 이전 미션이 `FAILED` 또는 `PARTIAL`인가 |
| `evidence` | dict[str, Any] | 필수 | 근거 지표 (6절의 지표 이름) |

**`NextMission`** — `next_missions[]`

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `priority` | int | 필수 | 미션 우선순위 (1이 먼저) |
| `type` | FeedbackType | 필수 | 원인 영역 (`FeedbackType`) |
| `slide_number` | int \| null | 필수 | 장 번호. `null`이면 Take 전체 |
| `target` | `NextMissionTarget` | 필수 | 다음 Take 에서 기계로 판정할 목표 |
| `observed` | float \| null | 필수 | 이번 Take 에서 잰 값 (목표는 여기서 한 번에 도달할 만큼만 낮춘다) |
| `reason_codes` | list[str] | 필수 | 이 미션을 고른 이유 코드 |
| `evidence` | dict[str, Any] | 필수 | 근거 지표 |

**`NextMissionTarget`** — `NextMission.target`. `Mission.target`(`MissionTarget`)과 같은 모양이라 다음 Take 요청의 `missions[].target`에 그대로 넣을 수 있습니다

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `metric` | str | 필수 | 지표 이름 (6절) |
| `operator` | "LT" \| "LTE" \| "GT" \| "GTE" \| "EQ" | 필수 | 비교 연산자 |
| `value` | float | 필수 | 목표값 |

**`MissionReview`** — `mission_results[]`. 판단하지 못한 `reason`은 `NOT_REACHED`(그 장에 가지 못함) · `LOW_DATA_COVERAGE`(데이터 덮개가 `min_coverage` 미만) · `UNSUPPORTED_METRIC` · `NO_TARGET` · `NO_DATA`입니다

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `mission_id` | str | 필수 | 이전 미션 ID |
| `type` | FeedbackType | 필수 | 원인 영역 (`FeedbackType`) |
| `slide_number` | int \| null | 필수 | 장 번호. `null`이면 Take 전체 |
| `status` | MissionStatus | 필수 | 판정 (`MissionStatus`) |
| `achieved` | bool | 필수 | 달성 여부 |
| `metric` | str | 필수 | 판정에 쓴 지표 이름 |
| `operator` | str \| null | 필수 | 미션의 비교 연산자 |
| `target` | float \| null | 필수 | 미션의 목표값 |
| `observed` | float \| null | 필수 | 이번 Take 에서 잰 값 |
| `coverage` | float \| null | 필수 | 그 값을 잰 데이터가 덮은 시간 비율 |
| `reason` | str \| null | `null` | NOT_EVALUABLE 인 이유 (NOT_REACHED · LOW_DATA_COVERAGE · UNSUPPORTED_METRIC · NO_TARGET · NO_DATA) |

**`MemoryReview`** — `memory_check[]`

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `type` | FeedbackType | 필수 | 원인 영역 (`FeedbackType`) |
| `slide_number` | int \| null | 필수 | 장 번호. `null`이면 Take 전체 |
| `label` | MemoryLabel | 필수 | 이번 Take 와 비교한 결과 (`MemoryLabel`) |
| `burden_s` | float | 필수 | 이번 Take 에서 그 문제의 부담 |

**`StrengthReview`** — `strengths[]`

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `kind` | StrengthKind | 필수 | 강점 종류 (`StrengthKind`) |
| `type` | FeedbackType | 필수 | 원인 영역 (`FeedbackType`) |
| `slide_number` | int \| null | `null` | 장 번호. `null`이면 Take 전체 |
| `evidence` | dict[str, Any] | 필수 | 근거 지표 (6절의 지표 이름) |

### 5-3. 세부 근거

**`SlideReview`** — `slides[]`. 장별 표 한 줄이며 리뷰의 구간 표(CAMERA/BOTTOM · Pace · Filler · Keyword)와 같은 축입니다

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `slide_number` | int | 필수 | 장 번호 |
| `visits` | int | 필수 | 이 장을 방문한 횟수 |
| `start_ms` | int | 필수 | 이 장을 처음 시작한 시각 (ms) |
| `duration_ms` | int | 필수 | 이 장에 머문 총 시간 (ms) |
| `target_ms` | int \| null | 필수 | 이 장의 목표 시간 (ms) |
| `over_ms` | int \| null | 필수 | 목표보다 더 쓴 시간 (음수면 덜 씀) |
| `script_ratio` | float \| null | 필수 | 보인 시간 중 대본 응시 비율 |
| `cpm` | float \| null | 필수 | 말 속도 (글자/분) |
| `relative_db` | float \| null | 필수 | 캘리브레이션 대비 음량 (dB) |
| `filler_count` | int | 필수 | 군더더기 수 |
| `filler_per_min` | float \| null | 필수 | 분당 군더더기 수 |
| `long_silence_ms` | int | 필수 | 긴 침묵 시간 (ms) |
| `keyword_coverage` | float \| null | 필수 | 필수 키워드 중 말한 비율 |
| `keywords_missing` | list[str] | 필수 | 말하지 않은 필수 키워드 |
| `gaze_coverage` | float \| null | 필수 | 시선 판단을 믿을 수 있던 시간 비율 |
| `speech_coverage` | float \| null | 필수 | STT를 믿을 수 있던 시간 비율 |
| `audio_coverage` | float \| null | 필수 | 오디오가 살아 있던 시간 비율 |
| `issue_types` | list[FeedbackType] | 필수 | 이 장에서 문제로 본 영역 |

**`SegmentReview`** — `segments[]`. 문제 구간입니다. `start_ms` · `end_ms`는 코치가 실제로 문제를 잡은 구간이고, `onset_ms` · `offset_ms`는 평가기 창의 지연을 되돌린 추정 구간입니다. 리뷰가 "몇 분 몇 초부터"를 말할 때는 `onset_ms` · `offset_ms`를 씁니다

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `candidate_id` | str | 필수 | 구간의 대표 문제 ID |
| `candidate_ids` | list[str] | 필수 | 합쳐진 문제 ID 전부 |
| `slide_number` | int \| null | 필수 | 장 번호. `null`이면 장 정보가 없는 경우 |
| `start_ms` | int | 필수 | 코치가 문제를 잡은 구간 시작 (ms) |
| `end_ms` | int | 필수 | 코치가 문제를 잡은 구간 끝 (ms) |
| `onset_ms` | int | 필수 | 창 지연을 되돌린 추정 시작 (ms) |
| `offset_ms` | int | 필수 | 창 지연을 되돌린 추정 끝 (ms) |
| `type` | FeedbackType | 필수 | 원인 영역 (`FeedbackType`) |
| `issue` | Issue | 필수 | 대표 문제 |
| `issues` | list[Issue] | 필수 | 합쳐진 문제 전부 |
| `peak_severity` | float | 필수 | 최고 심각도 |
| `mean_severity` | float | 필수 | 평균 심각도 |
| `reliability` | float | 필수 | 센서를 믿을 수 있던 시간 비율 |
| `hint` | SegmentHint | 필수 | 구간 꼬리표 (`SegmentHint`, 5절) |
| `intervention_ids` | list[str] | 필수 | 이 구간에서 말한 개입의 `event_id` |
| `suppressed_reasons` | list[str] | 필수 | 말하지 못한 이유 코드 |
| `evidence` | dict[str, Any] | 필수 | 구간이 가장 나빴을 때의 지표 |

**`InterventionReview`** — `interventions[]`. 개입마다 효과(`before` → `after`)

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `intervention_id` | str | 필수 | 개입 `event_id` |
| `t_ms` | int | 필수 | 개입 시각 (ms) |
| `slide_number` | int \| null | 필수 | 장 번호. `null`이면 장 정보가 없는 경우 |
| `type` | FeedbackType | 필수 | 원인 영역 (`FeedbackType`) |
| `instruction` | Instruction | 필수 | 발표자에게 하라는 행동 (`Instruction`) |
| `message` | str | 필수 | 화면에 띄운 문장 |
| `reason_codes` | list[str] | 필수 | 개입한 이유 코드 |
| `evidence` | dict[str, Any] | 필수 | 개입 시점 근거 지표 |
| `outcome` | Outcome | 필수 | 효과 판정 (`Outcome`) |
| `outcome_metric` | str \| null | `null` | 효과를 잰 지표 이름 |
| `before` | float \| null | `null` | 개입 시점 지표값 |
| `after` | float \| null | `null` | 효과를 잰 시점 지표값 (재지 못했으면 `null`) |

**`StrategyReview`** — `strategy_changes[]`

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `t_ms` | int | 필수 | 방법을 바꾼 시각 (ms) |
| `type` | FeedbackType | 필수 | 원인 영역 (`FeedbackType`) |
| `issue` | Issue | 필수 | 평가기가 찾은 문제 (`Issue`) |
| `slide_number` | int \| null | 필수 | 장 번호. `null`이면 장 정보가 없는 경우 |
| `change` | StrategyChange | 필수 | `ESCALATED` 또는 `GAVE_UP` |
| `from_instruction` | Instruction | 필수 | 바꾸기 전 행동 |
| `to_instruction` | Instruction \| null | 필수 | 바꾼 뒤 행동 (`GAVE_UP`이면 `null`) |

**`TypeSummary`** — `by_type[]`. 영역별 집계 (개입 또는 문제 구간이 있는 영역만. 개입은 교정 개입만 세고 `CONTINUE` 격려는 뺍니다)

| 필드 | 타입 | 기본값 | 뜻 |
|---|---|---|---|
| `type` | FeedbackType | 필수 | 원인 영역 (`FeedbackType`) |
| `interventions` | int | 필수 | 개입 수 |
| `effective` | int | 필수 | 효과 있던 개입 수 |
| `ineffective` | int | 필수 | 효과 없던 개입 수 |
| `not_measured` | int | 필수 | 효과를 재지 못한 개입 수 |
| `episodes` | int | 필수 | 문제 구간 수 |
| `episodes_unaddressed` | int | 필수 | 말하지 못한 문제 구간 수 |

### 5-4. `hint` — 구간 꼬리표

| `hint` | 뜻 | 리뷰가 할 만한 것 |
|---|---|---|
| `COACHED_EFFECTIVE` | 말했더니 바뀜 | 개선된 점 / 코칭 효과 |
| `COACHED_INEFFECTIVE` | 말했는데 안 바뀜 | 남은 문제 |
| `GAVE_UP` | 여러 방법을 써도 안 바뀌어 그만둠 | 다음 Mission 후보 (예: "6번 장 대본 응시 30% 이하") |
| `UNADDRESSED` | 문제였지만 말하지 못함 (`suppressed_reasons`에 이유 — 실전 모드, 간격 …) | 실전 모드 리포트, 남은 문제 |
| `COACHED_NOT_MEASURED` | 말했지만 효과를 재지 못함 | — |
| `UNRELIABLE` | 센서를 믿을 수 없던 구간 | **문제로 말하지 않는다** — 데이터 품질로만 |

`hint`는 위에서부터 차례로 검사해 처음 맞는 것이 정해집니다: 센서를 믿을 수 있던 비율이 `min_reliability`(기본 0.5) 미만이면 `UNRELIABLE`, 개입이 없으면 `UNADDRESSED`, 개입 중 `GAVE_UP`된 것이 있으면 `GAVE_UP`, 효과 있던 개입이 있으면 `COACHED_EFFECTIVE`, 없던 개입이 있으면 `COACHED_INEFFECTIVE`, 나머지는 `COACHED_NOT_MEASURED`입니다. `UNRELIABLE` 구간은 `summary.episodes`에서도 빠지고 `data_quality.unreliable_segments`로만 셉니다.

### 5-5. 계산 메모

- **키워드**: 실시간 `MENTION_KEYWORD` 개입은 기본으로 꺼져 있습니다(`Features.keyword_missing`, STT가 고유명사를 잘못 적어 오탐이 나기 때문). 리뷰 근거의 CONTENT 문제는 별개로 `SLIDE` 이벤트의 필수 · 찾은 키워드 집계로 계산합니다.
- **미션 판정**: 목표를 달성하면 `ACHIEVED`, 놓친 정도가 지표별 허용치(`partial_tolerance`) 이내면 `PARTIAL`, 그 밖은 `FAILED`입니다. 판정에 쓴 데이터 덮개가 `min_coverage`(기본 0.5) 미만이면 `NOT_EVALUABLE`입니다.
- **영역 판단 가능 여부**(`data_quality.evaluable`): GAZE는 시선 덮개, SPEED · FILLER는 STT 덮개, PAUSE는 오디오 덮개가 `min_coverage` 이상이어야 하고, CONTENT는 필수 키워드가 있고 STT 덮개가 충분해야 합니다. VOLUME은 오디오 덮개와 '말한 시간 중 음량을 잰 비율' 중 작은 쪽을 봅니다 — 음량 레벨 입력은 기준을 잡기 전에 말한 시간을 재지 못하므로, 오디오만 살아 있었다고 음량 문제가 해결됐다고 하지 않습니다. `relative_db` 미션도 같은 덮개를 씁니다.

### 5-6. 리뷰 에이전트 프롬프트에 넣을 규칙

1. **숫자는 이 근거에 있는 것만 인용한다.** 새로 계산하거나 추정하지 않는다.
2. `UNRELIABLE` 구간과 `NOT_EVALUABLE` 영역 · 미션은 문제로도 강점으로도 말하지 않고 "판단할 수 없었다"고 말한다 (`data_quality`가 이유).
3. "몇 분 몇 초부터"는 `onset_ms` · `offset_ms`로 말한다.
4. 가장 먼저 고칠 것은 `issues[0]`, 다음 미션은 `next_missions`의 `target`을 그대로 쓰고 `description` 문장만 쓴다.
5. 실시간 코칭 효과는 `interventions[].outcome`과 `summary`로만 말한다.

### 5-7. 예시

`14_recurring_persists`(3번 장 시선 문제가 이전 Take에서 남아 있고 이번에도 계속되는 시나리오) 한 판의 이벤트로 만든 근거입니다. 이 Take의 입력은 미션 `m-gaze-3`(3번 장 `script_ratio` ≤ 0.3)과 기억 `GAZE`/3번 장입니다. 길어서 `type_status`는 7개 중 2개, `strengths`는 6개 중 1개, `slides`는 4개 중 1개만 남겼고, 나머지 목록은 전부 실었습니다.

```json
{
  "schema_version": "1.1",
  "policy_version": "coach-v1.1",
  "config_hash": "dbe162df2a38",
  "take_id": "sim-14_recurring_persists",
  "summary": {
    "interventions": 1,
    "praises": 0,
    "effective": 0,
    "ineffective": 1,
    "not_measured": 0,
    "effective_rate": 0.0,
    "gave_up": 0,
    "episodes": 1,
    "episodes_unaddressed": 0,
    "suppressed_by_reason": {"COOLDOWN": 5, "MIN_GAP": 1, "NOT_PERSISTENT": 1}
  },
  "data_quality": {
    "duration_ms": 171000,
    "gaze_coverage": 1.0,
    "speech_coverage": 1.0,
    "audio_coverage": 1.0,
    "unreliable_segments": 0,
    "evaluable": {"GAZE": true, "SPEED": true, "FILLER": true, "VOLUME": true, "PAUSE": true, "CONTENT": true, "TIME": true}
  },
  "type_status": [
    {"type": "TIME", "status": "STRENGTH", "burden_s": 0.0, "evidence": {"duration_ms": 171000}},
    {"type": "GAZE", "status": "PRIORITY", "burden_s": 43.09, "memory": "RECURRING", "evidence": {"script_ratio": 0.3893, "coverage": 1.0}}
  ],
  "issues": [
    {
      "rank": 1,
      "type": "GAZE",
      "slide_number": 3,
      "issues": ["GAZE_SCRIPT"],
      "burden_s": 43.09,
      "score": 77.56,
      "duration_ms": 57000,
      "segments": 1,
      "peak_severity": 0.9,
      "interventions": 1,
      "effective": 0,
      "ineffective": 1,
      "gave_up": false,
      "memory": "RECURRING",
      "mission_failed": true,
      "evidence": {"script_ratio": 0.9, "continuous_script_gaze_ms": 7000, "gaze_uncertain_ratio": 0.0, "window_ms": 10000}
    },
    {
      "rank": 2,
      "type": "CONTENT",
      "slide_number": 3,
      "issues": ["KEYWORD_MISSING"],
      "burden_s": 10.0,
      "score": 10.0,
      "duration_ms": 0,
      "segments": 1,
      "peak_severity": 0.6,
      "interventions": 0,
      "effective": 0,
      "ineffective": 0,
      "gave_up": false,
      "memory": "NEW",
      "mission_failed": false,
      "evidence": {"keywords_missing": ["로컬 처리"], "keyword_coverage": 0.0}
    }
  ],
  "next_missions": [
    {
      "priority": 1,
      "type": "GAZE",
      "slide_number": 3,
      "target": {"metric": "script_ratio", "operator": "LTE", "value": 0.59},
      "observed": 0.7865,
      "reason_codes": ["TOP_BURDEN", "RECURRING", "MISSION_FAILED"],
      "evidence": {"burden_s": 43.09, "issues": ["GAZE_SCRIPT"]}
    },
    {
      "priority": 2,
      "type": "CONTENT",
      "slide_number": 3,
      "target": {"metric": "keyword_coverage", "operator": "GTE", "value": 1.0},
      "observed": 0.0,
      "reason_codes": [],
      "evidence": {"burden_s": 10.0, "issues": ["KEYWORD_MISSING"]}
    }
  ],
  "mission_results": [{"mission_id": "m-gaze-3", "type": "GAZE", "slide_number": 3, "status": "FAILED", "achieved": false, "metric": "script_ratio", "operator": "LTE", "target": 0.3, "observed": 0.7865, "coverage": 1.0}],
  "memory_check": [{"type": "GAZE", "slide_number": 3, "label": "RECURRING", "burden_s": 43.09}],
  "strengths": [{"kind": "CLEAN", "type": "TIME", "evidence": {"duration_ms": 171000}}],
  "slides": [
    {
      "slide_number": 1,
      "visits": 1,
      "start_ms": 0,
      "duration_ms": 28000,
      "target_ms": 30000,
      "over_ms": -2000,
      "script_ratio": 0.1793,
      "cpm": 300.0,
      "relative_db": 0.0,
      "filler_count": 0,
      "filler_per_min": 0.0,
      "long_silence_ms": 0,
      "keyword_coverage": null,
      "keywords_missing": [],
      "gaze_coverage": 1.0,
      "speech_coverage": 1.0,
      "audio_coverage": 1.0,
      "issue_types": []
    }
  ],
  "segments": [
    {
      "candidate_id": "GAZE_SCRIPT-89000",
      "candidate_ids": ["GAZE_SCRIPT-89000"],
      "slide_number": 3,
      "start_ms": 89000,
      "end_ms": 142000,
      "onset_ms": 82000,
      "offset_ms": 139000,
      "type": "GAZE",
      "issue": "GAZE_SCRIPT",
      "issues": ["GAZE_SCRIPT"],
      "peak_severity": 0.9,
      "mean_severity": 0.7559,
      "reliability": 1.0,
      "hint": "COACHED_INEFFECTIVE",
      "intervention_ids": ["ev-00004"],
      "suppressed_reasons": ["NOT_PERSISTENT", "MIN_GAP", "COOLDOWN"],
      "evidence": {"script_ratio": 0.9, "continuous_script_gaze_ms": 7000, "gaze_uncertain_ratio": 0.0, "window_ms": 10000}
    }
  ],
  "interventions": [
    {
      "intervention_id": "ev-00004",
      "t_ms": 92000,
      "slide_number": 3,
      "type": "GAZE",
      "instruction": "LOOK_AT_CAMERA",
      "message": "대본보다 청중을 조금 더 바라보세요",
      "reason_codes": ["GAZE_SCRIPT", "MISSION_RELEVANT", "MISSION_AT_RISK", "RECURRING", "WORSENING"],
      "evidence": {"start_ms": 89000, "end_ms": 92000, "slide_number": 3, "script_ratio": 0.7, "continuous_script_gaze_ms": 1000, "gaze_uncertain_ratio": 0.0, "window_ms": 10000},
      "outcome": "INEFFECTIVE",
      "outcome_metric": "script_ratio",
      "before": 0.6333,
      "after": 0.8556
    }
  ],
  "strategy_changes": [{"t_ms": 104000, "type": "GAZE", "issue": "GAZE_SCRIPT", "slide_number": 3, "change": "ESCALATED", "from_instruction": "LOOK_AT_CAMERA", "to_instruction": "LOOK_AT_CAMERA"}],
  "by_type": [{"type": "GAZE", "interventions": 1, "effective": 0, "ineffective": 1, "not_measured": 0, "episodes": 1, "episodes_unaddressed": 0}]
}
```

---

## 6. 공통 지표 이름

`evidence` · `Mission.target.metric` · 리뷰 근거가 **같은 이름**을 써야 미션 판정이 이어집니다. 미션 `target.metric`이 아래에 없으면 실시간으로는 검사하지 않고(조용히 넘어감), 리뷰 판정에서는 `UNSUPPORTED_METRIC`입니다.

| 지표 | 뜻 | 단위 |
|---|---|---|
| `script_ratio` | 보인 시간 중 대본 응시 비율 (최근 10초) | 0~1 |
| `continuous_script_gaze_ms` | 대본 연속 응시 시간 | ms |
| `gaze_uncertain_ratio` | UNCERTAIN 비율 | 0~1 |
| `cpm` · `cpm_recent` | 15초 말 속도 · 효과를 잴 때 쓰는 6초 말 속도 | 글자/분 |
| `relative_db` | 최근 5초 평균 (기준 대비) | dB |
| `silence_ms` | 지금 침묵 길이 | ms |
| `filler_count_60s` · `filler_count_30s` · `filler_per_min` | 군더더기 | 회 · 회/분 |
| `required_ratio` | r. 남은 내용을 남은 시간에 끝내려면 필요한 속도 배율 | 배 |
| `remaining_ms` · `remaining_content_ms` · `remaining_slides` | 남은 시간 · 남은 내용 시간 · 남은 장 | ms · ms · 장 |
| `slide_progress` · `slide_elapsed_ms` · `slide_target_ms` | 이 장 진행도 · 체류 · 목표 | 0~1 · ms · ms |
| `slide_duration_ms` | 지금 속도면 이 장이 걸릴 시간 (진행도 10% 미만이면 체류 시간) | ms |
| `projected_end_ms` · `early_limit_ms` | 예상 종료 · '너무 일찍'의 기준 | ms |
| `duration_ms` | Take 전체 시간 | ms |
| `long_silence_count` | 긴 멈춤 횟수 | 회 |
| `keyword_coverage` | 필수 키워드 중 말한 비율 | 0~1 |

실시간 `evidence`에는 이 밖에 상황 설명용 값(`required_cpm`, `over_ms`, `limit_ms`, `keyword` 등)이 문제에 따라 함께 실립니다. 값의 이름은 문제마다 정해져 있고 응답 예시([3-4](#3-4-예시))처럼 평평한 키입니다.

**미션 판정이 지원하는 지표** (`review.py`의 `SUPPORTED_MISSION_METRICS`)

| `target.metric` | 영역 | 데이터 덮개 | 비고 |
|---|---|---|---|
| `script_ratio` | GAZE | 시선 | |
| `cpm` | SPEED | STT | |
| `relative_db` | VOLUME | 오디오 | |
| `filler_per_min` | FILLER | STT | |
| `keyword_coverage` | CONTENT | STT | |
| `slide_duration_ms` | TIME | (항상 1.0) | 장을 지정해야 합니다 (`slide_number` 없으면 `UNSUPPORTED_METRIC`) |
| `duration_ms` | TIME | (항상 1.0) | Take 전체 시간 |
| `long_silence_count` | PAUSE | 오디오 | 믿을 수 있는 `PAUSE` 문제 구간 수 |

`duration_ms` · `long_silence_count` · `keyword_coverage`는 Take가 끝난 뒤에만 의미가 있습니다.

---

## 7. `coach_state`

코치의 기억입니다. **BE는 내용을 몰라도 됩니다.** 응답의 `coach_state`를 받은 그대로 저장했다가 다음 요청(과 `finalize` 요청)의 `coach_state`에 붙이면 됩니다. 안의 모양(`CoachingPlan`을 포함해)은 공개 계약이 아니며 예고 없이 바뀔 수 있으니 읽거나 고치지 않습니다.

| 내용 | 쓰임 |
|---|---|
| 최근 60초 기록 (1초마다 대본 응시 · CPM · dB · 말하는 중 · 새 군더더기 · r) | 지속 · 악화 · 음량 평균 · 군더더기 수 · 효과 비교 |
| 열린 문제 구간 | 지속시간, `candidate_id`, `EPISODE` 이벤트 |
| 마지막 개입 시각, 행동별 마지막 시각, 문제별 개입 횟수 | 간격 · 쿨다운 · 새로움 |
| 효과를 잴 개입, 문제별 전략 단계 · 포기 여부, 격려 후보, 문장 끝을 기다리는 후보 | 되돌아보기 |
| 장별 말한 글자 수, 찾은 키워드, STT가 끊긴 장, 오디오 · STT 복귀 시각, 최근 장 전환 기록 | 진행도 · 키워드 · 침묵 · 늦게 온 단어의 장 |
| 지금 장의 누적 (시선 · CPM · dB의 '값 × 시간', 군더더기, 데이터 덮개) | 장을 떠날 때 `SLIDE` 이벤트 |
| 평소 목소리 레벨과, 잡기 전까지 모은 말한 1초의 레벨 (`level_db` 입력에서 `baseline_db`가 없을 때) | 음량 기준 |
| 이벤트 번호(`seq`), 마지막 처리 시각(`last_t_ms`), 참은 기록 시각 | `event_id` · `STALE_TICK` · `SUPPRESSED` 간격 |
| 코칭 계획 (v1은 기본값) | focus · relax · 개입 상한 |

- **크기**: 시나리오 16개를 재생하며 매 응답의 `coach_state`(UTF-8 JSON)를 잰 최대는 10,875 ~ 14,848 바이트입니다 (가장 큰 것은 `07_exam_mode`). 최근 기록(1초마다의 지표)은 60초만 남기지만, 장별 글자 수 · 전략 단계 · 장 전환 기록 같은 것은 Take 동안 쌓입니다. 테스트(`tests/lab/test_replay.py`)는 10분짜리 재생의 끝 상태가 20,000 바이트 미만인지 확인합니다. 일반적인 상한을 보장하는 것은 아닙니다. 이 크기의 상태가 요청과 응답에 매번 실립니다.
- **버전**: 모양이 바뀌면 `STATE_VERSION`을 올립니다. 기본값이 있는 필드를 더하는 것은 이전 state도 그대로 읽히므로 올리지 않습니다. 다른 버전이거나 깨진 state가 오면 새로 시작하고 `reason_codes`에 `STATE_RESET`을 남깁니다 (`decide`/`decide_safe` 한정, `finalize`는 표시 없이 새로 시작). 쿨다운 · 전략 단계 · 열린 문제 구간 · 재지 못한 효과가 사라지고 이벤트 번호도 처음부터 다시 매겨집니다. 연습 중 AI 서버를 새 버전으로 배포하면 그 순간 한 번 일어날 수 있습니다.
- **null 필드는 빼고 보냅니다.** 되읽을 때 기본값(null)으로 복원되므로 BE는 그대로 돌려주면 됩니다.
- **왜 AI 서버에 저장하지 않나**: AI 서버 재시작 · 재배포 때 발표 중 기억이 사라지지 않고, worker를 늘려도 같은 Take 요청이 어느 worker로 가든 결과가 같습니다.

---

## 8. enum 값

값은 BE · FE · 리뷰 에이전트와 공유하는 계약입니다. 이름을 바꾸거나 빼면 스키마 버전을 올립니다. 값을 **추가**하는 것도 받는 쪽이 모든 경우를 처리하고 있으면(예: FE 의 switch) 깨질 수 있으니, 받는 쪽과 먼저 맞춥니다 ([9절](#9-버전과-호환)).

**`FeedbackType`** — 원인 영역. 리뷰의 `ReviewPoint.type` · `Mission.type`과 같은 enum

`GAZE` · `SPEED` · `VOLUME` · `PAUSE` · `FILLER` · `CONTENT` · `TIME`

**`Instruction`** — 발표자에게 하라는 행동. 같은 `type`이라도 상황에 따라 다릅니다 (`TIME` → `SPEED_UP` · `WRAP_UP`)

`LOOK_AT_CAMERA` · `SLOW_DOWN` · `SPEED_UP` · `SPEAK_LOUDER` · `RESUME` · `REDUCE_FILLER` · `MENTION_KEYWORD` · `CONDENSE` · `MOVE_ON` · `WRAP_UP` · `CONTINUE`

**`Action`** · **`CandidateStatus`** — 뜻은 [3-2](#3-2-action) · [3-3](#3-3-후보-상태-candidatesstatus)

`Action`: `WAIT` · `IGNORE` · `INTERVENE`

`CandidateStatus`: `SELECTED` · `OUTRANKED` · `WAITING` · `IGNORED`

**`Reason`** — `reason_codes` · `reasons`에 들어가는 이유 코드. `INTERVENE`의 `reason_codes`는 맨 앞에 `Issue` 값이 하나 더 붙습니다

| 구분 | 코드 |
|---|---|
| 개입한 이유 (INTERVENE) | `PERSISTENT` · `WORSENING` · `MISSION_RELEVANT` · `MISSION_AT_RISK` · `RECURRING` · `PLAN_FOCUS` · `TIME_CRITICAL` · `SPEED_LIMIT_EXCEEDED` · `ESCALATED` · `AFTER_EFFECTIVE_FEEDBACK` · `PAUSE_TIMEOUT` |
| 기다린 이유 (WAIT) | `NO_CANDIDATE` · `NOT_PERSISTENT` · `MIN_GAP` · `COOLDOWN` · `WAITING_FOR_PAUSE` · `STALE_TICK` |
| 버린 이유 (IGNORE) | `EXAM_MODE` · `SENSOR_UNUSABLE` · `LOW_CONFIDENCE` · `TIME_PRESSURE` · `STRATEGY_EXHAUSTED` · `ALREADY_DELIVERED` · `PLAN_RELAXED` · `BUDGET_EXHAUSTED` · `LOW_PRIORITY` |
| 운영 | `INTERNAL_ERROR` · `STATE_RESET` |

후보에 `WAIT` 구분의 `NOT_PERSISTENT` · `MIN_GAP` · `COOLDOWN` · `WAITING_FOR_PAUSE`(`WAIT_REASONS`)가 하나라도 걸리면 `WAITING`이고, `IGNORE` 구분의 이유(`IGNORE_REASONS`)가 하나라도 걸리면 `IGNORED`이며 `WAITING`보다 우선합니다. 리뷰 근거의 `NextMission.reason_codes`는 이 enum이 아니라 별도 문자열(`TOP_BURDEN` · `RECURRING` · `GAVE_UP` · `MISSION_FAILED`)입니다.

**`Outcome`** — 개입의 효과

`EFFECTIVE` · `INEFFECTIVE` · `NOT_MEASURED`

**`StrategyChange`**

| 값 | 뜻 |
|---|---|
| `ESCALATED` | 같은 말이 안 통해서 다른 방법으로 바꿈 |
| `GAVE_UP` | 방법을 다 써서 그 범위에서는 그만둠 |

**`SegmentHint`** — 구간 꼬리표 (뜻은 [5-4](#5-4-hint--구간-꼬리표))

`COACHED_EFFECTIVE` · `COACHED_INEFFECTIVE` · `COACHED_NOT_MEASURED` · `GAVE_UP` · `UNADDRESSED` · `UNRELIABLE`

**`TypeStatus`** — 영역별 이번 Take 상태. 리뷰 dimension의 IMPROVED / PRIORITY / STABLE / STRENGTH와 같습니다

| 값 | 뜻 |
|---|---|
| `PRIORITY` | 다음에 먼저 고칠 것 |
| `IMPROVED` | 이전 Take 의 문제가 사라졌거나 미션을 달성 |
| `STABLE` | 작은 문제는 있지만 우선은 아님 |
| `STRENGTH` | 문제가 없음 |
| `NOT_EVALUABLE` | 센서 · 데이터가 모자라 판단하지 않음 |

**`MissionStatus`** — 이전 미션 판정. 리뷰의 `previous_mission_result.status`와 같습니다

`ACHIEVED` · `PARTIAL` · `FAILED` · `NOT_EVALUABLE`

**`MemoryLabel`** — 이전 Take 기억과 비교. `memory_check[].label`에는 `RECURRING` · `RESOLVED` · `UNKNOWN`이, `issues[].memory`에는 `RECURRING` · `NEW`가 나옵니다

| 값 | 뜻 |
|---|---|
| `RECURRING` | 이전에도 있었고 이번에도 있음 |
| `RESOLVED` | 이전에 있었는데 이번엔 없음 |
| `NEW` | 이번에 새로 생김 |
| `UNKNOWN` | 이번 데이터로는 판단할 수 없음 |

**`StrengthKind`**

`CLEAN` · `RESOLVED_RECURRING` · `MISSION_ACHIEVED` · `RESPONDED_TO_COACHING` · `ON_TIME`

**`Mode`**

`PRACTICE` · `EXAM`

**`Issue`** — 평가기가 찾는 문제. 후보 하나는 문제 하나에서 나옵니다. 어느 `type`에 속하는지는 `vocab.py`의 `ISSUE_TYPE`입니다

`GAZE_SCRIPT` · `PACE_FAST` · `VOLUME_LOW` · `LONG_SILENCE` · `FILLER_FREQUENT` · `KEYWORD_MISSING` · `BEHIND_SCHEDULE` · `AHEAD_OF_SCHEDULE` · `SLIDE_OVER` · `FINAL_MINUTE` · `TIME_OVER` · `IMPROVED_AFTER_FEEDBACK`

**`Schedule` · `PaceLevel` · `GazeLevel` · `VolumeLevel`** — `indicators`의 값 ([3-1](#3-1-필드)에 기준)

| 필드 | 값 |
|---|---|
| `schedule` | `AHEAD` · `ON_TRACK` · `BEHIND` · `OVER` · `UNKNOWN` |
| `pace` | `SLOW` · `NORMAL` · `FAST` · `UNKNOWN` |
| `gaze` | `AUDIENCE` · `SCRIPT` · `UNCERTAIN` · `UNKNOWN` |
| `volume` | `LOW` · `OK` · `UNKNOWN` |

---

## 9. 버전과 호환

버전 값은 `src/coach/version.py`에 있고, 응답과 리뷰 근거에 실려 나가므로 BE가 결과와 함께 저장합니다. 개별 이벤트에는 버전 · `take_id`가 없으므로, 이벤트를 쌓을 때 그 응답의 `policy_version` · `config_hash`를 함께 남겨 두면 어느 규칙으로 낸 기록인지 알 수 있습니다 (`FinalizeResponse`에는 `config_hash`가 없습니다).

| 값 | 현재 | 올리는 때 |
|---|---|---|
| `SCHEMA_VERSION` | `"1.1"` | 요청 · 응답 · 이벤트 · 리뷰 근거의 **모양**이 바뀔 때. 1.1: 시선 1초 기록 · 음량 레벨 · 표시 없는 단어 입력 |
| `POLICY_VERSION` | `"coach-v1.1"` | 판단 규칙의 의미가 바뀔 때(기능 버전). 같은 입력에 다른 판단이 나오게 바꾸면 올립니다. coach-v1.1: 원자료 입력(시선 1초 기록 · 음량 레벨과 기준 · 군더더기 판단), 측정하지 못한 1초 · Take 시작 직후의 시선 판단 |
| `STATE_VERSION` | `1` | `coach_state` 모양이 바뀔 때. 다른 버전의 state가 오면 버리고 새로 시작합니다 |

- **기준값 · 가중치 같은 설정 조정은 버전을 올리지 않습니다.** 대신 응답과 리뷰 근거의 `config_hash`(예: `"dbe162df2a38"`)가 어떤 설정으로 판단했는지 남깁니다. 같은 `config_hash` · `POLICY_VERSION`이면 같은 입력에 같은 판단이 나옵니다.
- **입력은 관대하게, 출력은 엄격하게.** 입력 모델(`CoachRequest`와 그 안의 모델, `FinalizeRequest`, `ReviewEvidenceRequest`)은 모르는 필드를 무시합니다(`extra="ignore"`). 그래서 BE가 필드를 먼저 추가해도 깨지지 않습니다. 출력 모델(`CoachResponse`, 이벤트, `CoachReviewEvidence`와 그 안의 모델)은 `extra="forbid"`라 정해진 필드만 나가고, 소비자가 모르는 필드를 만날 일이 없습니다. 반대로 BE가 이벤트를 저장했다가 `build_review_evidence`에 돌려줄 때 필드를 더하거나 바꾸면 거부되니 이벤트는 받은 그대로 보관합니다.
- **호환 규칙**: 입력 모델에 선택 필드를 **추가**하는 것은 스키마 버전 안에서 가능합니다 (입력은 모르는 필드를 무시). 출력 · 이벤트 모델에 필드를 추가하면, 그 필드가 담긴 이벤트를 옛 버전의 `build_review_evidence`가 받을 때 거부하므로(`extra="forbid"`) AI 서버를 먼저 배포한 뒤 쓰는 쪽을 바꿉니다. enum 값 추가는 받는 쪽이 모든 경우를 처리하고 있으면 깨질 수 있어 받는 쪽과 먼저 맞춥니다. 필드 · 값의 이름을 바꾸거나 빼면 `SCHEMA_VERSION`을 올립니다. 요청의 `schema_version`은 기본값이 `"1.1"`이고 코드가 비교하지는 않으므로, 버전별 처리가 필요해지면 서버 쪽에 검사를 추가해야 합니다.
- **필드 이름은 snake_case**입니다 (BE DTO · 리뷰 DTO와 같게).
- **시간은 전부 ms**이고, 모든 시각(`t_ms` · `start_ms` · `end_ms` · `onset_ms` …)은 **Take 시작 기준 경과 시간**입니다. 예외로 `*_s` 접미사가 붙은 `burden_s`는 초 단위입니다.
- **비율 · 신뢰도는 0~1**입니다 (`confidence`, `script_ratio`, `*_coverage`, `effective_rate` …). `feedback` · `candidates[]` · 이벤트의 `priority`는 0~100 점수이고, `next_missions[].priority`는 순위(1이 먼저), `missions[].priority`는 받은 값 그대로입니다. `required_ratio`는 배수라 1 이상일 수 있습니다.
