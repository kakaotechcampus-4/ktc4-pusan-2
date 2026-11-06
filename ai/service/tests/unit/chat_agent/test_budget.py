"""budget.py 단위 시험: 남은 시간 · 마감 · 모델 호출 예약(조사 몫 16 · 답 몫 2 · 24초) · 토큰 예약 · 출력 정산 · 도구 · 행동 · 턴 한도 (기능 문서 8-1 · 8-2 · 8-3).

시간은 가짜 단조 시계로 흘린다. 이름의 L 번호는 기능 문서 7절 실패 행이다.
"""

from __future__ import annotations

import json
import math

import pytest

from pitch_coach_ai.features.chat_agent.budget import (
    Budget,
    Counters,
    LimitHit,
    estimate_tokens,
    limit_end_reason,
)
from pitch_coach_ai.features.chat_agent.config import DEFAULT_CONFIG as CFG
from pitch_coach_ai.features.chat_agent.schemas import LimitDetail


class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


def start(elapsed_ms: int = 0, **counts: int) -> tuple[Budget, Clock]:
    clock = Clock()
    return Budget.start(CFG, Counters(elapsed_ms=elapsed_ms, **counts), clock), clock


def assert_detail(hit: LimitHit | None) -> LimitHit:
    """진단 기록 LIMIT 단계로 그대로 나갈 수 있는 값인가(엄격한 비음수 정수)."""
    assert hit is not None
    LimitDetail.model_validate({"used": hit.used, "limit": hit.limit, "attempted": hit.attempted})
    return hit


# ── 시간 (8-1 · 8-3 · L1-04) ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("elapsed_ms", "remaining", "window", "exhausted"),
    [
        (0, 120.0, 45.0, False),
        (45_000, 75.0, 45.0, False),  # ms 를 초로 읽지 않는다
        (100_000, 20.0, 20.0, False),  # 마감 = min(45초, 남은 실행 시간)
        (119_999, 0.001, 0.001, False),
        (120_000, 0.0, 0.0, True),
        (150_000, 0.0, 0.0, True),  # 음수로 내려가지 않는다
    ],
)
def test_l1_04_remaining_time_and_deadline(
    elapsed_ms: int, remaining: float, window: float, exhausted: bool
) -> None:
    b, _ = start(elapsed_ms)
    assert b.remaining_turn_at_start() == pytest.approx(remaining)
    assert b.deadline - b.started == pytest.approx(window)
    assert b.left() == pytest.approx(window)
    assert b.exhausted_at_start() is exhausted


def test_left_and_elapsed_follow_the_clock() -> None:
    b, clock = start(10_000)
    clock.advance(2.5)
    assert b.left() == pytest.approx(42.5)
    assert b.elapsed_ms_now() == 12_500
    b.sync_elapsed()
    assert b.counters.elapsed_ms == 12_500
    clock.advance(60)
    assert b.left() == 0.0  # 음수가 아니다
    b.counters.elapsed_ms = 999_999
    b.sync_elapsed()
    assert b.counters.elapsed_ms == 999_999  # 줄지 않는다


def test_l1_04_exhausted_turn_blocks_every_model_call() -> None:
    b, _ = start(120_000)
    for kind in ("RESEARCH", "ANSWER", "FIX"):
        assert assert_detail(b.reserve_model(kind, 10, 10)).code == "CALL_TIME"  # type: ignore[arg-type]
    assert b.counters.model_calls == 0


# ── 모델 호출 수 (8-2 · 8-3 · L1-13) ─────────────────────────────────────────


def test_l1_13_research_stops_at_sixteen_and_keeps_two_for_answer_and_fix() -> None:
    """이번 시도를 포함한 누적 시도 수가 16을 넘으면(17번째 시도) 조사를 하지 않는다. 답 쓰기 · 고치기가 17 · 18번째."""
    b, _ = start()
    for _ in range(16):
        assert b.reserve_model("RESEARCH", 100, 100) is None
    hit = assert_detail(b.reserve_model("RESEARCH", 100, 100))
    assert (hit.code, hit.used, hit.limit) == ("MODEL_CALLS", 16, 16)
    assert b.counters.model_calls == 16
    assert b.reserve_model("ANSWER", 100, 100) is None  # 17번째
    assert b.reserve_model("FIX", 100, 100) is None  # 18번째
    hit = assert_detail(b.reserve_model("ANSWER", 100, 100))  # 19번째는 없다
    assert (hit.code, hit.used, hit.limit) == ("MODEL_CALLS", 18, 18)
    assert b.counters.model_calls == 18
    assert [h.code for h in b.hits] == ["MODEL_CALLS", "MODEL_CALLS"]


