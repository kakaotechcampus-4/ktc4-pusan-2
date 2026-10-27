# Pitch Coach Backend

FastAPI 기반 백엔드. 발표(pitch) 연습을 녹음·실시간 세션으로 수행하고 LLM 피드백을 제공한다.

## 기술 스택

| 영역 | 선택 |
|---|---|
| 언어 / 패키지 관리 | Python 3.14, uv |
| 웹 프레임워크 | FastAPI |
| ORM / 마이그레이션 | SQLAlchemy 2.x, Alembic |
| DB | PostgreSQL 17 (드라이버 psycopg 3) |
| 인증 | Google OAuth 2.0 (Authorization Code + PKCE) + JWT (PyJWT) |
| 비동기 작업 / 캐시 | Redis (OAuth state 저장에 사용 중). Celery 는 예정 |
| 실시간 STT | Deepgram Nova-3 스트리밍 (`ko`). FE → BE → Deepgram, WebSocket |
| 테스트 / 린트 | pytest, ruff |

## 시작하기

```bash
cd backend
uv sync                      # .python-version 의 Python 과 의존성을 자동으로 맞춘다
cp .env.example .env         # 값을 채운다. JWT_SECRET_KEY 는 openssl rand -hex 32
uv run alembic upgrade head  # DB 가 떠 있는 상태에서. 스키마를 최신으로 맞춘다
```

| 작업 | 명령 |
|---|---|
| 개발 서버 | `uv run fastapi dev src/pitch_coach_backend/main.py` |
| 테스트 | `uv run pytest` |
| 린트 / 포맷 | `uv run ruff check` / `uv run ruff format` |
| 마이그레이션 생성 | `uv run alembic revision --autogenerate -m "메시지"` |
| 마이그레이션 적용 | `uv run alembic upgrade head` |

로컬 DB 는 인프라 담당의 docker-compose 를 사용한다. `.env.example` 의 `DATABASE_URL` 기본값이 그 컨테이너와 맞춰져 있다.

### 테스트

- 테스트는 `DATABASE_URL` 의 DB 이름에 `_test` 를 붙인 DB 를 쓴다 (예: `pitch_coach_test`). 없으면 `conftest.py` 가 만든다. 개발 DB 는 건드리지 않는다.
- 각 테스트는 트랜잭션 안에서 실행되고 끝나면 롤백된다. service 가 `commit()` 을 호출해도 savepoint 로 처리되므로 테스트 사이에 데이터가 남지 않는다.
- `client` fixture 는 `get_db` 를 테스트 세션으로 바꿔 끼운 `TestClient` 다. HTTP 테스트는 이걸 쓴다.
- `tests/auth` · `coach` · `realtime` · `script_parse` · `take` 는 그 폴더의 `conftest.py` 가 같이 쓰는 fixture 와
  가짜를 둔다. 가짜는 함수가 정의된 곳이 아니라 **호출부가 실제로 참조하는 모듈**에 끼운다
  (예: `realtime/connection/handshake.py` 의 `user_service`, `pitch/service/presentation.py` 의 `upload`).
- 인증 테스트는 **Redis 15번 DB** 를 쓰고 매번 비운다. 개발용 0번은 건드리지 않는다.
- `client` fixture 의 base_url 이 `https://testserver` 인 이유: 쿠키를 `Secure` 로 심으므로
  http 로는 httpx 가 쿠키를 되돌려 보내지 않는다. 설정을 낮추는 대신 https 를 쓴다.
- 구글 연동 테스트는 **외부 HTTP 통신만 모킹하고 검증 로직은 실제로 실행**한다.
  테스트용 RSA 키로 진짜 RS256 토큰을 서명해 위조·만료·`aud` 불일치를 실제로 거부시킨다.
- CI 는 PostgreSQL·Redis 서비스와 `DATABASE_URL` 만 제공하면 된다.
  `JWT_SECRET_KEY` 와 구글 값은 conftest 가 테스트용 값을 넣는다.

## 서비스 평점·의견

홈 평점 팝업은 본인의 완료 Take와 `TAKE_RESULT`가 남아 있는 미평가자에게 최초 포함 최대 3회 요청한다.
별점은 사용자당 한 건이며 팝업 의견과 마이페이지 의견은 공통 테이블에 별도로 저장한다.
마이페이지 의견은 리포트·평점·노출 횟수와 관계없이 여러 번 보낼 수 있다.
회원 탈퇴 뒤에도 평점·의견은 보관하고 작성자 `user_id`만 NULL로 바꾼다. 표시 승인 기록은 삭제한다.

| API | 계약 |
|---|---|
| `POST /api/service-feedback/impressions` | `{display_request_id: UUID}`. 승인 시 `should_open=true`, 사용자 전체 최대 3회. 같은 ID 재시도는 추가 집계하지 않음 |
| `GET /api/service-feedback/me` | `has_report`, `submitted`, `display_count`, `display_limit`, `should_prompt`. 읽기 전용이며 홈의 필수 사전 요청은 아님 |
| `POST /api/service-feedback` | `{rating: 정수 1~5, suggestion?: string/null}`. 최초 201, 같은 내용 재시도 200, 다른 내용 409 |
| `POST /api/suggestions` | `{request_id: UUID, content: string}`. 최초 201, 같은 ID·내용 200, 같은 ID·다른 내용 409 |

모두 기존 Bearer 인증을 사용한다. 의견은 앞뒤 공백 제거 후 최대 2,000자다. 마이페이지는 빈 의견을 거절한다.
서버가 표시를 승인한 뒤 화면을 열며 응답 유실 시 같은 표시 ID를 재사용한다. 승인 뒤 탭 종료 시 실제로 열지
못했어도 사용한 1회로 남을 수 있다. 세 번째 팝업의 평점 제출·재시도는 계속 허용한다.

