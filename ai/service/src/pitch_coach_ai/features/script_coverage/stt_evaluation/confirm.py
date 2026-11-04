"""사용자 확인 뒤 점수 다시 계산. 코드만 쓴다 (LLM 호출 없음, DB 없음). 저장 · 읽기는 호출하는 쪽이 한다."""

from ..shared.rubric import EvaluationRubric
from ..shared.text import normalize_script, overlaps
from .align import fidelity_excluding, positioned_tokens
from .judge import sentence_units
from .merge import aggregate_key_points, status_from_units
from .schemas import SentenceResult, SimilarItem, SlideEvaluation, UnitResult
from .scoring import slide_scores

CONFIRM_TEXT = {
    "as_script": "대본대로 말함 (음성 인식이 잘못 적음)",
    "as_stt": "STT 대로 말함 (발표자가 다른 말을 함)",
}


def _units_at(
    sentence: SentenceResult, item: SimilarItem, rubric: EvaluationRubric
) -> list[UnitResult]:
    """비슷한 말 자리가 든 전달 단위: 같은 사실을 가졌거나 위치가 겹치는 단위. 없으면 위치가 가장 가까운 단위 하나."""
    spans = {u.id: u.span for u in sentence_units(rubric, sentence.sentence_index)}
    hit = [
        u
        for u in sentence.units
        if (item.fact_id and item.fact_id in u.fact_ids)
        or (u.unit_id in spans and overlaps(spans[u.unit_id], item.script_span))
    ]
    if hit or not sentence.units:
        return hit

    def gap(u):
        return abs(spans.get(u.unit_id, item.script_span)[0] - item.script_span[0])

    return [min(sentence.units, key=gap)]


def _sync_sentences(e: SlideEvaluation, applied: dict[str, str], items: dict, facts: dict) -> None:
    """문장의 사실 상태 · 비슷한 말 목록을 답을 반영한 현재 값으로 맞춘다.

    같은 사실이 문장과 핵심 내용 · 사실 목록에서 다른 상태로 보이지 않게 한다. 이전 답까지 모두
    (applied) 기준으로 맞추므로, 답 없이 다시 계산해도 같은 결과가 된다. 답한 비슷한 말은 문장의
    similar_ids 에서만 뺀다 — similar_items 는 대본 충실도에서 그 자리를 계속 빼는 데 쓰므로 남기고,
    1차 판정(first_status) · 교차 검증 기록도 그때의 판정으로 남긴다.
    """
    answered_facts = {
        items[i].fact_id: facts[items[i].fact_id].status
        for i in applied
        if i in items and items[i].fact_id in facts
    }
    for s in e.sentences:
        s.fact_statuses = {
            fid: answered_facts.get(fid, status) for fid, status in s.fact_statuses.items()
        }
        s.similar_ids = [i for i in s.similar_ids if i not in applied]


def rescore_evaluation(
    evaluation: SlideEvaluation, answers: dict[str, str], rubric: EvaluationRubric
) -> SlideEvaluation:
    """사용자 확인을 반영해 판정과 점수를 다시 계산한다 (코드만, LLM 호출 없음). 원래 평가는 바꾸지 않고 새 평가를 돌려준다.

    받은 평가는 `applied_answers` 의 답이 이미 반영된 상태다. 그래서 이미 반영한 item_id 의 답은 (같든 다르든)
    다시 적용하지 않고 처음 답을 유지하며, 새 item_id 의 답만 적용한다. 모르는 item_id 의 답은 버린다.
    보류 목록 · 핵심 내용 · 점수는 이전 답과 새 답을 합친 `applied_answers` 로 다시 계산한다.
    답이 없어도 다시 계산해 돌려준다 (같은 입력이면 같은 결과).

    - as_script(대본대로 말함): 사실 sound_alike → matched. 단위·문장 판정은 처음 채점이 이미 대본대로 말한 것으로 보고 했으므로 그대로
    - as_stt(STT 대로 말함): 사실 sound_alike → mismatched, 그 자리가 든 전달 단위 → contradicted → 문장 · Key Point 를 다시 모은다
    - 답하지 않은 항목은 계속 보류한다 (비율에서 빼고 개수만 센다)
    """
    if rubric.meta.rubric_id != evaluation.rubric_id:
        raise ValueError(
            f"{evaluation.take_id} 슬라이드 {evaluation.slide_number}: 평가 기준이 바뀌었다 — 다시 채점한다"
        )
    e = evaluation.model_copy(deep=True)
    items = {it.item_id: it for it in e.similar_items}
    previous = dict(e.applied_answers)
    new = {
        k: v for k, v in answers.items() if k in items and k not in previous and v in CONFIRM_TEXT
    }
    applied = previous | new
    facts = {c.fact_id: c for c in e.critical_facts}
    by_index = {s.sentence_index: s for s in e.sentences}
    for item_id, answer in new.items():
        item = items[item_id]
        fact = facts.get(item.fact_id) if item.fact_id else None
        if fact is not None and fact.status == "sound_alike":
            fact.status = "matched" if answer == "as_script" else "mismatched"
            fact.note = f"사용자 확인: {CONFIRM_TEXT[answer]}"
        sentence = by_index.get(item.script_sentence_index)
        if answer == "as_stt" and sentence is not None:
            for u in _units_at(sentence, item, rubric):
                u.status = "contradicted"
            sentence.status = status_from_units([u.status for u in sentence.units])
            sentence.reason += f" [사용자 확인] '{item.script_text}' 를 '{item.stt_text}' 로 말함"
    _sync_sentences(e, applied, items, facts)
    held = [it for it in e.similar_items if it.item_id not in applied]
    stt_norm, script_norm = normalize_script(e.stt_text), normalize_script(rubric.normalized_script)
    e.key_points = aggregate_key_points(rubric, e.sentences, e.critical_facts, held, stt_norm)
    # 대본 충실도: 아직 보류한 자리와 '대본대로 말함' 자리는 계속 빼고, 'STT 대로 말함' 자리는 넣어 다시 계산한다
    keep_out = [it for it in e.similar_items if applied.get(it.item_id) != "as_stt"]
    e.fidelity = fidelity_excluding(
        rubric, positioned_tokens(script_norm), positioned_tokens(stt_norm), keep_out
    )
    e.scores = slide_scores(e.key_points, e.critical_facts, e.fidelity, held)
    e.applied_answers = applied
    return e