def test_l1_13_research_after_answer_share_is_used_is_refused() -> None:
    """재개를 걸쳐 누적 시도가 16이면(답 몫 2만 남음) 조사는 더 보내지 않는다."""
    b, _ = start(model_calls=16)
    assert b.reserve_model("RESEARCH", 10, 10).code == "MODEL_CALLS"
    assert b.reserve_model("ANSWER", 10, 10) is None


def test_research_attempt_fifteen_to_sixteen_boundary() -> None:
    """누적 15에서 16번째 시도(이번 포함 16)는 보내고, 누적 16에서 17번째는 막는다."""
    ok, _ = start(model_calls=15)
    assert ok.reserve_model("RESEARCH", 10, 10) is None
    assert ok.counters.model_calls == 16
    assert ok.reserve_model("RESEARCH", 10, 10).code == "MODEL_CALLS"


def test_time_is_checked_before_call_count() -> None:
    """호출 전 확인 순서: 남은 시간 → 모델 호출 수(8-3)."""
    b, _ = start(120_000, model_calls=18)
    assert b.reserve_model("ANSWER", 10, 10).code == "CALL_TIME"


@pytest.mark.parametrize(("calls", "short"), [(0, False), (16, False), (17, True), (18, True)])
def test_answer_slots_short_when_two_calls_cannot_be_left(calls: int, short: bool) -> None:
    """이어 부를 때 남은 호출 수가 2번보다 적으면 모델 없이 끝낸다(8-3)."""
    b, _ = start(model_calls=calls)
    assert b.answer_slots_short() is short


# ── 조사 시간 조건 (8-3: 전송 뒤 24초 보장) ─────────────────────────────────


def test_research_allowed_while_answer_share_and_min_call_time_remain() -> None:
    """45초 마감에서 14초 지남(남은 31초): 조사를 보내고 시간 제한은 31 − 24 = 7초."""
    b, clock = start()
    clock.advance(14)
    assert b.left() == pytest.approx(31)
    assert b.reserve_model("RESEARCH", 10, 10) is None
    assert b.call_timeout("RESEARCH") == pytest.approx(7)


def test_research_boundary_at_min_research_call_seconds() -> None:
    b, clock = start()
    clock.advance(45 - CFG.reserved_answer_seconds - CFG.min_research_call_seconds)  # 남은 27초
    assert b.reserve_model("RESEARCH", 10, 10) is None
    assert b.call_timeout("RESEARCH") == pytest.approx(CFG.min_research_call_seconds)


def test_l1_13_research_refused_when_answer_share_is_not_guaranteed() -> None:
    """남은 26초: 26 − 24 = 2초 < 최소 3초 → 조사를 보내지 않고 예약하지 않는다. 답 쓰기는 보낸다."""
    b, clock = start()
    clock.advance(19)
    hit = assert_detail(b.reserve_model("RESEARCH", 10, 10))
    # used = 이 호출에서 지난 초, limit = 조사를 시작할 수 있는 마지막 초(45 − 24 − 3)
    assert (hit.code, hit.used, hit.limit) == ("CALL_TIME", 19, 18)
    assert (b.counters.model_calls, b.counters.input_tokens, b.counters.output_tokens) == (0, 0, 0)
    assert b.reserve_model("ANSWER", 10, 10) is None


def test_research_window_shrinks_with_remaining_turn_time() -> None:
    """이 호출의 마감이 남은 실행 시간(30초)으로 짧으면 그 안에서 같은 조건을 본다."""
    b, clock = start(90_000)
    assert b.reserve_model("RESEARCH", 10, 10) is None
    assert b.call_timeout("RESEARCH") == pytest.approx(6)
    clock.advance(4)
    hit = assert_detail(b.reserve_model("RESEARCH", 10, 10))
    assert (hit.code, hit.used, hit.limit) == ("CALL_TIME", 4, 3)
    short, _ = start(100_000)  # 남은 20초: 조사는 처음부터 못 하고 답 쓰기만
    hit = assert_detail(short.reserve_model("RESEARCH", 10, 10))
    assert (hit.code, hit.used, hit.limit) == ("CALL_TIME", 0, 0)
    assert short.reserve_model("ANSWER", 10, 10) is None


