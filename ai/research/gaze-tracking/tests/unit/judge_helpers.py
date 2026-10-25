"""judge 테스트용 입력 만들기. 1초 기록은 1000 ms 격자, 요청 t_ms 는 마지막 기록이 끝난 시각이다."""

from __future__ import annotations

from typing import Any

from gaze.config import DEFAULT, GazeConfig
from gaze.core import judge
from gaze.schemas import JudgmentResult

CFG = DEFAULT


def rec(t_ms: int, state: str, **kw: Any) -> dict[str, Any]:
    """1초 기록 하나. 기본은 잘 잰 1초(신뢰도 · 득표 높음)."""
    base = {
        "t_ms": t_ms,
        "duration_ms": 1000,
        "state": state,
        "direction": None,
        "confidence": 0.9,
        "reliability": 0.95,
        "issues": [],
        "frames": 8,
    }
    return {**base, **kw}


def secs(*spec: tuple[str, int], t0: int = 0) -> list[dict[str, Any]]:
    """("CAMERA", 3), ("BOTTOM", 2) → 0~5초의 1초 기록. "GAP" 은 기록을 보내지 않은 1초."""
    out, t = [], t0
    for state, n in spec:
        for _ in range(n):
            if state != "GAP":
                out.append(rec(t, state))
            t += 1000
    return out


def spoken(t0: int, t1: int, voiced_ms: int = 800) -> list[dict[str, int]]:
    """[t0, t1) 의 1초마다 발화 시간."""
    return [{"t_ms": t, "voiced_ms": voiced_ms} for t in range(t0, t1, 1000)]


def run(
    records: list[dict[str, Any]],
    t_ms: int,
    since_ms: int | None = None,
    voiced: list[dict[str, int]] | None = None,
    config: GazeConfig = CFG,
) -> JudgmentResult:
    """판정 하나. since_ms 를 주지 않으면 마지막 1초(t_ms − 1000)만 새것이다."""
    inputs = {
        "t_ms": t_ms,
        "since_ms": max(0, t_ms - 1000) if since_ms is None else since_ms,
        "records": records,
        "voiced": voiced or [],
    }
    [result] = judge(inputs, t_ms, config)
    return result


def issue_types(result: JudgmentResult) -> list[str]:
    return [i.issue_type for i in result.issues]


def totals(result: JudgmentResult) -> dict[str, int]:
    """tally 를 이름별로 더한 것."""
    out: dict[str, int] = {}
    for item in result.tally:
        for k, v in item.values.items():
            out[k] = out.get(k, 0) + v
    return out


def span(state: str, t0: int, t1: int, **kw: Any) -> list[dict[str, Any]]:
    """[t0, t1) 를 t0 부터 1초 기록으로 채운다. 마지막 기록은 남은 길이만큼 짧다."""
    return [rec(t, state, duration_ms=min(1000, t1 - t), **kw) for t in range(t0, t1, 1000)]
