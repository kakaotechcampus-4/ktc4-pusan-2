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
- 인증 테스트는 **Redis 15번 DB** 를 쓰고 매번 비운다. 개발용 0번은 건드리지 않는다.
- `client` fixture 의 base_url 이 `https://testserver` 인 이유: 쿠키를 `Secure` 로 심으므로
  http 로는 httpx 가 쿠키를 되돌려 보내지 않는다. 설정을 낮추는 대신 https 를 쓴다.
- 구글 연동 테스트는 **외부 HTTP 통신만 모킹하고 검증 로직은 실제로 실행**한다.
  테스트용 RSA 키로 진짜 RS256 토큰을 서명해 위조·만료·`aud` 불일치를 실제로 거부시킨다.
- CI 는 PostgreSQL·Redis 서비스와 `DATABASE_URL` 만 제공하면 된다.
  `JWT_SECRET_KEY` 와 구글 값은 conftest 가 테스트용 값을 넣는다.

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
│   ├── test_health.py
│   ├── test_security.py          # JWT 발급/검증
│   ├── test_exceptions.py        # 에러 응답 형식 계약
│   ├── test_user_entity.py
│   ├── test_auth_entity.py       # oauth_accounts·refresh_tokens 제약
│   ├── test_google_oauth.py      # ID Token 검증 (실제 RSA 서명으로)
│   ├── test_state_store.py       # Redis state 1회 소비
│   ├── test_auth_service.py      # 가입·재로그인·회전·재사용 탐지
│   ├── test_auth_endpoints.py    # 쿠키·CSRF·리다이렉트
│   ├── test_realtime_stt_adapter.py  # Deepgram 파싱 + 가짜 서버 왕복
│   ├── test_realtime_ws.py       # WS 인증·프레임·offset·재연결·종료 순서·자동 종료 (Deepgram 은 가짜)
│   ├── test_realtime_auto_end.py # 말 없음 판단과 자동 종료 조건 (숫자만)
│   ├── test_coach_client.py      # 코치 AI 호출: 응답 읽기·재시도·409·동시 개수 (AI 예시 파일을 읽는다)
│   ├── test_coach_requests.py    # 코치 요청 만들기: 시간 계획·창·replay·코칭 계획
│   └── test_<domain>.py          # 도메인이 늘면 같은 이름 규칙으로
│
└── src/pitch_coach_backend/
    ├── main.py                   # FastAPI 앱 생성, /api 라우터에 컨트롤러 등록, /api/health
    ├── entities.py               # 모든 도메인 entity 를 한 곳에서 import (Alembic·main 이 사용)
    │
    ├── core/                     # 설정·인프라 연결. 도메인 로직을 두지 않는다
    │   ├── config.py             # pydantic-settings. .env 는 backend/ 기준으로 읽는다
    │   ├── database.py           # 엔진, 세션, Base, get_db, UUIDPrimaryKeyMixin, TimestampMixin
    │   ├── security.py           # JWT 발급/검증
    │   ├── exceptions.py         # 공통 예외 베이스 + 에러 응답 핸들러
    │   ├── logging.py            # 앱 로거(pitch_coach_backend.*) 레벨 · 형식. 앱이 뜰 때 건다
    │   └── redis.py              # Redis 클라이언트 + get_redis 의존성
    │
    ├── module/                   # 도메인. 폴더마다 아래 6 파일을 같은 이름으로 둔다
    │   ├── auth/                 # Google OAuth, 토큰 발급·회전, get_current_user
    │   │   ├── controller.py     # google/start·callback, refresh, logout. 쿠키 설정
    │   │   ├── service.py        # 로그인·회전·로그아웃. 트랜잭션 경계
    │   │   ├── repository.py
    │   │   ├── entity.py         # OAuthAccount, RefreshToken
    │   │   ├── dto.py
    │   │   ├── dependencies.py   # get_current_user, verify_csrf (auth 에만 있음)
    │   │   ├── exception.py
    │   │   ├── google.py         # 구글 어댑터. DB·Redis 를 모른다 (6파일 규칙 밖)
    │   │   └── state_store.py    # state·nonce·PKCE 의 Redis 1회 소비 (6파일 규칙 밖)
    │   ├── user/                 # User
    │   ├── pitch/                # 발표 원고/자료
    │   │   ├── script_parser.py      # AI 대본 파서 HTTP 어댑터. 응답 검증·변환·재시도. DB 를 모른다 (6파일 규칙 밖)
    │   │   └── script_parse_runner.py  # 업로드 뒤 BackgroundTasks 로 도는 파싱 작업. 짧은 세션으로 저장 (6파일 규칙 밖)
    │   ├── take/                 # 발표 1회 수행 기록
    │   ├── rehearsal/            # 실시간 연습 세션
    │   └── feedback/             # LLM 리뷰 결과
    │
    ├── realtime/                 # WebSocket. entity 를 갖지 않고 저장은 도메인 service 에 위임
    │   ├── controller.py         # /ws/takes/{take_id} 엔드포인트
    │   ├── service.py            # 연결 하나의 수명: Origin → 인증 → Take 검사 → 스트림에 붙이기 → 프레임 전달
    │   ├── take_stream.py        # Take 의 STT 파이프라인 + 레지스트리. **연결보다 오래 산다**
    │   ├── transcript_store.py   # final·자동 종료 이유 저장. 짧은 DB 세션을 열어 take/service 에 넘긴다
    │   ├── auto_end.py           # Take 자동 종료 조건(말 없음·최대 시간). 네트워크를 모른다
    │   ├── stt_adapter.py        # Deepgram WebSocket 어댑터. DB 를 모른다 (auth/google.py 와 같은 위치)
    │   ├── event_ingestion.py    # 오디오 프레임 헤더 검증, 순서·중복, 갭 계산
    │   ├── audio.py              # 오디오 규격(16 kHz·mono·16-bit)과 무음 생성. 잎 모듈
    │   ├── dto.py                # BE ↔ FE 메시지
    │   ├── dependencies.py       # get_stt_adapter · get_transcript_store (테스트가 가짜로 바꿔 끼운다)
    │   ├── fillers.py            # 한국어 군더더기 목록. keyterm boosting + 후처리 사전의 단일 소스
    │   ├── keyterms.py           # filler + 대본 용어를 Deepgram keyterm 한도(100개·500토큰) 안에서 조립
    │   ├── state_aggregator.py   # (예정) 시간 정렬/sliding window/state 생성
    │   └── session_store.py      # (예정) take_stream 의 레지스트리를 Redis 로 옮길 때 분리한다
    │
    ├── coach/                    # AI 실시간 코치 호출. DB 를 모른다
    │   ├── client.py             # /coach/evaluate·finalize·plan HTTP. 응답 검증·재시도·409·동시 개수
    │   ├── requests.py           # 요청 만들기 (시간 계획·미션·창·replay·코칭 계획 고르기). 순수 함수
    │   └── dependencies.py       # get_coach_client. 프로세스에 하나
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
- **realtime, task → module**: 도메인 service 를 호출한다. module 은 realtime·task 를 모른다.
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

