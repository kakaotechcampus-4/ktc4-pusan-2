"""stream_json.py 단위 시험: 흘러오는 답 초안에서 최상위 값 · 판단 목록 · 문장 원소가 닫히는 때를 잡는다 (기능 문서 5-4 · 6-1 5번)."""

from __future__ import annotations

import json
import random

import pytest

from chat_agent.stream_json import AssessmentsDone, ClaimClosed, StreamTracker, ValueDone

DRAFT = {
    "route": "ANSWER",
    "assessments": [
        {
            "quote": "시선 {잘} 봤어",
            "type": "MEASURED",
            "verdict": "UNVERIFIABLE",
            "note": 'a "q" ]',
        },
    ],
    "scope_notes": ["QA_PRACTICE"],
    "claims": [
        {"kind": "RECORD", "text": "3장 시선 비율이 28%였어요 } ] {", "evidence_ids": ["e1", "e2"]},
        {"kind": "GUIDE", "text": '자료에는 "청중을 보세요"라고 해요\\n', "source_ids": ["g-07#2"]},
        {"kind": "ASSESSMENT", "text": "", "assessment_index": 0, "nested": {"x": [1, {"y": 2}]}},
    ],
    "start_practice": {"authorized_by": None},
}


def _run(text: str, sizes: list[int]) -> list[object]:
    t = StreamTracker()
    out: list[object] = []
    i = 0
    for n in sizes:
        out.extend(t.feed(text[i : i + n]))
        i += n
    out.extend(t.feed(text[i:]))
    return out


def _expect(draft: dict) -> list[object]:
    return [ValueDone("route", draft["route"]), AssessmentsDone(draft["assessments"])] + [
        ClaimClosed(i, c) for i, c in enumerate(draft["claims"])
    ]


@pytest.mark.parametrize("indent", [None, 2])
def test_events_match_whole_parse_for_any_chunking(indent: int | None) -> None:
    text = json.dumps(DRAFT, ensure_ascii=False, indent=indent)
    rnd = random.Random(7)
    for _ in range(50):
        sizes = [rnd.randint(1, 9) for _ in range(len(text) // 3)]
        assert _run(text, sizes) == _expect(DRAFT)


def test_one_char_at_a_time_and_ascii_escapes() -> None:
    text = json.dumps(DRAFT, ensure_ascii=True)
    assert _run(text, [1] * len(text)) == _expect(DRAFT)


def test_claims_before_assessments_and_order_is_kept() -> None:
    d = {"claims": DRAFT["claims"], "route": "ANSWER", "assessments": []}
    events = _run(json.dumps(d, ensure_ascii=False), [5] * 200)
    assert [type(e).__name__ for e in events] == ["ClaimClosed"] * 3 + [
        "ValueDone",
        "AssessmentsDone",
    ]
    assert events[-1] == AssessmentsDone([])


def test_element_is_not_emitted_before_it_closes() -> None:
    text = json.dumps(DRAFT, ensure_ascii=False)
    first_end = text.index("evidence_ids")  # 첫 원소 안쪽
    t = StreamTracker()
    events = t.feed(text[:first_end])
    assert events == [ValueDone("route", "ANSWER"), AssessmentsDone(DRAFT["assessments"])]
    assert t.claims_seen == 0
    rest = t.feed(text[first_end:])
    assert rest[0] == ClaimClosed(0, DRAFT["claims"][0])


def test_broken_element_gives_none_and_counting_continues() -> None:
    text = (
        '{"route":"ANSWER","claims":[{"kind":"RECORD","text":"a"},{"kind": tru},{"kind":"GUIDE"}]}'
    )
    events = StreamTracker().feed(text)
    assert events == [
        ValueDone("route", "ANSWER"),
        ClaimClosed(0, {"kind": "RECORD", "text": "a"}),
        ClaimClosed(1, None),
        ClaimClosed(2, {"kind": "GUIDE"}),
    ]


def test_non_object_elements_and_other_arrays_are_ignored() -> None:
    text = '{"scope_notes":["OTHER"],"claims":["문자열",{"kind":"SYSTEM"}],"extra":[{"kind":"X"}]}'
    events = StreamTracker().feed(text)
    # 문자열 원소는 객체가 아니므로 원소 사건이 나지 않는다(번호는 객체 원소만 센다)
    assert events == [ClaimClosed(0, {"kind": "SYSTEM"})]


def test_empty_feed_and_nested_key_named_claims_inside_value() -> None:
    t = StreamTracker()
    assert t.feed("") == []
    text = '{"meta":{"claims":[{"kind":"RECORD"}]},"claims":[{"kind":"GUIDE"}]}'
    # 최상위가 아닌 'claims'는 따라가지 않는다
    assert t.feed(text) == [ClaimClosed(0, {"kind": "GUIDE"})]


def test_top_level_string_value_with_escapes_is_not_read_as_keys() -> None:
    text = json.dumps(
        {"route": 'a "claims": [', "n": 3, "claims": [{"k": 1}], "tail": "끝"}, ensure_ascii=False
    )
    events = _run(text, [1] * len(text))
    assert events == [
        ValueDone("route", 'a "claims": ['),
        ClaimClosed(0, {"k": 1}),
        ValueDone("tail", "끝"),
    ]


def test_duplicate_top_level_key_is_flagged_but_nested_repeats_are_not() -> None:
    t = StreamTracker()
    t.feed('{"route":"ANSWER","claims":[{"kind":"A"},{"kind":"B"}],"x":{"route":1}')
    assert t.keys_seen == {"route", "claims", "x"} and not t.duplicate_key
    t.feed(',"route":"OUT_OF_SCOPE"}')
    assert t.duplicate_key
