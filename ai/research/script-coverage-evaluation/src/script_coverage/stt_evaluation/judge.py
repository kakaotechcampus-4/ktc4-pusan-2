"""LLM 의미 평가: 대본 문장의 전달 단위마다 STT 에서 말했는지 판정한다 (슬라이드당 1회)."""

from ..shared.llm_step import config_hash
from ..shared.rubric import ContentUnit, EvaluationRubric
from ..shared.text import NormalizedScript
from .prompts.semantic import SEMANTIC_EVAL_PROMPT
from .schemas import FactCheck, SemanticEvaluation


def eval_config_hash(model: str) -> str:
    return config_hash(
        {
            "model": model,
            "prompt": SEMANTIC_EVAL_PROMPT,
            "schema": SemanticEvaluation.model_json_schema(),
        }
    )


def judged_sentences(rubric: EvaluationRubric) -> list[int]:
    """판정 대상 대본 문장: 인사·전환(skip)이 아닌 문장과 Key Point 근거 문장."""
    in_key_points = {i for kp in rubric.key_points for i in kp.sentence_indices}
    return [
        i for i, role in enumerate(rubric.sentence_roles) if role != "skip" or i in in_key_points
    ]


def sentence_units(rubric: EvaluationRubric, i: int) -> list[ContentUnit]:
    """대본 문장 i 의 전달 단위. 평가 기준에 단위가 없는 문장(Key Point 근거인 skip 문장 등)은 문장 전체를 단위 하나로 본다."""
    own = [u for u in rubric.content_units if u.sentence_index == i]
    if own:
        return own
    s = rubric.sentences[i]
    return [
        ContentUnit(
            id=f"S{i}-U1",
            sentence_index=i,
            text=s.text,
            quote=s.text,
            span=(s.start, s.end),
            fact_ids=[f.id for f in rubric.critical_facts if i in f.sentence_indices],
            source="sentence",
        )
    ]


def semantic_eval_message(
    slide_number: int,
    rubric: EvaluationRubric,
    stt_norm: NormalizedScript,
    unverified: list[FactCheck],
) -> str:
    """입력: 판정 대상 대본 문장(번호 + 문장 속 핵심 수치·이름 + 전달 단위) + 번호를 붙인 STT.

    규칙 쪽 판정(정렬 점수, 사실 검증 결과)은 넣지 않는다. LLM 이 독립적으로 판단해야 둘을 비교해 충돌을 찾을 수 있다.
    """
    lines = [f"[슬라이드 {slide_number}]", "## 대본 문장 (판정 대상)"]
    for i in judged_sentences(rubric):
        key_facts = [f.value for f in rubric.critical_facts if i in f.sentence_indices]
        lines.append(
            f"[S{i}] {rubric.sentences[i].text}"
            + (f" (핵심 수치·이름: {', '.join(key_facts)})" if key_facts else "")
        )
        lines += [f"  - {u.id} {u.text}" for u in sentence_units(rubric, i)]
    if unverified:
        lines += ["", "## 이름 확인"]
        lines += [f"- N{i} {check.value}" for i, check in enumerate(unverified, 1)]
    lines += ["", "## 발표 STT"]
    lines += [f"[T{s.index}] {s.text}" for s in stt_norm.sentences] or ["(발화 없음)"]
    return "\n".join(lines)


def evaluate_semantics(message: str, eval_llm) -> SemanticEvaluation:
    """LLM API (의미 평가): 대본 문장별 전달 단위 판정 + 이름 확인을 한 번의 호출로 받는다."""
    return eval_llm.invoke([("system", SEMANTIC_EVAL_PROMPT), ("user", message)])
