"""연구용 판정 대역 테스트의 공용 도우미 — 지금 평가기를 1초마다 부르는 OldRun 과 계약 모양 검사."""

from __future__ import annotations

from typing import Any

from coach.config import load_config
from coach.core import _append_history
from coach.evaluators import gaze as old_gaze
from coach.evaluators import speech as old_speech
from coach.evaluators import voice as old_voice
from coach.evaluators.base import Tick
from coach.schemas import CoachRequest, JudgmentResult
from coach.state import CoachState
from coach.vocab import Issue
from tests.unit.conftest import make_request


class OldRun:
    """지금 평가기를 1초마다 부른다 — 코어처럼 coach_state 의 history 를 쌓는다."""

    def __init__(self) -> None:
        self.state = CoachState()
        self.cfg = load_config()

    def step(self, t: int, *, stt_ok: bool = True, **current: Any) -> Tick:
        req = CoachRequest.model_validate(make_request(t, **current))
        tick = Tick(
            req=req,
            cfg=self.cfg,
            state=self.state,
            t=t,
            slide_number=1,
            slide_start_ms=0,
            stt_ok=stt_ok,
        )
        old_gaze.evaluate(tick)
        old_voice.evaluate(tick)
        old_speech.ingest(tick)
        old_speech.evaluate(tick)
        _append_history(tick)
        return tick


def detection(tick: Tick, issue: Issue):
    return next((d for d in tick.detections if d.issue_type == issue), None)


def check_shape(result: JudgmentResult, since: int = 0) -> None:
    """계약 모양: 검증을 통과하고, 문제 영역 · 심각도 범위 · 집계 시각 · 커서가 맞다."""
    assert JudgmentResult.model_validate(result.model_dump()) == result
    assert result.counted_until_ms <= max(result.t_ms, since)
    # 시간 조각(total_ms 가 있는 것)은 새 시간 안에 있다. 단어 몫은 더 이를 수 있다
    for item in result.tally:
        if "total_ms" in item.values:
            assert item.t_ms >= since
    for issue in result.issues:
        assert issue.area == result.area
        assert 0.0 <= issue.severity <= 1.0
        assert 0.0 <= issue.confidence <= 1.0
    assert result.criteria_version.startswith(result.evaluator + "-0.1+")


def total(tally, key: str) -> float:
    return sum(item.values.get(key, 0) for item in tally)


def merged(results: list[JudgmentResult]) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in results:
        for item in r.tally:
            for k, v in item.values.items():
                out[k] = out.get(k, 0) + v
    return out


def pace_words(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"word": w["w"], "start_ms": w["start_ms"], "end_ms": w["end_ms"]} for w in raw]


def plain(word: str, start: int, end: int) -> dict[str, Any]:
    return {"word": word, "start_ms": start, "end_ms": end}
