
## 개요
* 이 문서는 관리자만이 접근할 수 있는 페이지인 /admin에 대한 정보를 기재합니다.
* 처음 들어가게 되면 가입자 정보와 수, Take 연습 시 정보 로그, AI 코칭 결과와 로그를 보실 수 있습니다.
* 서버 로그를 클릭하시면 release와 develop 버전에 대한 Docker Container들의 로그와 현재 상태를 확인하실 수 있습니다.

### 구성
| 경로 | 담당 | 내용 |
|---|---|---|
| `/admin/` | BE (`module/admin/static/`) | 콘솔 화면. `index.html` · `app.js` · `style.css` 세 파일입니다 |
| `/admin/api/*` | BE (`module/admin/controller.py`) | 콘솔이 부르는 조회 API. 모두 읽기 전용이며 DB를 바꾸지 않습니다 |
| `/admin/logs/` | Dozzle (`infra/docker-compose.yml`) | 서버 로그 뷰어. 보기만 가능하고 컨테이너 시작 · 중지 · 셸 접속은 꺼져 있습니다 |

* 앱 API가 아니라서 `/api` 밖에 두고, `/api/docs` 에도 싣지 않습니다.
* `/admin` 으로 들어오면 Caddy가 `/admin/` 으로 보냅니다.

### 로그인
* 브라우저 기본 로그인 창(HTTP Basic)으로 팀 공용 계정 하나를 확인합니다. 계정은 `backend/.env` 의 `ADMIN_USERNAME` · `ADMIN_PASSWORD` 입니다.
* 두 값 중 하나라도 비어 있으면 콘솔이 꺼진 것으로 보고 모든 `/admin` 요청에 404를 돌려줍니다.
* 계정이 틀리면 401과 `WWW-Authenticate: Basic` 을 돌려주고, 브라우저가 로그인 창을 다시 띄웁니다. 로그인 실패는 접속 IP와 경로만 로그에 남기고, 입력한 아이디는 남기지 않습니다.
* Dozzle에는 로그인이 없습니다. Caddy가 `/admin/logs/` 를 넘기기 전에 `GET /admin/api/auth` 로 같은 계정인지 BE에 묻습니다(forward_auth).

## 대시보드 정보
* 첫 화면은 아래 조회를 동시에 부르고, 하나가 실패해도 나머지는 그립니다. 실패한 조회는 화면 위에 모아 보여 줍니다.
* 자동으로 새로고침하지 않습니다. 오른쪽 위 **새로고침** 버튼으로 다시 불러옵니다.
* 날짜 집계는 한국 시간 기준입니다.

| 영역 | 보여 주는 것 | 데이터 |
|---|---|---|
| 요약 카드 | 가입자 수(전체 · 오늘), Take 수(전체 · 오늘), 연습 시간(합계 · 평균 · 중앙값 · 목표 시간 대비 비율) | `GET /admin/api/stats` |
| 일별 연습 · 일별 가입 | 최근 30일의 날짜별 Take 수와 가입 수. 빈 날도 0으로 표시합니다 | `GET /admin/api/stats` 의 `daily` |
| 상태 · 모드 · 대본 표시 · 종료 이유 | Take를 값별로 센 개수. 종료 이유는 끝난 Take만 세고, 사용자가 직접 끝낸 것은 `USER` 로 표시합니다 | `GET /admin/api/stats` 의 `takes.by_*` |
| 멈춘 연습 | `RUNNING` 인데 마지막 활동 뒤 이탈 대기 시간 + 10분이 지나도록 활동이 없는 Take (최대 50개) | `GET /admin/api/takes/stuck` |
| 분석이 멈춘 연습 | `ANALYZING` 인데 분석 단계가 오래 그대로이거나 리뷰가 보류된 Take (최대 50개) | `GET /admin/api/takes/stalled-analysis` |
| 실패한 연습 | 최근 `FAILED` Take 50개 | `GET /admin/api/takes?status=FAILED&limit=50` |
| 최근 연습 | 최근 Take 50개 | `GET /admin/api/takes?limit=50` |
| Take별 설문 | 문항 버전별 평균(체감 정확도 · 피드백 유용성)과 응답 목록. 페이지로 넘깁니다 | `GET /admin/api/take-surveys/summary`, `GET /admin/api/take-surveys` |
| 사용자 의견 | 마이페이지에서 받은 의견. 의견 유형으로 거르고 페이지로 넘깁니다 | `GET /admin/api/suggestions` |
| 사용자별 | 사용자마다 발표 수 · Take 수 · 총 연습 시간 · 마지막 연습 시각. 최근에 연습한 사용자부터 최대 500명 | `GET /admin/api/users` |

