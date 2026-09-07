from fastapi import FastAPI

from pitch_coach_backend import entities  # noqa: F401  # 모든 entity 를 매퍼에 등록
from pitch_coach_backend.core.exceptions import register_exception_handlers

app = FastAPI(title="Pitch Coach API", version="0.1.0")
register_exception_handlers(app)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}
