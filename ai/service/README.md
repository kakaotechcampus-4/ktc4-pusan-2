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
- 실제 줄 앞에는 시각이 붙습니다(`2026-10-24 07:30:24,902 INFO …`). 이 README의 로그 예시는 시각을 줄였습니다.
- uvicorn 자체의 접근 로그(`"GET /health HTTP/1.1" 200 OK`)는 끕니다. 요청마다 위 줄과 겹치고, 쿼리 문자열을 그대로 싣고, 요청 ID가 없기 때문입니다. uvicorn의 시작 · 오류 로그는 그대로 나옵니다.
- `http://localhost:8001/docs`에서 API 목록과 요청 · 응답 형식을 보고 직접 요청을 보내 볼 수 있습니다.
- `--reload`는 코드를 저장하면 서버를 다시 띄웁니다. 개발할 때만 씁니다.
- Windows에서 실행 파일(`pytest.exe` · `uvicorn.exe`)이 막히면 위처럼 `uv run python -m …`으로 부릅니다.
- 서버를 띄우고 `/health`를 보는 데는 LLM 키가 필요 없습니다. LLM을 쓰는 기능을 실제로 부를 때만 아래 환경변수를 채웁니다.
- 시작할 때 대본 전달도의 형태소 분석기(Kiwi)를 따로 불러옵니다(로컬 약 1초). 그동안 `/health`는 200이고 `/ready`와 대본 전달도 경로는 503 `NOT_READY`입니다. 다 불러오면 서버 창에 `warmup_done name=get_kiwi duration_ms=…`가 남고 `/ready`가 200 `{"status":"ready"}`입니다.

## API 목록
계약(경로 · 요청 · 응답)의 기준은 기능 문서 5절과 [`docs/ai/architecture.md` 1-2절](../../docs/ai/architecture.md)이고, 코드에서 만든 OpenAPI 문서가 [`contracts/openapi.json`](contracts/openapi.json)입니다(`uv run python tools/export_openapi.py`로 다시 만듦). BE만 부르고, 경로에 `/api` 접두어 · 버전이 없습니다.

| 경로 | 하는 일 | 기능 문서 | 부르는 쪽 · 방식 |
|---|---|---|---|
| `GET /health` | 프로세스가 살아 있으면 200 | `ai-service.md` 4-2 | 배포 확인, 헬스체크 |
| `GET /ready` | 시작할 때 불러오는 모델까지 준비됐으면 200, 아니면 503 `NOT_READY` | `ai-service.md` 6-3 | compose 헬스체크 |
| `POST /scripts/rubric` | 대본 → 장별 평가 기준 (LLM, AI 예산 120초) | [`script-coverage-evaluation.md`](../../docs/ai/features/script-coverage-evaluation.md) 5절 | BE 백그라운드 |
| `POST /takes/analyze` | 장별 STT + 평가 기준 → 장별 채점 · Take 값 (LLM, AI 예산 120초) | 같음 | BE 백그라운드 |
| `POST /takes/rescore` | 저장한 채점 + 확인 답 → 다시 계산 (LLM 없음) | 같음 | BE 동기 |

## 환경변수
`settings.py`만 환경변수를 읽습니다. 로컬은 `ai/.env`(git에 올리지 않음)를 읽고, 서버는 compose가 넣는 값을 씁니다. `ai/.env.example`을 `ai/.env`로 복사해 키를 채웁니다. 목록과 규칙의 기준은 [`architecture.md` 10절](../../docs/ai/architecture.md)입니다.

