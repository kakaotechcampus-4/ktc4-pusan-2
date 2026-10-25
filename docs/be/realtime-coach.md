# 실시간 코치 (AI)

코드 경로는 `backend/src/pitch_coach_backend/` 기준이고, 명령은 `backend/` 에서 실행한다.
설치 · 실행 · 다른 API 는 [backend/README.md](../../backend/README.md) 에 있다.

계약은 `docs/ai/features/coach-agent.md` · `docs/ai/architecture.md` 다.

현재 구현 범위는 발표 중 코치와 종료 뒤 Take 결과 저장(`FINALIZING` → `SCORING`), 코칭 계획,
원자료 보관이다. `SCORING` 뒤 대본 전달도 채점 · 사용자 확인 대기 · 리뷰 · 다음 코칭 계획은
finalize 가 띄우는 백그라운드 분석(`module/take/service/analysis_runner.py`)이 잇는다. 리뷰를 따로
부르는 API 는 없다.

```
발표 중   FE metrics ─▶ CoachSession ─ /coach/evaluate ─▶ AI ─▶ coach_state · events(coach_events) · WS coach
Take 종료  stop ─▶ 분석 단계 FINALIZING ─▶ /coach/finalize (409 면 replay) ─▶ Take 결과 저장 · 분석 단계 SCORING
다음 Take  리뷰 저장 뒤 · 대본 파싱 뒤 · 제한 시간 변경 뒤 ─▶ /coach/plan ─▶ coaching_plans 에 한 행
```

| 호출 | 언제 | 시간 제한 | 다시 부르기 |
|---|---|---|---|
| `POST /coach/evaluate` | FE `metrics` 마다 | 0.3초 | 없음. 늦은 응답은 버린다 |
| `POST /coach/finalize` | Take 종료 뒤 (백그라운드) | 10초, `replay` 를 붙이면 60초 | 일시 오류면 1번. 409 `REPLAY_REQUIRED` 는 부른 쪽이 `replay` 를 붙여 다시 부른다 |
| `POST /coach/plan` | 대본 파싱 뒤 · 대본/제한 시간 변경 뒤 · 리뷰 저장 뒤 (백그라운드) | 30초 | 일시 오류면 1번. `fallback_reason` 이 있어도 정상 결과 |

- 일시 오류는 연결 실패 · 시간 초과 · 408 · 429 · 5xx 다. 다시 불러도 `X-Request-ID` 는 같다.
- 백그라운드 호출(finalize · plan)은 프로세스 전체에서 동시에 4개까지다. 긴 요청이 AI 스레드 풀을 채우면 evaluate 가 0.3초를 넘긴다.
- AI 가 없으면(연결 거부 · 404 · 503) evaluate 를 10초 쉰다. 그동안 발표 · STT 는 그대로고, 쉬는 동안의 1초 기록은 다음 요청의 창에 실린다.
- 응답은 BE 가 쓰는 칸만 검사한다. `coach_state` · `events` · `take_result` · `coaching_plan` · `meta` 는 받은 그대로 둔다
  (AI 로 다시 가거나 그대로 저장한다). `meta.schema_version` 의 major 가 1 이 아니면 쓰지 않는다.
- 로그에는 상태 코드와 `error.code` 만 남긴다. 응답 본문에는 대본 · 전사가 되돌아올 수 있다.
- 창은 AI 의 replay(`ai/research/coach-agent/src/coach/recovery.py`)와 같은 규칙으로 고른다. 시선 · 음량은 30초 안에 시작해
  그 시각까지 끝난 1초 기록, 단어 · 문장 끝은 60초다. 그래야 응답이 빠져 replay 로 만든 Take 결과가 정상 진행과 같다.
- `takes.mode` · `script_mode` 가 계약에 없는 값(`PRACTICE` · `FULL` 등)이면 싣지 않는다. 그대로 보내면 AI 가 422 로 거절한다.
- **발표 중** (`coach/session.py`): Take 에 붙어 있어 연결 · 스트림이 바뀌어도 `coach_state` 가 이어진다. 한 Take 에서
  앞 응답을 받기 전에 온 `metrics` 는 창에만 넣고 그 1초는 부르지 않는다. `buffered` 인 `metrics` 도 부르지 않는다.
  응답의 이벤트는 받은 순서대로 `coach_events` 에 쌓고(같은 `event_id` 는 한 번), 저장에 실패하면 들고 있다가 다시 넣는다.
  코치가 한 말도 `coach_events` 에만 둔다 (`live_feedbacks` 는 쓰지 않는다).
  중복은 DB 와 같은 `(take_id, t_ms)` 로 가리고 null 센서 · STT 상태까지 첫 메시지를 유지한다.
  앱을 다시 띄운 뒤에는 이미 저장된 메시지로 창과 중복 키를 복원한다. 실시간 장은 실제 전환 시각을
  그대로 쓰며, 처음 확보한 장을 0초로 보정하는 것은 replay 에만 적용한다.
- **Take 결과** (`coach/finalize.py`): 발표 중 세션의 `coach_state` · 마지막 창 · 이벤트로 부른다. 세션이 없거나(앱이 다시 뜸 ·
  되살린 Take) `coach_state` 가 없으면 처음부터 DB 의 원자료로 `replay` 를 붙인다. 409 면 `replay` 를 붙여 한 번 더.
  이벤트 · `TAKE_RESULT` · `take_summaries` 측정 칸 · `duration_sec` · 분석 단계 `SCORING` · 동의 없는 Take 의 1초 원자료
  삭제를 한 트랜잭션에 하고, Take 가 아직 `ANALYZING` · `FINALIZING` 일 때만 저장한다. 끝내 실패하면 `FAILED` 로 두고
  원자료는 남긴다. 1초 원자료가 하나도 없으면(실시간 연결 없이 끝낸 Take) 부르지 않고 `FAILED` 다. 앱이 뜰 때
  `FINALIZING` 으로 남은 Take 를 이어 간다. 떠난 Take(`CLOSED` · `ABANDONED`)는 결과를 만들지 않는다.
- **코칭 계획** (`coach/planner.py`): 응답(기본 계획 포함)마다 `coaching_plans` 에 한 행을 더한다. 다음 Take 를 시작할 때
  바탕 값(가장 늦게 끝난 `COMPLETED` Take · 그 Take 의 대본 버전 · 제한 시간과 허용 범위)이 같은 행 중 요청 시각이
  가장 늦은 것을 싣는다. 장별 목표는 대본 파싱의 `expected_ms` 를 지금 목표 시간에 맞춰 비율로 바꾼다.
- **원자료 사용 동의**: `PATCH /api/users/me {"raw_data_consent": true}`. Take 를 시작할 때 복사한다. 동의 없이 `FAILED` 로
  끝난 Take 의 1초 원자료는 7일 뒤 주기 작업이 지운다 (`coach/retention.py`).
- **리뷰의 사용자 평가 기준**은 코치와 같은 `takes.standard_version` 으로 읽는다. Take 시작 때 기준이
  없었으면 나중에 기준을 등록해도 그 Take 는 끝까지 기준 없이 리뷰한다.
- 테스트는 AI 예시 파일(`ai/research/coach-agent/src/coach/examples/`)을 직접 읽는다. backend CI 는 `backend/**` 가 바뀔 때만
  돌아서, AI 가 예시만 바꾼 PR 에서는 이 테스트가 다시 돌지 않는다.
