"""테스트 공용 — 입력을 짧게 만드는 도우미와 공통 계약 검사.

`run` 은 판정할 때마다 공통 계약(`check_contract`)도 함께 본다.
"""

from __future__ import annotations

import re
from typing import Any

from pace.config import PaceConfig
from pace.core import judge
from pace.schemas import JudgmentResult, TallyItem

#: 규칙 테스트가 쓰는 기준값. 지금 config.py 기본값과 같지만, 기준값을 다시 골라도(pace.md 13절)
#: 규칙 테스트가 깨지지 않게 따로 적는다. 기본값 자체는 test_examples · test_contract 가 본다
CFG = PaceConfig(
    window_ms=15_000,
    min_speak_ms=4_000,
    min_words=5,
    short_window_ms=6_000,
    short_min_speak_ms=2_500,
    short_min_words=3,
    fast_cpm=350.0,
    fast_cpm_bad=450.0,
    slow_cpm=275.0,
    slow_cpm_bad=200.0,
    pause_min_ms=2_000,
    confidence_base=0.6,
    confidence_full_speak_ms=8_000,
    onset_lag_ms=7_500,
    offset_lag_ms=7_500,
)

WORD_KEYS = {"chars", "speak_ms", "pause_count", "pause_ms"}
TIME_KEYS = {"ok_ms", "total_ms"}


def word(text: str, start_ms: int, end_ms: int) -> dict[str, Any]:
    return {"word": text, "start_ms": start_ms, "end_ms": end_ms}


def steady(
    end_ms: int, *, chars: int, dur_ms: int, seconds: float = 15, gap_ms: int = 0
) -> list[dict[str, Any]]:
    """end_ms 에 끝나도록 seconds 초 안에서 같은 길이 단어를 이어 말한 확정 단어들.

    단어 하나가 chars 글자 · dur_ms 길이라 CPM 은 chars × 60000 ÷ dur_ms 다.
    """
    out: list[dict[str, Any]] = []
    t = end_ms - dur_ms
    while t >= end_ms - seconds * 1000:
        out.append(word("가" * chars, t, t + dur_ms))
        t -= dur_ms + gap_ms
    return out[::-1]


def inputs(words: list[dict[str, Any]] | tuple = (), **overrides: Any) -> dict[str, Any]:
    """판정 입력. 기본은 처음 부르는 요청(커서 0) · STT 정상 · 군더더기 없음."""
    base = {
        "since_ms": 0,
        "words_since_ms": 0,
        "stt_status": "ok",
        "stt_ok_since_ms": 0,
        "words": list(words),
        "fillers": [],
    }
    return {**base, **overrides}


def run(
    t_ms: int,
    words: list[dict[str, Any]] | tuple = (),
    *,
    config: PaceConfig = CFG,
    **overrides: Any,
) -> JudgmentResult:
    """judge 를 한 번 부르고 결과 하나를 공통 계약으로 검사해 돌려준다."""
    inp = inputs(words, **overrides)
    [result] = judge(inp, t_ms, config)
    check_contract(result, inp)
    return result


def check_contract(result: JudgmentResult, inp: dict[str, Any]) -> None:
    """1초 판정 결과의 공통 계약 (architecture.md 7절, pace.md 5-3 · 5-4 · 6-1)."""
    assert JudgmentResult.model_validate(result.model_dump()) == result
    assert (result.evaluator, result.area) == ("pace", "SPEED")
    assert re.fullmatch(r"pace-\d+\.\d+\+[0-9a-f]{12}", result.criteria_version)
    # 커서는 되돌아가지 않는다
    since = inp["since_ms"]
    assert result.counted_until_ms == max(result.t_ms, since)
    assert result.words_counted_until_ms >= inp["words_since_ms"]
    for item in result.tally:
        # 값이 0 인 키는 뺀다
        assert all(v > 0 for v in item.values.values()), item
        if "total_ms" in item.values:
            # 1초 몫: 새 시간 안의 1초 하나
            assert item.values.keys() <= TIME_KEYS
            assert since <= item.t_ms < result.t_ms
            assert item.values["total_ms"] <= 1000
            assert item.values.get("ok_ms", 0) <= item.values["total_ms"]
        else:
            assert item.values.keys() <= WORD_KEYS
    # 잴 수 없으면 UNKNOWN 이고 문제가 없다 (말 속도에는 측정 불가 문제 이름이 없다)
    if result.measurable:
        assert result.state != "UNKNOWN" and result.metrics.cpm is not None
    else:
        assert result.state == "UNKNOWN" and result.issues == []
    expected = {"FAST": ["PACE_FAST"], "SLOW": ["PACE_SLOW"]}.get(result.state, [])
    assert [i.issue_type for i in result.issues] == expected
    for issue in result.issues:
        assert issue.area == result.area
        assert 0.5 <= issue.severity <= 1.0
        assert 0.0 <= issue.confidence <= 1.0


def total(tally: list[TallyItem], key: str) -> int:
    return sum(item.values.get(key, 0) for item in tally)


def word_items(result: JudgmentResult) -> list[TallyItem]:
    """단어 몫 (1초 몫이 아닌 것)."""
    return [i for i in result.tally if "total_ms" not in i.values]


def time_items(result: JudgmentResult) -> list[TallyItem]:
    """1초 몫."""
    return [i for i in result.tally if "total_ms" in i.values]
