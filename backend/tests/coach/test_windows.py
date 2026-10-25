"""코치 창 · 장 · replay.

예시의 Take 전체 원자료(`replay`)로 창을 다시 고르면 예시 요청의 `inputs` 가 그대로 나오는지 본다.
발표 중 BE 가 보낸 창과 replay 로 다시 만든 창이 같아야 replay 로 만든 Take 결과가 정상 진행과 같다.
"""

from typing import Any

from pitch_coach_backend.coach.requests import (
    FinalWord,
    MetricSecond,
    SlideEvent,
    TranscriptSegment,
    replay_input,
    slide_at,
    slide_changes,
    window_inputs,
)
from tests.coach.conftest import example

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