운영자는 기존 Basic 인증의 `/admin/`에서 별점·의견을 확인한다. 조회 API는
`GET /admin/api/service-feedbacks`, `GET /admin/api/suggestions`이며 `limit` 1~200, `offset` 0 이상을 받는다.
의견 조회는 `source=RATING_POPUP|MY_PAGE` 필터를 제공한다.
자세한 요청/응답·오류·ERD·FE 연동은 [서비스 평가 문서](../docs/be/service-feedback-plan-540.md)에 있다.

## 홈 Take 응답

`GET /api/pitches/`의 `pitches[].takes[]`는 기존 필드에 `status`와 `end_reason`을 추가한다.

- `status`: `READY` · `RUNNING` · `ANALYZING` · `COMPLETED` · `FAILED`
- `end_reason`: `SILENCE` · `TIME_LIMIT` · `ABANDONED` · `CLOSED` 또는 `null`
- `null`은 수동 종료 또는 아직 종료 이유가 없는 경우다. `ABANDONED`·`CLOSED`는 떠난 Take를
  구분하며 `SILENCE`·`TIME_LIMIT`은 자동 종료 이유다. 상태는 `status`로 판단한다.

FE 표시 문구는 협의가 필요하다. `READY`는 시작 전, `RUNNING`은 연습 중, `ANALYZING`은
분석 중, `COMPLETED`는 완료로 제안한다. `FAILED` 중 `ABANDONED`·`CLOSED`는 중단,
나머지는 실패로 표시할 수 있다. 점수가 없다는 이유만으로 분석 중으로 판단하지 않는다.

`score`·`delta` 계산은 유지한다. `delta`는 바로 이전 Take와 현재 Take에 모두 점수가 있을 때만
계산하며 어느 한쪽이라도 점수가 없으면 `null`이다. Take 목록 API에는 변경이 없다.

## 폴더 구조