로컬은 프론트(3000)와 백엔드(8000)가 cross-origin 이라 CORS 와 `credentials: 'include'` 가
필요하다. 배포에서는 Caddy 가 같은 origin 으로 묶어 그 문제가 사라진다.
자세한 흐름과 프론트 연동 코드는 `docs/oauth-architecture.md` 에 있다 (로컬 문서).

## 연습 시작 (장치 점검 · Take 생성)

```
GET  /api/pitches/{pitch_id}/prepare   이번 연습에 쓸 버전 · 다음 Take 번호 · 지난번 대본 표시 방식
POST /api/pitches/{pitch_id}/takes/    {"mode", "script_mode", "presentation_version_id", "script_version_id", "client_session_id"?}
                                       → take_id · take_number
```

- `/prepare` 는 피치 생성을 거치지 않고 장치 점검에 들어왔을 때(홈 · 새로고침) Take 에 넣을 버전을 준다. 발표자료는
  가장 최근 것, 대본은 파싱이 끝난(`DONE`) 가장 최근 것이다. 없으면 `null`. 그보다 새 대본이 파싱 중이거나 실패했으면
  `newer_script` 로 알린다.
- 발표자료와 대본은 따로 최신을 고른다. BE 는 PDF 쪽수를 몰라서 두 버전이 짝인지(대본 장 수 = 쪽수) 확인하지 못한다.
- `next_take_number` 는 미리 보여 주는 값이다. 그사이 다른 Take 가 생기면 달라져서 실제 번호는 Take 생성 응답의
  `take_number` 를 본다.
- Take 생성은 파싱이 끝난 대본만 받는다 (아니면 `409 SCRIPT_NOT_PARSED`). 목표 시간은 받지 않고 발표 제한 시간을 박는다.
- `client_session_id`(FE 의 연습 세션 id)가 같으면 새로 만들지 않고 처음 만든 Take 를 돌려준다. 응답을 잃고 다시
  보내도 Take 는 하나다.