| 변수 | 필수 | 기본값 | 뜻 |
|---|---|---|---|
| `OPENAI_BASE_URL` · `OPENAI_API_KEY` · `OPENAI_MODEL` | LLM 기능에 필요 | 없음 | 기본 모델 묶음(주소 · 키 · 모델 이름). 셋이 다 있거나 다 없어야 합니다. 다 없으면 서버는 뜨고 LLM 요청만 503 `LLM_UNAVAILABLE` |
| `OPENAI_SOL_BASE_URL` · `OPENAI_SOL_API_KEY` · `OPENAI_SOL_MODEL` | 선택 | 없음 | sol 모델 묶음. 쓰지 않으면 세 줄을 주석으로 둡니다 |
| `LLM_TIMEOUT_SECONDS` | 선택 | `12` | LLM 호출 한 번 전체의 시간 제한(초). 0보다 커야 합니다 |
| `LLM_MAX_RETRIES` | 선택 | `1` | LLM 재시도 횟수. 요청 마감 안에서만, 시간 초과 · 연결 실패 · 429 · 5xx일 때만 |
| `WEB_CONCURRENCY` | 선택 | `1` | uvicorn 워커 수. uvicorn 이 시작할 때 **프로세스 환경변수**에서 직접 읽습니다. compose `env_file` · `docker run --env-file`은 환경변수로 넣어 먹지만, `ai/.env`에만 적고 `uv run`으로 띄우면 먹지 않습니다(로컬은 `--workers 4`) |
| `SCRIPT_COVERAGE_LLM_TIMEOUT_SECONDS` | 선택 | 없음(`LLM_TIMEOUT_SECONDS`) | 대본 전달도 LLM 호출 한 번의 시간 제한(초). 서버에는 `20`(잰 값, 기능 문서 8절). 빠지면 12초로 돌아 장이 많은 대본에서 `LLM_TIMEOUT`이 늘어납니다. 어느 역할이 쓰는지는 `LLM_ROLES`의 `timeout_setting` |

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
- 출력이 긴 기능은 `settings.py`의 `LLM_ROLES`에 역할을 더하고, 실제 데이터로 잰 출력 길이로 `max_output_tokens`를 정합니다. 호출 한 번 시간 제한이 기능마다 다르면 `Settings`에 칸을 더하고 역할의 `timeout_setting`에 그 칸 이름을 적습니다(예: 대본 전달도).
- 시작할 때 한 번 불러올 서버 안 모델이 있으면 라우터 모듈에 `WARMUP = [불러오는 함수]`를 두고 `main.py`의 `_WARMUP`에 더하고, 라우터에 `dependencies=[Depends(require_ready)]`를 붙입니다(`common/readiness.py`, 준비 전 503 `NOT_READY`).
- 경로 · 요청 · 응답 모델을 바꾸면 `uv run python tools/export_openapi.py`로 `contracts/openapi.json`을 다시 만듭니다(`tests/test_openapi.py`가 같은지 봅니다).
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
- 의존성 설치 층은 `pyproject.toml` · `uv.lock`이 그대로면 캐시를 다시 써서, 코드만 고친 뒤의 빌드는 몇 초면 끝납니다. 다만 `python:3.12-slim` 태그가 새 판으로 바뀌면(보안 패치 등) 맨 아래 층부터 다시 받아 의존성 층도 다시 만듭니다.

- 이미지 크기는 약 896 MB입니다(대본 전달도의 형태소 분석기 모델 · scikit-learn을 더하기 전 374 MB).

### 대본 전달도 확인
저장소 맨 위에서 키 없이 띄우고(`docker run --rm -p 8001:8000 pitch-coach-ai:local`), 다른 창에서 봅니다. 서버는 몇 초 뒤 요청을 받기 시작하고, 형태소 분석기는 그 뒤 약 1초 만에 준비됩니다.
```powershell
curl.exe -i http://localhost:8001/ready
curl.exe -i -X POST http://localhost:8001/takes/rescore -H "Content-Type: application/json" --data-binary "@ai/service/src/pitch_coach_ai/features/script_coverage/examples/rescore/request.json"
```
```
HTTP/1.1 200 OK
{"status":"ready"}

HTTP/1.1 200 OK
{"take_id":"take-123","evaluations":[{"take_id":"take-123","script_name":"sv-3","slide_number":2, …
```
본문은 `features/script_coverage/examples/rescore/response.json`과 같습니다(LLM을 쓰지 않아 키가 필요 없습니다). 키 없이 `/scripts/rubric` · `/takes/analyze`를 부르면 503 `LLM_UNAVAILABLE`입니다. 컨테이너 로그에는 요청마다 `script_coverage op=rescore slides=3 targets=3 chars=0 failed=2 code=- duration_ms=…`처럼 장 수 · 글자 수만 남습니다(대본 · STT 원문은 남기지 않음).
- PowerShell 5.1은 따옴표가 든 JSON을 `curl.exe`에 그대로 넘기지 못해, 요청 본문은 UTF-8 파일(`--data-binary "@파일"`)로 보냅니다.