```
backend/
├── pyproject.toml
├── uv.lock
├── .python-version
├── .env                          # 로컬 전용, 커밋하지 않는다
├── .env.example                  # 환경변수 계약서. 키를 추가하면 여기도 갱신한다
├── alembic.ini                   # sqlalchemy.url 은 비워둔다 (env.py 가 settings 에서 주입)
├── alembic/
│   ├── env.py                    # entities.py 를 import 해서 모든 테이블을 인식시킨다
│   ├── script.py.mako            # 새 마이그레이션 파일 템플릿
│   └── versions/                 # 자동 생성. 날짜 접두어로 정렬. ruff 검사 제외
│
├── dev/
│   ├── oauth-test.html           # 로그인 흐름 수동 확인용. 앱이 서빙하지 않는다
│   ├── stt-send-pcm.py           # PCM 파일을 WebSocket 으로 흘려 전사를 보는 스크립트
│   └── stt-test.html             # 마이크로 실시간 STT 를 확인하는 페이지 (3000 포트로 연다)
│
├── tests/
│   ├── conftest.py               # 테스트 DB 자동 생성, 트랜잭션 격리 세션, TestClient fixture
│   ├── coach_fakes.py            # 코치 AI · 저장소 가짜 (여러 코치 테스트가 같이 쓴다)
│   ├── test_health.py
│   ├── test_security.py          # JWT 발급/검증
│   ├── test_exceptions.py        # 에러 응답 형식 계약
│   ├── test_user_entity.py
│   ├── test_auth_entity.py       # oauth_accounts·refresh_tokens 제약
│   ├── test_google_oauth.py      # ID Token 검증 (실제 RSA 서명으로)
│   ├── test_state_store.py       # Redis state 1회 소비
│   ├── test_realtime_stt_adapter.py  # Deepgram 파싱 + 가짜 서버 왕복
│   ├── test_realtime_auto_end.py # 말 없음 판단과 자동 종료 조건 (숫자만)
│   ├── test_coach_session.py           # 발표 중 판단 · 상태 · 이벤트 처리 (AI · 저장소는 가짜)
│   ├── test_coach_finalize.py          # 종료 뒤 Take 결과 생성 · 실패 · 재시작 복구 (가짜)
│   ├── test_coach_planner.py           # 코칭 계획 요청 · 중복 방지 · 실패 처리 (가짜)
│   ├── test_coach_retention.py         # 원자료 정리 주기 작업 · 오류 복구 (가짜)
│   ├── test_coach_tasks.py             # 백그라운드 작업 보관 · 종료 · 실패 로그
│   ├── test_coach_start_storage.py     # 시작 때 미션 · 평가 기준 · 동의 고정, 리뷰 진입 조건
│   ├── test_coach_inputs_storage.py    # 장 변경 · 문장 끝 신호 · 종료 상태 저장
│   ├── test_coach_context_storage.py   # 저장 자료로 코치 입력 복원 · 이벤트 순서 · 중복 제거
│   ├── test_coach_finalize_storage.py  # Take 결과 · 요약 · 종료 상태의 원자적 저장
│   ├── test_coach_planner_storage.py   # 코칭 계획 저장 · 바탕 값과 요청 시각에 따른 선택
│   ├── test_coach_retention_storage.py # 동의 없는 실패 Take의 7일 지난 원자료 삭제
│   ├── test_coach_triggers.py          # 대본 파싱 · 시간 변경 · 리뷰 뒤 코칭 계획 요청
│   ├── test_coach_consent.py           # 원자료 사용 동의 조회 · 변경 API
│   ├── test_presentation_date.py       # 발표자료 상세의 한국 날짜 · 자정과 09시 경계
│   ├── test_<domain>.py          # 도메인이 늘면 같은 이름 규칙으로
│   │
│   │   # 커진 테스트는 시나리오별 폴더로 나눈다. 같이 쓰는 fixture · 가짜는 그 폴더의 conftest.py
│   ├── auth/                     # 가입·재로그인·회전·재사용 탐지 · 쿠키·CSRF·리다이렉트 · 탈퇴
│   ├── coach/                    # 코치 AI 호출 · 응답 읽기 · 요청 · 창 · replay · 코칭 계획 (AI 예시 파일을 읽는다)
│   ├── realtime/                 # WS 인증·프레임·offset·재연결·종료 순서·자동 종료·떠남·1초 원자료·코치 (Deepgram 은 가짜)
│   ├── script_parse/             # 대본 업로드 → 파싱 → 조회 · 재시도 · 경합 · runner
│   └── take/                     # Take PUT · abandon · 삭제 · 입력 검증
│
└── src/pitch_coach_backend/
    ├── main.py                   # FastAPI 앱 생성, /api 라우터에 컨트롤러 등록, /api/health. lifespan 이 앱 수명 작업을 건다
    ├── entities.py               # 모든 도메인 entity 를 한 곳에서 import (Alembic·main 이 사용)
    │
    ├── core/                     # 설정·인프라 연결. 도메인 로직을 두지 않는다
    │   ├── config.py             # pydantic-settings. .env 는 backend/ 기준으로 읽는다
    │   ├── database.py           # 엔진, 세션, Base, get_db, UUIDPrimaryKeyMixin, TimestampMixin, 요청 밖 짧은 세션 · 일시 오류 가르기
    │   ├── retry.py              # DB 가 잠깐 안 될 때만 다시 하기 (백그라운드 작업)
    │   ├── row_buffer.py         # 행을 모았다 한 번에 저장 · 실패하면 간격을 두고 다시 넣기 (1초 원자료 · 코치 이벤트)
    │   ├── tasks.py              # 백그라운드 태스크 보관 · 실패 로그 · 앱 종료 때 끊기
    │   ├── ai_slots.py           # 백그라운드 AI 호출 동시 개수 (합계 4, 그 안에서 대본 등록 2)
    │   ├── security.py           # JWT 발급/검증
    │   ├── exceptions.py         # 공통 예외 베이스 + 에러 응답 핸들러
    │   ├── logging.py            # 앱 로거(pitch_coach_backend.*) 레벨 · 형식. 앱이 뜰 때 건다
    │   └── redis.py              # Redis 클라이언트 + get_redis 의존성
    │
    ├── module/                   # 도메인. 기본은 controller · service · repository · entity · dto · exception 파일
    │   ├── hooks.py              # HTTP 요청이 발표 중 처리 · 코치에 알릴 자리. 구현은 main.py 가 꽂는다
    │   ├── auth/                 # Google OAuth, 토큰 발급·회전, get_current_user
    │   │   ├── controller.py     # google/start·callback, refresh, logout. 쿠키 설정
    │   │   ├── service.py        # 로그인·회전·로그아웃. 트랜잭션 경계
    │   │   ├── repository.py
    │   │   ├── entity.py         # OAuthAccount, RefreshToken
    │   │   ├── dto.py
    │   │   ├── dependencies.py   # get_current_user, verify_csrf
    │   │   ├── exception.py
    │   │   ├── google.py         # 구글 어댑터. DB·Redis 를 모른다
    │   │   └── state_store.py    # state·nonce·PKCE 의 Redis 1회 소비
    │   ├── user/                 # User. 기본 파일 구조 그대로
    │   ├── admin/                # 관리자 콘솔(/admin). 다른 도메인 표를 읽어 모은다
    │   ├── pitch/                # 발표 · 발표자료 · 대본 · 평가 기준 · 코칭 계획 행
    │   │   ├── controller.py · repository.py · entity.py · dto.py · dependencies.py · exception.py
    │   │   ├── service/          # 업무별로 나눈다. __init__.py 는 비워 두고 호출부는 하위 모듈을 import 한다
    │   │   │   ├── pitch.py              # 발표 생성 · 목록 · 자료 모음 · 준비 화면 · 수정 · 삭제 · 코칭 계획 행
    │   │   │   ├── presentation.py       # 발표자료(PDF) 업로드 · 조회
    │   │   │   ├── script.py             # 대본 검사 · 생성 · 파싱 상태 · 재파싱 · 결과 저장 · 장별 평가 기준 상태 · STT 용어
    │   │   │   ├── standard.py           # 평가 기준 저장 · 나누기 · 조회
    │   │   │   ├── script_parse_runner.py   # 업로드 뒤 BackgroundTasks 로 도는 파싱 작업. 짧은 세션으로 저장
    │   │   │   └── script_rubric_runner.py  # 파싱 뒤 장별 평가 기준 작업. 앱이 뜰 때 이어 간다
    │   │   └── adapter/          # 외부 연동. DB 를 모른다
    │   │       ├── storage.py            # S3 업로드 · presigned URL · prefix 삭제
    │   │       ├── presentation_file.py  # PDF 열기 · 쪽수
    │   │       ├── script_parser.py      # AI 대본 파서 HTTP. 응답 검증·변환·재시도
    │   │       ├── script_rubric.py      # AI 장별 평가 기준 HTTP
    │   │       └── standard_parser.py    # AI 평가 기준 분석 HTTP
    │   └── take/                 # 발표 1회 수행 기록
    │       ├── controller.py · repository.py · entity.py · dto.py · dependencies.py · exception.py
    │       ├── service/
    │       │   ├── take.py               # 생성 · 수정 · 삭제 · 소유권 조회 · 장치 점검 · Take 목록 · 리포트
    │       │   ├── lifecycle.py          # mark_* · 창 닫힘 · 진행 중 Take 조회 · 떠남 · 시작하지 않은 Take 기준
    │       │   ├── record.py             # 전사 커서 · 확정 전사 · 1초 원자료 · 문장 끝 신호 저장
    │       │   ├── review.py             # 리뷰 요청 조립 · 호출 · 결과 저장 (한 트랜잭션)
    │       │   ├── coverage.py           # 대본 전달도 분석 (SCORING) · 재시도 · 병합
    │       │   ├── confirmation.py       # 대본 전달도 확인 답 받기 · rescore 반영 · 건너뛰기 (AWAITING_CONFIRMATION)
    │       │   └── analysis_runner.py    # Take 결과 뒤 채점 → 확인 대기 → 리뷰 → 코칭 계획 (백그라운드). 앱이 뜰 때 이어 간다
    │       ├── coach_service.py      # 코치 연동의 DB 일. take 모듈의 모양(dto)으로 넘기고 받는다
    │       ├── coach_repository.py   # 코치 이벤트 · 장 변경 · Take 결과 · 미션 행
    │       ├── ai_response.py        # 리뷰 · 대본 전달도 응답 검증
    │       ├── measures.py           # Take 결과에서 지표 꺼내기 · 개수 반올림 (리포트 · take_summaries 공통)
    │       └── ready_sweep.py        # 시작하지 않은 Take 를 끝내는 주기 작업
    │
    ├── realtime/                 # WebSocket. entity 를 갖지 않고 저장은 도메인 service 에 위임
    │   ├── controller.py         # /ws/takes/{take_id} 엔드포인트
    │   ├── dependencies.py       # get_stt_adapter · get_transcript_store (테스트가 가짜로 바꿔 끼운다)
    │   ├── dto.py                # BE ↔ FE 메시지 · 오류 코드
    │   ├── audio.py              # 오디오 규격(16 kHz·mono·16-bit)과 무음 생성. 잎 모듈
    │   ├── event_ingestion.py    # 오디오 프레임 헤더 검증, 순서·중복, 갭 계산. I/O 없음
    │   ├── live_takes.py         # module/hooks.py 의 발표 중 Take 쪽 구현 (지움 · 창 닫음 · 끝내기 PUT)
    │   ├── connection/           # FE 연결 하나. 재접속하면 새로 만든다
    │   │   ├── session.py        # 연결 하나의 수명: 접속 허용 → 스트림에 붙이기 → 프레임 전달 → stop
    │   │   └── handshake.py      # Origin · 인증 · Take 존재·소유·상태 검사 · 거절
    │   ├── stream/               # Take 별 스트림. **연결보다 오래 산다**
    │   │   ├── registry.py       # 연결 붙이기(attach) · 떠난 Take 끝내기 · 앱 시작 복구 · 종료 정리
    │   │   ├── finish.py         # 사용자 · 자동 종료로 Take 끝내기: 마지막 창 · 원자료 저장 · DB 에 적기 · Take 결과
    │   │   ├── state.py          # 스트림 맵 · attach 잠금 · 끝낸 Take · Take 별 기록. registry · finish 만 쓴다
    │   │   ├── abandon.py        # 떠남 · 창 닫힘 타이머. 만료되면 받은 콜백을 부른다
    │   │   ├── runtime.py        # TakeStream: 수명 상태 기계 · grace · 종료 조율. 입력은 아래 부품에 넘긴다
    │   │   ├── record.py         # TakeRecord · StreamHost · CoachDeps. 스트림과 위 파일들의 경계
    │   │   ├── client.py         # 붙어 있는 연결 · 이전 연결 쫓아내기 · FE 로 보내기
    │   │   ├── audio_queue.py    # Deepgram 으로 보낼 오디오 큐. 차면 오래된 것부터 버린다
    │   │   ├── auto_end.py       # 자동 종료 조건(말 없음·최대 시간)과 최대 시간 타이머. 네트워크를 모른다
    │   │   └── heard.py          # 끊김 없이 받은 오디오 범위 → 1초 원자료의 STT 상태
    │   ├── stt/                  # 외부 음성 인식. FE 연결 · Take 상태를 모른다
    │   │   ├── adapter.py        # Deepgram WebSocket 어댑터. DB 를 모른다
    │   │   ├── loop.py           # Deepgram 접속 · 재접속 · 송수신 · 세션별 시간 기준 · 무음 채우기
    │   │   ├── keyterms.py       # filler + 대본 용어를 Deepgram keyterm 한도(100개·500토큰) 안에서 조립
    │   │   └── fillers.py        # 한국어 군더더기 목록. keyterm boosting + 후처리 사전의 단일 소스
    │   └── storage/              # 기록 버퍼와 도메인 저장 연결
    │       ├── store.py          # 저장소 Protocol · DB 구현. 짧은 DB 세션을 열어 take service 에 넘긴다
    │       ├── transcripts.py    # 전사 번호 · 저장 재시도 · 화면에 못 보낸 final
    │       └── metrics.py        # 1초 원자료 · 장 변경을 모았다 저장 (core/row_buffer.py)
    │
    ├── coach/                    # AI 실시간 코치 연동. entity 는 갖지 않는다 (DB 는 take/coach_service)
    │   ├── client.py             # /coach/evaluate·finalize·plan HTTP. 재시도·409
    │   ├── replies.py            # AI 응답 모양 · 받은 그대로 두는 칸 · take_summaries 측정 값
    │   ├── requests.py           # 요청 만들기 (시간 계획·미션·Take 상수·요청 모양). 순수 함수
    │   ├── windows.py            # 창 · 지금 장 · replay 입력. 순수 함수
    │   ├── coaching_plan.py      # 코칭 계획 행 · 바탕 값 · 고르기 · evaluate 에 실을 계획
    │   ├── session.py            # 발표 중 코치 (Take 하나). evaluate · 이벤트 저장 · 확정 시각 · 마지막 창
    │   ├── finalize.py           # Take 결과 (백그라운드). 409 면 replay, 앱이 뜰 때 · 오래 남으면 이어 가기
    │   ├── planner.py            # 코칭 계획 요청 · 저장 (백그라운드). module/hooks.py 의 코치 쪽 구현
    │   ├── retention.py          # 동의 없이 FAILED 로 끝난 Take 의 1초 원자료를 7일 뒤 지우는 주기 작업
    │   ├── background.py         # 앱이 뜰 때 · 내릴 때 위 작업을 띄우고 멈춘다
    │   ├── store.py              # 코치 저장소 (짧은 DB 세션 + threadpool). take/coach_service 값을 요청 모양으로
    │   └── dependencies.py       # get_coach_client · get_coach_store. 프로세스에 하나
    │
    ├── agent/                    # LLM 어댑터. module 을 import 하지 않는다
    │   ├── client.py             # 모델 호출·타임아웃·재시도. 동기 함수로 작성
    │   ├── realtime_context_builder.py
    │   └── review_context_builder.py
    │
    └── task/                     # Celery
        ├── celery_app.py         # 브로커 설정, 태스크 등록
        └── review_task.py        # feedback service 를 호출하는 얇은 껍데기
```