@pytest.mark.parametrize(
    ("spent", "allowed"), [(44.0, True), (44.4, True), (44.5, False), (50.0, False)]
)
def test_answer_needs_more_than_half_a_second(spent: float, allowed: bool) -> None:
    b, clock = start()
    clock.advance(spent)
    hit = b.reserve_model("ANSWER", 10, 10)
    if allowed:
        assert hit is None
    else:
        assert assert_detail(hit).code == "CALL_TIME" and hit.limit == 44


@pytest.mark.parametrize(
    ("spent", "research", "answer"),
    [(0, 12.0, 12.0), (15, 6.0, 12.0), (25, 0.0, 12.0), (40, 0.0, 5.0), (50, 0.0, 0.0)],
)
def test_call_timeout(spent: float, research: float, answer: float) -> None:
    b, clock = start()
    clock.advance(spent)
    assert b.call_timeout("RESEARCH") == pytest.approx(research)
    assert b.call_timeout("ANSWER") == pytest.approx(answer)
    assert b.call_timeout("FIX") == pytest.approx(answer)


# ── 토큰 예약 (8-2 · L1-39) ─────────────────────────────────────────────────


def test_l1_39_research_must_leave_input_tokens_for_answer_and_fix() -> None:
    """조사는 이번 입력 + 답 · 고치기 두 번 몫이 72k 안이어야 한다. 예약은 이번 몫만 한다."""
    b, _ = start()
    hit = assert_detail(b.reserve_model("RESEARCH", 24_001, 100))
    assert (hit.code, hit.used, hit.limit, hit.attempted) == ("INPUT_TOKENS", 0, 72_000, 72_003)
    assert b.reserve_model("RESEARCH", 24_000, 100) is None
    assert b.counters.input_tokens == 24_000


def test_l1_39_input_tokens_are_cumulative_up_to_72k() -> None:
    b, _ = start(input_tokens=40_000)
    assert b.reserve_model("ANSWER", 32_000, 100) is None  # 정확히 72k
    hit = assert_detail(b.reserve_model("FIX", 1, 100))
    assert (hit.code, hit.used) == ("INPUT_TOKENS", 72_000)
    assert b.counters.model_calls == 1


def test_l1_39_output_tokens_are_cumulative_up_to_8k() -> None:
    b, _ = start(output_tokens=2_001)
    hit = assert_detail(b.reserve_model("RESEARCH", 10, 2_000))  # 2,000 × 3 = 6,000 → 8,001
    assert (hit.code, hit.limit, hit.attempted) == ("OUTPUT_RESERVE", 8_000, 6_000)
    assert b.reserve_model("ANSWER", 10, 2_000) is None
    assert b.counters.output_tokens == 4_001
    edge, _ = start(output_tokens=2_000)
    assert edge.reserve_model("RESEARCH", 10, 2_000) is None  # 정확히 8k


def test_default_values_of_18_calls_and_per_call_output() -> None:
    assert (CFG.max_model_calls, CFG.max_input_tokens, CFG.max_output_tokens) == (18, 72_000, 8_000)
    # 한 번 보내기 전 조사 예약(이번 + 답 · 고치기 두 번 몫)이 출력 누적 안에 들어간다
    assert CFG.output_tokens_per_call * (1 + CFG.reserved_answer_calls) <= CFG.max_output_tokens


# ── 출력 정산 (8-3: 전송 전 예약 → 응답 뒤 실측) ─────────────────────────────


def test_settle_output_replaces_reservation_with_measured_usage() -> None:
    b, _ = start()
    assert b.reserve_model("RESEARCH", 100, 2_000) is None
    assert b.counters.output_tokens == 2_000
    b.settle_output(2_000, 350)
    assert b.counters.output_tokens == 350  # 쓰지 않은 1,650을 돌려줌
    assert b.reserve_model("ANSWER", 100, 2_000) is None
    b.settle_output(2_000, 120)
    assert b.counters.output_tokens == 470
    assert b.counters.budget_used()["output_tokens"] == 470


def test_settle_output_without_measurement_keeps_the_reservation() -> None:
    b, _ = start()
    assert b.reserve_model("RESEARCH", 100, 2_000) is None
    b.settle_output(2_000, None)
    assert b.counters.output_tokens == 2_000


