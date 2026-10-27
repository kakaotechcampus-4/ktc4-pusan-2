"""테스트 공용 — 입력을 짧게 만드는 도우미와 공통 계약 검사.

`run` 은 판정할 때마다 공통 계약(`check_contract`)도 함께 본다.
"""

from __future__ import annotations

import re
from typing import Any

from pitch_coach_ai.features.filler.config import FillerConfig
from pitch_coach_ai.features.filler.core import judge
from pitch_coach_ai.features.filler.schemas import JudgmentResult, TallyItem

#: 규칙 테스트가 쓰는 기준값. 지금 config.py 기본값과 같지만, 기준값을 다시 골라도
#: 규칙 테스트가 깨지지 않게 따로 적는다. 기본값 자체는 test_examples · test_contract 가 본다.
#: 규칙 테스트는 세 층을 모두 켠 설정으로 본다(기본값은 T1만 센다 — test_counted_tiers.py)
CFG = FillerConfig(
    counted_tiers=("T1", "T2", "T3"),
    gap_ms=400,
    sentence_gap_ms=1_000,
    window_ms=60_000,
    frequent_count=6,
    frequent_bad=15,
    min_rate_ms=10_000,
    onset_lag_ms=30_000,
    offset_lag_ms=30_000,
)

#: 사전에 없는 단어. 앞뒤 이웃으로 쓴다
PLAIN = "발표"

TIME_KEYS = {"elapsed_ms", "total_ms"}
TIER_KEYS = {"filler_t1", "filler_t2", "filler_t3"}


def word(text: str, start_ms: int, end_ms: int) -> dict[str, Any]:
    return {"word": text, "start_ms": start_ms, "end_ms": end_ms}


def inputs(t_ms: int, words: list[dict[str, Any]] | tuple = (), **overrides: Any) -> dict[str, Any]:
    """판정 입력. 기본은 처음 부르는 요청(커서 0) · STT 정상 · 문장 끝 신호 없음."""
    base = {
        "t_ms": t_ms,
        "since_ms": 0,
        "words_since_ms": 0,
        "stt_status": "ok",
        "stt_ok_since_ms": 0,
        "utterance_ends": [],
        "words": list(words),
    }
    return {**base, **overrides}


def run(
    t_ms: int,
    words: list[dict[str, Any]] | tuple = (),
    *,
    config: FillerConfig = CFG,
    **overrides: Any,
) -> JudgmentResult:
    """judge 를 한 번 부르고 결과 하나를 공통 계약으로 검사해 돌려준다."""
    inp = inputs(t_ms, words, **overrides)
    [result] = judge(inp, t_ms, config)
    check_contract(result, inp)
    return result


def check_contract(result: JudgmentResult, inp: dict[str, Any]) -> None:
    """1초 판정 결과의 공통 계약 (architecture.md 7절, filler.md 5-3 · 5-4 · 6-1)."""
    assert JudgmentResult.model_validate(result.model_dump()) == result
    assert (result.evaluator, result.area) == ("filler", "FILLER")
    assert re.fullmatch(r"filler-\d+\.\d+\+[0-9a-f]{12}", result.criteria_version)
    since = inp["since_ms"]
    assert result.counted_until_ms == max(result.t_ms, since)
    # 커서는 되돌아가지 않는다
    assert result.words_counted_until_ms >= inp["words_since_ms"]
    for item in result.tally:
        # 값이 0 인 키는 뺀다
        assert all(v > 0 for v in item.values.values()), item
        if "total_ms" in item.values:
            # 1초 몫: 새 시간 안의 1초 하나
            assert item.values.keys() <= TIME_KEYS
            assert since <= item.t_ms < result.t_ms
            assert item.values["total_ms"] <= 1000
            assert item.values.get("elapsed_ms", 0) <= item.values["total_ms"]
        else:
            # 단어 몫: 새 군더더기 하나
            assert item.values["filler_count"] == 1
            assert sum(k in TIER_KEYS for k in item.values) == 1
            assert sum(k.startswith("filler_word:") for k in item.values) == 1
    if result.measurable:
        assert result.state in ("NORMAL", "HIGH")
        assert result.metrics.recent_filler_count is not None
        assert [i.issue_type for i in result.issues] == (
            ["FILLER_FREQUENT"] if result.state == "HIGH" else []
        )
    else:
        assert result.state == "UNKNOWN" and result.issues == [] and result.words == []
        assert result.metrics.recent_filler_count is None and result.metrics.filler_per_min is None
        assert all("elapsed_ms" not in i.values for i in result.tally)
    for issue in result.issues:
        assert issue.area == result.area
        assert 0.5 <= issue.severity <= 1.0
        assert issue.confidence == 1.0
    for item in result.words:
        # 사전에 없으면 tier 가 없고 군더더기가 아니다. 보류는 사전에 있는 T2 · T3 단어뿐이다
        if item.tier is None:
            assert item.is_filler is False
        if item.is_filler is None:
            assert item.tier in ("T2", "T3")
    # 보류는 마지막 단어에만 생긴다
    assert all(w.is_filler is not None for w in result.words[:-1])


def verdicts(result: JudgmentResult) -> list[bool | None]:
    """단어마다 군더더기 여부(True · False · None=보류)."""
    return [w.is_filler for w in result.words]


def gap_case(
    text: str, before: int, after: int, *, config: FillerConfig = CFG, **overrides: Any
) -> JudgmentResult:
    """사전에 없는 앞 · 뒤 단어 사이에 `text` 를 끼운 요청. 간격은 ms.

    앞 단어는 1000 에 끝나고, `text`(길이 200)는 그 `before` 뒤에 시작하며, 뒤 단어는
    `text` 가 끝난 `after` 뒤에 시작한다. 판정 대상은 words[1] 이다.
    """
    start = 1_000 + before
    end = start + 200
    words = [
        word(PLAIN, 500, 1_000),
        word(text, start, end),
        word(PLAIN, end + after, end + after + 300),
    ]
    return run(end + after + 300, words, config=config, **overrides)


def fillers(n: int, *, start_ms: int = 1_000, step_ms: int = 1_000) -> list[dict[str, Any]]:
    """채움말 "음" n 개. step_ms 간격으로 300 ms 씩 말한다."""
    return [word("음", start_ms + k * step_ms, start_ms + k * step_ms + 300) for k in range(n)]


def total(tally: list[TallyItem], key: str) -> int:
    return sum(item.values.get(key, 0) for item in tally)


def word_items(result: JudgmentResult) -> list[TallyItem]:
    """단어 몫 (1초 몫이 아닌 것)."""
    return [i for i in result.tally if "total_ms" not in i.values]


def time_items(result: JudgmentResult) -> list[TallyItem]:
    """1초 몫."""
    return [i for i in result.tally if "total_ms" in i.values]
