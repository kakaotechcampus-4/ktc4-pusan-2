"""테스트 공용 — 요청을 짧게 만드는 도우미와, coach_state 를 들고 다니는 세션."""

from __future__ import annotations

from typing import Any

import pytest

from coach import decide
from coach.config import CoachConfig, load_config
from coach.schemas import CoachResponse

#: 3분 발표, 4장. 장 목표 30/60/60/30초. 글자 수는 계산하기 쉽게 장 목표 초 × 10
PLAN: dict[str, Any] = {
    "target_ms": 180_000,
    "min_ms": 165_000,
    "max_ms": 195_000,
    "slides": [
        {"slide_number": 1, "target_ms": 30_000, "script_chars": 300},
        {"slide_number": 2, "target_ms": 60_000, "script_chars": 600},
        {
            "slide_number": 3,
            "target_ms": 60_000,
            "script_chars": 600,
            "required_keywords": ["로컬 처리"],
        },
        {"slide_number": 4, "target_ms": 30_000, "script_chars": 300},
    ],
}

#: 말이 끊긴 틈. 이걸 기본으로 써서 '문장 끝 기다리기'가 다른 테스트를 방해하지 않게 한다
IN_PAUSE = {"relative_db": None, "silence_ms": 400, "audio_live": True}


def words(
    end_ms: int,
    *,
    cpm: float = 300,
    seconds: float = 15,
    final: bool = True,
    chars: int = 3,
    filler_every: int = 0,
) -> list[dict[str, Any]]:
    """end_ms 까지 seconds 초 동안 cpm 속도로 쉬지 않고 말한 단어들."""
    dur = round(chars * 60_000 / cpm)
    out: list[dict[str, Any]] = []
    t = int(end_ms - seconds * 1000)
    i = 0
    while t + dur <= end_ms:
        i += 1
        if filler_every and i % filler_every == 0:
            out.append(
                {"w": "음", "start_ms": t, "end_ms": t + 300, "final": final, "filler": True}
            )
            t += 300
            continue
        out.append({"w": "가" * chars, "start_ms": t, "end_ms": t + dur, "final": final})
        t += dur
    return out


def make_request(
    t_ms: int,
    *,
    state: dict[str, Any] | None = None,
    mode: str = "COACHING",
    plan: dict[str, Any] | None = None,
    slide: int | None = 1,
    slide_elapsed: int | None = None,
    gaze: dict[str, Any] | None = None,
    voice: dict[str, Any] | None = None,
    speech: dict[str, Any] | None = None,
    missions: list[dict[str, Any]] | None = None,
    memory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    timing = None
    if slide is not None:
        # slide_elapsed 를 안 주면 Take 시작부터 그 장이었던 것으로 본다
        timing = {
            "slide_number": slide,
            "slide_elapsed_ms": t_ms if slide_elapsed is None else slide_elapsed,
        }
    return {
        "take_id": "test-take",
        "t_ms": t_ms,
        "mode": mode,
        "plan": PLAN if plan is None else plan,
        "missions": missions or [],
        "memory": memory or {},
        "current": {
            "timing": timing,
            "gaze": gaze,
            "voice": IN_PAUSE if voice is None else voice,
            "speech": speech,
        },
        "coach_state": state,
    }


def gaze_script(ratio: float, *, uncertain: float = 0.0, streak_ms: int = 0) -> dict[str, Any]:
    """ratio 는 '보이던 시간 중' 대본 응시 비율."""
    valid = 1.0 - uncertain
    return {
        "window_ms": 10_000,
        "ratios": {"BOTTOM": ratio * valid, "CAMERA": (1 - ratio) * valid, "UNCERTAIN": uncertain},
        "current_label": "BOTTOM" if ratio >= 0.5 else "CAMERA",
        "current_label_ms": streak_ms,
    }


class Session:
    """BE 처럼 coach_state 를 받아 두었다가 다음 요청에 붙인다."""

    def __init__(self, config: CoachConfig | None = None, **defaults: Any) -> None:
        self.config = config or load_config()
        self.defaults = defaults
        self.state: dict[str, Any] | None = None
        self.responses: list[CoachResponse] = []

    def step(self, t_ms: int, **kw: Any) -> CoachResponse:
        args = {**self.defaults, **kw}
        resp = decide(make_request(t_ms, state=self.state, **args), self.config)
        self.state = resp.coach_state
        self.responses.append(resp)
        return resp

    def run(
        self, start_ms: int, end_ms: int, step_ms: int = 1000, **kw: Any
    ) -> list[CoachResponse]:
        return [self.step(t, **kw) for t in range(start_ms, end_ms + 1, step_ms)]

    @property
    def events(self) -> list[Any]:
        return [e for r in self.responses for e in r.events]

    @property
    def interventions(self) -> list[CoachResponse]:
        return [r for r in self.responses if r.feedback is not None]


@pytest.fixture
def session() -> Session:
    return Session()