폴더는 담당자가 작업을 시작할 때 만든다. 빈 폴더를 미리 커밋하지 않는다.

### 계층을 나누는 기준

- 기본은 `controller.py` · `service.py` · `repository.py` · `entity.py` · `dto.py` · `exception.py` 파일이다.
  모든 모듈의 폴더 깊이를 맞추지 않는다 (`user` · `auth` · `admin` 은 파일 그대로다).
- 서로 다른 업무가 섞여 읽기 어려운 계층만 나눈다. service 를 `service/` 로 나눠도 controller · repository ·
  entity · dto 는 파일로 둔다. 나누면 원래 `service.py` 는 지우고, `service/__init__.py` 는 비워 두고, 호출부는
  `from ...service import script as script_service` 처럼 하위 모듈을 바로 부른다. 함수를 전달하기만 하는 파일은
  두지 않는다.
- 줄 수는 검토 신호이지 목표가 아니다. 테이블마다 파일을 만들지 않는다 — 밀접한 entity 는 한 파일, 여러 표를
  같이 읽는 조회도 한 repository 에 둔다.
- 외부 연동(HTTP · S3 · 파일)은 업무 로직과 나눈다 (`pitch/adapter/`). 연결 설정은 `core/` 다.
- 한 업무의 DB 변경은 같은 세션에서 하고 service 가 커밋을 정한다. repository 마다 커밋하지 않는다.
  리뷰 결과 · 미션 · 요약 · Take 완료 저장(`take/service/review.py`)은 한 트랜잭션이다.
