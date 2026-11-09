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

# 인증 전에도 수신 메시지를 64KiB 로 제한한다. 최대 오디오 프레임은 32,008바이트다.
# 내릴 때 열린 요청을 5초까지만 기다리고 메모리의 기록을 저장하러 간다. 기본값은 끝없이 기다린다
# (main.shutdown_max_sec, infra/docker-compose.yml 의 stop_grace_period)
CMD ["uvicorn", "pitch_coach_backend.main:app", "--host", "0.0.0.0", "--port", "8000", "--ws-max-size", "65536", "--timeout-graceful-shutdown", "5"]
