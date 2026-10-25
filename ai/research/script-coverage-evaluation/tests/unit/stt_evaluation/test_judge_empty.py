"""STT 가 빈 장의 의미 평가를 코드가 만든다 (`empty_stt_evaluation`): LLM 없이 모두 말하지 않은 것으로 본다."""

from script_coverage.stt_evaluation.judge import empty_stt_evaluation, judged_sentences
from script_coverage.stt_evaluation.merge import resolve_name_checks
from script_coverage.stt_evaluation.schemas import FactCheck

from ..fakes import deck_rubrics


def _unverified(n: int) -> list[FactCheck]:
    return [
        FactCheck(
            fact_id=f"CF{i}",
            type="proper_noun",
            value=f"Name{i}",
            normalized=f"name{i}",
            importance="normal",
            key_point_ids=[],
            status="unverified",
        )
        for i in range(1, n + 1)
    ]


def test_every_judged_sentence_gets_all_units_missing():
    [rubric] = deck_rubrics((6,))
    result = empty_stt_evaluation(rubric, [])
    assert [s.sentence_id for s in result.sentences] == judged_sentences(rubric)
    assert result.sentences and all(
        s.evidence == [] and s.confidence == "high" and s.units for s in result.sentences
    )
    assert {u.status for s in result.sentences for u in s.units} == {"missing"}
    assert result.name_checks == []


def test_names_are_never_said():
    [rubric] = deck_rubrics((4,))
    unverified = _unverified(2)
    result = empty_stt_evaluation(rubric, unverified)
    assert [(c.name_id, c.said, c.evidence) for c in result.name_checks] == [
        ("N1", False, []),
        ("N2", False, []),
    ]
    resolve_name_checks(unverified, unverified, result)
    assert {c.status for c in unverified} == {"missing"}  # matched 가 되는 이름이 없다