### 연습 기록 (Take 상세)
* Take 표에서 **보기** 를 누르면 열립니다.
* 시작 전 · 진행 중(`READY` · `RUNNING`)인 Take는 3초마다 새로 받은 전사 · 1초 기록만 이어 붙입니다. 끝난 Take는 남은 기록을 다 받으면 멈춥니다.
* 장 변경은 끊겼던 연결이 나중에 몰아서 보내 앞 시각 행이 늦게 들어올 수 있어, 이어 붙이지 않고 3초마다 전체를 다시 받습니다.

| 항목 | 보여 주는 것 | 데이터 |
|---|---|---|
| 대본 ↔ 확정 전사 | 장마다 그 Take 대본 버전의 대본과, 그 장을 띄운 동안 말한 확정 전사. 전사는 구간 시작 시각 직전의 장 변경으로 장을 정하고, 첫 장 변경 전에 말한 것은 '장 전환 전' 줄에 둡니다 | `GET /admin/api/takes/{take_id}/script`, `GET /admin/api/takes/{take_id}/live` 의 `segments` · `slide_events` |
| 확정 전사 | 문장 · 시각 · 신뢰도 · STT 세션 번호 | `GET /admin/api/takes/{take_id}/live` 의 `segments` |
| 1초 기록 | 시각 · 장 번호 · STT 상태 · 시선 · 음량 | `GET /admin/api/takes/{take_id}/live` 의 `seconds` |
| AI 결과 | `TAKE_RESULT` · `SCRIPT_COVERAGE` · 리뷰 등 AI 결과와 `meta` | `GET /admin/api/takes/{take_id}/results` |
| 코치 이벤트 | 발표 중 코치가 낸 이벤트 (최대 2,000개) | `GET /admin/api/takes/{take_id}/results` |
| 요약 | 리포트에 쓰는 `take_summaries` 값 | `GET /admin/api/takes/{take_id}/results` |
| 판정 입력 | Take 설정 · 발표 전 점검 · 장 변경 · 문장 끝 · 전사 단어별 시각 | `GET /admin/api/takes/{take_id}/inputs` |
| 판정 묶음 내려받기 | finalize 복구 요청과 AI 결과를 JSON 파일 하나로 받습니다 | `GET /admin/api/takes/{take_id}/export` |

### 발표 AI 상태
* Take 표에서 발표 제목을 누르면 열립니다.

| 항목 | 보여 주는 것 |
|---|---|
| 대본 파싱 · 장별 평가 기준 | 대본 버전마다 파싱 상태 · 오류, 평가 기준 상태, 평가 기준이 빠진 장 |
| 평가 기준 분석 | 평가 기준 버전마다 기준 수 · 분석 결과 |
| 코칭 계획 | 최근 20개. 바탕 Take · 대본 버전 · 목표 시간 · `fallback_reason` · 모델 |

### 서버 로그
* 오른쪽 위 **서버 로그 ↗** 를 누르면 새 탭에서 `/admin/logs/` (Dozzle)가 열립니다.
* 서버의 Docker 소켓을 읽어 모든 컨테이너의 로그를 실시간으로 검색 · 필터하고 상태를 볼 수 있습니다.
* 컨테이너 로그는 서비스마다 10MB × 3개까지만 남습니다.

## API 명세
* FE -> BE 사이의 요청과 응답을 기재합니다.
* 모든 요청에 관리자 계정(HTTP Basic)이 필요합니다. 없거나 틀리면 401, 콘솔이 꺼져 있으면 404입니다.
* 없는 Take · 발표를 물으면 404입니다.

### 화면 · 인증

| 메서드 | 경로 | 용도 | 응답 |
|---|---|---|---|
| GET | `/admin/` | 콘솔 화면 | `index.html` |
| GET | `/admin/app.js` · `/admin/style.css` | 콘솔 스크립트 · 스타일 | 파일 |
| GET | `/admin/api/auth` | 로그인 확인 (Caddy forward_auth 용) | 204 |