def test_settle_output_counts_measured_above_the_reservation() -> None:
    b, _ = start()
    assert b.reserve_model("ANSWER", 100, 1_000) is None
    b.settle_output(1_000, 1_400)
    assert b.counters.output_tokens == 1_400


def test_settle_output_never_goes_below_the_value_at_call_start() -> None:
    """내보낸 `budget_used`는 줄지 않는다: 호출을 받을 때의 값 아래로는 정산하지 않는다(8-3)."""
    b, _ = start(output_tokens=3_000)
    assert b.reserve_model("ANSWER", 100, 2_000) is None
    b.settle_output(2_000, 0)
    assert b.counters.output_tokens == 3_000
    assert b.counters.output_tokens >= b.output_floor


def test_settle_input_replaces_estimate_with_measured_usage() -> None:
    """입력도 출력처럼: 추정량을 예약하고 응답 뒤 실측(캐시 포함 전체 입력)으로 바꿔 쓰지 않은 몫을 돌려준다."""
    b, _ = start()
    assert b.reserve_model("RESEARCH", 5_000, 2_000) is None
    assert b.counters.input_tokens == 5_000
    b.settle_input(5_000, 3_200)
    assert b.counters.input_tokens == 3_200
    assert b.reserve_model("ANSWER", 4_000, 2_000) is None
    b.settle_input(4_000, 6_000)  # 실측이 추정보다 커도 실측으로 센다
    assert b.counters.input_tokens == 3_200 + 6_000
    assert b.counters.budget_used()["input_tokens"] == 9_200


def test_settle_input_without_measurement_keeps_the_estimate() -> None:
    b, _ = start()
    assert b.reserve_model("RESEARCH", 5_000, 2_000) is None
    b.settle_input(5_000, None)
    assert b.counters.input_tokens == 5_000


def test_settle_input_never_goes_below_the_value_at_call_start() -> None:
    """내보낸 `budget_used`는 줄지 않는다: 호출을 받을 때의 입력 누적 아래로는 정산하지 않는다(8-3)."""
    b, _ = start(input_tokens=3_000)
    assert b.reserve_model("ANSWER", 2_000, 100) is None
    b.settle_input(2_000, 0)
    assert b.counters.input_tokens == 3_000 == b.input_floor


def test_settled_input_frees_room_for_more_calls() -> None:
    """추정 5k 로는 72k 에 14번째 시도가 막히지만 실측(1k)으로 정산하면 18번 시도가 모두 들어간다."""
    settled, _ = start()
    unsettled, _ = start()
    n_settled = n_unsettled = 0
    while n_settled < 18 and settled.reserve_model("ANSWER", 5_000, 100) is None:
        settled.settle_input(5_000, 1_000)
        n_settled += 1
    while n_unsettled < 18 and unsettled.reserve_model("ANSWER", 5_000, 100) is None:
        n_unsettled += 1
    assert (n_settled, n_unsettled) == (18, 14)  # 72k ÷ 5k = 14


def test_settled_output_frees_room_for_more_calls() -> None:
    """실측으로 정산하면 8k 안에서 더 보낼 수 있고, 정산하지 않으면 막힌다."""
    settled, _ = start()
    unsettled, _ = start()
    for _ in range(4):
        assert settled.reserve_model("ANSWER", 10, 2_000) is None
        settled.settle_output(2_000, 200)
        assert unsettled.reserve_model("ANSWER", 10, 2_000) is None
    assert unsettled.reserve_model("ANSWER", 10, 2_000).code == "OUTPUT_RESERVE"
    assert settled.reserve_model("ANSWER", 10, 2_000) is None


@pytest.mark.parametrize(
    ("code", "reason"),
    [
        ("CALL_TIME", "TIME"),
        ("TURN_TIME", "TIME"),
        ("MODEL_CALLS", "MODEL_CALLS"),
        ("TOOL_CALLS", "TOOL_CALLS"),
        ("INPUT_TOKENS", "TOKENS"),
        ("OUTPUT_RESERVE", "TOKENS"),
        ("ACTIONS", "LIMIT"),
        ("TURN_CALLS", "LIMIT"),
    ],
)
def test_limit_code_to_end_reason(code: str, reason: str) -> None:
    assert limit_end_reason(code) == reason