## 구조
```
ai/service/
├── .python-version · pyproject.toml · uv.lock
├── Dockerfile · .dockerignore
├── contracts/openapi.json        API 계약 (코드에서 만든다)
├── tools/export_openapi.py       contracts/openapi.json 을 만든다
├── src/pitch_coach_ai/
│   ├── main.py · settings.py
│   ├── api/health.py · script_coverage.py
│   ├── common/errors.py · request_log.py · llm.py · meta.py · readiness.py
│   └── features/
│       ├── script_coverage/   대본 전달도 코어 (파일 지도는 연구 폴더 README 3-2)
│       └── gaze/              시선 서버 판정 모듈 (파일 지도는 연구 폴더 README 3-2)
└── tests/
    ├── conftest.py · test_health.py · test_errors.py · test_request_log.py · test_openapi.py
    ├── unit/common/test_settings.py · test_llm.py · test_meta.py · test_readiness.py
    ├── unit/script_coverage/   대본 전달도 단위 · 계약 · 경계 · 엔드포인트 테스트
    ├── unit/gaze/              시선 판정 모듈 단위 · 계약 · 경계 테스트
    └── live/test_llm_live.py · test_script_coverage_live.py
```

| 파일 | 하는 일 | 입력 → 출력 | 부르는 곳 | 고칠 때 |
|---|---|---|---|---|
| `pyproject.toml` | 패키지 이름 · 의존성 범위 · ruff · pytest 설정(`live` 표시는 기본 실행에서 뺌) | — | uv | 의존성은 직접 고치지 않고 `uv add <패키지>`(도구는 `uv add --dev`), `uv.lock`과 함께 커밋 |
| `uv.lock` | 설치할 정확한 판 목록 | — | `uv sync --locked` | 손으로 고치지 않는다. `uv add` · `uv lock`이 만든다 |
| `Dockerfile` | 이미지 레시피: Python 3.12 + `uv.lock` 그대로의 운영 의존성 + `src`, `app` 사용자로 uvicorn 실행 | 이 폴더 → 이미지 | `docker build`, (배포) BE의 배포 워크플로 | 줄마다 이유는 파일 주석과 `docs/ai/ai-service.md` 11-1. 의존성 단계를 `COPY src`보다 앞에 둔다(캐시) |
| `.dockerignore` | 엔진에 보내지 않을 파일(`.venv` · `.env` · 캐시 · `tests` · `contracts` · `tools`) | — | `docker build` | 주석은 자기 줄에만 (줄 끝 주석은 패턴이 된다). 하위 폴더까지 거르려면 `**/` |
| `src/pitch_coach_ai/main.py` | 설정을 읽고 앱을 만들어 예외 처리 · 요청 로그 · 라우터를 붙인다 (`create_app()`, `app`). lifespan 에서 시작할 때 불러올 모델(`_WARMUP`)을 따로 불러오기 시작한다 | 설정 → FastAPI 앱 | uvicorn, 테스트 `client` | 새 라우터를 `create_app`에 `include_router`, 불러올 모델은 `_WARMUP`에 |
| `src/pitch_coach_ai/settings.py` | 환경변수 읽기(`settings`), 모델 묶음 검사, 역할 → 묶음 · 출력 상한 · 기능별 호출 시간 제한(`LLM_ROLES`, `timeout_for`) | 환경변수 · `ai/.env` → `Settings` | `main.py` · `common/llm.py` | 새 변수는 기본값과 함께 더하고 `ai/.env.example` · 위 표도 |
| `src/pitch_coach_ai/api/health.py` | `GET /health` · `GET /ready` | — → 200 `{"status": "ok"}` · 200 `{"status": "ready"}` 또는 503 `NOT_READY` | compose 헬스체크, 배포 확인 | LLM을 부르지 않게 둔다 |
| `src/pitch_coach_ai/api/script_coverage.py` | 대본 전달도 세 경로. 요청마다 마감(120초)과 역할 다섯 개의 LLM 객체를 만들어 코어에 넘기고, 코어의 `CoverageError` 를 공통 에러 본문으로, `meta.model` 을 응답한 모델로. 장 수 · 글자 수만 로그 | 기능 문서 5-2 요청 → 5-3 응답 | BE | LLM 만들기는 `get_llm_factory` 의존성(테스트가 가짜로 바꾼다). 시작할 때 Kiwi(`WARMUP`) |
| `src/pitch_coach_ai/common/errors.py` | 예외 → `{"error": {"code", "message"}}` · 상태 코드. `AppError` 와 LLM 예외 넷, `NotReady`(503), OpenAPI 에 싣는 공통 에러 본문(`ErrorResponse` · `ERROR_RESPONSES`) | 예외 → 에러 응답 | `main.py`(등록), `common/llm.py`(던짐), 기능(상속) | 새 공통 코드는 `architecture.md` 4절을 먼저 바꾼다 |
| `src/pitch_coach_ai/common/request_log.py` | `X-Request-ID`(받은 그대로 · 없으면 생성), 처리되지 않은 예외 → 500, 요청마다 한 줄 로그 | 요청 → 응답 + 로그 | `main.py` | 로그에 요청 본문 · 쿼리 · 키를 넣지 않는다 |
| `src/pitch_coach_ai/common/llm.py` | `structured_llm` · `deadline_after`: 구조화 출력 LLM, 호출 시간 제한 · 재시도 · 요청 마감 · 실패 분류 · 응답 모델 기록 · 호출 로그 | 스키마 · 역할 · 마감 → `.invoke` 를 가진 객체 | 라우터(코어에 넘김) | 시간 · 재시도는 환경변수, 묶음 · 상한은 `LLM_ROLES` |
| `src/pitch_coach_ai/common/readiness.py` | 시작할 때 불러오는 모델의 준비 상태(`Readiness`), 준비 전 503 `NOT_READY` 의존성(`require_ready`), `warmup_done` · `warmup_failed` 로그 | 불러오는 함수 목록 → 준비됨 | `main.py` · 모델을 쓰는 라우터 · `/ready` | 불러오기가 실패하면 uvicorn 이 쓰는 앱(`main.app`)만 프로세스를 끝낸다. 워커 하나면 compose 재시작 정책이 컨테이너를, 여러 워커면 uvicorn 이 워커를 다시 띄운다(unhealthy 표시만으로는 재시작되지 않는다). `require_ready` · `/health` · `/ready` 는 `async def`(스레드 풀이 차도 기다리지 않음) |
| `src/pitch_coach_ai/common/meta.py` | 응답 `meta`(`schema_version` · `feature_version` · `model`, 판정 응답은 `criteria_versions`) | 기능 `FEATURE_VERSION`(`<모듈>-<버전>`) · LLM → `Meta` | 라우터 | `criteria_version` 해시는 코치 연구 코드와 같은 방식으로 둔다 |
| `src/pitch_coach_ai/features/script_coverage/` | 대본 전달도 코어(`docs/ai/features/script-coverage-evaluation.md`). 진입 함수 `core.py`의 `analyze_script` · `evaluate_take` · `rescore_evaluation`, 요청 · 응답 `schemas.py`, 기준값 `config.py`, `version.py`(`script_coverage-1.1`), `examples/`(세 API 예시와 그 LLM 캐시), 하위 `shared/` · `script_analysis/` · `stt_evaluation/`. 파일마다의 역할은 연구 폴더 README 3-2 | 대본 · STT · 평가 기준 + LLM 객체 → 평가 기준 · 채점 | `api/script_coverage.py` | 연구 폴더(`ai/research/script-coverage-evaluation`)가 이 코어를 editable로 import한다. 코어는 여기서만 고치고, 바꾼 뒤 연구 캐시 재생으로 결과를 비교한다(연구 README '옮긴 뒤 검증'). 프롬프트 · 스키마 문자열은 캐시 키라 줄을 나누지 않는다(E501 제외) |
| `src/pitch_coach_ai/features/gaze/` | 시선 서버 판정 모듈(`docs/ai/features/gaze-tracking.md`). `core.py`의 `judge` · `summarize` · `criteria`, 입력 · 결과 `schemas.py`, 기준값 `config.py`, `version.py`(`gaze-1.3`), `examples/`. 자기 엔드포인트는 없고 코치가 판정 모듈 묶음으로 쓴다(코치 `evaluate` · `finalize`를 열 때). 파일마다의 역할은 연구 폴더(`ai/research/gaze-tracking`) README 3-2 | 1초 기록 · 커서 · 판정 시각 → `[JudgmentResult]` | (코치 라우터, 아직 없음) | 연구 폴더가 editable로 import한다(`gaze_lab` · `tools/make_examples.py`). 기준값을 바꾸면 연구 폴더 `tools/make_examples.py`로 예시를 다시 만든다 |
| `tests/conftest.py` | 묶음 환경변수를 비운 뒤 앱 import(실제 키를 읽지 않음), `client` · `ready_client`(시작할 때 불러오는 모델까지 기다림) · `probe_client`(실패를 내는 테스트 경로) · `log_records` | — | 모든 앱 테스트 | — |
| `tests/test_health.py` | `/health` 200(fixture 앱 · uvicorn 이 쓰는 `app` 둘 다), 등록된 경로에 `/api` 접두어 없음 | — | pytest | — |
| `tests/test_errors.py` · `test_request_log.py` | 에러 본문 · 상태 코드, `X-Request-ID`, 요청 로그(본문 · 쿼리 · 입력값 없음) | — | pytest | — |
| `tests/test_openapi.py` | 커밋된 `contracts/openapi.json` 이 코드와 같은지, 등록된 경로 목록, 에러 응답이 공통 본문으로 문서화됐는지 | — | pytest | 다르면 `tools/export_openapi.py` 로 다시 만든다 |
| `tests/unit/common/` | 설정(기능별 시간 제한 포함) · LLM(가짜 LLM) · meta · 준비 상태 단위 테스트 | — | pytest | — |
| `tests/unit/script_coverage/` | 대본 전달도 단위 · 계약(`test_examples.py`, 예시와 그 LLM 캐시) · 코어 경계(`test_core_boundary.py`, 코어 패키지 위치 기준) · 엔드포인트(`test_api.py`) · 마감이 중간에 지날 때(`test_deadline.py`) 테스트. 가짜 LLM `fakes.py` | — | pytest | 연구 lab 테스트가 `fakes.py` · `shared/slides.py`의 사본을 쓴다 |
| `tests/unit/gaze/` | 시선 판정 모듈 단위(`judge` · `summarize`) · 계약(`test_examples.py`) · 코어 경계 테스트. 소리 크기 기준값 비교 하나는 `features/volume/`이 올 때까지 건너뜀 | — | pytest | — |
| `tests/live/test_llm_live.py` | 실제 LLM 1회 호출 (과금) | `ai/.env` 기본 묶음 | `pytest -m live` | 기본 실행에서 빠진다 |
| `tests/live/test_script_coverage_live.py` | 대본 전달도를 실제 LLM 으로 장 하나 끝까지(평가 기준 → 채점, 약 4~5번 호출, 과금) | `ai/.env` 기본 묶음 | `pytest -m live` | 기본 실행에서 빠진다 |

## 테스트
```powershell
uv run python -m pytest              # 가짜 LLM, 키 · 과금 없음
```
```
651 passed, 1 skipped, 2 deselected
```
```powershell
uv run python -m pytest -m live      # 실제 LLM 약 6번 (ai/.env 의 기본 묶음, 과금)
```
```
2 passed, 652 deselected
```
LLM 호출마다 한 줄이 남습니다(프롬프트 · 답 · 키는 남기지 않음).
```
INFO pitch_coach_ai.llm llm_call request_id=- role=default model=gpt-6-luna attempt=1 outcome=ok cause=- tokens_in=79 tokens_out=35 duration_ms=1953.0
```
