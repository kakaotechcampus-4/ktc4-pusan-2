from typing import Literal

from pydantic import BaseModel


class Word(BaseModel):
    word: str
    start: float  # ms
    end: float  # ms


class SpeedResult(BaseModel):
    speed: float
    level: Literal["slow", "normal", "fast"]
    pauses: int
