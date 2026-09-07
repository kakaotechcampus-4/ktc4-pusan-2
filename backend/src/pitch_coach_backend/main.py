from fastapi import FastAPI

from pitch_coach_backend import entities  # noqa: F401  # 모든 entity 를 매퍼에 등록

app = FastAPI(title="Pitch Coach API", version="0.1.0")


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}