- realtime 은 처리 대상과 수명으로 나눈다: 연결 하나(`connection/`) · Take 별 스트림(`stream/`, 연결보다 오래 산다)
  · 외부 STT(`stt/`) · 기록 저장(`storage/`). 여러 폴더가 쓰는 메시지 · 오디오 규격은 realtime 바로 아래에 둔다.
- 테스트는 운영 폴더를 그대로 따라가지 않고 시나리오로 나눈다. 같이 쓰는 fixture 는 그 폴더의 `conftest.py`,
  여러 폴더가 같이 쓰는 가짜는 `tests/coach_fakes.py` 처럼 따로 둔다.

자세한 기준과 적용 결과는 [docs/be/file-split-plan.md](../docs/be/file-split-plan.md) 에 있다.

## 파일 이름 규칙

Spring / NestJS 용어를 쓴다. FastAPI 문서·오픈소스와의 대응은 아래와 같다.

| 이 프로젝트 | FastAPI 관례 | 내용 |
|---|---|---|
| `controller.py` | `router.py` | `APIRouter` 와 엔드포인트. 요청을 받아 service 에 넘기고 응답을 만든다 |
| `service.py` | `service.py` | 비즈니스 로직. 트랜잭션 경계 |
| `repository.py` | `crud.py` | DB 쿼리. SQLAlchemy 세션을 직접 다루는 유일한 곳 |
| `entity.py` | `models.py` | SQLAlchemy 모델 (테이블) |
| `dto.py` | `schemas.py` | Pydantic 모델 (요청/응답) |
| `exception.py` | `exceptions.py` | 도메인 전용 예외 |
| `dependencies.py` | `dependencies.py` | `Depends` 로 주입하는 함수 (auth 의 `get_current_user`) |

## Entity 규칙

- PK 는 `UUIDPrimaryKeyMixin` (UUIDv7). 모든 entity 가 같은 PK 타입을 쓴다.
- `created_at` / `updated_at` 은 `TimestampMixin`. 직접 선언하지 않는다.
- 상속 순서는 `class X(UUIDPrimaryKeyMixin, TimestampMixin, Base)`.
- 테이블 이름은 복수형 snake_case (`users`, `oauth_accounts`).
- 필요할 때 컬럼을 추가한다. "나중에 쓸 것 같은" 컬럼은 넣지 않는다. 마이그레이션 한 번이면 된다.

## 의존 방향

이 규칙이 순환 import 를 막는다. 어기고 싶어지면 구조를 다시 논의한다.

- **도메인 안**: `controller → service → repository → entity`. 역방향 없음. controller 가 repository 를 직접 부르지 않는다.
- **도메인 사이**: 상대 도메인의 **service 만** 부른다. 다른 도메인의 repository·entity 를 import 하지 않는다.
- **realtime, coach, task → module**: 도메인 service 를 호출한다. module 은 controller 까지 포함해 realtime·coach·task
  를 import 하지 않는다. HTTP 요청이 그쪽에 알릴 일(Take 지움 · 끝내기 PUT · 리뷰 뒤 코칭 계획 등)은
  `module/hooks.py` 를 거쳐 부르고, 실제 구현은 `main.py` 가 꽂는다 (realtime 쪽 구현은 `realtime/live_takes.py` 가
  `stream/registry.py` · `stream/finish.py` 의 공개 함수를 부른다).
