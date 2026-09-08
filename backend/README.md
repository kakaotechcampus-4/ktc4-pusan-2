# Pitch Coach Backend

FastAPI 기반 백엔드. 발표(pitch) 연습을 녹음·실시간 세션으로 수행하고 LLM 피드백을 제공한다.

## 기술 스택

| 영역 | 선택 |
|---|---|
| 언어 / 패키지 관리 | Python 3.14, uv |
| 웹 프레임워크 | FastAPI |
| ORM / 마이그레이션 | SQLAlchemy 2.x, Alembic |
| DB | PostgreSQL 17 (드라이버 psycopg 3) |
| 인증 | JWT (PyJWT). Google OAuth 2.0 은 별도 브랜치에서 진행 |
| 비동기 작업 / 캐시 | Celery + Redis (예정) |
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
- CI 는 PostgreSQL 서비스와 `DATABASE_URL` 만 제공하면 된다. `JWT_SECRET_KEY` 는 conftest 가 테스트용 값을 넣는다.

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
├── tests/
│   ├── conftest.py               # 테스트 DB 자동 생성, 트랜잭션 격리 세션, TestClient fixture
│   ├── test_health.py
│   ├── test_security.py          # JWT 발급/검증
│   ├── test_exceptions.py        # 에러 응답 형식 계약
│   ├── test_user_entity.py
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
    │   └── redis.py              # Redis 클라이언트 (realtime 시작 시 생성)
    │
    ├── module/                   # 도메인. 폴더마다 아래 6 파일을 같은 이름으로 둔다
    │   ├── auth/                 # Google OAuth, JWT 발급, get_current_user
    │   │   ├── controller.py
    │   │   ├── service.py
    │   │   ├── repository.py
    │   │   ├── entity.py         # OAuthAccount
    │   │   ├── dto.py
    │   │   ├── dependencies.py   # get_current_user (auth 에만 있음)
    │   │   └── exception.py
    │   ├── user/                 # User
    │   ├── pitch/                # 발표 원고/자료
    │   ├── take/                 # 발표 1회 수행 기록
    │   ├── rehearsal/            # 실시간 연습 세션
    │   └── feedback/             # LLM 리뷰 결과
    │
    ├── realtime/                 # WebSocket. entity 를 갖지 않고 저장은 rehearsal service 에 위임
    │   ├── controller.py         # 연결/인증/송수신
    │   ├── service.py            # 실시간 처리 orchestration
    │   ├── event_ingestion.py    # 이벤트 검증/정규화/중복 제거
    │   ├── state_aggregator.py   # 시간 정렬/sliding window/state 생성
    │   ├── session_store.py      # Redis session/lease/checkpoint
    │   └── stt_adapter.py        # Streaming STT 연결/결과 정규화
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
