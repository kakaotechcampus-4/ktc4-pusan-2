FROM python:3.12-slim

# uv 실행 파일만 공식 이미지에서 가져온다 (로컬 uv와 같은 0.11 계열. lock 형식이 같은 계열에서 맞는다)
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /bin/uv

WORKDIR /app

# 로그를 바로 출력하고, .pyc 파일을 만들지 않는다.
# PYTHONPATH: 프로젝트를 설치하지 않으므로(--no-install-project) src 아래 pitch_coach_ai 패키지를 찾게 한다
# UV_PYTHON_DOWNLOADS=0: 이미지에 있는 Python 3.12만 쓰고 uv가 Python을 따로 받지 않게 한다
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/src UV_PYTHON_DOWNLOADS=0
# CMD 의 uvicorn 을 .venv 에서 찾는다
ENV PATH="/app/.venv/bin:$PATH"

# 1) 의존성 먼저 설치한다. 소스만 바뀌면 이 단계는 캐시를 다시 써서 빌드가 빠르다
#    --locked: uv.lock 과 pyproject.toml 이 어긋나면 실패한다 (로컬 · CI · 이미지 버전 일치)
#    --no-dev: pytest · ruff 같은 개발 도구는 넣지 않는다
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --no-cache

# 2) 소스를 복사한다
COPY src ./src

# 3) root가 아닌 사용자로 실행한다 (LLM 키를 다루는 서버라 권한을 줄인다)
RUN useradd --create-home app
USER app

# 컨테이너 안에서 8000번으로 듣는다. --host 0.0.0.0 이 없으면 컨테이너 밖(-p 연결)에서 닿지 않는다.
# --reload 는 넣지 않는다 (개발용)
EXPOSE 8000
CMD ["uvicorn", "pitch_coach_ai.main:app", "--host", "0.0.0.0", "--port", "8000"]
