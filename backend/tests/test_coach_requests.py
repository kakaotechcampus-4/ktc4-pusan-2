"""코치 요청 만들기 — 시간 계획 · 미션 · 창 · 장 · replay · 코칭 계획.

AI 예시 요청(`ai/research/coach-agent/src/coach/examples/`)과 키가 같고, 예시의 Take 전체 원자료
(`replay`)로 창을 다시 고르면 예시 요청의 `inputs` 가 그대로 나오는지 본다. 발표 중 BE 가 보낸
창과 replay 로 다시 만든 창이 같아야 replay 로 만든 Take 결과가 정상 진행과 같다.
"""

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from pitch_coach_backend.coach.client import PlanReply
from pitch_coach_backend.coach.requests import (
    PLAN_MAX_MISSION_DESCRIPTION,
    PLAN_MAX_MISSIONS,
    PLAN_MAX_SLIDES,
    FinalWord,
    MetricSecond,
    MissionInput,
    PlanBasis,
    SlideEvent,
    SlideTiming,
    TakeConstants,
    TranscriptSegment,
    build_plan,
    evaluate_request,
    finalize_request,
    missions_payload,
    plan_basis,
    plan_for_evaluate,
    plan_payload,
    plan_request,
    replay_input,
    request_basis,
    slide_at,
    slide_changes,
    window_inputs,
)

EXAMPLES = Path(__file__).resolve().parents[2] / "ai/research/coach-agent/src/coach/examples"


def example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


SLIDES = [
    SlideTiming(slide_number=2, expected_ms=60_000, script_chars=300),
    SlideTiming(slide_number=1, expected_ms=60_000, script_chars=None),
]


def constants(**overrides: Any) -> TakeConstants:
    values: dict[str, Any] = {
        "take_id": "take-1",
        "mode": "COACHING",
        "plan": build_plan(300, time_limit_sec=300, lower_deviation_sec=30, upper_deviation_sec=30),
        "script_mode": "HIGHLIGHT",
    }
    return TakeConstants(**{**values, **overrides})


# ── 시간 계획 ─────────────────────────────────────────────────────────


def test_plan_target_is_the_goal_and_range_is_the_time_limit():
    plan = build_plan(
        240, time_limit_sec=300, lower_deviation_sec=30, upper_deviation_sec=60, slides=SLIDES
    )

    assert plan == {
        "target_ms": 240_000,
        "min_ms": 270_000,
        "max_ms": 360_000,
        "slides": [
            {"slide_number": 1, "target_ms": 60_000, "script_chars": 0},
            {"slide_number": 2, "target_ms": 60_000, "script_chars": 300},
        ],
    }


def test_plan_min_is_null_when_the_range_reaches_zero():
    plan = build_plan(60, time_limit_sec=60, lower_deviation_sec=60, upper_deviation_sec=0)

    assert (plan["min_ms"], plan["max_ms"]) == (None, 60_000)


def test_zero_deviation_means_no_slack_not_unknown():
    plan = build_plan(300, time_limit_sec=300, lower_deviation_sec=0, upper_deviation_sec=0)

    assert (plan["min_ms"], plan["max_ms"]) == (300_000, 300_000)


def test_slides_are_left_out_unless_every_slide_has_a_target():
    partial = [*SLIDES, SlideTiming(slide_number=3, expected_ms=None, script_chars=100)]

    plan = build_plan(
        300, time_limit_sec=300, lower_deviation_sec=0, upper_deviation_sec=0, slides=partial
    )

    assert plan["slides"] == []


def test_slide_targets_follow_the_current_goal_when_the_parse_used_another():
    """제한 시간만 바뀌면 다시 파싱하지 않는다. 장 목표를 지금 목표에 맞춰 비율로 바꾼다
    (script-parser.md 12-2). 누적으로 반올림해 합이 목표와 같다."""
    parsed = [
        SlideTiming(slide_number=1, expected_ms=100_000, script_chars=500),
        SlideTiming(slide_number=2, expected_ms=100_000, script_chars=500),
        SlideTiming(slide_number=3, expected_ms=100_000, script_chars=500),
    ]

    plan = build_plan(
        200,
        time_limit_sec=200,
        lower_deviation_sec=0,
        upper_deviation_sec=0,
        slides=parsed,
        parsed_target_ms=300_000,
    )

    targets = [s["target_ms"] for s in plan["slides"]]
    # 200000 × 1/3 = 66666.67 → 66667, 누적 133333.33 → 133333, 마지막은 남은 몫
    assert targets == [66_667, 66_666, 66_667]
    assert sum(targets) == 200_000


