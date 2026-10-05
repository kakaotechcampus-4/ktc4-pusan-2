# v1 — 실시간 코치 (규칙 실행 + 되돌아보기)

발표 중 **1초마다 "지금 발표자에게 말을 걸까, 건다면 무엇 하나를 말할까"** 를 정하는 첫 번째 모델입니다.
판단은 규칙으로 하고, 개입한 뒤 **실제로 행동이 바뀌었는지 보고 다음 행동을 바꿉니다**(되돌아보기).
LLM 은 쓰지 않습니다.

Take 가 끝나면 **리뷰 에이전트가 쓸 근거**(장별 표 · 문제 순위 · 이전 미션 판정 · 이전 Take 비교 ·
다음 미션 후보)를 코드로 계산해 넘깁니다. 그 품질은 정답이 있는 가상 발표로 채점해 규칙을 골랐습니다 (§6, §9).

```
FE  시선 · 음량 · 슬라이드 요약 ─┐
BE  최근 15초 STT 단어 · 모드 · 미션 ─┼─▶ decide() ─▶ WAIT / IGNORE / INTERVENE
지난 응답의 coach_state ──────────┘        + 이벤트 + 새 coach_state + 상태 표시

Take 종료:  finalize() ─▶ 남은 구간 닫기 ─▶ build_review_evidence(+ plan · missions · memory) ─▶ 리뷰 에이전트
```

이 문서는 **v1 모델의 전체 기술 레퍼런스**입니다 — 입출력 계약, 판단 규칙, 리뷰 에이전트 연동, 배포, 한계.
`local` 과 `deploy` 가 공유합니다.

> **코드를 돌려보려면 [local/README.md](local/README.md)** 로 가세요.
> 설치, 테스트, 시나리오 재생 같은 작업용 내용은 거기에 있습니다.

---

## 목차

