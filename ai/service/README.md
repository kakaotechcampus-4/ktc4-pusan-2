# AI 서비스 (`ai/service`)

AI 기능을 BE에 HTTP API로 여는 서버입니다. 이 폴더 하나가 Docker 이미지 하나이자 배포 단위이고, BE만 Docker 내부망 `http://ai:8000`으로 부릅니다. 설계는 [`docs/ai/ai-service.md`](../../docs/ai/ai-service.md), 기능 사이 공통 계약(에러 본문 · `meta` · 시간 예산 · 환경변수)은 [`docs/ai/architecture.md`](../../docs/ai/architecture.md)에 있습니다.

## 빠른 시작
준비: [uv](https://docs.astral.sh/uv/) 0.11. Python 3.12는 uv가 필요할 때 받습니다(`.python-version`).

```powershell
cd ai/service
uv sync --locked                    # .venv 를 만들고 uv.lock 그대로 설치 (lock 을 바꾸지 않음, 테스트 도구 포함)
uv run python -m pytest             # 테스트 (가짜 LLM, 키 · 과금 없음)
uv run ruff check .                 # 코드 검사
uv run python -m uvicorn pitch_coach_ai.main:app --reload --port 8001
```
다른 창에서:
```powershell
curl.exe -i http://localhost:8001/health
```
```
HTTP/1.1 200 OK
x-request-id: 4f1c…(32자리)
{"status":"ok"}
```
서버 창에는 요청마다 한 줄이 남습니다.
```
INFO pitch_coach_ai.request request_id=4f1c… method=GET path=/health status=200 code=- duration_ms=0.9
```
- uvicorn 자체의 접근 로그(`"GET /health HTTP/1.1" 200 OK`)는 끕니다. 요청마다 위 줄과 겹치고, 쿼리 문자열을 그대로 싣고, 요청 ID가 없기 때문입니다. uvicorn의 시작 · 오류 로그는 그대로 나옵니다.
- `http://localhost:8001/docs`에서 API 목록과 요청 · 응답 형식을 보고 직접 요청을 보내 볼 수 있습니다.
- `--reload`는 코드를 저장하면 서버를 다시 띄웁니다. 개발할 때만 씁니다.
- Windows에서 실행 파일(`pytest.exe` · `uvicorn.exe`)이 막히면 위처럼 `uv run python -m …`으로 부릅니다.
- 서버를 띄우고 `/health`를 보는 데는 LLM 키가 필요 없습니다. LLM을 쓰는 기능을 실제로 부를 때만 아래 환경변수를 채웁니다.

## 환경변수
`settings.py`만 환경변수를 읽습니다. 로컬은 `ai/.env`(git에 올리지 않음)를 읽고, 서버는 compose가 넣는 값을 씁니다. `ai/.env.example`을 `ai/.env`로 복사해 키를 채웁니다. 목록과 규칙의 기준은 [`architecture.md` 10절](../../docs/ai/architecture.md)입니다.

| 변수 | 필수 | 기본값 | 뜻 |
|---|---|---|---|
| `OPENAI_BASE_URL` · `OPENAI_API_KEY` · `OPENAI_MODEL` | LLM 기능에 필요 | 없음 | 기본 모델 묶음(주소 · 키 · 모델 이름). 셋이 다 있거나 다 없어야 합니다. 다 없으면 서버는 뜨고 LLM 요청만 503 `LLM_UNAVAILABLE` |
| `OPENAI_SOL_BASE_URL` · `OPENAI_SOL_API_KEY` · `OPENAI_SOL_MODEL` | 선택 | 없음 | sol 모델 묶음. 쓰지 않으면 세 줄을 주석으로 둡니다 |
| `LLM_TIMEOUT_SECONDS` | 선택 | `12` | LLM 호출 한 번 전체의 시간 제한(초). 0보다 커야 합니다 |
| `LLM_MAX_RETRIES` | 선택 | `1` | LLM 재시도 횟수. 요청 마감 안에서만, 시간 초과 · 연결 실패 · 429 · 5xx일 때만 |
| `WEB_CONCURRENCY` | 선택 | `1` | uvicorn 워커 수. uvicorn 이 시작할 때 **프로세스 환경변수**에서 직접 읽습니다. compose `env_file` · `docker run --env-file`은 환경변수로 넣어 먹지만, `ai/.env`에만 적고 `uv run`으로 띄우면 먹지 않습니다(로컬은 `--workers 4`) |

- 값은 따옴표와 줄 끝 주석 없이 `이름=값`으로 적습니다(`docker run --env-file`은 따옴표를 값에 그대로 넣습니다). 빈 값(`OPENAI_API_KEY=`)은 "없음"으로 봅니다.
- **묶음이 일부만 차 있으면 서버가 시작하지 않습니다.** 키를 빠뜨린 실수를 배포 직후 헬스체크에서 바로 보이게 하려는 것입니다.
  ```
  Value error, OPENAI_SOL_* 묶음이 일부만 채워졌습니다. 빈 칸: OPENAI_SOL_API_KEY (셋 다 채우거나, 쓰지 않으면 세 줄 모두 주석으로 둡니다)
  ```
- 기능이 어느 묶음을 쓰는지(역할 → 묶음)와 역할별 출력 토큰 상한은 환경변수가 아니라 `settings.py`의 `LLM_ROLES` 상수입니다. 바꾸는 것은 측정을 함께 실은 PR로 합니다.

## 기능을 붙일 때 공통 모듈 쓰는 법
라우터는 `def`로 쓰고(코어가 동기라 스레드 풀에서 돈다), 요청마다 마감 하나를 만들어 LLM 객체에 넘깁니다. 코어는 받은 객체의 `.invoke`만 씁니다.
```python
from fastapi import APIRouter

from pitch_coach_ai.common.llm import deadline_after, structured_llm
from pitch_coach_ai.common.meta import build_meta

router = APIRouter(prefix="/scripts", tags=["scripts"])


@router.post("/parse")
def parse(req: ParseRequest) -> ParseResponse:
    llm = structured_llm(ParsedScript, deadline=deadline_after(25))  # 역할 기본값 "default"
    result = parse_script(req, llm=llm)  # 코어 안에서 llm.invoke(...)
    return ParseResponse(
        **result.model_dump(),
        meta=build_meta(FEATURE_VERSION, llm=llm),  # "script_parser-1.0"
    )
```
- LLM 실패는 기능에서 잡지 않습니다. `structured_llm`이 공통 예외(`LLM_TIMEOUT` 504 · `LLM_ERROR` 502 · `LLM_INVALID_OUTPUT` 502 · `LLM_UNAVAILABLE` 503)를 던지고, `common/errors.py`가 `{"error": {"code", "message"}}`로 바꿉니다.
- 기본 결과를 내는 기능(코칭 계획 · 리뷰)은 예외의 `code`를 `fallback_reason`으로 쓰고 `build_meta(..., fallback=True)`로 `meta.model`을 null로 둡니다. 설정이 없으면 `structured_llm`이 바로 `LlmUnavailable`을 던지므로, 잡아서 `llm=None`으로 코어를 부릅니다.
- 기능이 직접 알려야 하는 실패(예: 분석할 발화가 너무 적음)는 `AppError`를 상속해 422로 만듭니다(`common/errors.py` 머리 설명의 예).
- 출력이 긴 기능은 `settings.py`의 `LLM_ROLES`에 역할을 더하고, 실제 데이터로 잰 출력 길이로 `max_output_tokens`를 정합니다.
- LLM은 스트리밍으로 부릅니다. 지금 게이트웨이는 스트리밍이 아닌 요청의 출력 상한을 2,000토큰 이하로만 받기 때문입니다(넘으면 400). 기능 코드는 이 차이를 모릅니다.

## Docker 이미지로 실행 (배포와 같은 이미지)
준비: Docker Desktop을 켜고 `docker version`에 `Server:` 블록이 나오는지 봅니다. 저장소 맨 위에서:
```powershell
docker build -t pitch-coach-ai:local ai/service
docker run --rm -p 8001:8000 pitch-coach-ai:local
```
```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```
다른 창에서 `curl.exe http://localhost:8001/health` → `{"status":"ok"}`. Ctrl+C로 끕니다.
- `-p 8001:8000`은 내 PC의 8001번을 컨테이너의 8000번에 잇습니다. 컨테이너는 자기 네트워크를 쓰므로 이 연결이 없으면 밖에서 닿지 않습니다.
- `--rm`은 끌 때 컨테이너를 지웁니다. 이미지는 남습니다(`docker images pitch-coach-ai`로 크기 확인).
- LLM 기능을 부를 때는 `--env-file ai/.env`를 더합니다. 키는 이미지에 넣지 않습니다.
- 의존성 설치 층은 `pyproject.toml` · `uv.lock`이 그대로면 캐시를 다시 써서, 코드만 고친 뒤의 빌드는 몇 초면 끝납니다.

## 구조
```
ai/service/
├── .python-version · pyproject.toml · uv.lock
├── Dockerfile · .dockerignore
├── src/pitch_coach_ai/
│   ├── main.py · settings.py
│   ├── api/health.py
│   └── common/errors.py · request_log.py · llm.py · meta.py
└── tests/
    ├── conftest.py · test_health.py · test_errors.py · test_request_log.py
    ├── unit/common/test_settings.py · test_llm.py · test_meta.py
    └── live/test_llm_live.py
```

| 파일 | 하는 일 | 입력 → 출력 | 부르는 곳 | 고칠 때 |
|---|---|---|---|---|
| `pyproject.toml` | 패키지 이름 · 의존성 범위 · ruff · pytest 설정(`live` 표시는 기본 실행에서 뺌) | — | uv | 의존성은 직접 고치지 않고 `uv add <패키지>`(도구는 `uv add --dev`), `uv.lock`과 함께 커밋 |
| `uv.lock` | 설치할 정확한 판 목록 | — | `uv sync --locked` | 손으로 고치지 않는다. `uv add` · `uv lock`이 만든다 |
| `Dockerfile` | 이미지 레시피: Python 3.12 + `uv.lock` 그대로의 운영 의존성 + `src`, `app` 사용자로 uvicorn 실행 | 이 폴더 → 이미지 | `docker build`, (배포) BE의 배포 워크플로 | 줄마다 이유는 파일 주석과 `docs/ai/ai-service.md` 11-1. 의존성 단계를 `COPY src`보다 앞에 둔다(캐시) |
| `.dockerignore` | 엔진에 보내지 않을 파일(`.venv` · `.env` · 캐시 · `tests` · `contracts`) | — | `docker build` | 주석은 자기 줄에만 (줄 끝 주석은 패턴이 된다). 하위 폴더까지 거르려면 `**/` |
| `src/pitch_coach_ai/main.py` | 설정을 읽고 앱을 만들어 예외 처리 · 요청 로그 · 라우터를 붙인다 (`create_app()`, `app`) | 설정 → FastAPI 앱 | uvicorn, 테스트 `client` | 새 라우터를 `create_app`에 `include_router` |
| `src/pitch_coach_ai/settings.py` | 환경변수 읽기(`settings`), 모델 묶음 검사, 역할 → 묶음 · 출력 상한(`LLM_ROLES`) | 환경변수 · `ai/.env` → `Settings` | `main.py` · `common/llm.py` | 새 변수는 기본값과 함께 더하고 `ai/.env.example` · 위 표도 |
| `src/pitch_coach_ai/api/health.py` | `GET /health` | — → 200 `{"status": "ok"}` | compose 헬스체크, 배포 확인 | LLM을 부르지 않게 둔다 |
| `src/pitch_coach_ai/common/errors.py` | 예외 → `{"error": {"code", "message"}}` · 상태 코드. `AppError` 와 LLM 예외 넷 | 예외 → 에러 응답 | `main.py`(등록), `common/llm.py`(던짐), 기능(상속) | 새 공통 코드는 `architecture.md` 4절을 먼저 바꾼다 |
| `src/pitch_coach_ai/common/request_log.py` | `X-Request-ID`(받은 그대로 · 없으면 생성), 처리되지 않은 예외 → 500, 요청마다 한 줄 로그 | 요청 → 응답 + 로그 | `main.py` | 로그에 요청 본문 · 쿼리 · 키를 넣지 않는다 |
| `src/pitch_coach_ai/common/llm.py` | `structured_llm` · `deadline_after`: 구조화 출력 LLM, 호출 시간 제한 · 재시도 · 요청 마감 · 실패 분류 · 응답 모델 기록 · 호출 로그 | 스키마 · 역할 · 마감 → `.invoke` 를 가진 객체 | 라우터(코어에 넘김) | 시간 · 재시도는 환경변수, 묶음 · 상한은 `LLM_ROLES` |
| `src/pitch_coach_ai/common/meta.py` | 응답 `meta`(`schema_version` · `feature_version` · `model`, 판정 응답은 `criteria_versions`) | 기능 `FEATURE_VERSION`(`<모듈>-<버전>`) · LLM → `Meta` | 라우터 | `criteria_version` 해시는 코치 연구 코드와 같은 방식으로 둔다 |
| `tests/conftest.py` | 묶음 환경변수를 비운 뒤 앱 import(실제 키를 읽지 않음), `client` · `probe_client`(실패를 내는 테스트 경로) · `log_records` | — | 모든 앱 테스트 | — |
| `tests/test_health.py` | `/health` 200(fixture 앱 · uvicorn 이 쓰는 `app` 둘 다), 등록된 경로에 `/api` 접두어 없음 | — | pytest | — |
| `tests/test_errors.py` · `test_request_log.py` | 에러 본문 · 상태 코드, `X-Request-ID`, 요청 로그(본문 · 쿼리 · 입력값 없음) | — | pytest | — |
| `tests/unit/common/` | 설정 · LLM(가짜 LLM) · meta 단위 테스트 | — | pytest | — |
| `tests/live/test_llm_live.py` | 실제 LLM 1회 호출 (과금) | `ai/.env` 기본 묶음 | `pytest -m live` | 기본 실행에서 빠진다 |

## 테스트
```powershell
uv run python -m pytest              # 가짜 LLM, 키 · 과금 없음
```
```
58 passed, 1 deselected
```
```powershell
uv run python -m pytest -m live      # 실제 LLM 1회 (ai/.env 의 기본 묶음, 과금)
```
```
1 passed, 58 deselected
```
LLM 호출마다 한 줄이 남습니다(프롬프트 · 답 · 키는 남기지 않음).
```
INFO pitch_coach_ai.llm llm_call request_id=- role=default model=gpt-6-luna attempt=1 outcome=ok cause=- tokens_in=79 tokens_out=35 duration_ms=1953.0
```
