"""입력 한도와 읽을 수 없는 기록 (features/coach-agent.md 5-2 · 8절 '입력 한도').

배열이 한도를 넘으면 요청 전체가 422 다. 한도 안이면 읽을 수 없는 기록 하나는 그 기록만 버리고
나머지로 판정한다 — 기록 하나 때문에 그 1초의 모든 판정을 잃지 않게 하기 위해서다.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from coach import finalize
from coach.schemas import (
    MAX_FINALIZE_EVENTS,
    MAX_REPLAY_RECORDS,
    MAX_REPLAY_WORDS,
    MAX_WINDOW_RECORDS,
    MAX_WINDOW_UTTERANCE_ENDS,
    MAX_WINDOW_WORDS,
    MAX_WORD_CHARS,
    MAX_WORD_SPAN_MS,
    CoachInputs,
    CoachRequest,
    FinalizeRequest,
    ReplayInput,
)

from .fakes import fake_judges
from .test_replay_recovery import raw


def gaze(t_ms: int, **kw) -> dict:
    return {"t_ms": t_ms, "duration_ms": 1000, "state": "CAMERA", **kw}


def voice(t_ms: int, **kw) -> dict:
    return {"t_ms": t_ms, "duration_ms": 1000, "voiced_ms": 600, **kw}


def word(start: int, text: str = "매출은") -> dict:
    return {"word": text, "start_ms": start, "end_ms": start + 400}


# ── 읽을 수 없는 기록은 그 기록만 버린다 ─────────────────────────────────────


def test_gaze_issues_null_is_an_empty_list():
    inputs = CoachInputs.model_validate({"gaze_records": [gaze(0, issues=None)]})
    assert inputs.gaze_records[0].issues == []


def test_gaze_issues_keep_only_strings():
    inputs = CoachInputs.model_validate({"gaze_records": [gaze(0, issues=["TOO_FAR", 3, None])]})
    assert inputs.gaze_records[0].issues == ["TOO_FAR"]


@pytest.mark.parametrize(
    "bad",
    [
        gaze(-1000),
        {"t_ms": 1000, "duration_ms": 0, "state": "CAMERA"},
        {"t_ms": 1000, "duration_ms": 1000},
        "not a record",
    ],
    ids=["negative_t_ms", "zero_duration", "no_state", "not_an_object"],
)
def test_unreadable_gaze_record_drops_only_that_second(bad):
    inputs = CoachInputs.model_validate({"gaze_records": [gaze(0), bad, gaze(2000)]})
    assert [r.t_ms for r in inputs.gaze_records] == [0, 2000]


@pytest.mark.parametrize(
    "bad",
    [voice(-1), voice(1000, voiced_ms=-5), voice(1000, voiced_ms=1500), voice(1000, silence_ms=-1)],
    ids=["negative_t_ms", "negative_voiced", "voiced_over_duration", "negative_silence"],
)
def test_unreadable_voice_record_drops_only_that_second(bad):
    inputs = CoachInputs.model_validate({"voice_records": [voice(0), bad, voice(2000)]})
    assert [r.t_ms for r in inputs.voice_records] == [0, 2000]


def test_unreadable_words_and_utterance_ends_are_dropped():
    inputs = CoachInputs.model_validate(
        {
            "words": [word(0), word(-10), word(500, "가" * (MAX_WORD_CHARS + 1)), word(900)],
            "utterance_ends": [400, -1, "x", True, 900],
        }
    )
    assert [w.start_ms for w in inputs.words] == [0, 900]
    assert inputs.utterance_ends == [400, 900]


def test_word_longer_than_the_span_limit_is_dropped():
    # 늦게 확정된 단어가 말한 장을 찾도록 장 전환을 남기는 여유와 같은 상한이다
    long = {"word": "매출은", "start_ms": 0, "end_ms": MAX_WORD_SPAN_MS + 1}
    backwards = {"word": "매출은", "start_ms": 500, "end_ms": 400}
    inputs = CoachInputs.model_validate({"words": [word(0), long, backwards]})
    assert [w.start_ms for w in inputs.words] == [0]


@pytest.mark.parametrize("status", ["unknown", None, 3, [], {}])
def test_unknown_stt_status_is_untrusted(status):
    # 판정 모듈은 다섯 값만 받는다. 모르는 값은 믿지 않는 상태로 읽는다
    assert CoachInputs.model_validate({"stt_status": status}).stt_status == "degraded"


def test_known_stt_status_is_kept():
    assert CoachInputs.model_validate({"stt_status": "reconnecting"}).stt_status == "reconnecting"


def test_one_bad_gaze_record_does_not_reject_the_request():
    req = {"take_id": "t", "t_ms": 3000, "inputs": {"gaze_records": [gaze(0, issues=None)]}}
    assert CoachRequest.model_validate(req).inputs.gaze_records[0].issues == []


# ── 배열 한도를 넘으면 요청 전체가 422 ───────────────────────────────────────


@pytest.mark.parametrize(
    ("field", "item", "limit"),
    [
        ("gaze_records", gaze(0), MAX_WINDOW_RECORDS),
        ("voice_records", voice(0), MAX_WINDOW_RECORDS),
        ("words", word(0), MAX_WINDOW_WORDS),
        ("utterance_ends", 100, MAX_WINDOW_UTTERANCE_ENDS),
    ],
)
def test_window_arrays_have_a_limit(field, item, limit):
    CoachInputs.model_validate({field: [item] * limit})
    with pytest.raises(ValidationError):
        CoachInputs.model_validate({field: [item] * (limit + 1)})


def test_finalize_events_have_a_limit():
    base = {"take_id": "t", "t_ms": 1000}
    event = {
        "kind": "OUTCOME",
        "event_id": "oc-1",
        "t_ms": 0,
        "intervention_id": "iv-0",
        "outcome": "NOT_MEASURED",
    }
    FinalizeRequest.model_validate({**base, "events": [event] * MAX_FINALIZE_EVENTS})
    with pytest.raises(ValidationError, match="events"):
        FinalizeRequest.model_validate({**base, "events": [event] * (MAX_FINALIZE_EVENTS + 1)})


def test_replay_records_count_the_same_second_once():
    # 같은 1초가 여러 번 와도(재전송) 한 번으로 센다 — 먼저 온 것만 쓰는 규칙과 같다
    twice = [gaze(i * 1000) for i in range(MAX_REPLAY_RECORDS)] * 2
    ReplayInput.model_validate({"gaze_records": twice})
    over = [gaze(i * 1000) for i in range(MAX_REPLAY_RECORDS + 1)]
    with pytest.raises(ValidationError):
        ReplayInput.model_validate({"gaze_records": over})


def test_replay_words_have_a_limit():
    w = {**word(0), "final_at_ms": 500}
    with pytest.raises(ValidationError):
        ReplayInput.model_validate({"words": [w] * (MAX_REPLAY_WORDS + 1)})


def test_replay_unknown_stt_status_change_is_untrusted():
    changes = [{"t_ms": 0, "status": "??"}, {"t_ms": 10, "status": []}]
    replay = ReplayInput.model_validate({"stt_status_changes": changes})
    assert [c.status for c in replay.stt_status_changes] == ["degraded", "degraded"]


def test_replay_keeps_the_first_record_of_the_same_second():
    first, again = gaze(0, reliability=0.9), gaze(0, reliability=0.1)
    replay = ReplayInput.model_validate({"gaze_records": [first, again, gaze(1000)]})
    assert [(r.t_ms, r.reliability) for r in replay.gaze_records] == [(0, 0.9), (1000, None)]


def test_replay_unreadable_t_ms_is_dropped_not_an_error():
    # 같은 1초를 셀 때 읽을 수 없는 t_ms(배열 · 객체)도 예외 없이 세고, 그 기록은 버린다
    records = [gaze(i * 1000) for i in range(MAX_REPLAY_RECORDS)] + [
        {"t_ms": [], "duration_ms": 1000, "state": "CAMERA"}
    ]
    replay = ReplayInput.model_validate({"gaze_records": records})
    assert len(replay.gaze_records) == MAX_REPLAY_RECORDS


def test_replay_unreadable_slide_change_is_dropped():
    slides = [{"number": 1, "started_ms": 0}, None, {"number": 2, "started_ms": 5000}]
    replay = ReplayInput.model_validate({"slides": slides})
    assert [s.number for s in replay.slides] == [1, 2]


def test_replay_with_repeated_seconds_still_recovers():
    # 같은 1초가 다섯 번씩 와도(창 하나에 150개) 복구가 만드는 1초 요청은 창 한도에 걸리지 않는다
    data = raw(31_000)
    data["gaze_records"] = data["gaze_records"] * 5
    data["voice_records"] = data["voice_records"] * 5
    req = {"take_id": "t", "t_ms": 31_000, "coach_state": None, "replay": data}
    assert finalize(req, fake_judges()).take_result.replayed is True


def test_replay_records_have_an_overall_limit_with_repeats():
    # 같은 1초가 되풀이돼도 받지만, 배열 전체는 한도의 4배까지다(버릴 기록이 끝없이 오지 않게)
    same = [gaze(0)] * (MAX_REPLAY_RECORDS * 4 + 1)
    with pytest.raises(ValidationError):
        ReplayInput.model_validate({"gaze_records": same})
