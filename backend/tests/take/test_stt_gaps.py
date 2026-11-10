"""STT 끊김 구간과 장별 끊김 시간 (`module/take/stt_gaps.py`)."""

import pytest

from pitch_coach_backend.module.take.stt_gaps import slide_stt_gaps, stt_gap_ranges


def _seconds(*statuses: str, start_ms: int = 1_000) -> list[tuple[int, str]]:
    """1초마다 하나씩, `t_ms` 는 그 1초가 끝난 시각이다."""
    return [(start_ms + index * 1_000, status) for index, status in enumerate(statuses)]


def test_take_with_every_second_ok_has_no_gaps() -> None:
    assert stt_gap_ranges(_seconds(*["ok"] * 5), 5_000) == {
        "version": 1,
        "duration_ms": 5_000,
        "not_ok": [],
        "unobserved": [],
    }


def test_reconnecting_seconds_in_the_middle_are_one_not_ok_range() -> None:
    statuses = ["connecting", "ok", "ok", "reconnecting", "reconnecting", "degraded", "ok"]

    gaps = stt_gap_ranges(_seconds(*statuses), 7_000)

    # 맞닿은 구간은 하나로 합친다
    assert gaps["not_ok"] == [[0, 1_000], [3_000, 6_000]]
    assert gaps["unobserved"] == []


def test_missing_seconds_are_unobserved() -> None:
    seconds = [(1_000, "ok"), (2_000, "ok"), (5_000, "closed")]

    gaps = stt_gap_ranges(seconds, 6_000)

    assert gaps["not_ok"] == [[4_000, 5_000]]
    assert gaps["unobserved"] == [[2_000, 4_000], [5_000, 6_000]]


def test_without_any_second_the_gaps_are_unknown() -> None:
    assert stt_gap_ranges([], 5_000) is None


def test_last_partial_second_ends_at_the_take_end() -> None:
    """마지막 metrics 의 `t_ms` 는 Take 가 끝난 시각이다. 앞 1초와 겹쳐도 한 번만 덮는다."""
    seconds = [*_seconds("ok", "ok", "ok", "ok"), (4_500, "reconnecting")]

    gaps = stt_gap_ranges(seconds, 4_500)

    assert gaps["not_ok"] == [[3_500, 4_500]]
    assert gaps["unobserved"] == []


def test_seconds_outside_the_take_are_cut() -> None:
    # Take 끝 뒤에 온 1초는 버리고, 걸친 1초는 끝까지만 덮는다
    seconds = [(1_000, "ok"), (2_000, "reconnecting"), (3_000, "reconnecting")]

    gaps = stt_gap_ranges(seconds, 1_500)

    assert gaps == {
        "version": 1,
        "duration_ms": 1_500,
        "not_ok": [[1_000, 1_500]],
        "unobserved": [],
    }


GAPS = {
    "version": 1,
    "duration_ms": 9_000,
    "not_ok": [[2_000, 4_000]],
    "unobserved": [[7_000, 8_000]],
}


def test_slide_times_add_up_every_visit() -> None:
    events = [(1, 0), (2, 3_000), (1, 6_000)]

    assert slide_stt_gaps([1, 2, 3], events, GAPS) == {
        # [0, 3000) + [6000, 9000)
        1: {"duration_ms": 6_000, "stt_gap_ms": 1_000, "stt_unobserved_ms": 1_000},
        2: {"duration_ms": 3_000, "stt_gap_ms": 1_000, "stt_unobserved_ms": 0},
        # 한 번도 보이지 않은 장
        3: {"duration_ms": 0, "stt_gap_ms": 0, "stt_unobserved_ms": 0},
    }


def test_time_before_the_first_slide_event_belongs_to_no_slide() -> None:
    events = [(1, 2_500), (2, 3_500)]

    assert slide_stt_gaps([1, 2], events, GAPS) == {
        # 끊김 [2000, 4000) 중 [2500, 3500) 만 1장이다
        1: {"duration_ms": 1_000, "stt_gap_ms": 1_000, "stt_unobserved_ms": 0},
        2: {"duration_ms": 5_500, "stt_gap_ms": 500, "stt_unobserved_ms": 1_000},
    }


def test_slide_events_outside_the_take_are_cut() -> None:
    events = [(1, 0), (2, 9_000), (3, 12_000)]

    timing = slide_stt_gaps([1, 2, 3], events, GAPS)

    assert timing[1]["duration_ms"] == 9_000
    assert timing[2]["duration_ms"] == timing[3]["duration_ms"] == 0


@pytest.mark.parametrize(
    "gaps",
    [
        None,
        {**GAPS, "version": 2},
        {**GAPS, "duration_ms": None},
        {**GAPS, "not_ok": [[0]]},
        {**GAPS, "unobserved": "[]"},
    ],
    ids=["unknown", "other-version", "no-duration", "bad-range", "bad-list"],
)
def test_unknown_gaps_leave_every_slide_time_null(gaps) -> None:
    unknown = {"duration_ms": None, "stt_gap_ms": None, "stt_unobserved_ms": None}

    assert slide_stt_gaps([1, 2], [(1, 0)], gaps) == {1: unknown, 2: unknown}


def test_stored_ranges_out_of_order_are_counted_once() -> None:
    gaps = {**GAPS, "not_ok": [[3_000, 4_000], [2_000, 3_500]], "unobserved": []}

    assert slide_stt_gaps([2], [(1, 0), (2, 3_000)], gaps)[2]["stt_gap_ms"] == 1_000


def test_many_visits_and_gaps_add_up_like_every_pair() -> None:
    """장을 자주 넘기고 STT 가 자주 끊긴 긴 Take 도 짝을 하나씩 맞대 본 것과 같은 값이다."""
    duration_ms = 600_000
    seconds = [
        (t_ms, "ok" if t_ms % 3_000 else "reconnecting")
        for t_ms in range(1_000, duration_ms + 1, 1_000)
    ]
    del seconds[::7]
    gaps = stt_gap_ranges(seconds, duration_ms)
    events = [
        (1 + index % 4, started_ms) for index, started_ms in enumerate(range(0, duration_ms, 700))
    ]

    timing = slide_stt_gaps([1, 2, 3, 4], events, gaps)

    by_slide: dict[int, list[tuple[int, int]]] = {}
    for index, (number, started_ms) in enumerate(events):
        ended_ms = events[index + 1][1] if index + 1 < len(events) else duration_ms
        by_slide.setdefault(number, []).append((started_ms, ended_ms))
    for number, spans in by_slide.items():
        for key, ranges in (
            ("stt_gap_ms", gaps["not_ok"]),
            ("stt_unobserved_ms", gaps["unobserved"]),
        ):
            expected = sum(
                max(0, min(end, r_end) - max(start, r_start))
                for start, end in spans
                for r_start, r_end in ranges
            )
            assert timing[number][key] == expected