- 새 Take 를 만들면 같은 발표에서 `READY` 로 남은 Take 를 `FAILED`(`ABANDONED`)로 끝낸다. FE 는 가장 최근 Take 로만
  발표한다. 시간으로 끝내지 않는 이유: 마이크 없이 하는 발표는 실시간 연결이 없어 끝날 때까지 `READY` 다.

## 실시간 STT (WebSocket)

발표 중 마이크 오디오를 Deepgram 으로 흘리고 전사를 FE 로 돌려준다. **BE 가 Deepgram 에 API 키를
붙인다** — 브라우저 WebSocket 은 헤더를 못 붙이므로 FE → BE → Deepgram 구조가 필수다.

```
WS /api/ws/takes/{take_id}
```

| 순서 | 방향 | 프레임 | 내용 |
|---|---|---|---|
| 1 | FE → BE | text | `{"type":"auth","token":"<access>"}` — 5초 안에 와야 한다. 브라우저는 헤더를 못 붙이므로 첫 메시지로 |
| 2 | BE → FE | text | `{"type":"ready","take_id":…,"stt_session_no":0,"stt_state":"connecting"}` — 사용자·Take(존재·소유·상태) 검사를 통과했다. 이제 오디오를 보내도 된다 |
| 3 | FE → BE | **binary** | `[seq u32 LE][offset_ms u32 LE][PCM 16-bit LE 16 kHz mono]`. 100 ms = 3,200 bytes. `offset_ms` 는 Take 시작 = 0 |
| 4 | BE → FE | text | `{"type":"transcript","segment_id","is_final","speech_final","start_ms","end_ms","text","confidence","words":[…]}` — `segment_id` 가 같으면 덮어쓴다. **타임스탬프는 Take 기준** |
| 5 | FE → BE | text | `{"type":"stop"}` — BE 가 Deepgram 을 정리(`CloseStream` → `Metadata`)하고 |
| 6 | BE → FE | text | `{"type":"stt_status","state":"closed",…}` 를 보낸 뒤 `1000` 으로 닫는다. **FE 는 이걸 받기 전에 닫지 않는다** |

- **한 번 보낸 오디오 프레임은 재연결 뒤에도 다시 보내지 않는다.** `seq` 는 Take 안에서 계속 오르고 재연결해도
  이어 센다. FE 는 아직 못 보낸 프레임만 들고 있다가 `ready` 뒤에 순서대로 보낸다. BE 는 새 연결의 첫 프레임이
  뒤로 가면 탭을 새로고침해 처음부터 다시 센 것으로 본다 — 보낸 프레임을 다시 보내면 중복이 아니라 재시작으로
  읽힌다. 확인 응답이 없을 때 다시 보내는 기능을 넣으려면 먼저 이어 보내는지 처음부터 다시 세는지를 FE 가
  첫 메시지에서 알려 주도록 이 규약부터 바꾼다.
- `stt_status` 는 상태가 바뀔 때마다 온다. `connecting` → `ok` → (`reconnecting` → `degraded`) → `closed`.
  숫자(`frames`·`dropped_frames`·`silence_ms`·`lost_ms`)는 연결 단위가 아니라 **Take 누적**이다.
- **Deepgram 이 죽어도 연결을 끊지 않는다.** 오디오를 최대 5초 큐에 담고 백오프로 재접속하며
  `degraded` 만 알린다 — 그동안 FE 1단 코치는 계속 돌아야 하기 때문이다.
- **키가 거절되면 다음 키로 넘어간다** (`DEEPGRAM_API_KEYS`). 연결 요청이 401(틀린 키) · 402(크레딧 부족)로
  거절되면 그 키를 소진으로 표시하고 기다리지 않고 다음 키로 붙는다. 발표 도중 크레딧이 떨어져 끊겨도 재접속이
  402 로 거절되므로 같은 길로 넘어간다. 소진 표시는 서버 메모리에만 있고 모든 Take 가 같이 쓴다. 키가 전부
  소진이면 에러 로그를 남기고 위처럼 `degraded` 로 재시도하며, 그때는 첫 키부터 다시 확인한다