def test_slide_targets_are_kept_without_a_parse_target():
    """파싱 때 목표가 없었으면(기준 말 속도로 잼) 비율로 바꿀 기준이 없다. 그대로 싣는다."""
    plan = build_plan(
        200,
        time_limit_sec=200,
        lower_deviation_sec=0,
        upper_deviation_sec=0,
        slides=SLIDES,
        parsed_target_ms=None,
    )

    assert [s["target_ms"] for s in plan["slides"]] == [60_000, 60_000]


# ── Take 상수 ─────────────────────────────────────────────────────────


def test_missions_leave_out_missing_priority_and_description():
    payload = missions_payload(
        [
            MissionInput(
                "m-1", "GAZE", 3, {"metric": "script_ratio", "operator": "LTE", "value": 0.4}
            ),
            MissionInput("m-2", "FILLER", None, None, priority=2, description="덜 말하기"),
        ]
    )

    assert payload == [
        {
            "mission_id": "m-1",
            "area": "GAZE",
            "slide_number": 3,
            "target": {"metric": "script_ratio", "operator": "LTE", "value": 0.4},
        },
        {
            "mission_id": "m-2",
            "area": "FILLER",
            "slide_number": None,
            "target": None,
            "priority": 2,
            "description": "덜 말하기",
        },
    ]


@pytest.mark.parametrize(
    ("mode", "sent"),
    [("COACHING", "COACHING"), ("EXAM", "EXAM"), ("exam", "EXAM"), ("PRACTICE", "COACHING")],
)
def test_mode_outside_the_contract_falls_back_to_coaching(mode: str, sent: str):
    assert constants(mode=mode).common()["mode"] == sent


@pytest.mark.parametrize(
    ("script_mode", "sent"),
    [("KEYWORD", "KEYWORD"), ("off", "OFF"), ("FULL", None), (None, None)],
)
def test_script_mode_outside_the_contract_is_not_sent(script_mode: str | None, sent: str | None):
    request = finalize_request(constants(script_mode=script_mode), 1000, {}, None, [])

    assert request["script_mode"] == sent


def test_broken_base_level_is_not_sent():
    assert constants(base_level_db=float("nan")).common()["calibration"] == {"base_level_db": None}
    assert constants(base_level_db=-29.5).common()["calibration"] == {"base_level_db": -29.5}


# ── 요청 모양 ─────────────────────────────────────────────────────────


def test_evaluate_request_has_the_example_keys():
    body = evaluate_request(constants(), 92_000, {}, None)

    assert body.keys() == example("evaluate/request.json").keys()
    assert (body["t_ms"], body["coach_state"]) == (92_000, None)


def test_finalize_request_has_the_example_keys():
    events = example("finalize/request.json")["events"]

    body = finalize_request(constants(), 92_000, {}, {"v": 2}, events)
    replayed = finalize_request(constants(), 92_000, {}, None, events, replay={"words": []})

    assert body.keys() == example("finalize/request.json").keys()
    assert replayed.keys() == example("finalize/request_replay.json").keys()
    assert body["events"] == events


def test_example_request_round_trips_through_take_constants():
    """예시 요청의 Take 상수를 TakeConstants 에 넣으면 같은 값이 다시 나온다."""
    body = example("evaluate/request.json")
    take = TakeConstants(
        take_id=body["take_id"],
        mode=body["mode"],
        plan=body["plan"],
        missions=body["missions"],
        recurring_issues=body["recurring_issues"],
        coaching_plan=body["coaching_plan"],
        script_used=body["script_used"],
        base_level_db=body["calibration"]["base_level_db"],
    )

    rebuilt = evaluate_request(take, body["t_ms"], body["inputs"], body["coach_state"])

    assert rebuilt == body


# ── 창 ────────────────────────────────────────────────────────────────


def replay_words(raw: dict[str, Any]) -> list[FinalWord]:
    return [
        FinalWord(w["word"], w["start_ms"], w["end_ms"], w["final_at_ms"]) for w in raw["words"]
    ]


