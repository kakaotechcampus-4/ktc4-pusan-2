FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /bin/uv

WORKDIR /app

# 로그 버퍼링 비활성화, 실시간으로 로그 확인 가능
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src \
    UV_PYTHON_DOWNLOADS=0
ENV PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-cache

COPY src/ ./src/

# alembic 마이그레이션 복사
# alembic.ini 파일은 config 파일과 유사한 개념이라, 반드시 복사가 필요하다.
COPY alembic.ini ./
COPY alembic/ ./alembic/

# 포트 8000 노출 명시
EXPOSE 8000

# uvicorn 실행, 8000번 포트로 외부 접속 허용
CMD ["uvicorn", "pitch_coach_backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