- 에러는 `{"type":"error","code","message"}`. **에러가 곧 종료는 아니다** — `code` 마다 다르다
  (`realtime/dto.py` 의 `WsErrorCode`).

  | code | 연결 |
  |---|---|
  | `UNAUTHORIZED` | `1008` 로 닫는다 — 토큰이 없거나 틀리거나 사용자가 없다 |
  | `TAKE_NOT_FOUND` | `1008` 로 닫는다 — Take 가 없거나 내 것이 아니다 (REST 404 처럼 둘을 구분하지 않는다). 연결 중에 Take 나 발표가 지워져도 이 코드로 닫는다 |
  | `TAKE_ENDED` | `1008` 로 닫는다 — 이미 끝난 Take (`ANALYZING`·`COMPLETED`·`FAILED`. 자동 종료된 Take 도 `ANALYZING` 이다). FE 는 **재연결하지 않는다** |
  | `FORBIDDEN` | `1008` 로 닫는다 — 다른 사용자가 쓰고 있는 스트림 (정상 흐름에서는 안 나온다) |
  | `BAD_MESSAGE` | 유지 |
  | `BAD_AUDIO_FRAME` | 유지. 연결당 첫 오류만 알린다 |
  | `TAKE_TAKEN_OVER` | `1008` 로 닫는다. FE 는 **재연결하지 않는다** |

- `Origin` 이 있으면 `FRONTEND_BASE_URL` 과 같아야 한다. 없으면(스크립트) 통과.
- **인증 뒤 Take 를 검사한다.** 사용자 존재 → Take 존재·소유(`takes` → `pitches.user_id`) → 상태
  (`READY`·`RUNNING` 만 통과) 를 DB 왕복 한 번으로 본다. `READY` 는 첫 연결이라 받고, 통과하면
  `RUNNING` 으로 바꾼다 (`docs/ai/architecture.md` 1-3). 못 바꿔도 STT 는 그대로 돈다.
- **final 은 `take_transcript_segments` 에 저장한다** (interim 은 저장하지 않는다). 리포트의 유일한
  원천이다. 시각은 Take 기준(ms), `words` 는 `[{word, punctuated_word, start_ms, end_ms, confidence}]`
  JSONB. 저장은 realtime 이 아니라 `take/service.py` 가 하고(`append_transcript_segment`), 스트림이
  DB 를 직접 만지지 않도록 `realtime/transcript_store.py` 가 저장할 때마다 짧은 세션을 연다.
  **저장 실패는 스트림을 멈추지 않는다** — 로그만 남기고 화면 전사는 계속 나간다. 단 Take 가 지워져
  저장할 곳이 없으면(외래 키 위반) 아래처럼 스트림을 끊는다.
- **Take 나 발표를 지우면 그 Take 의 스트림을 바로 끊는다.** 지우는 요청이 커밋한 뒤 Deepgram 을
  닫고, 붙어 있는 FE 에는 `TAKE_NOT_FOUND` 를 보낸다. 저장할 곳이 없으니 `stop` 처럼 남은 전사를
  기다리지 않는다. 끊기 전에 온 final 이 저장에 실패해도 스트림이 그걸 보고 스스로 끊는다.
- **STT 파이프라인은 연결보다 오래 산다** (`take_stream.py`). 연결이 끊겨도 30초 동안 Deepgram
  세션을 살려 두고, 그 안에 다시 붙으면 세그먼트 번호와 프레임 수가 이어진다. 떨어져 있는 동안
  화면에 못 간 final 은 모아 두었다가 `ready` 직후 순서대로 다시 보낸다 (DB 에는 이미 있다).
  번호가 리셋되면 FE 가 같은 `segment_id` 를 덮어써 **발표 앞부분 전사가 사라진다** — 그래서
  30초가 지나 스트림이 사라진 뒤 다시 붙어도 **저장된 마지막 `seq`·`stt_session_no` 에서 이어 받는다.**
  오디오를 한 번도 안 받은 스트림은 살려 둘 전사가 없어서 끊기면 바로 정리한다.
- 스트림은 만든 사용자에게 묶인다 (`FORBIDDEN`). 위 DB 검사의 이중 안전장치라 정상 흐름에서는 나오지 않는다.
- 같은 사용자의 두 번째 연결이 오면 **기존 것을 `TAKE_TAKEN_OVER` 로 쫓아낸다** (탭 복구).
  반대로 하면 재연결 레이스에서 사용자가 영영 못 붙는다. 쫓겨난 연결이 닫히기 전에 보낸 프레임과 `stop` 은 버린다.
  정리 중인 스트림에는 붙이지 않고
  끝나기를 기다렸다가 새로 만든다.
