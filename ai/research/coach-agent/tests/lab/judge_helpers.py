"""연구용 판정 대역 테스트의 공용 도우미 — 계약 모양 검사와 입력 만들기."""

from __future__ import annotations

from typing import Any

from coach.schemas import JudgmentResult

IN_PAUSE = {"relative_db": None, "silence_ms": 400, "audio_live": True}


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