- **realtime → coach**: 발표 중 코치를 부른다. coach 는 realtime 을 모른다 (같이 쓰는 저장 버퍼는 `core/row_buffer.py`).
- **realtime 안**: `controller → connection/session → connection/handshake`, `session → stream/registry · stream/finish
  → stream/state → stream/runtime → stt/loop → stt/adapter`, `runtime → storage/transcripts · storage/metrics →
  storage/store → take service`.
  - stt · storage 는 connection · stream 을 import 하지 않는다. STT 이벤트는 받은 콜백으로 돌려준다.
  - runtime 과 그 부품(client · audio_queue · heard · auto_end)은 registry · finish · state 를 import 하지 않는다.
    Take 별 기록은 registry 가 만들어(`state.take_record`) 넘기고, 알릴 일은 `record.StreamHost` 로 넘긴다.
    부품은 `TakeStream` 도 모르고 값과 콜백만 받는다. abandon 도 registry 를 모르고 만료 콜백을 받는다.
  - 옮긴 이름을 옛 모듈에서 다시 내보내지 않는다. 테스트의 monkeypatch 가 옛 이름에만 걸린다.
- **registry 수명**: 레지스트리 상태(`stream/state.py`)는 프로세스에 하나이고 HTTP 요청 · WebSocket 연결 · 재연결이
  같은 것을 쓴다. 연결마다 새로 만드는 것은
  `RealtimeSession` 뿐이다. `main.py` 의 `lifespan` 이 뜰 때 떠난 Take 타이머를 다시 걸고(`arm_abandoned_takes`),
  내릴 때 스트림 drain · 기록 flush · 백그라운드 태스크 정리를 기다린다(`stop_all`). 테스트는
  `tests/realtime/conftest.py` 가 테스트마다 레지스트리 상태를 비운다.
- **운영 전제**: 앱 하나 · uvicorn 워커 하나 · 프로세스 하나. 스트림 · 잠금 · 끝낸 Take 기록과 WebSocket 은 프로세스
  메모리에 있어서 워커를 늘리면 레지스트리가 여럿 생긴다. 늘리려면 스트림 소유권 · 요청 라우팅 · 종료 통지 · 공유
  상태를 따로 설계한다.
- **agent**: 어디서든 불려가기만 한다. module 을 import 하지 않는다.
- **core**: 모두가 import 한다. core 는 module·realtime·agent·task 를 import 하지 않는다 (core 안에서는 서로 import 가능).
- **entity ↔ dto 변환**: service 가 entity 를 반환하고 controller 의 `response_model` 이 변환한다 (`model_config = ConfigDict(from_attributes=True)`). 도메인마다 다르게 하지 않는다.

## URL 규칙

**모든 경로는 `/api` 아래에 둔다.** `infra/Caddyfile` 이 `/api/*` 만 백엔드로 넘기고 나머지는
프론트로 보내기 때문이다. 접두어를 빠뜨린 경로는 404 가 아니라 프론트 화면을 받게 되어
원인을 찾기 어렵다.

- `/api` 는 `main.py` 의 `API_PREFIX` 한 곳에만 적는다. controller 는 `prefix="/users"` 처럼
  설계 문서에 적힌 경로만 선언하고 `main.py` 의 `api` 라우터에 붙는다.
- **버전 접두어(`/v1`)는 쓰지 않는다.** 설계 문서 경로에 `/api` 만 앞에 붙는 형태다.
  `/auth/google/start` -> `/api/auth/google/start`
- Swagger·OpenAPI 도 같은 이유로 `/api/docs`, `/api/redoc`, `/api/openapi.json` 이다.

`tests/test_health.py` 가 OpenAPI 경로와 문서 URL 전부를 검사해 접두어 누락을 막는다.

## 인증

Google OAuth 2.0 (Authorization Code + PKCE). **백엔드가 시작·콜백·code 교환을 전부 담당한다.**
프론트는 구글과 직접 통신하지 않고 `client_secret` 도 보지 않는다.

```
GET  /api/auth/google/start     구글로 302. state·nonce·PKCE 를 Redis 에 저장
GET  /api/auth/google/callback  ID Token 검증 -> 쿠키 심고 프론트로 302
POST /api/auth/refresh          Access 발급 + Refresh 회전. Access 를 얻는 유일한 경로
POST /api/auth/logout           현재 세션 폐기
```

| 토큰 | 형식 | 수명 | 어디에 |
|---|---|---|---|
| Access | JWT | 15분 | 응답 바디 -> 프론트 **메모리만** -> `Authorization: Bearer` |
| Refresh | 난수 (JWT 아님) | 14일 | HttpOnly 쿠키. DB 에는 **해시만** |

- 사용자 식별 키는 이메일이 아니라 검증된 ID Token 의 `sub` 다.
- 이메일이 같아도 기존 계정에 **자동으로 연결하지 않는다** (`409 CONFLICT`).
- `refresh` 는 호출할 때마다 Refresh 를 회전시킨다. 폐기된 토큰이 다시 오면
  유출로 보고 그 세션(`device_id`)의 토큰을 전부 끊는다. 다른 기기는 살아남는다.
- 쿠키로 인증하는 `refresh`·`logout` 은 **출처 대조 + CSRF 토큰**(double-submit)으로 막는다.
  출처는 `Origin`, 없으면 `Referer` 순으로 보고 **둘 다 없으면 막는다** (OWASP 권장).
  프론트는 `csrf_token` 쿠키를 읽어 `X-CSRF-Token` 헤더에 실어야 한다.
- 구글이 준 토큰은 신원 확인에만 쓰고 **저장하지 않는다.** Google refresh token 은 요청하지 않는다.