- Deepgram 타임스탬프는 "그 연결의 첫 바이트 = 0" 이라 BE 가 그 세션 첫 프레임의 `offset_ms` 를 더한다.
  프레임 사이 갭(> 50 ms) 은 **무음으로 채워** 두 타임라인을 맞추고, 5초를 넘는 갭은 무음 대신
  **Deepgram 세션을 갈아** base 를 다시 잡는다 — 일부만 채우면 그 뒤 전사 시각이 통째로 앞당겨진다.
  역행·중복 `seq` 는 버린다. 단 **새 연결의 첫 프레임**이 뒤로 가면(`seq` 또는 `offset_ms`) FE 가 처음부터
  다시 센 것이므로 버리지 않고 세션을 갈아 그 offset 으로 base 를 잡는다. 탭을 새로고침하면 FE 는 `seq` 를
  1 부터 다시 세고 `offset_ms` 는 이어받은 무대 시계부터 보낸다 (grace 가 지나 새 스트림이 열린 경우와 같은 결과). 세션이 끝나면 Deepgram 이 보고한 길이와 우리가 보낸 길이를 대조해
  어긋나면 로그에 남긴다.
- 한국어 군더더기("음", "어", "그", "이제")는 Deepgram 이 기본으로 버리므로 `realtime/fillers.py` 의
  목록을 `keyterm` 으로 넘겨 전사에 남긴다. `filler_words` 옵션은 영어 전용이다.
- filler 뒤에 Take 대본의 용어를 `keyterm` 으로 붙인다 (고유명사 오인식 방지). 파싱된 `terms` 만
  우선순위 순으로 쓰고, 슬라이드 `keywords` 는 발음 잡기용이 아니라 넣지 않는다. 인가 때 같은 DB 왕복에서
  읽고, 파싱이 안 끝난 대본이면 filler 만 보낸다.
  Deepgram 한도는 **100개·전체 500 토큰**이고 넘으면 연결 자체가 거절된다. 토큰 수는 실측으로 정한 상한
  (한글·ASCII 글자당 1, 그 밖 문자는 UTF-8 바이트, 용어당 +2)으로 세고 50 토큰을 남긴다 (`realtime/keyterms.py`).
  한도로 빠진 개수는 로그에 남는다. 그래도 추정이 빗나가 Deepgram 이 400 으로 거절하면 뒤쪽 대본 용어를
  5개씩 빼며 기다리지 않고 바로 다시 붙는다 (filler 는 남긴다). 용어 조회가 실패해도 연결은 닫지 않고 filler 만 쓴다.
- 정리가 늦어도 FE 를 붙잡아 두지 않는다. `stop` 뒤 Deepgram drain 이 12초 안에 안 끝나면 태스크를
  끊고, 그마저 2초 안에 안 끝나면 닫힌 것으로 확정해 `closed` 를 보낸다. Deepgram 소켓 닫기 자체도
  3초 상한이 있다. `stop` 을 보낸 직후 FE 가 사라져도(탭 닫힘) 서버 쪽 종료·저장 절차는 같다.
- **`stop` 이 Take 를 끝낸다.** 마지막 확정 전사까지 저장한 뒤 Take 를 `ANALYZING` 으로 바꾸고 `closed` 를
  보낸다 (`ended_at` 이 비어 있으면 지금 시각으로 채운다). `stop` 을 받은 순간부터 같은 Take 로 붙는 연결은
  `TAKE_ENDED` 로 거절한다 — DB 에 적기 전의 틈은 프로세스 안의 기록이 막는다. 종료를 DB 에 못 적어도
  `closed` 는 보낸다. 30초 대기 만료 · 오디오 없는 연결 · 앱 종료는 스트림만 정리하고 Take 는 끝내지 않는다
  (FE 는 끊긴 뒤에도 다시 붙는다).
