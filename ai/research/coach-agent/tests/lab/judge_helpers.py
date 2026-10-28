"""연구용 판정 대역 · 예시 테스트의 공용 도우미 — 계약 모양 검사와 입력 만들기.

`words` 는 코치 단위 테스트(서비스로 옮겨 감)의 같은 이름 함수 사본이다.
"""

from __future__ import annotations

from typing import Any

from pitch_coach_ai.features.coach.schemas import JudgmentResult


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
        # 측정할 수 없으면 이슈는 측정 불가 신호(actionable false)뿐이다
        if not result.measurable:
            assert not issue.actionable
    version, plus, _ = result.criteria_version.partition("+")
    assert plus and version.startswith(result.evaluator + "-0.")


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


def words(
    end_ms: int,
    *,
    cpm: float = 300,
    seconds: float = 15,
    final: bool = True,
    chars: int = 3,
    filler_every: int = 0,
) -> list[dict[str, Any]]:
    """이전 요청 모양(w · final · filler)의 단어. 말 속도 대역 테스트가 계약 모양으로 바꿔 쓴다."""
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