### 집계 · 목록

| 메서드 | 경로 | 쿼리 | 응답 |
|---|---|---|---|
| GET | `/admin/api/stats` | — | `{users: {total, today}, takes: {total, today, by_status, by_mode, by_script_mode, by_end_reason}, practice_time: {measured_takes, total_sec, avg_sec, median_sec, avg_goal_ratio}, daily: [{date, signups, takes}]}` |
| GET | `/admin/api/users` | — | `[{id, name, email, created_at, pitch_count, take_count, total_practice_sec, last_take_at}]` |
| GET | `/admin/api/takes` | `status` (`READY` · `RUNNING` · `ANALYZING` · `COMPLETED` · `FAILED`), `limit` (1~200, 기본 50) | Take 행 목록 (아래 'Take 행') |
| GET | `/admin/api/takes/stuck` | — | Take 행 목록 |
| GET | `/admin/api/takes/stalled-analysis` | — | Take 행 목록 |
| GET | `/admin/api/take-surveys` | `limit` (1~200, 기본 50), `offset` (기본 0) | `{items: [{id, user_id, take_id, pitch_title, take_number, accuracy_rating, usefulness_rating, comment, question_version, created_at}], next_offset}` |
| GET | `/admin/api/take-surveys/summary` | — | `[{question_version, survey_count, avg_accuracy, avg_usefulness}]` |
| GET | `/admin/api/suggestions` | `source`, `category`, `user_id`, `limit` (1~200, 기본 50), `offset` (기본 0) | `{items: [{id, user_id, content, source, category, created_at}], next_offset}` |

* **Take 행**: `{id, take_number, pitch_id, pitch_title, user_name, user_email, mode, script_mode, status, end_reason, created_at, started_at, ended_at, elapsed_sec, goal_time_sec, analysis_stage, analysis_stage_at, review_status, analysis_hold_reason}`
* 페이지가 있는 목록은 다음 페이지가 없으면 `next_offset` 이 null입니다.
* 설문에서 탈퇴한 사용자는 `user_id` 가, 지운 발표 · Take는 `take_id` 이하가 비어 있습니다.

### Take 상세 · 발표 AI

| 메서드 | 경로 | 쿼리 | 응답 |
|---|---|---|---|
| GET | `/admin/api/takes/{take_id}/live` | `after_seq` (기본 0), `after_t_ms` | `{status, segments: [{seq, stt_session_no, start_ms, end_ms, transcript, confidence, speech_final}], seconds: [{t_ms, slide_number, gaze, voice, stt_status}], slide_events: [{started_ms, slide_number}]}`. `segments` · `seconds` 는 각각 한 번에 최대 500개이며, 받은 마지막 값 뒤부터 이어 받습니다. `slide_events` 는 커서와 상관없이 매번 전체를 시각 순서로 줍니다 |
| GET | `/admin/api/takes/{take_id}/script` | — | `[{slide_number, full_content}]`. Take가 고정한 대본 버전의 장별 대본을 장 번호 순서로 줍니다 |
| GET | `/admin/api/takes/{take_id}/results` | — | `{ai_results: [{kind, revision, created_at, replayed, criteria_changed, measured_ratios, fallback_reason, model, payload, meta}], coach_events: [{seq, t_ms, kind, event_id, payload, meta}], summary}` |
| GET | `/admin/api/takes/{take_id}/inputs` | — | `{script_mode, standard_version, mission_ids, raw_data_consent, utterance_ends, slide_events: [{started_ms, slide_number}], segments: [{seq, start_ms, end_ms, final_at_ms, words}], calibration}` |
| GET | `/admin/api/takes/{take_id}/export` | — | `{take_id, raw_data_consent, metric_second_count, request, results: {<kind>: {payload, meta, revision}}}` |
| GET | `/admin/api/pitches/{pitch_id}/ai` | — | `{scripts: [{version, parse_status, parse_error, rubric_status, slide_count, rubric_slide_count, missing_rubric_slides, ...}], standards: [{version, criteria_count, values, meta, analyzed_at, ...}], coaching_plans: [{requested_at, source_take_id, target_ms, fallback_reason, model, payload, meta, ...}]}` |