- **탭을 닫고 떠난 Take 는 30분 뒤 BE 가 끝낸다.** 스트림이 끝나지 않은 채 정리된 뒤 30분 동안(FE 가 끊긴 뒤
  다시 붙어 보는 시간과 같다) 아무도 다시 붙지 않으면 `FAILED` 로 바꾸고 `end_reason` 에 `ABANDONED` 를 남긴다.
  시선 기록 · 끝난 시각을 FE 가 보내지 못한 Take 라 분석하지 않는다. `ended_at` 은 마지막으로 연결이 끊긴 시각이다.
  그 안에 다시 붙으면(리허설 주소를 다시 연 경우 포함) 이어서 연습하고, 그 뒤로는 `TAKE_ENDED` 로 거절한다.
  최대 시간(서버 시계 1시간 + 60초)까지 남은 발표가 30분보다 길면 그만큼 기다린다. 토큰 만료 등으로 재연결을
  포기한 FE 는 STT 없이 발표를 이어 가기 때문이다. 그래도 먼저 끝냈으면 끝내기 PUT 이 되살린다 (아래).
  타이머는 메모리에 있어서 앱이 다시 뜨면 `RUNNING` Take 의 마지막 활동 시각(만든 시각 · 시작 시각 · 마지막
  전사 저장 시각 중 가장 늦은 것)에서 다시 건다. 남은 발표 시간도 사라져 최대 시간 전체를 기다린다.
  이 Take 들을 10초 안에 읽지 못하면 걸지 않고 앱을 띄운다 (다음에 연결이 붙었다 끊길 때 걸린다).
- **STT 없이 끝낸 Take 는 Take PUT 이 끝낸다.** 마이크를 못 열었거나 재연결을 포기한 FE 는 `stop` 을 보내지 못한다.
  종료 흐름의 `PUT /api/pitches/{pitch_id}/takes/{take_id}` 에 `ended_at` 이 있으면 BE 가 `ANALYZING` 으로 바꾼다.
  연결이 붙어 있거나 `stop` 으로 끝내는 중이면 그 연결에 맡긴다. 끊긴 뒤 30초 안이라 스트림이 남아 있으면
  `stop` 처럼 남은 전사까지 저장한 뒤 바꾼다. 떠난 것으로 끝낸 Take(`ABANDONED`)도 `ANALYZING` 으로 되살리고
  이유를 지운다.
- Take PUT 은 상태와 순서를 본다. `COMPLETED`·`FAILED` 는 `409 TAKE_ENDED`, `ANALYZING` 은 시각만 받는다
  (`event_logs` 는 409). `ABANDONED` 로 끝난 Take 는 `ended_at` 이 있는 끝내기만 받는다.
  `ended_at` 이 `started_at` 보다 앞서면 `400 INVALID_TAKE_TIME`, 시간대가 없는 시각은 422.
  `null` 은 보내지 않은 것으로 본다. 점검 결과(`calibration`)는 `READY` 일 때만 받는다 (아니면 `409 TAKE_STARTED`).
- **Take 를 자동으로 끝낸다** (`realtime/auto_end.py`). 둘 중 하나라도 맞으면 `{"type":"auto_end","reason","message"}`
  를 보내고 `stop` 과 같은 순서로 정리한다 (drain → `ANALYZING` → `closed` → `1000`). 오디오는 그 자리에서 끊는다.
  FE 는 끝내기 버튼과 같은 종료 흐름을 타고 `message` 를 보여 준다.
  - `SILENCE` — 최근 2분 동안 말이 없고 직전 5분 중 80% 이상이 말 없음. **전사로 판단한다** (소리 크기는
    소음에도 커진다). 시간은 벽시계가 아니라 Deepgram 이 final(빈 final 포함)로 "들었다" 고 확인한 오디오로
    잰다 — STT 가 죽어 있거나 오디오가 유실된 구간, BE 가 타임라인을 맞추려고 메워 보낸 무음(프레임 사이 틈 ·
    큐가 넘쳐 버린 오디오)을 말 없음으로 세면 STT · 네트워크 장애가 곧 강제 종료가 된다. 기록은 Take 에
    붙어 있어 30초 넘게 끊겼다 새 스트림으로 붙어도 이어진다.
  - `TIME_LIMIT` — 1시간. 기준은 프레임의 `offset_ms`(FE 무대 시계)다. offset 이 가지 않는 Take(오디오 없이
    연결만 붙여 둠)는 서버 시계가 막는다 — **이 Take 의 스트림이 열려 있던 시간의 합**이 1시간 + 60초를
    넘으면 끝낸다. 스트림이 없던 시간(탭이 죽어 있던 동안)은 세지 않는다. FE 가 떨어져 있는 동안(grace)에도 끝낸다.
  - `stop` 처럼 Take 를 `ANALYZING` 으로 바꾸고, `takes.end_reason` 에 이유를 남긴다. `auto_end` 를 받은 FE 가
    `stop` 을 보내도 이유는 그대로 남는다. 다시 붙으면 `auto_end` 를 한 번 더 보내고 `TAKE_ENDED` 로 거절한다 —
    자동 종료 순간 떨어져 있던 FE 도 종료 흐름을 탄다. 이유를 못 적어도(DB 장애) 종료는 그대로 한다.
  - 판단 코드가 예외를 내도 STT 는 계속 돈다 (로그만). 값은 `TAKE_SILENCE_*`·`TAKE_MAX_DURATION_SEC` 로 바꾸고,
    틀린 값이면 앱이 시작할 때 실패한다.