def test_live_window_matches_the_example_evaluate_inputs():
    """예시 Take 의 원자료에서 92초 요청 때 BE 가 들고 있었을 것만 넘기면 예시 요청과 같다."""
    expected = example("evaluate/request.json")
    raw = example("finalize/request_replay.json")["replay"]
    t_ms = expected["t_ms"]
    # 요청을 만들 때까지 받은 확정 단어. 92초에 확정된 두 단어는 STT 를 닫을 때 들어온 것이다
    received = [w for w in replay_words(raw) if w.final_at_ms < t_ms]

    inputs = window_inputs(
        t_ms,
        gaze_records=raw["gaze_records"],
        voice_records=raw["voice_records"],
        words=received,
        utterance_ends=raw["utterance_ends"],
        stt_status="ok",
        slide={"number": 3, "started_ms": 59_390},
    )

    assert inputs == expected["inputs"]


def test_last_window_matches_the_example_finalize_inputs():
    """마지막 창은 STT 를 닫은 뒤 확정된 단어까지 넘긴다. 고르는 규칙은 발표 중과 같다."""
    expected = example("finalize/request.json")
    raw = example("finalize/request_replay.json")["replay"]

    inputs = window_inputs(
        expected["t_ms"],
        gaze_records=raw["gaze_records"],
        voice_records=raw["voice_records"],
        words=replay_words(raw),
        utterance_ends=raw["utterance_ends"],
        stt_status="ok",
        slide={"number": 3, "started_ms": 59_390},
    )

    assert inputs == expected["inputs"]


def test_window_edges_and_duplicates():
    def record(t_ms: int, duration_ms: int = 1000, tag: str = "a") -> dict[str, Any]:
        return {"t_ms": t_ms, "duration_ms": duration_ms, "tag": tag}

    words = [
        FinalWord("앞", 30_000, 40_000, 40_000),  # end 가 60초 창 경계 — 빠진다
        FinalWord("안", 40_500, 41_000, 41_000),
        # t_ms 뒤에 끝났다 — 다음 요청에. 마지막 창이어도 빠진다(AI replay 의 마지막 창과 같게)
        FinalWord("뒤", 99_500, 100_500, 100_500),
    ]

    inputs = window_inputs(
        100_000,
        gaze_records=[
            record(69_000),  # 30초 창 밖
            record(70_000),
            record(70_000, tag="b"),  # 같은 1초 — 먼저 것
            record(99_000),
            record(99_500),  # 아직 안 끝난 1초
        ],
        voice_records=[],
        words=words,
        utterance_ends=[40_000, 40_000, 100_000, 100_001],
        stt_status="reconnecting",
        slide=None,
    )

    assert inputs["gaze_records"] == [record(70_000), record(99_000)]
    assert [w["word"] for w in inputs["words"]] == ["안"]
    assert inputs["utterance_ends"] == [40_000, 100_000]
    assert (inputs["stt_status"], inputs["slide"]) == ("reconnecting", None)


def test_slide_changes_keep_the_first_of_each_time_and_start_at_zero():
    # 늦게 온 것 · 다시 보낸 것이 섞여 와도 시각 순서로, 같은 시각은 먼저 온 것만
    events = [
        SlideEvent(2, 59_390),
        SlideEvent(1, 1_200),
        SlideEvent(3, 90_000),
        SlideEvent(9, 59_390),
        SlideEvent(2, 120_000),
    ]

    changes = slide_changes(events)

    # 처음 알게 된 장은 Take 시작부터로 본다 (coach-agent.md 5-2 replay)
    assert changes == [
        {"number": 1, "started_ms": 0},
        {"number": 2, "started_ms": 59_390},
        {"number": 3, "started_ms": 90_000},
        {"number": 2, "started_ms": 120_000},
    ]
    assert slide_changes([]) == []


def test_slide_at_is_the_latest_change_at_or_before_the_request():
    changes = slide_changes([SlideEvent(1, 0), SlideEvent(2, 59_390), SlideEvent(3, 90_000)])

    assert slide_at(0, changes) == {"number": 1, "started_ms": 0}
    assert slide_at(59_389, changes) == {"number": 1, "started_ms": 0}
    # 요청 시각과 같은 시각에 넘긴 장은 그 요청의 장이다
    assert slide_at(59_390, changes) == {"number": 2, "started_ms": 59_390}
    assert slide_at(200_000, changes) == {"number": 3, "started_ms": 90_000}
    # 장을 아직 모르면 싣지 않는다 — 코치가 마지막으로 알던 장을 쓴다
    assert slide_at(5_000, []) is None


