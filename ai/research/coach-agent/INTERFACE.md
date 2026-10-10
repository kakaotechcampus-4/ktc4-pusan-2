# 인터페이스 — BE ↔ 코치

BE가 코치를 부르는 세 가지 호출의 입력 · 출력입니다. 모양의 기준은 `src/coach/schemas.py`이고, 이 문서는 그것을 사람이 읽기 좋게 풀어 쓴 것입니다.
실제 값은 실행 가능한 예시로 확인합니다 (`python -m coach_lab.examples`로 만들고, 계약 테스트가 코치에 넣어 응답과 맞는지 봅니다).

| 호출 | 코어 함수 | 언제 | 예시 |
|---|---|---|---|
| `POST /coach/evaluate` | `decide_safe(request, judges)` | 발표 중 1초마다 | `src/coach/examples/evaluate/` |
| `POST /coach/finalize` | `finalize(request, judges)` | Take 종료 때 한 번 | `src/coach/examples/finalize/` (정상 · replay) |
| `POST /coach/plan` | `plan_coaching(request, llm=…)` | Take 시작 전 한 번 (선택) | — |

`src/coach/examples/timing/`에는 코치 안의 시간 판정(`timing`)의 입력 · 출력 · 기준 · 합계 예시가 있습니다.

공통 규칙