OAuth 요청의 `state`·`nonce`·PKCE verifier는 콜백까지 필요한 임시 값이라 Redis에 10분만 둔다
(`module/auth/state_store.py`의 `STATE_TTL_SECONDS`). 사용자가 돌아오지 않으면 TTL로 자동 삭제된다.
콜백에서는 `GETDEL` 한 번으로 읽기와 삭제를 원자적으로 처리해 같은 state의 중복 콜백 중 하나만
소비할 수 있다. 조회와 삭제를 따로 하면 동시에 온 콜백 둘이 같은 값을 읽을 수 있다.

로컬은 프론트(3000)와 백엔드(8000)가 cross-origin 이라 CORS 와 `credentials: 'include'` 가
필요하다. 배포에서는 Caddy 가 같은 origin 으로 묶어 그 문제가 사라진다.
자세한 흐름과 프론트 연동 코드는 `docs/oauth-architecture.md` 에 있다 (로컬 문서).

## 홈 발표 목록

`GET /api/pitches/` 는 다가오는 발표(오늘 포함) → 날짜 없는 발표 → 지난 발표 순서로 준다.
다가오는 발표는 가까운 날짜부터, 지난 발표는 최근에 지난 것부터, 같은 날짜면 최근에 만든 발표부터다.
오늘은 한국 날짜다. FE 는 이 순서를 그대로 보여 준다.

## Take 목록

`GET /api/takes/` 는 내 모든 발표의 Take 를 시작 시각 최신순으로 준다 (Take 기록 화면).
`?pitch_id=` 를 주면 그 발표 것만 준다 (Take 비교 화면). 없거나 남의 발표면 404 다.

- 시작하지 않은 Take(장치 점검에서 버려진 것)는 빠진다. 시작 시각은 첫 실시간 연결이 붙을 때 BE 가
  적고, 끝내기 PUT 의 `started_at` 이 오면 그 값으로 바뀐다
- 시선 비율 · 말 속도 · 군더더기 수는 `take_summaries` 에서, 시선 엔진 버전은 장치 점검 결과에서 읽는다.
  없으면 null 이다. 미션은 Take 를 시작할 때 실은 수와 그중 리뷰가 달성으로 판정한 수다

## 연습 시작 (장치 점검 · Take 생성)

```
GET  /api/pitches/{pitch_id}/prepare   이번 연습에 쓸 버전 · 다음 Take 번호 · 지난번 대본 표시 방식 · 이번 Take 미션
POST /api/pitches/{pitch_id}/takes/    {"mode", "script_mode", "presentation_version_id", "script_version_id", "client_session_id"?}
                                       → take_id · take_number
```

- `/prepare` 는 피치 생성을 거치지 않고 장치 점검에 들어왔을 때(홈 · 새로고침) Take 에 넣을 버전을 준다. 발표자료는
  가장 최근 것, 대본은 파싱이 끝난(`DONE`) 가장 최근 것이다. 없으면 `null`. 그보다 새 대본이 파싱 중이거나 실패했으면
  `newer_script` 로 알린다.
- 발표자료는 대본 장 수와 쪽수가 같은 가장 최근 것을 고른다 (FE 매핑 확인과 같은 규칙). 맞는 것이 없으면 가장 최근
  발표자료를 주고, 응답의 `page_count` · `slide_count` 가 달라 FE 가 시작을 막는다.
- `next_take_number` 는 미리 보여 주는 값이다. 그사이 다른 Take 가 생기면 달라져서 실제 번호는 Take 생성 응답의
  `take_number` 를 본다.
- `missions` 는 Take 를 시작할 때 싣는 미션과 같은 기준으로 고른다. 리뷰가 끝난 Take 중 가장 늦게 끝난 Take 의
  `ACTIVE` 미션을 리뷰가 읽는 순서로 주고, 그 Take 번호를 `source_take_number` 로 준다. 리뷰가 끝난 Take 가 없으면
  `null`, 리뷰가 다음 미션을 내지 않았으면 `items` 가 빈 목록이다. 그사이 리뷰가 끝나면 시작할 때 다시 고른 미션이 실린다.
- Take 생성은 파싱이 끝난 대본만 받는다 (아니면 `409 SCRIPT_NOT_PARSED`). 발표자료 쪽수와 대본 장 수가 다르면
  `409 SLIDE_COUNT_MISMATCH` 다 (쪽수를 모르는 옛 발표자료는 검사하지 않는다). 목표 시간은 받지 않고 발표 제한 시간을 박는다.
- `client_session_id`(FE 의 연습 세션 id)가 같으면 새로 만들지 않고 처음 만든 Take 를 돌려준다. 응답을 잃고 다시
  보내도 Take 는 하나다. 모드 · 대본 표시 방식 · 버전이 처음과 다르면 `409 TAKE_SESSION_MISMATCH`, 그 Take 가 이미
  끝났으면(`ABANDONED` 포함) `409 TAKE_ENDED` 다. 둘 다 새 세션 id 로 다시 만든다.
- `READY` 는 시작하지 않은 Take 다. FE 는 마이크를 열고 첫 WS 연결이 `ready` 를 받은 뒤에 발표를 시작하고, 그
  연결이 `RUNNING` 으로 바꾼다. 그래서 새 Take 를 만들면 같은 발표에서 `READY` 로 남은 Take 를, 다시 연습하지
  않아도 만든 지 10분이 지난 `READY` 를(1분마다, `module/take/ready_sweep.py`) `FAILED`(`ABANDONED`)로 끝낸다.
  끝난 시각은 만든 시각으로 적는다.

## 실시간 STT (WebSocket)

발표 중 마이크 오디오를 Deepgram 으로 흘리고 전사를 FE 로 돌려준다 (`WS /api/ws/takes/{take_id}`).
메시지 순서 · 에러 코드 · 재연결 · 저장 · 종료 · 자동 종료 규칙은 [docs/be/realtime-stt.md](../docs/be/realtime-stt.md) 에 있다.
Take 가 언제 어떤 상태로 끝나는지는 [docs/be/take-lifecycle.md](../docs/be/take-lifecycle.md) 다.