# ── replay ────────────────────────────────────────────────────────────


def seconds_from(raw: dict[str, Any]) -> list[MetricSecond]:
    """예시 원자료를 take_metric_seconds 행처럼 펼친다 (metrics 하나 = 시선 · 음량 기록 하나).
    `slide_number` 는 참고값이라 replay 에 쓰지 않는다 — 일부러 틀린 값을 넣는다."""
    voice = {r["t_ms"]: r for r in raw["voice_records"]}
    return [
        MetricSecond(gaze["t_ms"] + gaze["duration_ms"], 99, gaze, voice[gaze["t_ms"]], "ok")
        for gaze in raw["gaze_records"]
    ]


def slide_events_from(raw: dict[str, Any]) -> list[SlideEvent]:
    """예시 원자료의 장 변경을 take_slide_events 행처럼 (FE 가 보낸 WS slide 그대로)."""
    return [SlideEvent(s["number"], s["started_ms"]) for s in raw["slides"]]


def test_replay_rebuilds_the_example_raw_data():
    raw = example("finalize/request_replay.json")["replay"]
    # 확정 시각이 같은 단어끼리 한 구간이다
    by_final: dict[int, list[dict[str, Any]]] = {}
    for w in raw["words"]:
        by_final.setdefault(w["final_at_ms"], []).append(
            {"word": w["word"], "punctuated_word": w["word"] + ".", **w}
        )
    segments = [
        TranscriptSegment(end_ms=ws[-1]["end_ms"], words=ws, final_at_ms=final_at)
        for final_at, ws in by_final.items()
    ]

    replay = replay_input(
        seconds_from(raw),
        reversed(segments),
        raw["utterance_ends"],
        reversed(slide_events_from(raw)),
        t_end_ms=92_000,
    )

    assert replay["gaze_records"] == raw["gaze_records"]
    assert replay["voice_records"] == raw["voice_records"]
    assert replay["stt_status_changes"] == raw["stt_status_changes"]
    assert replay["utterance_ends"] == raw["utterance_ends"]
    assert sorted(replay["words"], key=lambda w: w["start_ms"]) == raw["words"]
    # 장은 장 변경 기록 그대로다 (metrics.slide_number 를 쓰지 않는다)
    assert replay["slides"] == raw["slides"]
    assert replay.keys() == raw.keys()


def test_replay_stt_history_and_old_rows():
    seconds = [
        MetricSecond(2_000, 1, None, None, "ok"),
        MetricSecond(1_000, 1, None, None, "connecting"),
        MetricSecond(3_000, 1, None, None, "ok"),
        MetricSecond(4_000, 1, None, None, None),  # 마이그레이션 전 행
        MetricSecond(5_000, 1, None, None, "reconnecting"),
    ]
    old_segment = TranscriptSegment(
        end_ms=900, words=[{"word": "가", "start_ms": 100, "end_ms": 900}], final_at_ms=None
    )

    replay = replay_input(seconds, [old_segment], [900, 900], [], t_end_ms=6_000)

    assert replay["stt_status_changes"] == [
        {"t_ms": 0, "status": "connecting"},
        {"t_ms": 2_000, "status": "ok"},
        {"t_ms": 5_000, "status": "reconnecting"},
    ]
    # 확정 시각이 없으면 구간 끝으로 대신한다
    assert replay["words"] == [{"word": "가", "start_ms": 100, "end_ms": 900, "final_at_ms": 900}]
    assert replay["utterance_ends"] == [900]


def test_replay_cuts_words_finalized_after_the_take_ended():
    """STT 를 닫은 뒤 확정된 단어는 확정 시각이 Take 끝보다 늦다. AI 는 마지막 창에 확정 시각이
    Take 끝 이하인 단어만 넣으므로, 자르지 않으면 정상 finalize 가 센 단어를 replay 가 빠뜨린다."""
    closed = TranscriptSegment(
        end_ms=91_500,
        words=[{"word": "끝", "start_ms": 91_000, "end_ms": 91_500}],
        final_at_ms=93_200,
    )

    replay = replay_input([], [closed], [], [], t_end_ms=92_000)

    assert replay["words"][0]["final_at_ms"] == 92_000