- 필드 이름은 snake_case, 시각은 모두 **Take 시작 기준 ms**(`t_ms`)입니다.
- 요청은 모르는 필드를 무시합니다(BE가 필드를 먼저 추가해도 깨지지 않음). 응답은 정해진 필드만 냅니다.
- 코치는 아무것도 저장하지 않습니다. 기억은 `coach_state`에 담겨 나가고 BE가 다음 요청에 그대로 붙입니다. 시계 · 파일 · 네트워크를 쓰지 않아 같은 요청에는 같은 응답이 나옵니다.
- 판정(시선 · 속도 · 음량 · 군더더기)은 코치가 하지 않습니다. 판정 모듈(#152~#155)이 하고, 코치는 그 결과로 **말할지 · 무엇을 말할지**를 정합니다. 시간 판정(`timing`)만 계획이 필요해 코치 안에 있습니다.

---

## 1. `POST /coach/evaluate`

### 1-1. 요청

| 필드 | 설명 |
|---|---|
| `take_id` | Take id |
| `t_ms` | 지금 시각 (Take 시작 기준). 이미 처리한 시각 이하면 `STALE_TICK` |
| `mode` | `COACHING`(말을 건다) · `EXAM`(판정과 기록만 하고 말하지 않는다). 기본 `COACHING` |
| `plan` | 발표 계획. Take 동안 바뀌지 않는다 |
| `missions` | 직전 리뷰의 다음 미션 |
| `recurring_issues` | 직전 리뷰가 "아직 남은 문제"로 꼽은 것 `[{area, slide_number}]` |
| `coaching_plan` | Take 시작 전 코칭 계획(`/coach/plan` 응답의 `plan`). `null`이면 계획 없음 |
| `script_used` | 대본을 보며 해도 되는 발표인가. `true`면 대본 응시(`GAZE_ON_SCRIPT`)는 말하지 않는다(`SCRIPT_ALLOWED`) |
| `calibration.base_level_db` | 평소 목소리 기준 음량. `null`이면 코치가 Take 첫 발화로 잡는다 |
| `inputs` | 판정 모듈에 넘길 원자료 (아래) |
| `coach_state` | 지난 응답의 `coach_state` 그대로. 첫 요청이면 `null` |

`plan`

| 필드 | 설명 |
|---|---|
| `target_ms` · `min_ms` · `max_ms` | 전체 목표와 허용 범위 |
| `slides[].slide_number` | 장 번호 |
| `slides[].target_ms` | 그 장의 목표 시간. `0`이면 그 장의 `SLIDE_OVER`를 내지 않는다 |
| `slides[].script_chars` | 그 장 대본 글자 수(공백 제외). 진행도 = 말한 글자 수 ÷ `script_chars` |
| `slides[].required_keywords` | 코칭 계획(LLM) 입력에만 쓴다. 발표 중 코치는 보지 않는다 |

`missions[]`: `mission_id`, `area`, `slide_number`, `priority`, `description`, `target{metric, operator(LT·LTE·GT·GTE), value}`.
`coaching_plan`: `focus[{area, slide_number, weight, why}]`(먼저 챙길 것, `weight`는 코치가 0.5~2.0으로 자른다), `relax[{area, slide_number, why}]`(봐줄 것. 미션과 겹치면 봐주지 않는다), `max_interventions`(말할 최대 횟수, `null`이면 제한 없음. 시간 마무리는 상한에서 뺀다).

`inputs` (판정 모듈에 넘길 원자료. FE · STT 기록을 **가공하지 않고** 담는다)

| 필드 | 창 | 설명 |
|---|---|---|
| `gaze_records[]` | 최근 30초 | 시선 1초 기록 `{t_ms, duration_ms, state, direction, confidence, reliability, issues, frames}` |
| `voice_records[]` | 최근 30초 | 음량 1초 기록 `{t_ms, duration_ms, level_db, voiced_ms, silence_ms, audio_live}`. 말하지 않은 1초는 `level_db`가 `null` |
| `words[]` | 최근 60초 | STT 확정 단어 `{word, start_ms, end_ms}` (끝난 시각 `end_ms` 기준) |
| `utterance_ends[]` | 최근 60초 | 문장 끝 시각 |
| `stt_status` | | `ok`이면 믿는다. 그 밖의 값(`connecting` 등)이면 STT 기반 판정은 잴 수 없음 |
| `slide` | | `{number, started_ms}` 지금 장과 그 장이 시작된 시각. 없으면 마지막으로 알던 장 |

창은 BE가 매번 겹쳐 보내도 됩니다. 코치가 판정 모듈별 커서(`since_ms`)로 이미 센 시간을 건너뛰므로 두 번 세지 않습니다. 같은 1초 기록이 두 번 오면 먼저 온 것만 씁니다. 요청 한두 번이 빠져도 다음 창(30초)이 메우고, 30초보다 길게 빠지면 그 구간은 Take 결과를 합계로 못 만듭니다(3-4).

```json
// src/coach/examples/evaluate/request.json (발췌)
{
  "take_id": "take-123", "t_ms": 92000, "mode": "COACHING",
  "plan": {"target_ms": 300000, "min_ms": 270000, "max_ms": 330000,
           "slides": [{"slide_number": 3, "target_ms": 90000, "script_chars": 450}]},
  "missions": [{"mission_id": "m-2", "area": "GAZE", "slide_number": 3,
                "target": {"metric": "script_ratio", "operator": "LTE", "value": 0.4}}],
  "coaching_plan": {"focus": [{"area": "GAZE", "slide_number": 3, "weight": 1.5}], "relax": [], "max_interventions": null},
  "inputs": {
    "gaze_records": [{"t_ms": 62000, "duration_ms": 1000, "state": "CAMERA", "confidence": 0.88, "reliability": 0.95, "issues": [], "frames": 12}],
    "voice_records": [{"t_ms": 62000, "duration_ms": 1000, "level_db": -29.5, "voiced_ms": 400, "silence_ms": 0, "audio_live": true}],
    "words": [{"word": "가나다", "start_ms": 31680, "end_ms": 32280}],
    "utterance_ends": [33000, 38270, 43070],
    "stt_status": "ok", "slide": {"number": 3, "started_ms": 59390}
  },
  "calibration": {"base_level_db": -29.5},
  "coach_state": {"v": 2, "last_t_ms": 91000, "...": "..."}
}
```

### 1-2. 응답

| 필드 | 설명 |
|---|---|
| `action` | `WAIT`(지금은 말하지 않고 본다) · `IGNORE`(문제는 있지만 이번엔 버린다) · `INTERVENE`(지금 말한다) |
| `feedback` | `INTERVENE`일 때만. `{area, instruction, message, priority(0~100), confidence(0~1), evidence}` — `evidence`에는 `start_ms` · `end_ms` · `slide_number`와 판정 모듈이 준 값이 든다 |
| `indicators` | 영역(`GAZE` `SPEED` `VOLUME` `PAUSE` `FILLER` `TIME`) → 판정 모듈이 준 상태 문자열. 상태 표시용이라 여러 개를 함께 띄워도 된다 |
| `reason_codes` | 왜 이 행동인가 (3-2의 표). `INTERVENE`이면 첫 값이 문제 코드(`issue_type`) |
| `events` | 이번 응답에서 생긴 이벤트 (1-3). BE가 그대로 쌓는다 |
| `coach_state` | 다음 요청에 그대로 붙인다. 내용은 몰라도 된다 |
| `meta` | `schema_version`, `feature_version`, `criteria_versions`(모듈 이름 → 기준 버전. `coach`는 `<기능 버전>+<설정 해시>`), `model`(항상 `null`) |

`instruction`: `LOOK_AT_CAMERA` `SLOW_DOWN` `SPEED_UP` `SPEAK_LOUDER` `RESUME` `REDUCE_FILLER` `CONDENSE` `MOVE_ON` `WRAP_UP` `CONTINUE`(효과가 있을 때 "지금처럼").
`message`는 템플릿 문장이고 LLM이 만들지 않습니다.

```json
// src/coach/examples/evaluate/response.json (발췌)
{
  "action": "INTERVENE",
  "feedback": {"area": "GAZE", "instruction": "LOOK_AT_CAMERA", "message": "대본보다 청중을 조금 더 바라보세요",
               "priority": 100, "confidence": 1.0,
               "evidence": {"start_ms": 88000, "end_ms": 92000, "slide_number": 3, "script_ratio": 0.8, "window_ms": 10000}},
  "indicators": {"GAZE": "SCRIPT", "SPEED": "NORMAL", "VOLUME": "NORMAL", "PAUSE": "NORMAL", "FILLER": "NORMAL", "TIME": "AHEAD"},
  "reason_codes": ["GAZE_ON_SCRIPT", "MISSION_RELEVANT", "RECURRING", "PLAN_FOCUS", "WORSENING"],
  "events": [{"event_id": "iv-92000", "t_ms": 92000, "kind": "INTERVENTION", "intervention_id": "iv-92000", "...": "..."}],
  "coach_state": {"v": 2, "last_t_ms": 92000, "...": "..."},
  "meta": {"schema_version": "1.0", "feature_version": "coach-1.2", "model": null,
           "criteria_versions": {"gaze": "gaze-0.2+b6f8b464a3ee", "timing": "timing-1.0+305f8e44237d", "coach": "coach-1.2+9d82f5b1d7bb", "...": "..."}}
}
```

### 1-3. 이벤트

BE는 `events`를 Take별로 **받은 순서대로 그대로 쌓습니다**(추가만). 종료 때 `/coach/finalize`에 그대로 돌려줍니다. 공통 필드는 `event_id` · `t_ms`(생긴 시각) · `kind`입니다.

| kind | 언제 | 필드 (공통 외) |
|---|---|---|
| `INTERVENTION` | 말을 걸었다 | `intervention_id`(= `event_id`), `issue_type`, `area`, `instruction`, `message`, `priority`, `confidence`, `reason_codes`, `slide_number` |
| `OUTCOME` | 개입 몇 초 뒤 행동이 바뀌었는지 쟀다 | `intervention_id`, `outcome`(`EFFECTIVE` · `INEFFECTIVE` · `NOT_MEASURED`), `metric`, `before`, `after` |
| `EPISODE` | 문제 구간 하나가 끝났다 (개입했든 안 했든) | `issue_type`, `area`, `slide_number`, `start_ms`, `end_ms`, `peak_severity`, `mean_severity`, `intervention_ids`, `suppressed_reasons`, `reliable_ms`, `unreliable_ms` |
| `SLIDE` | 장 방문 하나가 끝나고 그 장의 단어가 다 확정됐다 (또는 Take가 끝났다) | `slide_number`, `start_ms`, `end_ms`, `target_ms`, `tally` |
| `STRATEGY` | 효과가 없어 방법을 바꿨다(`ESCALATED`) 또는 그만뒀다(`GAVE_UP`) | `issue_type`, `area`, `slide_number`, `change`, `from_instruction`, `from_variant`, `to_instruction`, `to_variant`, `intervention_id` |
| `SUPPRESSED` | 걸렸지만 말하지 않았다. 같은 문제는 10초에 한 번만 | `issue_type`, `area`, `slide_number`, `instruction`, `status`(`WAITING` · `IGNORED`), `priority`, `reasons` |

- `EPISODE`의 `start_ms` · `end_ms`는 문제를 처음 · 마지막으로 본 판정 시각입니다. `mean_severity`는 구간 평균 심각도(0~1)이고 부담은 이것으로 잽니다. `reliable_ms` · `unreliable_ms`는 센서를 믿을 수 있던 / 없던 시간입니다.
- `SLIDE.tally`는 **그 방문의** 영역별 합계입니다: 영역 → 이름 → 합 (`{"GAZE": {"camera_ms": 24000.0, "total_ms": 30000.0, ...}, "TIME": {"elapsed_ms": ..., "planned_ms": ...}}`). 이름은 판정 모듈이 정합니다. 다시 돌아온 장은 방문마다 하나씩 냅니다. 평균 같은 지표는 합계를 모듈의 `summarize`에 넣어 냅니다.
- `SLIDE`는 장을 떠난 직후가 아니라, 군더더기 · 속도 모듈의 단어 커서가 방문 끝을 지난 뒤(단어 확정이 늦을 수 있어서) 나옵니다. 그래서 `t_ms`가 `end_ms`보다 늦습니다.

`event_id`는 종류와 대상으로 만듭니다. 번호를 세지 않으므로 같은 이벤트를 다시 만들어도 같은 id이고, BE가 두 번 저장해도 `finalize`가 id로 걸러냅니다.

| kind | `event_id` |
|---|---|
| INTERVENTION | `iv-<t_ms>` |
| OUTCOME | `oc-<intervention_id>` (예: `oc-iv-73000`) |
| EPISODE | `ep-<issue_type>-<장>-<start_ms>` |
| SLIDE | `sl-<장>-<start_ms>` |
| STRATEGY | `st-<issue_type>-<장>-<t_ms>` |
| SUPPRESSED | `su-<issue_type>-<t_ms>` |

- `<장>`은 장 번호이고, 장이 없으면 `none`입니다 (장이 없는 `EPISODE`는 `ep-<issue_type>-none-<start_ms>`).
- 한 응답 안에서 같은 모양의 id가 또 나오면(요청이 오래 빠져 같은 문제의 효과 둘을 한 번에 쟀을 때 등) 뒤의 것에 `-2`, `-3`…을 붙입니다. 같은 요청이면 순서가 같아 id도 같습니다. 시각이 다른 응답끼리는 겹치지 않습니다.

```json
// src/coach/examples/finalize/request.json 의 events (발췌)
{"event_id": "oc-iv-73000", "t_ms": 83000, "kind": "OUTCOME", "intervention_id": "iv-73000", "outcome": "INEFFECTIVE", "metric": "cpm_short", "before": 300.0, "after": 300.0}
{"event_id": "st-AHEAD_OF_SCHEDULE-3-83000", "t_ms": 83000, "kind": "STRATEGY", "issue_type": "AHEAD_OF_SCHEDULE", "area": "TIME", "slide_number": 3, "change": "GAVE_UP", "from_instruction": "SLOW_DOWN", "from_variant": "time_ahead", "to_instruction": null, "to_variant": null, "intervention_id": "iv-73000"}
{"event_id": "su-AHEAD_OF_SCHEDULE-60000", "t_ms": 60000, "kind": "SUPPRESSED", "issue_type": "AHEAD_OF_SCHEDULE", "area": "TIME", "slide_number": 3, "instruction": "SLOW_DOWN", "status": "WAITING", "priority": 99, "reasons": ["NOT_PERSISTENT"]}
```

### 1-4. 문제 코드와 이유 코드

문제(`issue_type` → `area`)

| area | issue_type |
|---|---|
| GAZE | `GAZE_ON_SCRIPT` `GAZE_AWAY` `GAZE_LOW_EYE_CONTACT` · (말하지 않고 구간에만 남김) `GAZE_ON_SCREEN` `GAZE_UNMEASURABLE` |
| SPEED | `PACE_FAST` · (구간에만) `PACE_SLOW` |
| VOLUME / PAUSE | `VOLUME_LOW` / `LONG_SILENCE` |
| FILLER | `FILLER_FREQUENT` |
| TIME | `BEHIND_SCHEDULE` `AHEAD_OF_SCHEDULE` `SLIDE_OVER` `FINAL_MINUTE` `TIME_OVER` |

`IMPROVED_AFTER_FEEDBACK`은 효과가 있을 때의 격려(`CONTINUE`)이고, 교정했던 영역을 물려받습니다.

`reason_codes`

| 분류 | 코드 |
|---|---|
| 개입한 이유 (`INTERVENE`) | `PERSISTENT` `WORSENING` `MISSION_RELEVANT` `MISSION_AT_RISK` `RECURRING` `PLAN_FOCUS` `TIME_CRITICAL` `SPEED_LIMIT_EXCEEDED` `ESCALATED` `AFTER_EFFECTIVE_FEEDBACK` `PAUSE_TIMEOUT` |
| 기다린 이유 (`WAIT`) | `NO_CANDIDATE` `NOT_PERSISTENT` `MIN_GAP` `COOLDOWN` `WAITING_FOR_PAUSE`(말하는 도중이라 문장 끝을 기다림) `STALE_TICK` |
| 버린 이유 (`IGNORE`) | `EXAM_MODE` `SENSOR_UNUSABLE` `LOW_CONFIDENCE` `SCRIPT_ALLOWED` `TIME_PRESSURE` `STRATEGY_EXHAUSTED` `ALREADY_DELIVERED` `PLAN_RELAXED` `BUDGET_EXHAUSTED` `LOW_PRIORITY` |
| 운영 | `INTERNAL_ERROR` `STATE_RESET` |

값은 v1 안에서 **추가**만 합니다. 이름을 바꾸거나 빼면 소비자가 깨지므로 스키마 버전을 올립니다.

### 1-5. 실패와 예외 상황

| 상황 | 코치의 동작 | BE가 할 일 |
|---|---|---|
| 요청 형식 오류 (필수 필드 누락 · 타입 불일치) | 예외(`ValidationError`)를 그대로 올린다 → API가 **422** | 그 1초를 건너뛴다. `coach_state`는 바꾸지 않는다 |
| 코치 안의 예외 | `decide_safe`가 잡아 `WAIT` + `INTERNAL_ERROR`. 이벤트 · `indicators`는 비고, 그 시간은 **잴 수 없던 시간**으로 넘긴 `coach_state`를 돌려준다 | 응답의 `coach_state`를 평소처럼 붙인다. 같은 요청을 다시 보내도 `STALE_TICK`이라 같은 예외를 되풀이하지 않는다 |
| `coach_state`의 버전이 다르거나 깨졌다 | 버리고 새 상태로 시작하고 `STATE_RESET`을 남긴다 (쿨다운이 한 번 풀리는 정도의 손해). 이때 Take 앞부분의 합계가 없으므로 종료 때 `REPLAY_REQUIRED`가 된다 | 응답의 `coach_state`를 이어 붙인다 |
| `t_ms`가 이미 처리한 시각 이하 (재시도 · 순서 뒤바뀜) | `WAIT` + `STALE_TICK`. 아무것도 바꾸지 않고 이벤트도 없다 | 무시해도 된다 |
| 응답을 못 받음 | — | 이전 `coach_state`로 다시 보내면 같은 판단 · 같은 이벤트가 나오므로 그 응답 하나만 쓴다 |
| 판정 모듈 하나가 예외 | 그 영역만 잴 수 없음으로 두고 나머지는 계속한다 (2-3) | — |

---

## 2. 판정 모듈 묶음 (`Judges`)

코치는 판정 모듈을 **import하지 않습니다**. 부르는 쪽(배포에서는 API 라우터, research에서는 `coach_lab`)이 모듈을 묶어 넘깁니다. 모듈과 코치 사이에는 JSON 모양(dict)만 오갑니다. 코치는 받은 결과를 자기 `JudgmentResult`로 검증하고 모르는 필드는 무시합니다.

```python
Judges(gaze=…, pace=…, volume=…, filler=…, baseline=…)
```

| 이름 | 모양 | 설명 |
|---|---|---|
| `gaze` `pace` `volume` `filler` | 모듈 객체 | 아래 세 함수를 가진다 (파이썬 모듈이어도 된다) |
| `baseline` | `(list[float]) -> float \| None` | 기준 음량 잡기: 말한 1초의 `level_db` 표본 → 값 (표본이 모자라면 `None`) |

모듈 하나가 가진 세 함수

| 함수 | 설명 |
|---|---|
| `judge(inputs: dict, t_ms: int) -> list[JudgmentResult]` | 1초 판정. 모델이든 dict든 받는다. `volume`은 `VOLUME`과 `PAUSE` 두 개를 낸다 |
| `summarize(tally: dict) -> dict` | 영역 합계(`{영역: {이름: 합}}`) → 지표 `{영역: {…, measured_ratio}}` |
| `criteria() -> dict` | `issue_type` → `{metric, direction, threshold, bad, onset_lag_ms, offset_lag_ms}` |

`timing`은 `Judges`에 없습니다. 계획이 필요해서 코치 안(`src/coach/timing.py`)에 있고, 같은 모양의 결과를 냅니다.

### 2-1. 호출 순서와 입력

한 요청에서 **filler → pace → gaze → volume → timing** 순서로 부릅니다.

| 모듈 | `inputs` |
|---|---|
| `filler` | `t_ms`, `stt_status`, `stt_ok_since_ms`(STT가 끊긴 적 없으면 0), `utterance_ends`, `words`, `since_ms`, `words_since_ms` |
| `pace` | filler와 같은 값 + `fillers[{start_ms, end_ms, is_filler}]` — filler 결과의 단어 중 군더더기(`true`)와 보류(`null`)만 |
| `gaze` | `t_ms`, `since_ms`, `records`(시선 1초 기록), `voiced[{t_ms, voiced_ms}]`(음량 기록에서 뽑은 말한 시간) |
| `volume` | `t_ms`, `since_ms`, `records`(음량 1초 기록), `base_level_db` |
| `timing` | `t_ms`, `since_ms`, `plan`, `slide{number, started_ms}`, `slide_chars`, `slide_dwell_ms`, `slide_stt_ok_ms`, `pace{cpm, measurable, fast_threshold}` |

- `since_ms`는 그 모듈이 이미 센 곳까지입니다(이전 결과의 `counted_until_ms`). `words_since_ms`는 단어를 이미 센 곳까지입니다(`words_counted_until_ms`).
- pace를 timing보다 먼저 부르는 이유: 이번 라운드에 pace가 센 글자 수까지 더해 장 진행도를 한 박자 늦지 않게 하려는 것입니다.
- `timing.pace.measurable`은 pace가 잴 수 있고, STT를 믿을 수 있고(`stt_status == "ok"`이고 오디오가 살아 있음), filler · pace가 실패하지 않았을 때만 `true`입니다.

### 2-2. 결과 모양 (`JudgmentResult`)

`evaluator`(`gaze` `pace` `volume` `filler` `timing`), `area`, `t_ms`, `counted_until_ms`, `words_counted_until_ms`, `criteria_version`, `measurable`, `state`, `metrics`, `tally[{t_ms, values}]`, `issues[{issue_type, area, severity, confidence, persistence_sec, threshold, bad, evidence, actionable}]`, `words`(filler만 — 단어별 `is_filler` 판정).

- `tally`는 구간마다 남긴 합계 조각입니다. 코치가 Take 합계 · 장 번호별 합계에 더하고, `summarize`에 넣어 Take 결과의 지표를 만듭니다.
- 결과의 `evaluator`와 `area` 목록이 모듈이 내야 할 것과 다르면(예: `volume`이 `PAUSE`를 빠뜨림) 그 모듈은 실패로 봅니다.

```json
// src/coach/examples/evaluate/judge_results.json 의 gaze.judge (발췌)
{"evaluator": "gaze", "area": "GAZE", "t_ms": 92000, "counted_until_ms": 92000, "criteria_version": "gaze-0.2+b6f8b464a3ee",
 "measurable": true, "state": "SCRIPT", "metrics": {"script_ratio": 0.8, "measured_ratio": 1.0, "script_run_ms": 1000.0},
 "tally": [{"t_ms": 91000, "values": {"script_ms": 1000.0, "total_ms": 1000.0, "measured_ms": 1000.0, "reading_ms": 1000.0}}],
 "issues": [{"issue_type": "GAZE_ON_SCRIPT", "area": "GAZE", "severity": 0.7, "confidence": 1.0, "persistence_sec": 1.0,
             "threshold": 5000.0, "bad": 15000.0, "evidence": {"script_ratio": 0.8, "window_ms": 10000}, "actionable": true}]}
```

그 파일의 `criteria`는 `{"GAZE_ON_SCRIPT": {"metric": "script_run_ms", "direction": "HIGHER_IS_WORSE", "threshold": 5000.0, "bad": 15000.0, "onset_lag_ms": 7000, "offset_lag_ms": 3000}}` 모양이고, `summarize`는 합계를 받아 `{"GAZE": {"audience_ratio": 0.6562, "script_ratio": 0.3438, "measured_ratio": 1.0, …}}`를 냅니다.

### 2-3. 모듈이 예외를 냈을 때

모듈이 예외를 내거나, 결과가 계약에 맞지 않거나(`JudgmentResult` 검증 실패, 영역 불일치), filler의 단어 판정이 깨졌으면(`is_filler`가 bool · null이 아님) 그 모듈만 **잴 수 없음**으로 처리하고 나머지는 계속합니다.

- 그 영역은 `measurable: false`, `state`는 `gaze`이면 `UNMEASURABLE`, 그 밖에는 `UNKNOWN`입니다.
- 커서를 이번 창 끝까지 넘깁니다 (순수 코드의 예외는 같은 입력에서 되풀이되므로, 건너뛰어야 빠져나온다).
- 그 시간은 영역 합계의 `total_ms`(timing은 `elapsed_ms`)에만 셉니다.
- 그 모듈의 `criteria_version`은 기록하지 않습니다 (모듈이 낸 버전이 아니다).
- filler나 pace가 실패하면 `timing`에는 속도를 모르는 것으로 넘기고 STT 신뢰 합계에도 넣지 않습니다.
- `summarize` · `criteria`가 예외를 내면 해당 지표(미션 가중 · Take 결과의 그 영역 값)나 기준만 빠집니다.

### 2-4. 기준 음량

- `calibration.base_level_db`가 있으면 그 값을 쓰고 출처는 `CALIBRATION`입니다.
- 없으면 Take 첫 발화로 잡습니다. 새 음량 기록 중 같은 `t_ms`는 먼저 온 것만, `level_db`가 있는 것(말한 1초)만 표본에 모으고, 라운드마다 `baseline(표본)`을 불러 값이 나오면 고정합니다(출처 `TAKE`, 표본 비움). `None`이나 예외면 다음 요청에서 다시 시도합니다.
- 출처는 Take 결과의 `areas.VOLUME.take.base_level_source`에 남습니다.

### 2-5. 코치 안의 `timing` 판정

계획 대비 진행을 판정합니다. 기준값은 `config.TimingConfig`에만 있습니다. 입력 · 출력은 `src/coach/examples/timing/`에 있습니다.

| 파일 | 내용 |
|---|---|
| `judge_input.json` · `judge_output.json` | `timing.judge`의 입력과 결과 (상태 `BEHIND`, 문제 `BEHIND_SCHEDULE`) |
| `criteria.json` | 다섯 문제의 판정 기준 |
| `summarize_input.json` · `summarize_output.json` | 합계 `{"TIME": {"elapsed_ms", "planned_ms"}}` → `{"TIME": {"duration_ms", "measured_ratio"}}` |

| 문제 | 지표 | 방향 |
|---|---|---|
| `TIME_OVER` | `elapsed_ratio` | 클수록 나쁨 |
| `FINAL_MINUTE` | `remaining_ms` | 작을수록 나쁨 |
| `BEHIND_SCHEDULE` | `required_ratio` (남은 내용 시간 ÷ 남은 시간) | 클수록 나쁨 |
| `SLIDE_OVER` | `slide_time_ratio` | 클수록 나쁨 |
| `AHEAD_OF_SCHEDULE` | `projected_end_ratio` | 작을수록 나쁨 |

장 진행도는 말한 글자 수 ÷ `script_chars`이고, STT를 믿을 수 없으면 머문 시간 ÷ 목표 시간으로 대신합니다. 이 모듈은 상태를 갖지 않고, 호출하는 쪽(코치)이 장별 누적을 들고 매번 넘깁니다.

---

## 3. `POST /coach/finalize`

Take가 끝났을 때 한 번 부릅니다. 마지막 창을 판정하고, 열린 것을 닫고, **Take 결과**(사실: 지표 · 문제 구간 · 개입 · 판정 기준)를 만듭니다. 결론(좋았다 · 나빴다)은 담지 않습니다.

### 3-1. 요청

`/coach/evaluate`와 같은 Take 상수에 마지막 창 · 이벤트 · `coach_state`를 더한 것입니다.

| 필드 | 설명 |
|---|---|
| `take_id` · `t_ms` | `t_ms`는 Take가 끝난 시각 |
| `mode` · `plan` · `missions` · `recurring_issues` · `coaching_plan` · `script_used` · `calibration` | evaluate와 같은 값 |
| `script_mode` | 대본 보기 `HIGHLIGHT` · `KEYWORD` · `OFF`. 판정은 바꾸지 않고 Take 결과에 남긴다 |
| `coach_state` | 마지막 응답의 `coach_state`. 잃었으면 `null` |
| `events` | 이 Take에서 BE가 쌓은 코치 이벤트 전부 (받은 순서대로) |
| `inputs` | 마지막 창. evaluate의 `inputs`와 같은 모양 |
| `replay` | (선택) Take 전체 원자료. 있으면 처음부터 다시 판정한다 (3-4) |

`replay`

| 필드 | 설명 |
|---|---|
| `gaze_records[]` · `voice_records[]` | Take 전체의 1초 기록 |
| `stt_status_changes[]` | `{t_ms, status}` STT 상태가 바뀐 시각. 이력이 없으면 Take 내내 `connecting`(믿지 않음) |
| `words[]` | `{word, start_ms, end_ms, final_at_ms}` — 확정된 시각(`final_at_ms`)이 지난 요청에만 실려 있었던 것처럼 재현한다 |
| `utterance_ends[]` | 문장 끝 시각 |
| `slides[]` | `{number, started_ms}` 장 전환 |

### 3-2. 처리

1. **마지막 창**: evaluate와 같은 판정 · 누적을 한 번 더 하되 말은 걸지 않습니다. **Take 끝을 문장 끝 신호로 넣어** 뒤 간격을 기다리던 단어까지 판정합니다. 이미 그 시각을 처리했으면 건너뜁니다. 마지막 창이 예외를 내도 이미 센 합계로 Take 결과를 냅니다.
2. **닫기**: 재지 못한 효과는 `OUTCOME(NOT_MEASURED)`, 열린 문제 구간은 `EPISODE`, 남은 장 방문은 `SLIDE`로 냅니다.
3. **덮임 확인**: 센 구간이 `[0, t_ms)`를 덮지 못했거나 `coach_state`가 `null` · 새로 시작한 상태면 `ReplayRequired` → API가 **409 `REPLAY_REQUIRED`** (빠진 구간 `[시작, 끝)` 목록을 메시지로).
4. **지표**: Take 합계와 장 번호별 합계를 영역 모듈의 `summarize`에 넣어 `areas`를 만들고, 모듈의 `criteria()`를 `criteria`에 남깁니다.
5. **목록**: 받은 이벤트 + 이번 이벤트(`event_id` 중복은 처음 것 하나만)에서 문제 구간 · 개입 · 포기를 만듭니다.

### 3-3. 응답

```
{ take_result, events, meta }
```

`events`는 **이번에 닫은** 이벤트(효과 · 문제 구간 · 장)만입니다. BE는 이것도 평소처럼 쌓습니다. `meta`는 evaluate와 같은 모양입니다.

`take_result`

| 필드 | 설명 |
|---|---|
| `take_id` · `duration_ms` · `script_mode` | |
| `replayed` | 원자료로 다시 판정해 만들었는가 |
| `criteria_changed` | Take 도중 어느 모듈의 기준 버전이 바뀌었는가 (지표를 해석할 때 주의) |
| `areas` | 영역(`GAZE` `SPEED` `VOLUME` `PAUSE` `FILLER` `TIME`) → 값 |
| `problem_segments[]` | 문제 구간 (3-5) |
| `interventions[]` | 말을 건 기록과 효과 |
| `gave_up[]` | 효과가 없어 더 말하지 않기로 한 `{issue_type, slide_number}` (같은 문제 · 장은 한 번) |
| `criteria` | 영역 → `{criteria_version, issues{issue_type → 기준}}`. `volume` 모듈의 문제는 원인 영역(`VOLUME` · `PAUSE`)으로 나눠 담는다 |

`areas.<영역>`

| 필드 | 설명 |
|---|---|
| `measured_ratio` | Take에서 잰 시간 비율 |
| `take` | 모듈의 `summarize` 결과(`measured_ratio` 제외). `VOLUME`은 `base_level_source`도 든다 |
| `slides[]` | 장 번호별 `{slide_number, start_ms, end_ms, target_ms, measured_ratio, metrics}`. 다시 온 장은 합친다(시작은 첫 방문, 끝은 마지막 방문). `TIME`의 장 `metrics`는 `slide_duration_ms` |
| `unmeasured_reason` | 측정 비율이 `take_result.min_measured_ratio`(설정) 미만이면 `LOW_MEASURED_RATIO`이고 `take` · `slides`는 비운다. 장도 같은 기준으로 그 장의 `metrics`만 `null` |

`interventions[]`: `intervention_id`, `t_ms`, `area`, `issue_type`, `instruction`, `message`, `outcome`(없으면 `null`), `metric`, `before`, `after`.

```json
// src/coach/examples/finalize/response.json (발췌)
{
  "take_result": {
    "take_id": "take-123", "duration_ms": 92000, "script_mode": "HIGHLIGHT", "replayed": false, "criteria_changed": false,
    "areas": {
      "GAZE": {"measured_ratio": 0.9674,
               "take": {"audience_ratio": 0.7753, "script_ratio": 0.2247, "reading_runs": 1.0, "...": "..."},
               "slides": [{"slide_number": 3, "start_ms": 59390, "end_ms": 92000, "target_ms": 90000, "measured_ratio": 1.0,
                           "metrics": {"audience_ratio": 0.6562, "script_ratio": 0.3438, "...": "..."}}],
               "unmeasured_reason": null},
      "TIME": {"measured_ratio": 1.0, "take": {"duration_ms": 92000.0}, "slides": ["..."]},
      "VOLUME": {"take": {"level_db": -29.5, "base_level_source": "TAKE", "...": "..."}, "...": "..."}
    },
    "problem_segments": [
      {"area": "GAZE", "issue_type": "GAZE_UNMEASURABLE", "slide_number": 1, "start_ms": 0, "end_ms": 2000, "mean_severity": 0.7205, "reliable": false, "coached": false},
      {"area": "GAZE", "issue_type": "GAZE_ON_SCRIPT", "slide_number": 3, "start_ms": 81000, "end_ms": 90000, "mean_severity": 0.57, "reliable": true, "coached": true}
    ],
    "interventions": [{"intervention_id": "iv-73000", "t_ms": 73000, "area": "TIME", "issue_type": "AHEAD_OF_SCHEDULE", "instruction": "SLOW_DOWN",
                       "message": "시간 여유가 있어요. 천천히 말해도 돼요", "outcome": "INEFFECTIVE", "metric": "cpm_short", "before": 300.0, "after": 300.0}],
    "gave_up": [{"issue_type": "AHEAD_OF_SCHEDULE", "slide_number": 3}],
    "criteria": {"GAZE": {"criteria_version": "gaze-0.2+b6f8b464a3ee", "issues": {"GAZE_ON_SCRIPT": {"metric": "script_run_ms", "direction": "HIGHER_IS_WORSE", "threshold": 5000.0, "bad": 15000.0, "onset_lag_ms": 7000, "offset_lag_ms": 3000}}}, "...": "..."}
  },
  "events": [{"event_id": "oc-iv-92000", "kind": "OUTCOME", "outcome": "NOT_MEASURED", "...": "..."}, {"event_id": "ep-GAZE_ON_SCRIPT-3-88000", "kind": "EPISODE", "...": "..."}],
  "meta": {"...": "..."}
}
```

### 3-4. 복구 — `ReplayRequired`와 replay

응답이 30초보다 길게 빠졌거나 `coach_state`를 잃으면 센 합계로는 Take를 덮지 못합니다. 이때 코치는 **409 `REPLAY_REQUIRED`**를 돌려줍니다. BE는 Take 전체 원자료를 `replay`에 실어 같은 요청으로 다시 부릅니다 (`coach_state`는 `null`이어도 된다). 처음부터 `replay`를 실어도 됩니다.

replay 처리

- BE가 1초마다 보냈을 evaluate 요청을 `t_ms = 0, 1000, … , (끝 직전)`에 대해 다시 만들어 `decide_safe`로 처음부터 판정합니다. 창 길이는 evaluate와 같고(시선 · 음량 30초, 단어 · 문장 끝 60초), 단어는 `final_at_ms`가 지난 것만 넣습니다. **말은 걸지 않도록 `EXAM` 모드로** 돌립니다.
- 끝에 평소처럼 마지막 창을 판정하고 닫습니다.
- 지표 · 문제 구간 · 판정 기준은 **다시 판정한 결과**에서, `interventions` · 효과 · `gave_up`은 **받은 `events`(실제로 한 코칭)**에서 가져옵니다.
- 문제 구간의 `coached`는 같은 문제(장 단위 문제면 같은 장)의 실제 `INTERVENTION`이 구간 안에 있었는가로 정합니다.
- 응답의 `events`에는 다시 판정한 이벤트를 내지 않습니다. 실제 개입 중 효과 기록이 없는 것만 `OUTCOME(NOT_MEASURED)`로 냅니다 (`CONTINUE` 격려와 효과를 재지 않는 문제는 제외).
- `take_result.replayed = true`.

예시: `src/coach/examples/finalize/request_replay.json`(`coach_state: null`, `replay`에 기록 92개 · 단어 116개 · `stt_status_changes` · `slides`)와 `response_replay.json`.

```json
// request_replay.json 의 replay (발췌)
{"words": [{"word": "가나다", "start_ms": 0, "end_ms": 600, "final_at_ms": 1800}],
 "stt_status_changes": [{"t_ms": 0, "status": "ok"}],
 "slides": [{"number": 1, "started_ms": 0}, {"number": 2, "started_ms": 29400}, {"number": 3, "started_ms": 59390}]}
```

### 3-5. 문제 구간 규칙 (`problem_segments`)

`EPISODE` 이벤트에서 만듭니다. 순서대로:

| 규칙 | 설명 |
|---|---|
| 본 시간 | `EPISODE`의 끝은 마지막으로 본 판정 시각이라, 그 1초를 덮도록 1초(`default_tick_ms`)를 더해 쓴다 |
| 신뢰도 | 믿을 수 있던 시간 비율이 `min_reliability` 미만이면 `reliable: false`. 리뷰는 이 구간을 문제로 말하지 않는다 |
| 합치기 | 같은 문제 · 같은 장 · 같은 신뢰도이고 간격이 `merge_gap_ms` 이하면 한 구간으로. 평균 심각도는 각 조각이 지켜본 시간(믿을 수 있던 + 없던)으로 가중한다 |
| 짧은 잡음 | 합친 구간이 `min_segment_ms`보다 짧고 말을 건 적이 없으면 뺀다 (보정 전 길이로 잰다) |
| 판정 지연 보정 | 시작을 그 문제의 `onset_lag_ms`만큼, 끝을 `offset_lag_ms`만큼 앞으로 당긴다 (기준이 없으면 그대로) |
| 자르기 | Take와 (장 단위 문제면) 그 장이 보이던 시간 안으로 자른다. 자른 뒤 길이가 0 이하면 뺀다 |
| `coached` | 그 구간에 그 문제로 말을 걸었는가 |
| replay일 때 | 다시 판정할 때는 개입이 없어 짧은 `EPISODE`(`min_episode_ms` 미만)를 거르지 않고 냈으므로, 실제 개입과 겹치지 않는 것을 여기서 뺀다 |

결과는 시작 시각 순입니다. 설정값(`merge_gap_ms` · `min_segment_ms` · `min_reliability` · `min_measured_ratio`)은 `src/coach/config.py`의 `TakeResultConfig`에 있고, 실시간 판단에는 쓰이지 않습니다.

---

## 4. `POST /coach/plan`

Take 시작 전에 한 번 부릅니다. LLM이 대본 · 미션 · 직전 리뷰를 읽고 이번 Take의 **코칭 계획**을 만듭니다. 1초 판단은 계속 규칙이 하고, 계획은 우선순위 가중치(`focus`)와 참을 이유(`relax` · 개입 상한)로만 반영됩니다. 구현은 `src/coach/planner.py`의 `plan_coaching`입니다.

> 이 호출의 입력 · 출력은 Take 결과와 리뷰 출력에 맞춰 후속 작업(#162)에서 바꿉니다. 아래는 지금 구현된 모양입니다.

요청 (`PlanRequest`)

| 필드 | 설명 |
|---|---|
| `schema_version` · `take_id` · `mode` | `EXAM`이면 LLM을 부르지 않는다 |
| `plan` | 발표 계획 (evaluate와 같은 모양) |
| `scripts[]` | `{slide_number, script}` 장별 대본 원문 (장마다 `max_script_chars`까지 넘긴다) |
| `missions[]` | 직전 리뷰의 미션 |
| `memory` | `{recurring_issues[{area, slide_number}]}` |
| `previous_review` | 직전 Take의 리뷰 요약 그대로(dict). 없으면 `null` |

응답 (`PlanResponse`)

| 필드 | 설명 |
|---|---|
| `schema_version` · `policy_version` · `planner_hash` | `planner_hash`는 프롬프트 · 출력 스키마 · 모델로 만든 해시 |
| `take_id` | |
| `plan` | `CoachingPlan` `{source: DEFAULT·LLM, focus[], relax[], max_interventions}` — 매 evaluate 요청의 `coaching_plan`에 그대로 싣는다 |
| `fallback_reason` | 기본 계획(`focus`·`relax` 비어 있음)으로 돌아간 이유: `EXAM_MODE` · `NO_LLM` · `LLM_ERROR`. LLM 계획이면 `null` |
| `dropped[]` | 코치가 검증에서 뺀 항목과 이유 |

- LLM 클라이언트는 코어가 만들지 않고 인자(`llm=`, 출력 스키마는 `PlanDraft`)와 캐시(`cache=`)로 받습니다. LLM 실패 · 없음은 예외 없이 기본 계획 + `fallback_reason`입니다.
- LLM 초안은 그대로 쓰지 않고 `config.planner` 범위(`focus`·`relax` 개수, `relax` 가능 영역, 개입 상한 하한, 가중치 범위)로 검증하고 자릅니다.
- 요청 형식 오류는 `ValidationError`를 그대로 올려 API가 **422**로 바꿉니다.