1. [v1의 범위](#1-v1의-범위)
2. [전체 흐름](#2-전체-흐름)
3. [입력 — CoachRequest](#3-입력--coachrequest)
4. [출력 — CoachResponse](#4-출력--coachresponse)
5. [판단 로직](#5-판단-로직)
6. [리뷰 에이전트로 넘기는 것](#6-리뷰-에이전트로-넘기는-것)
7. [coach_state](#7-coach_state)
8. [배포 — ai/releases/ 로 승격](#8-배포--aireleases-로-승격)
9. [현재 상태와 실험](#9-현재-상태와-실험)
10. [설계 결정](#10-설계-결정)
11. [알려진 한계](#11-알려진-한계)
12. [다음 버전](#12-다음-버전)
13. [팀과 정해야 할 것](#13-팀과-정해야-할-것)

---

## 1. v1의 범위

**하는 것**

- 1초마다 측정값과 지난 기억(`coach_state`)을 받아 **행동 하나, 또는 말하지 않음**을 정합니다
- 7개 영역을 봅니다: 시선 · 말 속도 · 음량 · 침묵 · 군더더기 · 시간(장별 계획 대비) · 핵심 키워드(기본 꺼짐)
- **한 번에 하나만** 말합니다. 메시지 사이 15초, 같은 지시는 60초 쿨다운, 말하는 도중이면 문장이 끝날 때까지 최대 3초 기다립니다
- 개입하고 몇 초 뒤 **효과를 재서** 셋 중 하나를 고릅니다 — 유지 격려 / 다른 방법 / 그 범위에서 그만두기
- 실전(EXAM) 모드에서는 말하지 않고 **기록만** 남깁니다
- 판단 기록을 **이벤트**로 내보냅니다. BE 가 쌓아 두면 Take 종료 뒤 리뷰 에이전트용 근거로 묶습니다
- 읽지 않아도 되는 **상태 표시**(`indicators`)를 함께 줍니다 — 시간 진행 · 속도 · 시선 · 음량

**하지 않는 것**

- 측정 — 시선 추론(FE), dB 측정(FE), 음성 인식(BE · Deepgram)은 다른 모듈이 합니다. 코치는 그 결과를 읽기만 합니다
- 저장 — 아무것도 저장하지 않습니다. 기억은 응답에 담겨 나가고 BE 가 다음 요청에 돌려보냅니다 (§7)
- 문장 생성 — 화면 문구는 템플릿입니다. LLM 이 문장을 만들지 않습니다
- LLM 판단 — v1.1(발표 전 코칭 계획), v2(애매할 때 후보 선택)에서 붙입니다 (§12)
- 대본 내용 전달 판정 — 실시간으로는 필수 키워드 언급만 봅니다(기본 꺼짐). 문장 단위 전달은 리뷰(script-coverage-evaluation)가 합니다
- 전송 계층 — local 은 함수 호출뿐입니다. 서비스는 `ai/releases/` 로 승격해 HTTP 로 엽니다 (§8)

---

## 2. 전체 흐름

```
FE ── 시선·음량·슬라이드 요약 (0.5~1초) ──▶ BE  (Take 마다 TakeStream 하나)
                                            │  1초마다 POST /v1/coach/evaluate
                                            │    요청 = 지금 상황 + coach_state
                                            ▼
                                          AI  decide()   ← 규칙만, 저장하지 않음
                                            │    응답 = action + feedback + events + 새 coach_state
                                            ▼
FE ◀── WS "coach" (INTERVENE 일 때만) ──── BE  feedback 전달 · events 저장 · coach_state 보관

Take 종료:  BE ── POST /v1/coach/finalize ──▶ AI  남은 구간 · 재지 못한 효과를 이벤트로
            BE ── POST /v1/takes/analyze (쌓인 events 포함) ──▶ AI  build_review_evidence() → 리뷰 에이전트
```

BE 가 할 일은 셋뿐입니다.

1. **1초마다 부른다.** 타임아웃은 짧게(약 300ms). 실패하면 그 1초는 건너뛰고 `coach_state` 는 그대로 둡니다
2. **응답을 나눠 담는다.** `feedback` 은 FE 로, `events` 는 그대로 쌓고(추가만), `coach_state` 는 다음 요청에 붙입니다
3. **끝나면 닫는다.** `finalize` 의 이벤트까지 쌓은 뒤, 종료 분석 요청에 그 이벤트를 넘깁니다

코치 쪽은 시계 · 파일 · 네트워크를 쓰지 않습니다. 시간은 요청의 `t_ms` 뿐이라 **같은 요청에는 언제나 같은 응답**이 나옵니다.

---

## 3. 입력 — CoachRequest

전체 필드는 [`ai/src/coach/schemas.py`](local/ai/src/coach/schemas.py) 가 단일 진실 원천입니다.
입력 모델은 **모르는 필드를 무시**합니다 — BE 가 필드를 먼저 추가해도 깨지지 않습니다.

### 3-1. 설계 그림의 7가지 입력이 어디서 오나

| 설계 그림의 입력 | 요청 필드 | 출처 |
|---|---|---|
| 현재 상태 | `current` | FE 요약 + BE STT |
| 최근 30~60초 상태 | `coach_state` 안 (최근 60초 기록) | 코치가 직접 누적 — BE 추가 작업 없음 |
| 문제 후보 | (요청에 없음) | 코치 안에서 평가기 → 후보 생성 (§5) |
| 현재 슬라이드 맥락 | `plan` + `current.timing` | 대본 분석 (장별 목표 시간 · 글자 수 · 필수 키워드) |
| 이번 Mission | `missions` | 직전 리뷰의 `next_missions` 그대로 |
| 이전 Take 기억 | `memory` | 직전 리뷰가 '아직 남은 문제'로 꼽은 영역 · 슬라이드 |
| 최근 피드백 이력 | `coach_state` 안 | 코치가 직접 누적 (개입 · 효과 · 전략 단계) |

### 3-2. 필드

| 필드 | 뜻 |
|---|---|
| `schema_version` | 요청 스키마 버전. 기본 `"1.0"` — 응답 · 이벤트 · 리뷰 근거에도 같은 값이 붙습니다 |
| `take_id`, `t_ms` | Take ID, Take 시작 기준 경과 ms. **모든 시각이 이 시간축**입니다 |
| `mode` | `PRACTICE` \| `EXAM` |
| `plan.target_ms` · `min_ms` · `max_ms` | 목표 시간과 허용 범위. `max_ms` 를 넘으면 시간 초과, 예상 종료가 `min_ms` 보다 이르면 '너무 빨리 끝남' |
| `plan.slides[]` | `slide_number`, `target_ms`(장 목표), `script_chars`(공백 제외 대본 글자 수), `required_keywords` |
| `missions[]` | `mission_id`, `type`(7개 영역), `slide_number`, `priority`, `description`(사용자에게 보인 문장, 판정에는 안 씀), `target{metric, operator, value}` |
| `memory.recurring_issues[]` | `type`, `slide_number` |
| `current.timing` | `slide_number`, `slide_elapsed_ms` |
| `current.gaze` | `window_ms`(기본 10초), `ratios`(라벨 → 비율, UNCERTAIN 포함 합 1), `current_label`, `current_label_ms` |
| `current.voice` | `relative_db`(캘리브레이션 대비, 말하지 않으면 null), `silence_ms`, `audio_live` |
| `current.speech` | `stt_status`, `words[]`(최근 15초. `w`, `start_ms`, `end_ms`, `final`, `filler`), `utterance_end_ms` |
| `coach_state` | 지난 응답의 것 그대로. 첫 요청이면 `null` |

`coach_state` 안의 내용은 공개 계약이 아닙니다 — 그 안의 `CoachingPlan`(v1.1 코칭 계획 예고, §12)도 마찬가지로,
BE 는 읽거나 만들지 말고 받은 그대로 돌려주기만 합니다. 모양은 예고 없이 바뀔 수 있습니다 (§7).

`current` 안의 영역이 빠지면 **그 영역 판단만** 건너뜁니다. `speech` 가 없으면 STT 가 없는 것으로 보고
말 속도 판단을 끄며, 장 진행도는 시간으로 잽니다.

### 3-3. 예시

```json
{
  "schema_version": "1.0",
  "take_id": "take-123",
  "t_ms": 115000,
  "mode": "PRACTICE",
  "plan": {
    "target_ms": 180000, "min_ms": 170000, "max_ms": 190000,
    "slides": [
      {"slide_number": 1, "target_ms": 30000, "script_chars": 300},
      {"slide_number": 2, "target_ms": 60000, "script_chars": 600},
      {"slide_number": 3, "target_ms": 60000, "script_chars": 600, "required_keywords": ["로컬 처리"]},
      {"slide_number": 4, "target_ms": 30000, "script_chars": 300}
    ]
  },
  "missions": [
    {"mission_id": "mission-9", "type": "TIME", "slide_number": 3, "priority": 1,
     "target": {"metric": "slide_duration_ms", "operator": "LTE", "value": 60000}}
  ],
  "memory": {"recurring_issues": [{"type": "TIME", "slide_number": 3}]},
  "current": {
    "timing": {"slide_number": 3, "slide_elapsed_ms": 35000},
    "gaze":   {"window_ms": 10000, "ratios": {"CAMERA": 0.22, "BOTTOM": 0.75, "UNCERTAIN": 0.03},
               "current_label": "BOTTOM", "current_label_ms": 6000},
    "voice":  {"relative_db": -1.5, "silence_ms": 0, "audio_live": true},
    "speech": {"stt_status": "ok", "utterance_end_ms": 114600,
               "words": [{"w": "그래서", "start_ms": 100200, "end_ms": 100600, "final": true, "filler": false}]}
  },
  "coach_state": {"v": 1, "…": "지난 응답 그대로 — 여기서는 3번 장에서 150자(1/4)를 말한 상태"}
}
```

---

## 4. 출력 — CoachResponse

### 4-1. 필드

| 필드 | 뜻 |
|---|---|
| `action` | `WAIT` 지금은 말하지 않고 계속 봄 (문제가 없거나 타이밍을 기다리는 중) · `IGNORE` 문제는 있지만 지금 말할 가치가 낮아 이번엔 버림 · `INTERVENE` 지금 말함 |
| `candidate_id` | 이번 판단의 대상이 된 문제. `문제코드-시작시각` 이라 **문제가 이어지는 동안 같은 ID** 입니다 |
| `reason_codes` | 이 action 을 고른 이유 (§5-5, §5-6) |
| `feedback` | INTERVENE 일 때만. `type`(원인 영역) · `instruction`(하라는 행동) · `message` · `priority`(0~100) · `confidence`(0~1) · `evidence`(`start_ms`, `end_ms`, `slide_number` + 지표) |
| `candidates[]` | 모든 후보와 상태(`SELECTED` · `OUTRANKED` · `WAITING` · `IGNORED`), 점수, 이유 |
| `indicators` | 읽지 않아도 되는 상태 표시 (§4-4) |
| `events[]` | BE 가 그대로 쌓는 기록 (§6) |
| `coach_state` | 다음 요청에 그대로 붙인다 (§7) |
| `policy_version`, `config_hash` | 어떤 규칙 · 설정으로 판단했는지. 재현용 |

**`type` 과 `instruction` 이 따로인 이유** — `type` 은 원인 영역, `instruction` 은 하라는 행동이고 1:1 이 아닙니다.
같은 TIME 이라도 상황에 따라 `SPEED_UP` · `CONDENSE` · `WRAP_UP` 이 나오고, 같은 `SLOW_DOWN` 이라도 원인이
말이 빨라서(SPEED)일 수도, 시간이 남아서(TIME)일 수도 있습니다. `type` 은 리뷰의 `ReviewPoint.type` ·
`Mission.type` 과 같은 7개 enum 입니다 (`GAZE` `SPEED` `VOLUME` `PAUSE` `FILLER` `CONTENT` `TIME`).

### 4-2. 문제와 행동 — 이 표가 v1 규칙의 전부입니다

| type | 문제 | 조건 (잠정) | 지속 | 행동 사다리 (효과 없으면 다음 칸) | 다 써 보면 |
|---|---|---|---|---|---|
| GAZE | `GAZE_SCRIPT` | 보인 시간 중 대본 응시 ≥ 70%, 또는 대본 연속 응시 ≥ 5초이면서 창 비율 ≥ 60% | 3초 | `LOOK_AT_CAMERA` "대본보다 청중을 조금 더 바라보세요" → `LOOK_AT_CAMERA` "문장을 시작할 때만이라도 고개를 들어 청중을 보세요" | 그 장에서 그만 |
| SPEED | `PACE_FAST` | 15초 CPM > 350 (말한 시간 4초 · 단어 5개 이상일 때만 잼) | 5초 | `SLOW_DOWN` "조금 천천히 말해 보세요" → `SLOW_DOWN` "문장 끝에서 한 박자 쉬고 이어가 보세요" | Take 에서 그만 |
| VOLUME | `VOLUME_LOW` | 말하는 동안 최근 5초 평균(표본 3개 이상)이 기준 대비 −6dB 미만 | 5초 | `SPEAK_LOUDER` "목소리를 조금 더 크게 내 보세요" → `SPEAK_LOUDER` "뒷자리 청중에게 말한다고 생각하고 소리를 키워 보세요" | Take 에서 그만 |
| PAUSE | `LONG_SILENCE` | 침묵 > 5초 (오디오가 되살아난 뒤부터만 셈) | — | `RESUME` "다음 문장으로 이어가 보세요" | 그만두지 않음 |
| FILLER | `FILLER_FREQUENT` | 최근 60초 군더더기 ≥ 6회 | — | `REDUCE_FILLER` "'음' 대신 잠시 호흡하고 이어가세요" → `REDUCE_FILLER` "말을 고를 땐 소리 내지 말고 잠깐 멈춰 보세요" | Take 에서 그만 |
| CONTENT | `KEYWORD_MISSING` (기본 꺼짐) | 이 장을 80% 말했는데 필수 키워드를 아직 안 말함 | — | `MENTION_KEYWORD` "이 슬라이드의 핵심인 '로컬 처리'를 언급해 보세요" | 장마다 1회 |
| TIME | `BEHIND_SCHEDULE` | r ≥ 1.05. CPM × r ≤ 350 이면 `SPEED_UP` 부터, 넘으면 `CONDENSE` 부터 | — | `SPEED_UP` "조금만 빠르게 — 남은 2장, 1분 5초" → `CONDENSE` "핵심만 말하고 넘어가세요 — 남은 2장, 1분 5초" → `WRAP_UP` "결론으로 넘어가 마무리하세요" | 마지막 칸에 머묾 |
| TIME | `AHEAD_OF_SCHEDULE` | 지금 속도로 예상한 종료 < 허용 최소(`min_ms`, 없으면 목표 × 0.85). 발표 20% 지난 뒤부터 | 10초 | `SLOW_DOWN` "시간 여유가 있어요. 천천히 말해도 돼요" | Take 에서 그만 |
| TIME | `SLIDE_OVER` | 이 장 체류 > 장 목표 × 1.5 (마지막 장 제외) | — | `MOVE_ON` "이 장은 목표보다 25초 넘었어요 — 정리하고 다음 장으로" → `MOVE_ON` "지금 다음 장으로 넘어가세요" | 그 장에서 그만 |
| TIME | `FINAL_MINUTE` | 남은 시간 ≤ 60초, 남은 장 ≥ 2, **장별 계획이 없거나 실제로 늦을 때만** | — | `WRAP_UP` "남은 시간이 짧으니 결론으로 넘어가세요" | 1회 |
| TIME | `TIME_OVER` | 경과 > `max_ms` (없으면 목표) | — | `WRAP_UP` "제한 시간을 넘겼어요 — 한 문장으로 마무리하세요" | 1회 |
| (교정한 영역) | `IMPROVED_AFTER_FEEDBACK` | 시선 · 속도 · 음량 · 군더더기 교정이 효과 있음 | — | `CONTINUE` "좋아요, 지금처럼 이어가세요" — 우선순위 최하, 20초 안에만 | — |

- **r (필요 속도 비율)** = 남은 내용 시간 ÷ 남은 시간. 남은 내용 시간 = 이 장 목표 × (1 − 진행도) + 뒤 장 목표 합
- **진행도** = 이 장에서 말한 글자 수 ÷ 이 장 대본 글자 수. STT 가 나쁘거나 이 장에서 STT 가 끊긴 적이 있으면 이 장에 머문 시간 ÷ 장 목표
- **CPM** = 글자 수(공백 제외) ÷ 실제로 말한 시간(단어별 end − start 합) × 60초. stt-live v1 과 같은 공식이고 군더더기는 뺍니다
- **지속**은 센서를 믿을 수 있는 상태로 이어진 시간입니다. 센서가 나빴다가 좋아지면 처음부터 다시 셉니다
- 늦는 중에는 `SLOW_DOWN` 을 하지 않습니다 (`TIME_PRESSURE`) — 시간을 더 모자라게 만들기 때문입니다
- 말이 느린 것 단독은 알리지 않습니다. 실제로 늦어질 때 TIME 으로 다룹니다

### 4-3. 상태 표시 — `indicators`

지시는 한 번에 1개지만, 힐끗 보면 되는 상태 표시는 여러 개를 함께 띄워도 말이 멈추지 않습니다. 띄울지는 FE 가 정합니다.

| 필드 | 값 |
|---|---|
| `schedule` | `AHEAD` · `ON_TRACK` · `BEHIND` · `OVER` · `UNKNOWN` |
| `required_ratio` | r |
| `pace`, `cpm` | `SLOW`(< 275) · `NORMAL` · `FAST`(> 350) · `UNKNOWN` |
| `gaze` | `AUDIENCE` · `SCRIPT`(보인 시간 중 ≥ 50%) · `UNCERTAIN`(UNCERTAIN > 50%) · `UNKNOWN` |
| `volume` | `LOW` · `OK` · `UNKNOWN` |

### 4-4. 예시 (§3-3 요청의 실제 응답)

1분 55초, 3번 장의 1/4 을 말한 상태입니다. 남은 내용 75초 ÷ 남은 시간 65초 = r 1.15 로 늦는 중이고,
지금 370 CPM × 1.15 = 427 이 '빠름' 기준 350 을 넘어 속도로는 따라잡지 못하므로 `CONDENSE` 를 고릅니다.
말하는 중이지만 0.4초 전에 문장이 끝나(`utterance_end_ms`) 바로 말합니다.
시선 후보는 아직 3초를 채우지 않았고(`NOT_PERSISTENT`), '천천히'는 늦는 중이라 버립니다(`TIME_PRESSURE`).

```json
{
  "schema_version": "1.0", "policy_version": "coach-v1", "config_hash": "…",
  "take_id": "take-123", "t_ms": 115000,
  "action": "INTERVENE",
  "candidate_id": "BEHIND_SCHEDULE-115000",
  "reason_codes": ["BEHIND_SCHEDULE", "SPEED_LIMIT_EXCEEDED", "MISSION_RELEVANT", "MISSION_AT_RISK", "RECURRING"],
  "feedback": {
    "type": "TIME", "instruction": "CONDENSE",
    "message": "핵심만 말하고 넘어가세요 — 남은 2장, 1분 5초",
    "priority": 100, "confidence": 0.9,
    "evidence": {"start_ms": 115000, "end_ms": 115000, "slide_number": 3,
                 "required_ratio": 1.1538, "remaining_content_ms": 75000, "remaining_ms": 65000,
                 "remaining_slides": 2, "cpm": 370.4, "required_cpm": 427.4}
  },
  "candidates": [
    {"candidate_id": "BEHIND_SCHEDULE-115000", "issue": "BEHIND_SCHEDULE", "type": "TIME",
     "instruction": "CONDENSE", "priority": 100, "confidence": 0.9, "status": "SELECTED",
     "reasons": ["SPEED_LIMIT_EXCEEDED", "MISSION_RELEVANT", "MISSION_AT_RISK", "RECURRING"]},
    {"candidate_id": "GAZE_SCRIPT-115000", "issue": "GAZE_SCRIPT", "type": "GAZE",
     "instruction": "LOOK_AT_CAMERA", "priority": 63, "confidence": 0.97, "status": "WAITING",
     "reasons": ["NOT_PERSISTENT"]},
    {"candidate_id": "PACE_FAST-115000", "issue": "PACE_FAST", "type": "SPEED",
     "instruction": "SLOW_DOWN", "priority": 60, "confidence": 1.0, "status": "IGNORED",
     "reasons": ["TIME_PRESSURE", "NOT_PERSISTENT"]}
  ],
  "indicators": {"schedule": "BEHIND", "required_ratio": 1.1538, "pace": "FAST", "cpm": 370.4,
                 "gaze": "SCRIPT", "volume": "OK"},
  "events": [
    {"kind": "INTERVENTION", "event_id": "ev-00001", "t_ms": 115000, "…": "…"},
    {"kind": "SUPPRESSED", "event_id": "ev-00002", "…": "…"},
    {"kind": "SUPPRESSED", "event_id": "ev-00003", "…": "…"}
  ],
  "coach_state": {"v": 1, "…": "다음 요청에 그대로"}
}
```

---

## 5. 판단 로직

`decide()` 한 번이 아래 순서로 돕니다 ([`engine.py`](local/ai/src/coach/engine.py)).

```
① 기록 갱신    장 추적, 새로 확정된 STT 단어를 장별 글자 수 · 군더더기 수 · 키워드에 누적
② 평가기       측정값 → 문제(심각도 · 신뢰도 · 근거). 말할지는 판단하지 않는다
   되돌아보기   잴 때가 된 개입의 효과 판정 → 전략 수정 (§5-7)
   문제 구간    열기 · 이어가기 · 닫기 (§5-8)
③ 후보 생성    문제 → 행동 후보 (사다리 몇 번째 칸인지, 속도로 따라잡을 수 있는지)
④ 적격성 필터  지금 말하면 안 되는 후보를 WAITING / IGNORED 로
⑤ 우선순위     남은 후보에 점수
⑥ 행동 선택    WAIT / IGNORE / INTERVENE        ← 설계 그림의 '코치 에이전트' 자리
⑦ 문구 렌더링  instruction + variant → 템플릿 → message
⑧ 마무리       이벤트 · 최근 60초 기록 · 새 coach_state
```

### 5-1. 평가기 — 계산 세부

| 평가기 | 계산 | 센서를 믿을 수 없을 때 |
|---|---|---|
| 시선 [`gaze.py`](local/ai/src/coach/evaluators/gaze.py) | `script_ratio` = 대본 라벨 비율 ÷ (1 − UNCERTAIN). **보인 시간 중** 비율이라 얼굴이 자주 안 잡혀도 문제를 놓치지 않는다. 연속 응시만으로 잡을 때는 창 비율 ≥ 60% 도 요구한다 (라벨 흔들림으로 5초 연속이 우연히 생긴다) | 지금 UNCERTAIN 비율과 최근 10초 평균 중 **나쁜 쪽** > 50% → `SENSOR_UNUSABLE`, 지표 비움. 나빠질 때는 바로, 좋아질 때는 천천히 |
| 음량 · 침묵 [`voice.py`](local/ai/src/coach/evaluators/voice.py) | 말하는 동안의 최근 5초 평균 dB (표본 3개 이상). 침묵은 오디오가 되살아난 뒤부터만 센다 | 오디오 정지 → 소리 판단 전부 `SENSOR_UNUSABLE` |
| 말 [`speech.py`](local/ai/src/coach/evaluators/speech.py) | 15초 CPM, 효과용 6초 CPM, 60초 · 30초 군더더기 수, 장별 말한 글자 수, 키워드 (확정 단어만 누적). 확정이 늦게 온 단어는 **말한 시각의 장**에 붙인다 | `stt_status` ≠ ok 또는 오디오 정지 → STT 판단 `SENSOR_UNUSABLE`, 그 장은 STT 가 끊긴 장으로 표시. **돌아온 뒤에는 돌아온 뒤의 단어로만** 잰다 |
| 시간 [`timing.py`](local/ai/src/coach/evaluators/timing.py) | r, 예상 종료(지금까지 소화한 계획 시간 대비 실제 경과로 남은 내용을 환산), 장 예상 소요, 남은 장 수 | STT 가 끊긴 장은 진행도를 시간으로 재고 신뢰도를 낮춘다(0.9 → 0.6) |

**'빠름'은 r 이 아니라 예상 종료로 봅니다.** 끝으로 갈수록 r 의 분모(남은 시간)가 작아져, 9초 앞선 것만으로
r 이 0.82 까지 내려갑니다. 허용 범위(165~195초) 안에 끝날 발표자에게 "천천히"를 말하게 되므로,
'지금 속도면 허용 최소보다 일찍 끝나나'를 봅니다. '늦음'은 그대로 r 입니다 — "몇 % 빨라져야 하나"가 곧 r 이라서.

### 5-2. 공통 지표 이름

`evidence` · `Mission.target.metric` · 리뷰 evidence 가 **같은 이름**을 써야 미션 판정이 이어집니다.
미션 target 의 metric 이 아래에 없으면 실시간으로는 검사하지 않습니다(조용히 넘어감).

| 지표 | 뜻 | 단위 |
|---|---|---|
| `script_ratio` | 보인 시간 중 대본 응시 비율 (최근 10초) | 0~1 |
| `continuous_script_gaze_ms` | 대본 연속 응시 시간 | ms |
| `gaze_uncertain_ratio` | UNCERTAIN 비율 | 0~1 |
| `cpm` · `cpm_recent` | 15초 말 속도 · 효과를 잴 때 쓰는 6초 말 속도 | 글자/분 |
| `relative_db` | 최근 5초 평균 (기준 대비) | dB |
| `silence_ms` | 지금 침묵 길이 | ms |
| `filler_count_60s` · `filler_count_30s` · `filler_per_min` | 군더더기 | 회 · 회/분 |
| `required_ratio` | r | 배 |
| `remaining_ms` · `remaining_content_ms` · `remaining_slides` | 남은 시간 · 남은 내용 시간 · 남은 장 | ms · 장 |
| `slide_progress` · `slide_elapsed_ms` · `slide_target_ms` | 이 장 진행도 · 체류 · 목표 | 0~1 · ms |
| `slide_duration_ms` | 지금 속도면 이 장이 걸릴 시간 (진행도 10% 미만이면 체류 시간) | ms |
| `projected_end_ms` · `early_limit_ms` | 예상 종료 · '너무 일찍'의 기준 | ms |

리뷰의 미션 판정(§6-4)은 위 이름 중 `script_ratio` · `cpm` · `relative_db` · `filler_per_min` · `slide_duration_ms` 와,
Take 종료 뒤에만 의미가 있는 `duration_ms`(Take 전체 시간) · `long_silence_count`(긴 멈춤 횟수) · `keyword_coverage`(필수 키워드 비율)를 씁니다.

### 5-3. 후보 생성

후보 하나 = 문제 하나. 행동은 `사다리[max(전략 단계, 최소 단계)]` 입니다.
전략 단계는 효과가 없을 때마다 올라가고(§5-7), 최소 단계는 평가기가 정합니다 — 늦었는데 지금 속도에 r 을 곱한 값이
'빠름' 기준을 넘으면 `SPEED_UP` 을 건너뛰고 `CONDENSE` 부터 씁니다 (`SPEED_LIMIT_EXCEEDED`).

전략은 범위마다 따로입니다. 시선 · 장 초과 · 키워드는 **슬라이드마다**(4번 장에서 시선 지적을 포기해도 5번 장에선 다시 시도),
나머지는 Take 전체입니다.

### 5-4. 적격성 필터

점수와 상관없이 무조건 적용됩니다. 걸린 이유는 **전부** 남깁니다 — 실전 모드에서 버린 후보도 다른 이유까지 남아야
리뷰가 "이때 이런 게 걸렸다"를 보여줄 수 있습니다.

| 결과 | 이유 | 규칙 |
|---|---|---|
| IGNORED (기다려도 소용없음) | `EXAM_MODE` | 실전 모드 |
| | `SENSOR_UNUSABLE` | 그 영역 센서를 믿을 수 없음 |
| | `LOW_CONFIDENCE` | 신뢰도 < 0.6 |
| | `TIME_PRESSURE` | 늦는 중에 `SLOW_DOWN` |
| | `STRATEGY_EXHAUSTED` | 그 범위에서 사다리를 다 써 봄 |
| | `ALREADY_DELIVERED` | 1회만 하는 안내를 이미 함 |
| | `PLAN_RELAXED` · `BUDGET_EXHAUSTED` | 코칭 계획이 참으라고 함 · 개입 횟수 상한 (v1.1 계획) |
| WAITING (나중에 다시) | `NOT_PERSISTENT` | 지속시간 미달 |
| | `MIN_GAP` | 직전 메시지 뒤 15초 안 |
| | `COOLDOWN` | 같은 instruction 을 한 지 60초 안 |

### 5-5. 우선순위

```
priority = round( min(1, 심각도 × 신뢰도 × 지속 × 미션 × 반복 × 계획 × 시간 × 악화 × 새로움) × 100 )
```

| 가중치 | 값 | 붙는 이유 코드 |
|---|---|---|
| 심각도 | 기준선에서 0.5, '아주 나쁨'에서 1.0 으로 선형 | — |
| 지속 | 요구 지속시간을 넘긴 만큼 최대 ×1.3 (15초에서 최대) | 5초 이상 넘기면 `PERSISTENT` |
| 미션 | 같은 type(· 슬라이드) ×1.3, 지금 값이 미션 target 을 벗어나면 ×1.5 | `MISSION_RELEVANT`, `MISSION_AT_RISK` |
| 반복 | 이전 Take 기억에 같은 type(· 슬라이드) ×1.2 | `RECURRING` |
| 계획 | 코칭 계획 focus 의 weight (0.5~2.0 으로 자름) | 1 보다 크면 `PLAN_FOCUS` |
| 시간 | TIME 후보만, 발표가 진행될수록 최대 ×1.5 | 남은 시간 ≤ 60초면 `TIME_CRITICAL` |
| 악화 | 30초 전보다 나빠짐 ×1.1 (대본 응시 +0.15, CPM +30, dB −3, r +0.1) | `WORSENING` |
| 새로움 | 같은 문제로 이미 n 번 말했으면 × max(0.55, 1 − 0.15n) | — |

### 5-6. 행동 선택 — `RulePolicy`

| 상황 | action | reason_codes |
|---|---|---|
| 후보 없음 | `WAIT` | `NO_CANDIDATE` |
| 적격 후보가 있고 1등 ≥ 40점, 발표자가 말이 끊긴 틈 | `INTERVENE` | 문제 코드 + 1등의 가중치 이유 |
| 위와 같지만 말하는 중 | `WAIT` (최대 3초) | `WAITING_FOR_PAUSE` → 3초가 지나면 `INTERVENE` + `PAUSE_TIMEOUT` |
| 적격 후보가 있지만 1등 < 40점 | `IGNORE` | `LOW_PRIORITY` |
| 적격 후보 없음, WAITING 후보 있음 | `WAIT` | 그 후보의 기다림 이유 |
| 모든 후보가 IGNORED | `IGNORE` | 1등 후보의 버림 이유 |

'말이 끊긴 틈' = 침묵 ≥ 300ms, 또는 Deepgram 문장 끝 신호(`utterance_end_ms`)가 1초 안. 판단할 신호가 없으면 틈으로 봅니다.
1등이 아닌 적격 후보는 `OUTRANKED` 로 남고 15초 뒤 다시 경쟁합니다. **한 번에 지시는 하나뿐입니다** (§10).

### 5-7. 되돌아보기

개입마다 효과를 잴 시점을 예약하고([`reflection.py`](local/ai/src/coach/reflection.py)), 그 시점에 같은 지표를 다시 봅니다.

| 문제 | 언제 | 효과 있음 |
|---|---|---|
| `GAZE_SCRIPT` | 12초 뒤 | 대본 응시 < 60% (기준선 아래로 돌아옴) |
| `PACE_FAST` | 10초 뒤 | 6초 CPM ≤ 330, 또는 10% 이상 줄어듦 |
| `VOLUME_LOW` | 8초 뒤 | 기준 이상으로 돌아옴, 또는 3dB 이상 커짐 |
| `FILLER_FREQUENT` | 30초 뒤 | 뒤 30초 군더더기 ≤ 앞 30초의 절반 |
| `LONG_SILENCE` | 5초 뒤 | 다시 말함 — 지금 침묵이 개입 뒤에 시작됐으면 그 사이 말을 한 것 |
| `BEHIND_SCHEDULE` | 15초 뒤 | r < 1.05, 또는 0.05 이상 줄어듦 |
| `AHEAD_OF_SCHEDULE` | 10초 뒤 | 6초 CPM 이 10% 이상 줄었거나 예상 종료가 허용 범위로 돌아옴 |
| `SLIDE_OVER` | 15초 뒤 | 다음 장으로 넘어감 |
| `KEYWORD_MISSING` | 15초 뒤 | 그 키워드를 말함 (다음 장으로 넘어가서 말해도 인정) |
| `FINAL_MINUTE` · `TIME_OVER` | — | 재지 않음 |

전후 값은 순간값이 아니라 **최근 3초 평균**입니다. 세 가지는 실험(§9-5)으로 정했습니다.

- **시선은 '얼마나 줄었나'를 쓰지 않는다** — 개입은 측정값이 잡음으로 높게 튄 순간에 일어나기 쉬워 그 뒤엔 저절로 내려옵니다(평균으로의 회귀). 그래서 기준선 아래로 돌아왔는지만 봅니다
- **시선은 12초 뒤에 잰다** — FE 의 10초 창이 반응(약 2초 뒤) 이후로 다 채워진 뒤
- **속도는 6초 CPM 으로 잰다** — 15초 창에는 개입 전의 빠른 단어가 남아 효과를 못 봅니다

- **효과 있음** → 같은 방법 유지 + `CONTINUE` 후보(20초 안에, 그 문제가 다시 나타나지 않았을 때만)
- **효과 없음** → 사다리 다음 칸 (`STRATEGY` 이벤트 `ESCALATED`). 마지막 칸에서도 없으면 그 범위에서 그만 (`GAVE_UP`)
- **잴 수 없음**(센서가 나빠짐) → 전략을 바꾸지 않습니다. 모르는 것으로 멀쩡한 방법을 포기하지 않으려는 것입니다

효과는 실시간 지표로 잽니다. 리뷰는 확정 STT 로 다시 정확하게 계산할 수 있습니다.

### 5-8. 문제 구간

같은 문제가 이어지는 동안을 한 구간으로 묶습니다([`episodes.py`](local/ai/src/coach/episodes.py)).
3초 동안 안 보이면 닫습니다 — 1~2초 깜빡임으로 지속시간이 끊기지 않게.
구간이 닫히면 `EPISODE` 이벤트가 됩니다. **개입하지 못한 구간도 남깁니다** (2초 미만이고 아무 일도 없던 깜빡임만 뺍니다).
구간은 센서를 믿을 수 있던 시간 · 없던 시간과 평균 심각도를 함께 남깁니다 — 리뷰가 근거 없는 지적을 거르고 부담을 재는 데 씁니다.

---

## 6. 리뷰 에이전트로 넘기는 것

리뷰 에이전트는 코치를 직접 부르지 않습니다(설계 문서 6-3). 코치가 1초마다 낸 이벤트를 BE 가 쌓아 두고,
Take 가 끝나면 그 이벤트를 요약된 근거로 묶어 넘깁니다 — 리뷰가 원본 이벤트 수백 개를 직접 읽지 않게(설계 문서 6-2).

```
decide() 응답의 events  ─┐  BE 가 Take 별로 그대로 쌓음 (추가만)
finalize() 응답의 events ─┘
              │   + 이 Take 의 plan · missions · memory (요청에 넣었던 것과 같은 것)
              ▼  Take 종료 분석 (/v1/takes/analyze 안에서)
   build_review_evidence(take_id, events, plan=, missions=, memory=) → CoachReviewEvidence → 리뷰 에이전트
```

**숫자는 전부 코드가 계산합니다.** 리뷰 에이전트(LLM)는 인용만 하고 새로 만들지 않습니다 —
설계 문서 12 의 '근거 없는 수치 생성 건수'를 0 으로 두기 위한 구조입니다. 문장은 만들지 않습니다.
꼬리표 · 상태 · 순위 · 목표값까지만 주고 문장은 리뷰가 씁니다.

### 6-1. 이벤트 6종

| kind | 언제 | 주요 필드 | 리뷰에서 쓰는 곳 |
|---|---|---|---|
| `INTERVENTION` | 화면에 띄움 | `candidate_id`, `type`, `instruction`, `variant`, `message`, `priority`, `reason_codes`, `slide_number`, `evidence` | 실시간 코칭 이력 |
| `OUTCOME` | 효과를 잼 (또는 종료 때 못 잼) | `intervention_id`, `outcome`(`EFFECTIVE` · `INEFFECTIVE` · `NOT_MEASURED`), `metric`, `before`, `after` | "실시간 코칭이 효과가 있었나" |
| `EPISODE` | 문제 구간이 끝남 (또는 종료) | `candidate_id`, `type`, `slide_number`, `start_ms`, `end_ms`, `peak_severity`, `mean_severity`, `peak_evidence`, `reliable_ms`, `unreliable_ms`, `intervention_ids`, `suppressed_reasons`, `closed_by` | 문제 구간 · 부담 · 순위 |
| `SLIDE` | 장을 떠남 (또는 종료) | `slide_number`, `start_ms`, `end_ms`, `target_ms`, 시선 · STT · 오디오가 살아 있던 시간, 대본 응시 · CPM · dB 의 '값 × 시간' 합, 군더더기 수, 긴 침묵 시간, 필수 · 찾은 키워드 | 장별 표 · 미션 판정 · 시간 문제 |
| `STRATEGY` | 방법을 바꿈 / 그만둠 | `change`(`ESCALATED` · `GAVE_UP`), `from_instruction`, `to_instruction`, `intervention_id` | "여러 번 말해도 안 바뀐 곳" |
| `SUPPRESSED` | 걸렸지만 말하지 않음 (같은 문제는 10초에 한 번) | `status`, `reasons`, `priority` | 기준값 조정 · "왜 안 떴나" |

`event_id` 는 `coach_state` 의 번호로 매겨 Take 안에서 겹치지 않습니다(`ev-00042`). 같은 요청을 다시 보내도
이미 처리한 `t_ms` 는 아무것도 바꾸지 않으므로(`STALE_TICK`) 이벤트가 중복되지 않습니다.

### 6-2. CoachReviewEvidence — 리뷰가 먼저 볼 것

| 필드 | 내용 | 리뷰 출력에서 |
|---|---|---|
| `type_status[]` | 영역 7개마다 `PRIORITY` · `IMPROVED` · `STABLE` · `STRENGTH` · `NOT_EVALUABLE` + 부담 + 이전 Take 비교 + Take 지표 | dimension 상태 |
| `issues[]` | 순위 매긴 문제(영역 × 장): 부담 · 순위 점수 · 구간 수 · 개입과 효과 · 포기 여부 · 이전 Take 비교(`RECURRING` / `NEW`) · 미션 실패 여부 · 근거 | improvements · remaining_issues(`RECURRING`) · new_issues(`NEW`) |
| `next_missions[]` | 다음 미션 후보 최대 3개: 영역 · 장 · `target{metric, operator, value}` · 이번 값 · 이유 | next_missions — `target` 을 그대로 쓰면 다음 Take 에서 기계로 판정된다 |
| `mission_results[]` | 이전 미션마다 `ACHIEVED` · `PARTIAL` · `FAILED` · `NOT_EVALUABLE` + `achieved` + 이번 값 · 데이터 덮개 · 판단 못 한 이유 | mission_results / previous_mission_result |
| `memory_check[]` | 이전 Take 기억 한 줄마다 `RECURRING` · `RESOLVED` · `UNKNOWN` | improved · remaining_issues |
| `strengths[]` | `CLEAN`(문제 없음) · `RESOLVED_RECURRING`(이전 문제를 고침) · `MISSION_ACHIEVED` · `RESPONDED_TO_COACHING`(코칭에 바로 반응) · `ON_TIME` + 근거 | strengths |
| `data_quality` | 시선 · STT · 오디오 데이터 덮개, 믿을 수 없어 뺀 구간 수, 영역별 판단 가능 여부 | '판단할 수 없었다'는 안내 |
| `summary` | 개입 · 격려 · 효과 · 포기 · 문제 구간 · 참은 이유 | 실시간 코칭 요약 |

### 6-3. 세부 근거

| 필드 | 내용 |
|---|---|
| `slides[]` | 장별 표 한 줄: 시간 · 목표 · 초과, 대본 응시 비율, CPM, dB, 군더더기 수 · 분당, 긴 침묵, 키워드 비율 · 빠뜨린 키워드, 데이터 덮개, 문제로 본 영역. 리뷰의 구간 표(CAMERA/BOTTOM · Pace · Filler · Keyword)와 같은 축 |
| `segments[]` | 문제 구간: `start_ms` · `end_ms`(실제로 잡힌 구간), `onset_ms` · `offset_ms`(창 지연을 되돌린 추정 구간), 평균 · 최고 심각도, 신뢰도, 꼬리표 `hint`, 개입, 말하지 못한 이유, 근거. 리뷰 DTO `SegmentReview` 와 같은 축 |
| `interventions[]` · `strategy_changes[]` · `by_type[]` | 개입마다 효과(before → after), 방법 변경 · 포기, 영역별 집계 |

| `hint` | 뜻 | 리뷰가 할 만한 것 |
|---|---|---|
| `COACHED_EFFECTIVE` | 말했더니 바뀜 | 개선된 점 / 코칭 효과 |
| `COACHED_INEFFECTIVE` | 말했는데 안 바뀜 | 남은 문제 |
| `GAVE_UP` | 여러 방법을 써도 안 바뀌어 그만둠 | 다음 Mission 후보 (예: "6번 장 대본 응시 30% 이하") |
| `UNADDRESSED` | 문제였지만 말하지 못함 (`suppressed_reasons` 에 이유 — 실전 모드, 간격 …) | 실전 모드 리포트, 남은 문제 |
| `COACHED_NOT_MEASURED` | 말했지만 효과를 재지 못함 | — |
| `UNRELIABLE` | 센서를 믿을 수 없던 구간 | **문제로 말하지 않는다** — 데이터 품질로만 |

### 6-4. 규칙 ([`review.py`](local/ai/src/coach/review.py))

판정 층(`assess`)은 순수 함수입니다. 실험(§9)은 정답 데이터에 **같은 판정**을 돌려, 리뷰 근거가 정답과 다르면
그 차이가 측정에서 온 것인지 판정 규칙에서 온 것인지 나눠 봅니다.

**① 문제 구간 정리** — 값은 실험의 격자(§9-4)로 골랐습니다

| 단계 | 규칙 | 기본값 |
|---|---|---|
| 신뢰도 | 센서를 믿을 수 있던 시간이 50% 미만인 구간은 `UNRELIABLE` — 문제 · 순위 · 미션에서 뺀다 | 50% |
| 창 지연 보정 | 평가기의 창 때문에 늦게 잡힌 만큼 되돌린다. 시선: 시작 − 0.7 × 창, 끝 − 0.3 × 창 (창 비율이 기준 0.7 을 넘으려면 창의 70% 를 대본에 써야 하므로). 속도 ±7.5초(15초 CPM 창의 절반), 음량 ±2.5초, 군더더기 ±30초, 침묵 시작 −5초 | ×1.0 |
| 병합 | 같은 영역 · 같은 장 구간이 10초 안에 다시 시작되면 하나로 | 10초 |
| 깜빡임 | 실제로 잡힌 시간(보정 전)이 3초 미만이고 개입도 없던 구간은 뺀다 | 3초 |

**② 부담과 순위**

- **부담** = 구간 길이(보정 후) × 평균 심각도. 최고 심각도가 아니라 평균인 것은 잡음 많은 지표(CPM)의 최고값이 부풀어 순위를 흔들었기 때문입니다
- **시간**은 구간이 아니라 장별 실제 시간에서 잽니다 — 장 목표 × 1.1 을 넘은 초 × 심각도, Take 전체가 `max_ms` 를 넘거나 `min_ms` 보다 일찍 끝난 초
- **키워드**는 빠뜨린 키워드 하나 = 10초
- 부담 3 미만은 문제로 보지 않습니다
- **순위 점수** = 부담 × (이전 Take 에도 있었으면 1.5) × (포기했으면 1.3) × (그 영역 미션이 실패 · 부분 달성이면 1.2)
- 순위는 **영역 합계가 먼저**, 그 안에서 장 순서입니다 — 시선처럼 장마다 나뉘는 문제도 '다음에 먼저 고칠 영역'이 흔들리지 않게

**③ 영역 상태** (위에서 먼저 걸리는 것)

| 상태 | 조건 |
|---|---|
| `NOT_EVALUABLE` | 그 영역 데이터가 Take 의 50% 미만 (시선 · STT · 오디오 덮개, 키워드는 필수 키워드가 있어야) |
| `PRIORITY` | 영역 점수 상위 2개 |
| `IMPROVED` | 이전 Take 기억의 문제가 이번엔 없음(`RESOLVED`), 또는 그 영역 미션을 달성하고 이번 문제도 없음 |
| `STRENGTH` | 문제 없음 |
| `STABLE` | 그 밖 (작은 문제는 있지만 우선은 아님) |

**④ 미션 판정**

| 지표 | 범위 | 데이터 덮개 | `PARTIAL` 허용 오차 |
|---|---|---|---|
| `script_ratio` | 장 / Take | 시선 | 0.05 |
| `cpm` | 장 / Take | STT | 20 |
| `relative_db` | 장 / Take | 오디오 | 2 dB |
| `filler_per_min` | 장 / Take | STT | 1 |
| `keyword_coverage` | 장 / Take | STT | 0 |
| `slide_duration_ms` | 장 | — | 5초 |
| `duration_ms` | Take | — | 5초 |
| `long_silence_count` | 장 / Take | 오디오 | 0 |

목표를 만족하면 `ACHIEVED`, 허용 오차 안으로 놓치면 `PARTIAL`, 그보다 많이 놓치면 `FAILED` 입니다.
판단할 수 없으면 `NOT_EVALUABLE` 과 이유를 남깁니다 — `NOT_REACHED`(그 장에 가지 못함), `LOW_DATA_COVERAGE`(데이터 50% 미만),
`UNSUPPORTED_METRIC`(위 표에 없는 지표), `NO_TARGET`(목표 없는 미션), `NO_DATA`.

**⑤ 이전 Take 비교** — 기억 한 줄마다 같은 영역(· 장)에 문제가 있으면 `RECURRING`, 없고 판단할 수 있으면 `RESOLVED`,
판단할 수 없으면(데이터 부족 · 그 장에 가지 못함) `UNKNOWN`. 이번 문제는 기억에 있으면 `RECURRING`, 없으면 `NEW`.

**⑥ 다음 미션 후보** — 순위대로 영역당 하나, 최대 3개, 판단할 수 없는 영역은 뺍니다.
그 영역 부담의 60% 이상이 한 장에 몰려 있으면 장 단위, 아니면 Take 단위(시간 · 키워드는 늘 장 단위)입니다.
목표는 **한 번에 도달할 만큼만** 잡습니다.

| 영역 | 목표 |
|---|---|
| 시선 | `script_ratio` ≤ max(0.3, 이번 값 − 0.2) — 0.9 였으면 0.7 부터 |
| 속도 | `cpm` ≤ 350 |
| 음량 | `relative_db` ≥ −6 |
| 군더더기 | `filler_per_min` ≤ max(2, 이번 값 ÷ 2) |
| 침묵 | `long_silence_count` ≤ 0 |
| 키워드 | `keyword_coverage` ≥ 1.0 |
| 시간 | 장: `slide_duration_ms` ≤ 장 목표 × 1.1 · Take: `duration_ms` ≤ `max_ms` (일찍 끝났으면 ≥ `min_ms`) |

### 6-5. 리뷰 에이전트 프롬프트에 넣을 규칙

1. **숫자는 이 근거에 있는 것만 인용한다.** 새로 계산하거나 추정하지 않는다
2. `UNRELIABLE` 구간과 `NOT_EVALUABLE` 영역 · 미션은 문제로도 강점으로도 말하지 않고 "판단할 수 없었다"고 말한다 (`data_quality` 가 이유)
3. "몇 분 몇 초부터"는 `onset_ms` · `offset_ms` 로 말한다
4. 가장 먼저 고칠 것은 `issues[0]`, 다음 미션은 `next_missions` 의 `target` 을 그대로 쓰고 `description` 문장만 쓴다
5. 실시간 코칭 효과는 `interventions[].outcome` 과 `summary` 로만 말한다

## 7. coach_state

코치의 기억입니다. **BE 는 내용을 몰라도 됩니다** — 받은 그대로 다음 요청에 붙이면 됩니다.

| 내용 | 쓰임 |
|---|---|
| 최근 60초 기록 (1초마다 대본 응시 · CPM · dB · 말하는 중 · 새 군더더기 · r) | 지속 · 악화 · 음량 평균 · 군더더기 수 · 효과 비교 |
| 열린 문제 구간 | 지속시간, `candidate_id`, `EPISODE` 이벤트 |
| 마지막 개입 시각, instruction 별 마지막 시각, 문제별 개입 횟수 | 간격 · 쿨다운 · 새로움 |
| 효과를 잴 개입, 문제별 전략 단계 · 포기 여부, 격려 후보 | 되돌아보기 |
| 장별 말한 글자 수, 찾은 키워드, STT 가 끊긴 장, 오디오 · STT 복귀 시각, 최근 장 전환 기록 | 진행도 · 키워드 · 침묵 · 늦게 온 단어의 장 |
| 지금 장의 누적 (시선 · CPM · dB 의 '값 × 시간', 군더더기, 데이터 덮개) | 장을 떠날 때 `SLIDE` 이벤트 |
| 코칭 계획 (v1 은 기본값) | focus · relax · 개입 상한 |

- **크기**: 재생 시나리오 기준 최대 약 14KB. 기록이 최근 60초만 남아 10분 발표에서도 커지지 않습니다 (테스트로 20KB 미만 확인)
- **버전**: 모양이 바뀌면 `STATE_VERSION` 을 올립니다. 기본값이 있는 필드를 더하는 것은 이전 state 도 그대로 읽히므로 올리지 않습니다. 다른 버전이나 깨진 state 가 오면 새로 시작하고 `reason_codes` 에 `STATE_RESET` 을 남깁니다 — 쿨다운이 한 번 풀리는 정도의 손해입니다
- **왜 AI 에 저장하지 않나**: AI 재시작 · 재배포 때 발표 중 기억이 사라지지 않고, worker 를 늘려도 같은 Take 요청이 어느 worker 로 가든 결과가 같습니다 (§10)

---

## 8. 배포 — ai/releases/ 로 승격

Docker 가이드대로 배포 단위는 `ai/releases/` 하나이고 `workspaces/` 는 이미지에 들어가지 않습니다.
그래서 `v1/deploy/` 는 만들지 않고 코어 폴더를 그대로 옮깁니다.

1. `local/ai/src/coach/` 를 `ai/releases/src/pitch_coach_ai/coach/` 로 복사합니다. 코어 안의 import 는 전부 상대 경로라 고칠 곳이 없고, 의존성은 pydantic 하나뿐입니다
2. 라우터를 하나 둡니다

```python
# ai/releases/src/pitch_coach_ai/api/coach.py
from fastapi import APIRouter

from ..coach import decide_safe, finalize
from ..coach.schemas import CoachRequest, CoachResponse, FinalizeRequest, FinalizeResponse

router = APIRouter(prefix="/coach", tags=["coach"])


@router.post("/evaluate", response_model=CoachResponse)
def evaluate(req: CoachRequest) -> CoachResponse:  # 형식 오류는 FastAPI 가 422 로 돌려준다
    return decide_safe(req)  # 내부 예외는 WAIT + 이전 coach_state


@router.post("/finalize", response_model=FinalizeResponse)
def finalize_take(req: FinalizeRequest) -> FinalizeResponse:
    return finalize(req)
```

3. 리뷰 근거는 API 로 따로 열지 않습니다 — 통합 계획의 "AI 내부 컴포넌트를 API 로 노출하지 않는다"에 따라 `/v1/takes/analyze` 안에서 `build_review_evidence(take_id, events, plan=, missions=, memory=)` 를 부릅니다. BE 는 종료 분석 요청에 쌓아 둔 코치 이벤트와 이 Take 의 계획 · 미션 · 기억을 함께 넣습니다

| API | 언제 | 무엇 |
|---|---|---|
| `POST /v1/coach/evaluate` | 발표 중 1초마다 | `decide_safe()` |
| `POST /v1/coach/finalize` | Take 종료 직후 | `finalize()` |
| `POST /v1/takes/analyze` | Take 종료 분석 | 안에서 `build_review_evidence()` → 리뷰 에이전트 |
| `POST /v1/coach/plan` | (v1.1) Take 시작 전 | LLM 코칭 계획을 담은 첫 `coach_state` |

`decide()` 는 CPU 만 0.5ms 남짓 쓰므로 동기 함수(`def`)로 둡니다.

---

## 9. 현재 상태와 실험

### 9-1. 상태

이 저장소는 **local 리그**입니다. 아래는 실제로 실행해 확인한 것입니다.

| 항목 | 상태 |
|---|---|
| 판단 파이프라인 | ✅ 동작 — 평가기 · 후보 · 필터 · 우선순위 · 선택 · 되돌아보기 · 문제 구간 · 장별 누적 · 이벤트 · 리뷰 근거 |
| 테스트 | ✅ 120개 통과 (계약 · 평가기 · 판단 · 되돌아보기 · 측정 수정 · 리뷰 근거 규칙 · 재생 · 실험 하한선 · 예외) — 테스트 수는 이 표에만 적습니다 |
| 재생 시나리오 | ✅ 16/16 통과 — 01~12 실시간 행동, 13~16 리뷰 근거(이전 Take 기억 · 미션 · 순위 · 센서 불량) |
| 리뷰 근거 실험 | ✅ 176 Take(시나리오 16 × 잡음 3단계) — §9-3 |
| 판단 지연 | ✅ p50 약 0.5ms, p95 1.3ms 이하 (재생 16개, Windows 11 · Python 3.11) |
| coach_state | ✅ 최대 약 14KB, 10분 발표에서도 20KB 미만 |
| 린트 | ✅ ruff (backend 와 같은 규칙) |
| 실제 발표 데이터 | ⬜ **없음** — 모든 시나리오가 가상 발표자 |
| 기준값 근거 | ⬜ 없음 — FE 코치 · stt-live 의 잠정값과 손으로 고른 값 (리뷰 근거 규칙의 값만 실험으로 골랐다) |
| 전송 계층 | ⬜ local 은 함수 호출뿐 (§8) |

### 9-2. 실험 방법 — 리뷰 근거를 정답과 비교해 채점한다

- **가상 발표** — 시나리오 16개를 잡음 3단계로 재생합니다 (clean 1회, noisy · harsh 각 seed 5 → 176 Take)
- **정답** — 시뮬레이터는 발표자가 **실제로** 어땠는지(잡음 전 상태)를 매초 기록합니다. 정답 리뷰 = 그 실제 상태 × 코치와 **같은 판정 규칙**.
  그래서 코치 리뷰 근거가 정답과 다르면 그 차이는 측정(잡음 · 창 지연 · 구간 처리)에서 온 것입니다
- **원칙** — 센서가 볼 수 없던 문제는 정답도 말하지 않습니다. 이상적인 리뷰는 '그랬을 것 같다'가 아니라 '믿을 수 있는 데이터로 봤다'만 말해야 합니다
- **잡음** ([`tools/simulator.py`](local/tools/simulator.py) `NOISE_PRESETS`) — 시선은 FE 처럼 10초 창의 1초 라벨 비율로 만듭니다

| 잡음 | 시선 라벨 뒤집힘 | UNCERTAIN 끼어듦 | 말 속도 흔들림 | 음량 σ | STT 단어 누락 | 확정 지연 흔들림 |
|---|---|---|---|---|---|---|
| clean | 0 | 0 | 0 | 0 | 0 | 0 |
| noisy | 8%/초 | 4%/초 | ±12% | 1.5 dB | 3% | 0~0.8초 |
| harsh | 15%/초 | 10%/초 | ±20% | 3 dB | 8% | 0~1.5초 |

- **변형** — 리뷰 근거 규칙만 바꿉니다. 리뷰 설정은 실시간 판단에 쓰이지 않으므로 발표는 한 번만 재생하고 변형마다 근거만 다시 만듭니다

| 변형 | 신뢰도 필터 | 구간 병합 | 창 지연 보정 |
|---|---|---|---|
| A 기준선 (v1.0 의 리뷰 근거 규칙) | ✗ | ✗ | ✗ |
| B | ✓ | ✗ | ✗ |
| C | ✓ | 10초 | ✗ |
| D (지금 기본값) | ✓ | 10초 | ×1.0 |

### 9-3. 결과 (seed 5)

↑ 클수록 좋음, ↓ 작을수록 좋음. 칸은 clean / noisy / harsh.

재현: `v1/local` 에서 `python -m tools.evaluate` (기본 seed 5, 약 16초). 결과 JSON 은 `reports/evaluation.json` 에
생기지만 git 에는 올리지 않습니다. 이 표는 `config_hash` `0f4bc4102193` 설정으로 만들었습니다 — 결과 JSON 의
`config_hash` 가 같으면 같은 설정으로 돌린 것입니다.

| 지표 | A 기준선 | D 지금 |
|---|---|---|
| 구간 재현율 ↑ — 볼 수 있던 실제 문제 중 리뷰가 말한 비율 | 1.000 / 0.934 / 0.952 | 1.000 / 0.934 / 0.952 |
| 구간 정밀도 ↑ — 리뷰가 말한 문제 중 실제 문제 | 0.667 / 0.623 / 0.587 | **1.000 / 0.984 / 0.873** |
| 구간 F1 ↑ | 0.800 / 0.747 / 0.726 | **1.000 / 0.959 / 0.911** |
| 근거 없는 지적 / Take ↓ | 0.375 / 0.500 / 0.625 | **0.000 / 0.013 / 0.113** |
| 구간 IoU ↑ — 시간 겹침 | 0.590 / 0.575 / 0.503 | **0.870 / 0.832 / 0.803** |
| 시작 시각 오차 ↓ | 7.3초 / 8.5초 / 9.5초 | **1.6초 / 2.5초 / 2.9초** |
| 1순위 영역 일치 ↑ | 0.750 / 0.713 / 0.700 | **1.000 / 0.913 / 0.913** |
| 1순위 다음 미션 일치 ↑ | 0.750 / 0.713 / 0.700 | **1.000 / 0.913 / 0.913** |
| 영역 상태 정확도 ↑ | 0.938 / 0.938 / 0.936 | **1.000 / 0.989 / 0.977** |
| 거짓 강점 / Take ↓ — 문제가 있던 영역을 강점이라 함 | 0.125 / 0.125 / 0.125 | **0.000 / 0.063 / 0.050** |
| 미션 판정 정확도 ↑ | 1.000 / 1.000 / 0.867 | 1.000 / 1.000 / 0.867 |
| 이전 Take 비교 정확도 ↑ | 1.000 / 1.000 / 1.000 | 1.000 / 1.000 / 1.000 |
| 효과 판정 정확도 ↑ (실시간, 변형과 무관) | 1.000 / 0.980 / 0.936 | 같음 |
| 불필요한 개입률 ↓ (실시간, 변형과 무관) | 0.0% / 2.1% / 4.2% | 같음 |

장별 표의 평균 절대 오차(D): 대본 응시 0.03 / 0.06 / 0.11, CPM 1.6 / 3.7 / 6.4, 장 시간 0.4 / 0.5 / 0.5초.

변형별 기여 (noisy): 신뢰도 필터가 정밀도를 0.62 → 0.93, 근거 없는 지적을 0.50 → 0.05 로 줄였고,
창 지연 보정이 IoU 를 0.59 → 0.83, 시작 오차를 8.5 → 2.5초로 줄였습니다. 병합은 단독으로 조각남을 1.16 → 1.00 으로 줄였습니다.

### 9-4. 격자로 고른 값 (`python -m tools.evaluate --sweep`)

| 값 | 고른 것 | 근거 (noisy / harsh) |
|---|---|---|
| 창 지연 보정 정도 | ×1.0 | IoU 0.83 — ×0.5 는 0.71, ×1.5 는 0.75 (noisy). 정밀도는 보정이 있으면 거의 같음 |
| 병합 간격 | 10초 | 5초 이상이면 F1 · IoU 차이 없음. 조각남 1.23(0초) → 1.14 |
| 리뷰에 넘길 최소 구간 (보정 전 길이) | 3초 | 3초 이하면 clean 재현율 1.000, 4초 이상이면 clean 의 짧은 실제 문제 하나를 놓친다(0.917). 6초면 harsh 의 근거 없는 지적이 0.11 → 0.06 으로 줄어든다 — **실제 데이터로 다시 고를 값** |

### 9-5. 실험으로 찾아 고친 측정 문제 (`--explain D`)

틀린 사례를 하나씩 따라가 원인을 찾았습니다. 모두 회귀 테스트(`tests/test_measurement.py`)가 있습니다.

| 문제 | 사례 | 고친 것 |
|---|---|---|
| 센서가 나쁜 시간도 지속시간에 들어가, 잠깐 괜찮아진 1초에 시선 지적 | 04 | 지속시간은 믿을 수 있는 시간만 센다 |
| 10초 창의 UNCERTAIN 이 잡음으로 잠깐 50% 아래로 내려감 | 04 · 16 | 지금 값과 최근 10초 평균 중 나쁜 쪽 |
| STT 가 불량이던 동안의 단어로 '빠르다' | 16 | 불량에서 돌아오면 돌아온 뒤의 단어로만 |
| 장 정보가 없는 발표에서 null 장 누적이 매 틱 state 를 초기화 (개입 140회) | 12 | null 이 될 수 있는 필드는 모두 기본값 |
| 확정이 늦게 온 단어를 다음 장에 붙여 키워드를 못 찾음 | 11 | 최근 장 전환 기록으로 말한 시각의 장에 붙인다 |
| 시선 효과를 평균으로의 회귀로 '줄었다'고 오판 | 03 · 14 | 기준선(0.6) 아래만 인정, 12초 뒤, 3초 평균 |
| 15초 CPM 창에 개입 전 단어가 남아 속도 효과를 못 봄 | 06 | 효과는 6초 CPM |
| 단어 사이 틈을 침묵으로 보고 '다시 말함'을 못 봄 | 09 | 침묵이 개입 뒤에 다시 시작됐는지 |
| 라벨 흔들림으로 5초 연속 응시가 우연히 생김 | harsh 06 · 08 · 09 | 연속 응시는 창 비율 ≥ 0.6 일 때만 |
| 음량 표본 1~2개의 평균 | harsh | 표본 3개 이상 |
| 부담을 최고 심각도로 재 잡음 많은 지표가 부풀어 순위가 흔들림 | 07 | 평균 심각도 |
| 시선은 장마다 나뉘어 영역 1순위가 흔들림 | 07 | 순위는 영역 합계가 먼저 |

같은 seed(1~3)로 고치기 전과 비교하면 효과 판정 정확도가 noisy 0.85 → 1.00, harsh 0.69 → 0.93, harsh 불필요한 개입률이 6.7% → 3.6% 가 됐습니다.
다만 그 사이 시뮬레이터도 현실에 가깝게 고쳤으므로(침묵은 FE 처럼 소리 기준) 이 수치는 측정 수정만의 효과는 아닙니다.

### 9-6. 남은 오류

- **1순위가 근소한 차이로 갈리는 Take** (07: 정답 부담 시선 54 · 속도 51) — 실제로도 어느 쪽이 먼저인지 애매합니다
- **빠뜨린 키워드(10초로 셈)가 짧은 시선 문제보다 앞서는 경우** (02) — 키워드 하나의 무게는 근거 없이 정한 값입니다
- **10초 미만의 짧은 대본 응시를 놓침** (15) — FE 10초 창의 한계입니다
- **센서 편향** (harsh 13) — 시선 라벨이 15% 뒤집히면 실제 0.20 이 0.31 로 측정돼, 목표 0.3 미션이 `ACHIEVED` 대신 `PARTIAL` 이 됩니다
- **harsh 의 근거 없는 지적 0.11/Take** — 대부분 시선 잡음입니다

> ### 검증된 것과 검증되지 않은 것을 구분하세요
>
> **리뷰 근거가 '정답에 가깝게' 만들어지는 것은 확인했습니다** — 단, 정답은 시뮬레이터가 아는 발표자 상태이고
> 잡음 모델은 실제 FE · Deepgram 의 오차와 다를 수 있습니다.
>
> **기준값과 실제 효과는 검증하지 않았습니다.** 70% · 350 CPM · −6dB 같은 값이 실제 발표자에게 맞는지,
> 코칭이 실제로 행동을 바꾸는지는 실제 연습 데이터가 있어야 알 수 있습니다.

### v1을 "완료"라고 부르려면

1. 실제 연습 Take 를 시나리오 형식으로 기록하고, 사람이 문제 구간 · 미션 판정 · 1순위를 라벨링해 실험의 정답 자리에 넣는다
2. 같은 지표(§9-3)와 실시간 지표(불필요한 개입률, 피드백 뒤 행동 변화율, 판단 지연)를 실제 데이터로 잰다
3. 결과로 기준값과 리뷰 근거 규칙 값(최소 구간 길이 등)을 다시 고른다 (`config_hash` 로 추적)
4. FE · BE 계약 확정 (§13) 후 `ai/releases/` 승격

---

## 10. 설계 결정

| 결정 | 이유 |
|---|---|
| **AI 는 아무것도 저장하지 않고 coach_state 를 왕복시킨다** | 재시작 · 재배포 · worker 증설에도 판단이 같다. 같은 요청 → 같은 응답이라 재생 테스트가 배포 결과와 같다. BE 에는 이미 Take 마다 연결보다 오래 사는 객체(TakeStream)가 있다 |
| **BE 가 1초마다 부른다 (AI 가 가져가지 않는다)** | AI 는 BE 주소를 모르는 구조다 (대본 파싱과 같다) |
| **판단은 규칙, 에이전트성은 되돌아보기로** | 매초 LLM 은 지연(수백 ms~수 초) · 비용(10분에 600회) · 흔들림 문제가 있다. 규칙 코치가 에이전트답지 않은 이유는 규칙이어서가 아니라 결과를 보고 행동을 바꾸지 않아서다 |
| **지시는 한 번에 하나** | 말하면서 읽는 건 같은 언어 채널이라 두 개면 둘 다 못 읽거나 말을 멈춘다. 지시끼리 충돌한다(천천히 + 핵심만 빨리). 여러 개를 동시에 말하면 무엇이 효과였는지 알 수 없어 되돌아보기가 망가진다. 대신 상태 표시(indicators)는 여러 개를 함께 준다 |
| **문구는 템플릿** | 발표 중 화면 문장이 매번 달라지거나 엉뚱한 말이 뜨면 안 된다 |
| **type(원인)과 instruction(행동)을 나눈다** | 원인과 행동이 1:1 이 아니다. type 은 리뷰 · 미션과 같은 enum 이라 미션 가중치와 리뷰 연결이 그대로 된다 |
| **문장이 끝날 때 말한다 (최대 3초)** | 말하는 도중 끼어들지 않아야 사람 코치처럼 느껴진다. 3초 상한은 끝없이 미루지 않기 위해서 |
| **효과를 못 재면 전략을 바꾸지 않는다** | 센서가 나빠 '효과 없음'으로 오판하면 멀쩡한 방법을 포기한다 |
| **script_ratio 는 보인 시간 기준** | 비율이 UNCERTAIN 까지 합쳐 1 이라, 전체 기준이면 '대본 70%'와 'UNCERTAIN 50% 초과'가 동시에 성립할 수 없어 센서 필터가 무의미해진다 |
| **'빠름'은 예상 종료, '늦음'은 r** | §5-1. r 은 끝으로 갈수록 몇 초 차이에도 크게 흔들린다 |
| **FINAL_MINUTE 는 계획이 없거나 늦을 때만** | 계획상 마지막 1분에 3번 장이 정상일 수 있다. FE 코치의 '1분 남았어요'는 계획이 없을 때의 대체 판단으로 남긴다 |
| **오디오가 멈추면 STT 도 믿지 않는다** | 못 들은 말 때문에 진행도가 낮게 잡혀 '늦다'고 오판한다. 그 장은 시간으로 잰다 |
| **이벤트를 내고, 리뷰 근거는 Take 종료 때 묶는다** | 리뷰가 코치를 직접 부르지 않는다는 설계 원칙. 중간에 끊겨도 이미 쌓인 이벤트는 남는다 |
| **리뷰 근거의 숫자 · 판정은 코드가 한다** | LLM 이 수치를 만들면 '근거 없는 수치 생성'이 생긴다(설계 문서 12). 리뷰는 인용 · 문장만 |
| **판정 층(assess)을 순수 함수로 나눈다** | 정답 데이터에 같은 판정을 돌려, 측정 오차와 규칙 오류를 나눠 평가할 수 있다 |
| **믿을 수 없던 구간은 문제로 말하지 않는다** | 근거 없는 지적이 noisy 0.50 → 0.05/Take (§9-3) |
| **창 지연을 되돌린 구간을 따로 준다** | 시작 오차 8.5초 → 2.5초. 실제로 잡힌 구간(start/end)도 함께 남겨 둘 다 볼 수 있다 |
| **부담은 평균 심각도, 순위는 영역 합계 먼저** | 최고값은 잡음에 부풀고, 시선은 장마다 나뉘어 순위가 흔들렸다 (§9-5) |
| **다음 미션 목표는 한 번에 도달할 만큼** | 0.9 → 0.3 처럼 큰 목표는 다음 Take 에서도 FAILED 가 반복돼 미션 수용률(설계 문서 12)을 떨어뜨린다 |
| **센서 판단은 지금 값과 최근 평균 중 나쁜 쪽** | 평균만 쓰면 나빠지기 시작할 때 몇 초를 더 믿고, 지금 값만 쓰면 잡음으로 잠깐 믿게 된다 |
| **효과는 '기준선 복귀' · 짧은 창 · 3초 평균으로** | 평균으로의 회귀와 창 지연이 효과 판정을 흔들었다 (§5-7) |

---

## 11. 알려진 한계

- **실제 데이터 없음** — 가상 발표자로만 확인했습니다. 실제 Deepgram 출력의 확정 지연, 실제 FE 의 비율 계산 방식, 실제 발표자의 반응은 다를 수 있습니다
- **음량 기준의 단위가 정해지지 않음** — FE 1단 코치는 절대 dBFS(−38)를 쓰고, 코치는 캘리브레이션 대비 dB 를 기대합니다. FE 와 `relative_db` 정의를 맞춰야 합니다
- **효과 측정이 짧은 창의 실시간 지표** — 8~30초 뒤 한 번 봅니다. 우연히 좋아졌거나 나빠진 것과 구분하지 못합니다. 리뷰가 확정 데이터로 다시 봐야 합니다
- **진행도는 글자 수 비례** — 대본에 없는 말(즉흥 설명)도 진행으로 셉니다. 대본을 건너뛰면 늦지 않았는데 늦은 것으로 봅니다
- **키워드는 문자열 일치** — STT 가 고유명사를 잘못 적으면(`노쇼` → `노조`) 말했는데 못 찾습니다. 그래서 기본으로 꺼 두었습니다
- **군더더기 목록은 BE 가 표시** — 단어의 `filler` 표시가 없으면 군더더기 판단이 동작하지 않습니다
- **시뮬레이터 기반 실험** — 잡음 모델(§9-2)은 실제 FE 시선 분류기 · Deepgram 의 오차 분포와 다를 수 있습니다. 리뷰 근거 규칙의 값은 실제 데이터로 다시 골라야 합니다
- **짧은 대본 응시(10초 미만)는 놓칠 수 있음** — FE 10초 창의 한계 (§9-6)
- **센서 편향은 되돌리지 않음** — 시선 분류기가 대칭으로 틀리면 비율이 0.5 쪽으로 끌려, 목표값 근처의 미션 판정이 흔들립니다
- **키워드 하나의 무게(10초)는 근거 없는 값** — 짧은 시선 문제와 순위가 바뀔 수 있습니다
- **우선순위가 100 에서 잘림** — 여러 가중치가 겹치면 100 으로 포화됩니다. 순서는 자르기 전 점수로 정하므로 선택에는 영향이 없지만, 응답의 숫자만 보면 구분되지 않습니다

---

## 12. 다음 버전

| 버전 | 무엇 | LLM |
|---|---|---|
| **v1 (지금)** | 규칙 실행 + 되돌아보기 + 문장 끝 기다리기 + 상태 표시 + 리뷰 근거 | 없음 |
| v1.1 | **코칭 계획** — Take 시작 전 LLM 이 미션 · 이전 리뷰 · 장별 대본을 읽고 `CoachingPlan`(focus · relax · 개입 상한)을 만든다. 예: "4번 장은 수치가 많아 대본을 봐도 괜찮다". 스키마 검증 + 가중치 0.5~2.0 으로 자름, 실패하면 기본 계획 | Take 당 1회 |
| v2 | **선택기** — 적격 후보가 2개 이상이고 점수 차가 작을 때만 LLM 이 고른다. 후보 밖은 못 고르고, 1초 안에 답이 없으면 `RulePolicy` 결과. 그림자 모드(기록만)로 시작해 재생 평가에서 나을 때 켠다 | 애매할 때만 |
| 이후 | 쌓인 `OUTCOME` 으로 사람마다 잘 통하는 방법을 고르는 학습형 정책(문맥적 밴딧) | — |

v1.1 · v2 를 위한 자리는 이미 있습니다 — `CoachingPlan` 은 `coach_state` 안에 있고(`initial_state(plan)`),
선택기는 `policy.Policy` 인터페이스(`select(tick, candidates)`)로 `RulePolicy` 를 바꿔 끼웁니다.

---

## 13. 팀과 정해야 할 것

| 누구와 | 무엇 |
|---|---|
| FE | 1초 요약 메시지 — `gaze.ratios`(UNCERTAIN 포함 합 1) · `current_label_ms`, `voice.relative_db` 의 정의(캘리브레이션 대비) · `silence_ms` · `audio_live`, 슬라이드 번호 · 체류 시간 |
| FE | `indicators` 를 띄울지와 모양 · FE 1단 코치(`useCoach.ts`)는 서버 연결이 끊겼을 때만 대신 도는 것을 제안 |
| BE | STT 단어에 `final` · `filler` 표시, `utterance_end_ms`(지금 버리는 `speech_final` · `UtteranceEnd` 이벤트), 최근 15초 창 |
| BE | `coach_state` 보관(TakeStream), 이벤트 저장 공간 — 지금 `live_feedbacks` 에는 type · message · 시각 · confidence 만 있어 instruction · priority · evidence 를 담을 곳이 필요 |
| BE · AI | `/v1/coach/finalize` 추가, 종료 분석 요청에 **코치 이벤트 전체와 이 Take 의 plan · missions · memory** 포함, (v1.1) `/v1/coach/plan` |
| 대본 분석 | 장별 `target_ms` · `script_chars` · `required_keywords`, 전체 `min_ms` · `max_ms` |
| 리뷰 | 공통 지표 이름(§5-2)을 `Mission.target.metric` 과 리뷰 evidence 에 같게 쓰기, 리뷰 6개 dimension 과 7개 type 의 대응 |
| 리뷰 | 리뷰 에이전트 프롬프트에 §6-5 규칙 넣기, `next_missions[].target` 을 Mission.target 으로 그대로 쓰기, 리뷰 결과를 같은 실험(§9)으로 채점할 정답 라벨 형식 |
| 팀 | 설계 문서 행동 목록에 추가된 instruction(`SPEED_UP` · `CONDENSE` · `MOVE_ON` · `RESUME` · `REDUCE_FILLER` · `MENTION_KEYWORD`) 공유, `CONTINUE` 를 '교정 효과 뒤 유지 격려'로 쓰는 것 확인 |