# ── 코칭 계획 ─────────────────────────────────────────────────────────


PLAN_REPLY = PlanReply.model_validate(
    {
        "coaching_plan": {
            "focus": [{"area": "GAZE", "slide_number": 3, "weight": 1.5, "why": "근거"}],
            "relax": [{"area": "FILLER", "slide_number": 1, "why": "근거"}],
            "max_interventions": 4,
        },
        "source_take_id": "take-122",
        "script_version_id": "sv-3",
        "fallback_reason": None,
        "dropped": ["relax TIME 3: 봐줄 수 없는 영역"],
        "planner_hash": "0123456789ab",
        "meta": {"schema_version": "1.0", "feature_version": "coach-1.2", "model": "m"},
    }
)
PITCH_PLAN = build_plan(300, time_limit_sec=300, lower_deviation_sec=30, upper_deviation_sec=30)
PLAN_REQUEST = plan_request(
    pitch_id="p-1",
    source_take_id="take-122",
    script_version_id="sv-3",
    plan=PITCH_PLAN,
    scripts=[(1, "첫 장")],
)
NOW = {
    "source_take_id": "take-122",
    "script_version_id": "sv-3",
    "time_limit_sec": 300,
    "lower_deviation_sec": 30,
    "upper_deviation_sec": 30,
}


def test_plan_row_keeps_the_reply_and_what_it_was_asked_for():
    assert PLAN_REQUEST is not None

    payload = plan_payload(PLAN_REQUEST, PLAN_REPLY)
    basis = request_basis(PLAN_REQUEST)

    assert payload == {
        "coaching_plan": PLAN_REPLY.coaching_plan,
        "fallback_reason": None,
        "dropped": ["relax TIME 3: 봐줄 수 없는 영역"],
        "planner_hash": "0123456789ab",
    }
    assert basis == PlanBasis("take-122", "sv-3", 300_000, 270_000, 330_000)
    # JSONB 에 넣을 수 있어야 한다
    assert json.loads(json.dumps(payload)) == payload


def test_plan_basis_comes_from_the_request_even_if_the_reply_drops_the_ids():
    assert PLAN_REQUEST is not None
    reply = PLAN_REPLY.model_copy(update={"source_take_id": None, "script_version_id": None})

    plan_payload(PLAN_REQUEST, reply)

    assert request_basis(PLAN_REQUEST) == plan_basis(**NOW)


def test_plan_is_sent_without_why():
    assert PLAN_REQUEST is not None
    payload = plan_payload(PLAN_REQUEST, PLAN_REPLY)

    sent = plan_for_evaluate(payload)

    assert sent == {
        "focus": [{"area": "GAZE", "slide_number": 3, "weight": 1.5}],
        "relax": [{"area": "FILLER", "slide_number": 1}],
        "max_interventions": 4,
    }
    # 저장된 값은 그대로다
    assert payload["coaching_plan"]["focus"][0]["why"] == "근거"


@pytest.mark.parametrize("payload", [None, {}, {"coaching_plan": None}, {"coaching_plan": []}])
def test_broken_plan_row_is_not_sent(payload: Any):
    assert plan_for_evaluate(payload) is None


def test_plan_ids_compare_as_strings():
    """DB 의 Uuid 칼럼은 uuid.UUID 로, JSON 으로 오간 id 는 문자열로 읽힌다."""
    take_id, script_id, pitch_id = uuid.uuid7(), uuid.uuid7(), uuid.uuid7()
    request = plan_request(
        pitch_id=pitch_id,
        source_take_id=take_id,
        script_version_id=script_id,
        plan=PITCH_PLAN,
        scripts=[],
    )
    assert request is not None
    request = json.loads(json.dumps(request))

    now = plan_basis(**{**NOW, "source_take_id": take_id, "script_version_id": script_id})

    assert request["pitch_id"] == str(pitch_id)
    assert request_basis(request) == now


