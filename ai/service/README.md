# AI 서비스 (`ai/service`)

AI 기능을 BE에 HTTP API로 여는 서버입니다. 이 폴더 하나가 Docker 이미지 하나이자 배포 단위이고, BE만 Docker 내부망 `http://ai:8000`으로 부릅니다. 설계는 [`docs/ai/ai-service.md`](../../docs/ai/ai-service.md), 기능 사이 공통 계약(에러 본문 · `meta` · 시간 예산 · 환경변수)은 [`docs/ai/architecture.md`](../../docs/ai/architecture.md)에 있습니다.

## 빠른 시작
준비: [uv](https://docs.astral.sh/uv/) 0.11. Python 3.12는 uv가 필요할 때 받습니다(`.python-version`).

```powershell
cd ai/service
uv sync --locked                    # .venv 를 만들고 uv.lock 그대로 설치 (lock 을 바꾸지 않음, 테스트 도구 포함)
uv run python -m pytest             # 테스트
uv run ruff check .                 # 코드 검사
uv run python -m uvicorn pitch_coach_ai.main:app --reload --port 8001
```
다른 창에서:
```powershell
curl.exe http://localhost:8001/health
```
```
{"status":"ok"}
```
- `http://localhost:8001/docs`에서 API 목록과 요청 · 응답 형식을 보고 직접 요청을 보내 볼 수 있습니다.
- `--reload`는 코드를 저장하면 서버를 다시 띄웁니다. 개발할 때만 씁니다.
- Windows에서 실행 파일(`pytest.exe` · `uvicorn.exe`)이 막히면 위처럼 `uv run python -m …`으로 부릅니다.

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
- 의존성 설치 층은 `pyproject.toml` · `uv.lock`이 그대로면 캐시를 다시 써서, 코드만 고친 뒤의 빌드는 몇 초면 끝납니다.

## 구조
```
ai/service/
├── .python-version · pyproject.toml · uv.lock
├── Dockerfile · .dockerignore
├── src/pitch_coach_ai/
│   ├── main.py
│   └── api/health.py
└── tests/
    ├── conftest.py
    └── test_health.py
```

| 파일 | 하는 일 | 입력 → 출력 | 부르는 곳 | 고칠 때 |
|---|---|---|---|---|
| `pyproject.toml` | 패키지 이름 · 의존성 범위 · ruff · pytest 설정 | — | uv | 의존성은 직접 고치지 않고 `uv add <패키지>`(도구는 `uv add --dev`), `uv.lock`과 함께 커밋 |
| `uv.lock` | 설치할 정확한 판 목록 | — | `uv sync --locked` | 손으로 고치지 않는다. `uv add` · `uv lock`이 만든다 |
| `Dockerfile` | 이미지 레시피: Python 3.12 + `uv.lock` 그대로의 운영 의존성 + `src`, `app` 사용자로 uvicorn 실행 | 이 폴더 → 이미지 | `docker build`, (배포) BE의 배포 워크플로 | 줄마다 이유는 파일 주석과 `docs/ai/ai-service.md` 11-1. 의존성 단계를 `COPY src`보다 앞에 둔다(캐시) |
| `.dockerignore` | 엔진에 보내지 않을 파일(`.venv` · `.env` · 캐시 · `tests` · `contracts`) | — | `docker build` | 주석은 자기 줄에만 (줄 끝 주석은 패턴이 된다) |
| `src/pitch_coach_ai/main.py` | 앱을 만들고 라우터를 붙인다 (`create_app()`, `app`) | — → FastAPI 앱 | uvicorn, 테스트 `client` | 새 라우터를 `create_app`에 `include_router` |
| `src/pitch_coach_ai/api/health.py` | `GET /health` | — → 200 `{"status": "ok"}` | compose 헬스체크, 배포 확인 | LLM을 부르지 않게 둔다 |
| `tests/conftest.py` | `client` fixture (테스트마다 새 앱) | — → `TestClient` | 모든 앱 테스트 | — |
| `tests/test_health.py` | `/health` 200, `/api` 접두어 없음 | — | pytest | — |

## 테스트
```powershell
uv run python -m pytest
```
```
2 passed
```
