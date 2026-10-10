"""가짜 판정 모듈 — 코치 규칙만 시험한다. 판정 기준은 모른다.

#158 계약 모양의 결과를 낸다. tally 는 단순하다: 새 시간마다 total_ms = t − since, STT 모듈은
새 단어마다 글자 수 조각(start_ms 시각)을 더 낸다. 입력은 calls 에 남겨 무엇을 받았는지 본다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from coach.judges import Judges

AREAS = {
    "gaze": ["GAZE"],
    "pace": ["SPEED"],
    "volume": ["VOLUME", "PAUSE"],
    "filler": ["FILLER"],
}
FILLER_WORDS = {"음": True, "어": None}  # 음 = 군더더기, 어 = 보류, 그 밖의 단어 = 아님


class FakeJudge:
    """이름 하나의 판정 모듈. script 가 결과를 정하고, raise_at 의 t_ms 에서는 예외를 낸다."""

    def __init__(
        self,
        name: str,
        *,
        script: Callable[[dict[str, Any], int], list[dict[str, Any]]] | None = None,
        raise_at: set[int] | None = None,
        order: list[str] | None = None,
    ) -> None:
        self.name = name
        self.script = script
        self.raise_at = raise_at or set()
        self.order = order
        self.calls: list[tuple[dict[str, Any], int]] = []

    def judge(self, inputs: dict[str, Any], t_ms: int) -> list[dict[str, Any]]:
        self.calls.append((inputs, t_ms))
        if self.order is not None:
            self.order.append(self.name)
        if t_ms in self.raise_at:
            raise RuntimeError(f"{self.name} 가짜 예외")
        return self.script(inputs, t_ms) if self.script else self._default(inputs, t_ms)

    def _default(self, inputs: dict[str, Any], t: int) -> list[dict[str, Any]]:
        since = inputs.get("since_ms", 0)
        words_since = inputs.get("words_since_ms", -1)
        new_words = [w for w in inputs.get("words", []) if w["end_ms"] > words_since]
        total = [{"t_ms": since, "values": {"total_ms": t - since}}] if t > since else []
        results = []
        for area in AREAS[self.name]:
            result: dict[str, Any] = {
                "evaluator": self.name,
                "area": area,
                "t_ms": t,
                "counted_until_ms": max(t, since),
                "criteria_version": f"{self.name}-fake-1",
                "measurable": True,
                "state": "NORMAL",
                "metrics": {"cpm": 300.0} if self.name == "pace" else {},
                "tally": list(total),
                "issues": [],
            }
            if self.name in ("pace", "filler"):
                ends = [w["end_ms"] for w in new_words]
                result["words_counted_until_ms"] = max([words_since, *ends])
            if self.name == "pace":
                result["tally"] += [
                    {"t_ms": w["start_ms"], "values": {"chars": len(w["word"])}} for w in new_words
                ]
            if self.name == "filler":
                result["words"] = [
                    {
                        "start_ms": w["start_ms"],
                        "end_ms": w["end_ms"],
                        "word": w["word"],
                        "is_filler": FILLER_WORDS.get(w["word"], False),
                    }
                    for w in new_words
                ]
            results.append(result)
        return results

    def summarize(self, tally: dict[str, dict[str, float]]) -> dict[str, Any]:
        return {area: dict(values) for area, values in tally.items()}

    def criteria(self) -> dict[str, Any]:
        if self.name == "pace":
            return {"PACE_FAST": _criteria("cpm", 350.0, 450.0)}
        return {}


def _criteria(metric: str, threshold: float, bad: float) -> dict[str, Any]:
    return {
        "metric": metric,
        "direction": "HIGHER_IS_WORSE",
        "threshold": threshold,
        "bad": bad,
        "onset_lag_ms": 0,
        "offset_lag_ms": 0,
    }


def default_baseline(levels: list[float]) -> float | None:
    """표본이 셋 모이면 평균."""
    return round(sum(levels[:3]) / 3, 2) if len(levels) >= 3 else None


def fake_judges(
    *,
    order: list[str] | None = None,
    baseline: Callable[[list[float]], float | None] = default_baseline,
    **overrides: FakeJudge,
) -> Judges:
    """네 모듈을 가짜로 묶는다. overrides 로 모듈 하나를 바꾼다(예: filler=FakeJudge(...))."""
    mods = {n: overrides.get(n) or FakeJudge(n, order=order) for n in AREAS}
    return Judges(**mods, baseline=baseline)