# ── 도구 · 행동 · 계획 · 턴 (8-2 · L1-16 · L1-23 · L1-31) ───────────────────


def test_l1_16_tool_calls_up_to_twelve() -> None:
    b, _ = start()
    for _ in range(12):
        assert b.reserve_tool() is None
    hit = assert_detail(b.reserve_tool())
    assert (hit.code, hit.used, hit.limit) == ("TOOL_CALLS", 12, 12)
    assert b.counters.tool_calls == 12
    resumed, _ = start(tool_calls=12)
    assert resumed.reserve_tool().code == "TOOL_CALLS"


def test_l1_23_actions_five_total_one_save_one_reassess() -> None:
    b, _ = start()
    assert b.reserve_action("SAVE_GOAL") is None
    assert assert_detail(b.reserve_action("SAVE_GOAL")).code == "SAVE_ACTIONS"
    assert b.reserve_action("REASSESS") is None
    assert assert_detail(b.reserve_action("REASSESS")).code == "REASSESS_PER_TURN"
    assert b.reserve_action("FETCH_TAKE") is None
    assert b.reserve_action("FETCH_PITCH") is None
    assert b.reserve_action("START_PRACTICE") is None
    hit = assert_detail(b.reserve_action("FETCH_TAKE"))
    assert (hit.code, hit.used, hit.limit) == ("ACTIONS", 5, 5)
    c = b.counters
    assert (c.actions, c.saves, c.reassess) == (5, 1, 1)  # 막힌 예약은 세지 않는다


def test_l1_23_total_limit_is_checked_before_save_limit() -> None:
    b, _ = start(actions=5)
    assert b.reserve_action("SAVE_GOAL").code == "ACTIONS"
    assert b.counters.saves == 0


def test_plan_edits_up_to_three() -> None:
    b, _ = start()
    for _ in range(3):
        assert b.reserve_plan_edit() is None
    hit = assert_detail(b.reserve_plan_edit())
    assert (hit.code, hit.used, hit.limit) == ("PLAN_EDITS", 3, 3)


@pytest.mark.parametrize(("turn_count", "hit"), [(1, False), (6, False), (7, True), (9, True)])
def test_l1_31_seventh_turn_call_ends_without_model(turn_count: int, hit: bool) -> None:
    b, _ = start(turn_count=turn_count)
    got = b.turn_limit_hit()
    if not hit:
        assert got is None
    else:
        assert (assert_detail(got).code, got.used, got.limit) == ("TURN_CALLS", turn_count - 1, 6)


# ── 토큰 추정 · 카운터 ───────────────────────────────────────────────────────


def test_estimate_tokens_formula_and_conservative_for_korean() -> None:
    msgs = [{"role": "user", "content": "가나다"}]
    blob = json.dumps(msgs, ensure_ascii=False, separators=(",", ":"))
    assert estimate_tokens(msgs) == math.ceil(len(blob.encode("utf-8")) / 3) + 8
    korean = [{"role": "user", "content": "가" * 1000}]
    assert estimate_tokens(korean) >= 1000  # 한글 한 글자 ≈ 한 토큰 이상
    ascii_ = [{"role": "user", "content": "a" * 3000}]
    assert estimate_tokens(ascii_) >= 3000 / 4


def test_estimate_tokens_counts_messages_and_tools() -> None:
    one = [{"role": "user", "content": "시선"}]
    two = one + [{"role": "assistant", "content": ""}]
    assert estimate_tokens(two) >= estimate_tokens(one) + 8
    tools = [{"type": "function", "function": {"name": "get_screen", "parameters": {}}}]
    assert estimate_tokens(one, tools) > estimate_tokens(one)
    assert estimate_tokens(one, []) == estimate_tokens(one)


def test_counters_budget_used_shape_and_round_trip() -> None:
    c = Counters(
        model_calls=3,
        tool_calls=4,
        elapsed_ms=8400,
        input_tokens=12_000,
        output_tokens=6_000,
        turn_count=2,
        actions=1,
        saves=1,
    )
    assert c.budget_used() == {
        "model_calls": 3,
        "tool_calls": 4,
        "elapsed_ms": 8400,
        "input_tokens": 12_000,
        "output_tokens": 6_000,
    }
    assert Counters.load(c.dump()) == c
    assert Counters.load({**c.dump(), "unknown": 5}) == c