- 아직 없는 것: `metrics`/`coach` 메시지 (AI 팀 규칙 확정 후).

FE 없이 확인하는 두 가지 —

| | 무엇 | 어떻게 |
|---|---|---|
| 파일 | `dev/stt-send-pcm.py` | `uv run python dev/stt-send-pcm.py 녹음.pcm --email <가입한 이메일>` (파일 상단에 변환 명령). `--take-id` 를 안 주면 그 사용자의 Take 를 하나 만들어 쓴다 |
| 마이크 | `dev/stt-test.html` | `python3 -m http.server 3000 --directory dev` 로 띄우고 `http://localhost:3000/stt-test.html`. **3000 포트로 열어야** Origin 검사를 통과한다. Take 칸에는 `uv run python dev/stt-send-pcm.py --create-take --email <이메일>` 로 만든 id 를 넣는다 |

## 대본 파싱 (AI)

올린 대본을 AI 서버가 슬라이드 단위로 나누고 키워드를 뽑는다. **BE 가 AI 를 부르고 기다린다** —
AI 는 BE 주소를 모른다. FE 쪽은 비동기다 (즉시 `202` + 폴링).

```
POST /api/pitches/{pitch_id}/scripts                          202  {"content"} → script_version_id·version, PENDING
GET  /api/pitches/{pitch_id}/scripts/{script_version_id}           PENDING | DONE(+slides) | FAILED(+error_code)
POST /api/pitches/{pitch_id}/scripts/{script_version_id}/parse  202  FAILED 일 때만. 아니면 409
POST /api/pitches/add/{pitch_id}/presentation                     발표자료(PDF)만. 대본은 받지 않는다
```

- **대본은 발표자료와 따로 올린다.** 대본만 여러 번 고치는 게 정상 흐름이라, 발표자료 업로드에 묶으면 PDF 를 매번 다시 올려야 한다.
  버전은 pitch 안에서 자동으로 1, 2, … 로 매기고, 발표자료 버전과는 따로 센다.
- 대본은 JSON `{"content": "..."}` 로 받는다 (FE 는 textarea 내용 그대로). 비어 있거나 5만 자를 넘거나 NUL 문자가 있으면
  `400 INVALID_SCRIPT` 이고 아무것도 저장하지 않는다. NUL 은 JSON(`\u0000`)으로는 오지만 PostgreSQL TEXT 가 받지 못한다.
  AI 응답에 NUL 이 있어도 `AI_INVALID_OUTPUT`.
- 대본을 올리면 pitch 행을 `FOR NO KEY UPDATE` 로 잠그고 버전을 매긴다. 같은 pitch 에 동시에 올려도 버전이 겹치지 않는다
  (잠금이 없으면 둘 다 `max+1` 을 받아 한쪽이 unique 위반 500). `FOR UPDATE` 는 같은 pitch 의 Take 생성까지 막아서 쓰지 않는다.
- **대본 원문은 S3 가 아니라 DB(`script_versions.content`)에 둔다.** 5만 자 이하 텍스트라 TEXT 로 충분하고, 슬라이드와
  한 트랜잭션으로 저장되며, 재시도·편집 화면이 네트워크 호출 없이 읽는다. S3 는 발표자료(PDF)만 쓴다.
  `script_slides.full_content` 는 AI 가 구분자·제목을 빼고 공백을 합친 글이라 원문을 대신하지 못한다.
- 이 기능 전에 올라온 대본은 원문이 S3 파일(`pitches/{pitch_id}/scripts/{version}.<확장자>`)로만 있어 migration 이 `FAILED (LEGACY_UNPARSED)` 로 두고,
  재시도하면 `409 SCRIPT_REUPLOAD_REQUIRED` — 새로 올려야 한다.
