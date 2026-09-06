FROM python:3.12-slim
WORKDIR /app

# 로그 버퍼링 비활성화, 실시간으로 로그 확인 가능
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# 의존성 설치
COPY pyproject.toml ./
# 소스코드 복사
COPY src/ ./src/
RUN pip install --no-cache-dir .

# alembic 마이그레이션 복사
COPY alembic/ ./alembic/

# 포트 8000 노출 명시
EXPOSE 8000

# uvicorn 실행, 8000번 포트로 외부 접속 허용
CMD ["uvicorn", "pitch_coach_backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