## 대본 파싱 (AI)

올린 대본을 AI 서버가 슬라이드 단위로 나누고 키워드를 뽑는다. FE 쪽은 비동기다 (즉시 `202` + 폴링).
엔드포인트 · AI 호출 · 재시도 · 만료 · 저장 규칙은 [docs/be/script-parse.md](../docs/be/script-parse.md) 에 있다.

## 실시간 코치 (AI)

발표 중 코치(`/coach/evaluate`), 종료 뒤 Take 결과(`/coach/finalize`), 다음 Take 의 코칭 계획(`/coach/plan`)과
원자료 보관 규칙은 [docs/be/realtime-coach.md](../docs/be/realtime-coach.md) 에 있다.

## 에러 응답 형식

모든 에러는 `core/exceptions.py` 의 핸들러를 거쳐 같은 형태로 나간다. 프론트엔드는 `code` 로 분기한다.

```json
{"code": "NOT_FOUND", "message": "사용자를 찾을 수 없습니다.", "detail": null}
```

| status | code | 베이스 클래스 |
|---|---|---|
| 400 | `BAD_REQUEST` | `AppException` |
| 401 | `UNAUTHORIZED` | `UnauthorizedException` (+ `WWW-Authenticate: Bearer`) |
| 403 | `FORBIDDEN` | `ForbiddenException` |
| 404 | `NOT_FOUND` | `NotFoundException`, 없는 경로 (라우터가 자동 생성) |
| 405 | `METHOD_NOT_ALLOWED` | 허용되지 않는 메서드 (자동, `Allow` 헤더 유지) |
| 409 | `CONFLICT` | `ConflictException` |
| 422 | `VALIDATION_ERROR` | FastAPI 검증 실패 (자동, `detail` 에 필드별 오류) |
| 500 | `INTERNAL_SERVER_ERROR` | 처리되지 않은 예외 (자동, 원인은 서버 로그에만 남는다) |

도메인 예외는 `module/<name>/exception.py` 에서 위 클래스를 상속하고 `message` 만 바꾼다.
`HTTPException` 을 직접 던지지 않는다. 라우터가 만드는 404·405 와 처리되지 않은 예외도
핸들러가 같은 형태로 바꾸므로, 프론트엔드는 모든 에러에서 `code` 만 보면 된다.

## 새 도메인 추가 절차

1. `module/<name>/` 에 기본 파일을 만든다 (`controller`, `service`, `repository`, `entity`, `dto`, `exception`).
   service 가 커져 업무가 섞이면 그때 `service/` 로 나눈다 (위 '계층을 나누는 기준').
2. `entities.py` 에 `import pitch_coach_backend.module.<name>.entity  # noqa: F401` 를 추가한다. 빠뜨리면 Alembic 이 테이블을 인식하지 못한다.
3. controller 에 `router = APIRouter(prefix="/<names>", tags=["<name>"])` 를 두고,
   `main.py` 의 `api.include_router(...)` 에 추가한다. `/api` 는 다시 적지 않는다.
4. `uv run alembic revision --autogenerate -m "..."` 로 마이그레이션을 만들고, 생성된 파일을 읽어 의도와 같은지 확인한 뒤 `uv run alembic upgrade head` 로 적용한다. 마이그레이션 파일은 커밋한다.
5. `tests/test_<name>.py` 를 만든다. 커지면 `tests/<name>/` 에 시나리오별 파일과 `conftest.py` 로 나눈다.

## 환경변수

`.env.example` 이 유일한 목록이다. 키를 추가하면 다음 세 곳을 같이 챙긴다.

**필수 설정**은 `Settings` 에 기본값 없이 선언하고 `.env.example` 에 키를 적는다. **선택 설정**은 코드에 기본값을 두고 `.env.example` 에는 주석으로만 표시한다. 환경마다 바뀌지 않는 값(예: JWT 알고리즘)은 설정이 아니라 코드 상수로 둔다.

1. `.env.example` 에 키와 로컬 기본값(비밀은 빈 값) 추가
2. `core/config.py` 의 `Settings` 에 필드 추가
3. 인프라 담당에게 알린다 (docker-compose, EC2, GitHub Actions secrets 에 반영해야 한다)

`.env`, `alembic.ini` 의 URL, 코드·문서·노트북 본문에 실제 비밀값을 넣지 않는다. 이 저장소는 public 이다.

## 인프라 담당에게 넘기는 정보

| 항목 | 값 |
|---|---|
| 앱 실행 | `uvicorn pitch_coach_backend.main:app --host 0.0.0.0 --port 8000` |
| 배포 전 마이그레이션 | `alembic upgrade head` |
| 헬스체크 | `GET /api/health` |
| CI 테스트 / 린트 | `uv run pytest` / `uv run ruff check` / `uv run ruff format --check` (테스트는 PostgreSQL 서비스 + `DATABASE_URL` 필요) |
| 환경변수 목록 | `.env.example` |
| Google OAuth 리다이렉트 | `/api/auth/google/callback` (운영 도메인 확정 시 Google Console 에 추가 등록 필요) |
| 추가로 필요한 것 | Redis (`REDIS_URL`). OAuth state 저장에 쓴다 |
| Deepgram | `DEEPGRAM_API_KEY` 하나, 또는 `DEEPGRAM_API_KEYS` 에 쉼표로 여러 개. 운영 도메인은 HTTPS 여야 한다 — 브라우저 마이크(`getUserMedia`)는 secure context 에서만 열린다 |
| AI 서버 | `AI_BASE_URL` (기본 `http://localhost:8001`, 운영은 compose 내부 `http://ai:8000`). 없으면 BE 는 뜨고 대본 파싱만 `AI_UNAVAILABLE` 로 실패한다 |