- BE → AI 는 `POST {AI_BASE_URL}/scripts/parse` `{"script_text", "target_ms"}`. `target_ms` 는 발표 제한 시간(`time_limit_sec` × 1000)이고,
  다시 파싱할 때는 그때의 제한 시간을 싣는다. 응답 모양은 [`docs/ai/features/script-parser.md`](../docs/ai/features/script-parser.md) 5절이고
  `script_parser.py` 가 바꾼다 (`status` → `segmented`, highlight `{"start": 0, "end": 6}` · `"0:6"` → `[0, 6]`, 틀린 highlight 는 그 항목만 버림).
  highlight 는 AI 가 새 모양으로 바꾸는 동안 두 모양을 다 받는다.
  시도 한 번 전체 30초(httpx 의 읽기 30초·연결 5초는 단계별 상한이라 따로 묶는다). 타임아웃·연결 실패·5xx·429·408·계약 위반은
  1초 뒤 1회 재시도, 그 밖의 4xx 는 바로 실패. 결과 저장이 실패하면 `FAILED (INTERNAL_ERROR)` 로 남긴다 (PENDING 으로 두지 않는다).
- 장별 `script_chars` · `expected_ms` 는 `script_slides` 에, 응답의 `meta` 에 요청한 `target_ms` 를 더한 것은 `script_versions.parse_meta` 에 둔다.
  이 값들을 주지 않는 응답(지금 AI 연구 코드)이면 비워 둔다. 제한 시간만 바뀌면 다시 파싱하지 않는다 — 쓰는 쪽이 `expected_ms` 를 `parse_meta.target_ms` 에 대한 비율로 읽는다.
- 작업은 `BackgroundTasks` + `async`. **AI 를 기다리는 동안 DB 세션을 쥐지 않고**, 저장만 threadpool 의
  짧은 세션으로 한다 (`transcript_store.py` 와 같은 이유 — 같은 이벤트 루프에서 STT 가 돈다).
- 상태 전이는 전부 **조건부 UPDATE** 다. 재시도 두 번 눌러도 작업은 하나, 늦게 끝난 옛 작업은
  `parse_requested_at` 이 달라 새 결과를 못 덮는다. 만료가 지난 작업도 쓰지 못한다 (FAILED 가 DONE 으로 뒤집히지 않게).
- BE 가 재시작돼 작업이 사라지면 PENDING 이 남는다. 요청 후 **90초**가 지난 PENDING 은 읽을 때
  `FAILED (PARSE_EXPIRED)` 로 보여주고 재시도를 연다. 90초는 어댑터 최악 소요(30 × 2 + 1 = 61초)보다 길어야 한다 (테스트가 확인한다).
- 테스트는 `conftest.py` 가 파싱 작업을 기본으로 끈다(실제 AI 를 부르지 않게). 흐름 테스트는 가짜 파서를 끼운
  실제 runner 로 바꿔 끼운다 (`tests/test_script_parse.py`).
- 파싱된 `terms`(STT 가 틀리기 쉬운 용어)는 리허설 STT `keyterm` 으로 쓰인다 (위 실시간 STT 절).

## 실시간 코치 (AI)

계약은 `docs/ai/features/coach-agent.md` · `docs/ai/architecture.md` 다. 지금은 AI 를 부르는 코드(`coach/client.py`)와
요청 만들기(`coach/requests.py`)까지 있다. 발표 중 판단(WS `metrics` → evaluate → WS `coach`), 종료 뒤 Take 결과 저장,
코칭 계획을 부르는 곳은 #187 의 다음 PR 에서 붙인다.

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
- 테스트는 AI 예시 파일(`ai/research/coach-agent/src/coach/examples/`)을 직접 읽는다. backend CI 는 `backend/**` 가 바뀔 때만
  돌아서, AI 가 예시만 바꾼 PR 에서는 이 테스트가 다시 돌지 않는다.

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

1. `module/<name>/` 에 6 파일을 만든다 (`controller`, `service`, `repository`, `entity`, `dto`, `exception`).
2. `entities.py` 에 `import pitch_coach_backend.module.<name>.entity  # noqa: F401` 를 추가한다. 빠뜨리면 Alembic 이 테이블을 인식하지 못한다.
3. controller 에 `router = APIRouter(prefix="/<names>", tags=["<name>"])` 를 두고,
   `main.py` 의 `api.include_router(...)` 에 추가한다. `/api` 는 다시 적지 않는다.
4. `uv run alembic revision --autogenerate -m "..."` 로 마이그레이션을 만들고, 생성된 파일을 읽어 의도와 같은지 확인한 뒤 `uv run alembic upgrade head` 로 적용한다. 마이그레이션 파일은 커밋한다.
5. `tests/test_<name>.py` 를 만든다.

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