@pytest.mark.parametrize(
    "now",
    [
        {"source_take_id": "take-123"},
        {"source_take_id": None},
        {"script_version_id": "sv-4"},
        {"time_limit_sec": 360},
        {"lower_deviation_sec": 0},
        {"upper_deviation_sec": 0},
    ],
)
def test_plan_basis_differs_when_anything_changed(now: dict[str, Any]):
    assert PLAN_REQUEST is not None

    assert request_basis(PLAN_REQUEST) != plan_basis(**{**NOW, **now})


def test_plan_basis_with_no_slack_keeps_null_min():
    """허용 미달이 제한 시간 이상이면 min_ms 는 null 이다. null 끼리는 같은 값이다 (12-2)."""
    basis = plan_basis(
        source_take_id=None,
        script_version_id=None,
        time_limit_sec=60,
        lower_deviation_sec=60,
        upper_deviation_sec=0,
    )

    assert basis == PlanBasis(None, None, 60_000, None, 60_000)


def test_plan_request_has_the_contract_fields():
    body = plan_request(
        pitch_id="p-1",
        source_take_id=None,
        script_version_id="sv-3",
        plan=PITCH_PLAN,
        scripts=[(2, "둘째 장"), (1, "첫 장")],
        missions=[MissionInput("m-1", "GAZE", None, None)],
        recurring_issues=[{"area": "GAZE", "slide_number": 3, "dimension": "script_independence"}],
    )

    assert body is not None
    # coach-agent.md 5-2 plan 표
    assert list(body) == [
        "pitch_id",
        "source_take_id",
        "script_version_id",
        "plan",
        "scripts",
        "missions",
        "recurring_issues",
        "previous_take_result",
        "previous_review",
    ]
    assert body["scripts"] == [
        {"slide_number": 1, "script": "첫 장"},
        {"slide_number": 2, "script": "둘째 장"},
    ]
    assert body["recurring_issues"][0]["dimension"] == "script_independence"


def test_plan_request_over_the_input_limit_is_not_made():
    many_slides = [SlideTiming(n, 1_000, 10) for n in range(1, PLAN_MAX_SLIDES + 2)]
    big_plan = build_plan(
        300, time_limit_sec=300, lower_deviation_sec=0, upper_deviation_sec=0, slides=many_slides
    )
    common: dict[str, Any] = {"pitch_id": "p", "source_take_id": None, "script_version_id": None}

    assert plan_request(**common, plan=big_plan, scripts=[]) is None
    assert (
        plan_request(
            **common,
            plan=PITCH_PLAN,
            scripts=[],
            missions=[MissionInput(f"m-{i}", "GAZE", None, None) for i in range(21)],
        )
        is None
    )
    assert PLAN_MAX_MISSIONS == 20
    assert (
        plan_request(
            **common,
            plan=PITCH_PLAN,
            scripts=[],
            missions=[MissionInput(f"m-{i}", "GAZE", None, None) for i in range(20)],
        )
        is not None
    )


def test_plan_request_drops_only_a_too_long_description():
    long = "가" * (PLAN_MAX_MISSION_DESCRIPTION + 1)
    exact = "나" * PLAN_MAX_MISSION_DESCRIPTION

    body = plan_request(
        pitch_id="p",
        source_take_id=None,
        script_version_id=None,
        plan=PITCH_PLAN,
        scripts=[],
        missions=[
            MissionInput("m-1", "GAZE", 3, None, priority=2, description=long),
            MissionInput("m-2", "GAZE", 3, None, description=exact),
        ],
    )

    assert body is not None
    assert body["missions"][0] == {
        "mission_id": "m-1",
        "area": "GAZE",
        "slide_number": 3,
        "target": None,
        "priority": 2,
    }
    assert body["missions"][1]["description"] == exact


def test_finalize_request_copies_the_events():
    events = [{"event_id": "e", "kind": "SLIDE", "t_ms": 1}]

    body = finalize_request(constants(), 1000, {}, None, events)
    body["events"][0]["t_ms"] = 999

    assert events[0]["t_ms"] == 1


def test_take_id_is_sent_as_a_string():
    """realtime 은 Take id 를 uuid.UUID 로 들고 있다. 그대로 실으면 JSON 으로 못 바꾼다."""
    take_id = uuid.uuid7()

    body = evaluate_request(constants(take_id=take_id), 1000, {}, None)

    assert body["take_id"] == str(take_id)
    json.dumps(body)
