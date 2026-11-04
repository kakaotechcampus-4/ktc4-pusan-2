"""최종 결론: 1차 분석 · 규칙 분석 · 검증 경고를 보고 LLM 이 최종 채점 기준을 정한다."""

import re

from ..shared.facts import CriticalFact
from ..shared.llm_step import config_hash
from ..shared.rubric import NAME_TYPES, EvaluationRubric, Keyword, NameDecision
from ..shared.text import NormalizedScript
from .config import REJECTION_TEXT, WARNING_TEXT
from .draft import (
    key_term_rejection,
    link_facts_to_key_points,
    make_key_points,
    merge_key_terms,
    new_rubric,
    resolve_roles,
    term_spans,
    validate_rubric,
)
from .prompts.final import FINAL_SYSTEM_PROMPT
from .prompts.semantic import SENTENCE_LINE
from .schemas import FinalReview, SlideScript


def final_config_hash(model: str) -> str:
    return config_hash(
        {
            "model": model,
            "prompt": FINAL_SYSTEM_PROMPT,
            "schema": FinalReview.model_json_schema(),
        }
    )


def name_candidates(
    draft: EvaluationRubric,
    rule_facts: list[CriticalFact],
    norm: NormalizedScript,
    deck_texts: list[str],
) -> list[CriticalFact]:
    """최종 결론이 판단할 이름·용어 후보: 규칙이 찾은 고유명사 + 1차 분석의 key_terms (걸러내지 않고 모두)."""
    key_points = [kp.model_copy(deep=True) for kp in draft.key_points]
    rule_copy = [f.model_copy(deep=True) for f in rule_facts]
    merged = merge_key_terms(key_points, rule_copy, norm, deck_texts, [], filter_terms=False)
    return [f for f in merged if f.type in NAME_TYPES]


def _describe_warning(warning: str) -> str:
    code, _, detail = warning.partition(":")
    return WARNING_TEXT.get(code, code) + (f" — {detail}" if detail else "")


def final_user_message(
    slide: SlideScript,
    norm: NormalizedScript,
    draft: EvaluationRubric,
    rule_facts: list[CriticalFact],
    candidates: list[CriticalFact],
    deck_texts: list[str],
) -> str:
    """최종 결론의 입력: 문장 번호가 붙은 대본 + 1차 분석 + 규칙 기반 분석 + 코드 검증 경고."""
    lines = [f"[슬라이드 {slide.slide_number}]", "## 대본"]
    lines += [SENTENCE_LINE.format(index=s.index, text=s.text) for s in norm.sentences]
    lines += [
        "",
        "## 1차 분석",
        "문장 역할: " + ", ".join(f"S{i}={role}" for i, role in enumerate(draft.sentence_roles)),
        f"core_claim: {draft.core_claim}",
        "Key Point:",
    ]
    lines += [
        f"- {kp.id} [{kp.importance}] ({','.join(f'S{i}' for i in kp.sentence_indices)}) {kp.content}"
        for kp in draft.key_points
    ]
    lines += ["", "## 규칙 기반 분석", "수치 사실 (코드가 자동으로 채점하므로 결정할 필요 없음):"]
    numeric = [f for f in rule_facts if f.type not in NAME_TYPES]
    lines += [f"- {f.value} → {f.normalized} (S{f.sentence_indices[0]})" for f in numeric] or [
        "- 없음"
    ]
    lines.append("이름·용어 후보 (모두 결정 필요):")
    for i, fact in enumerate(candidates, 1):
        if fact.source == "llm":
            origin = "1차 분석 key_term"
        else:
            origin = "영문 이름" if re.search(r"[A-Za-z0-9]", fact.value) else "한글 고유명사 추정"
        n_slides = sum(bool(term_spans(fact.value, text)) for text in deck_texts)
        hints = [
            origin,
            ",".join(f"S{j}" for j in sorted(set(fact.sentence_indices))),
            f"이 발표 슬라이드 {n_slides}개에 나옴",
        ]
        verdict = key_term_rejection(fact.value, deck_texts)
        if verdict:
            hints.append("코드 판정: " + REJECTION_TEXT[verdict])
        lines.append(f"- N{i} '{fact.value}' ({' · '.join(hints)})")
    if not candidates:
        lines.append("- 없음")
    lines += ["", "## 코드 검증 경고"]
    lines += [f"- {_describe_warning(w)}" for w in draft.warnings] or ["- 없음"]
    return "\n".join(lines)


def review_final(message: str, final_llm) -> FinalReview:
    """LLM API (최종 결론): 1차 분석·규칙 분석·검증 경고를 보고 최종 채점 기준을 정한다."""
    return final_llm.invoke([("system", FINAL_SYSTEM_PROMPT), ("user", message)])


def finalize_rubric(
    script_name: str,
    slide: SlideScript,
    norm: NormalizedScript,
    rule_facts: list[CriticalFact],
    keywords: list[Keyword],
    draft: EvaluationRubric,
    review: FinalReview,
    candidates: list[CriticalFact],
    deck_texts: list[str],
    model: str,
) -> EvaluationRubric:
    """최종 결론 → Evaluation Rubric (코드). 중요도·사실 연결·검증은 1차와 같은 규칙으로 다시 계산한다.

    - 수치 사실은 규칙 결과를 그대로 쓴다 (LLM 이 빼지 못한다)
    - 이름·용어 후보는 최종 결론이 keep 으로 판단한 것만 사실로 올린다
    """
    warnings: list[str] = []
    roles = resolve_roles(review.sentence_roles, len(norm.sentences), warnings)
    key_points = make_key_points(review.key_points, roles, norm, warnings)

    verdicts = {v.candidate_id: v for v in review.name_verdicts}
    decisions, kept = [], []
    for i, cand in enumerate(candidates, 1):
        verdict = verdicts.get(f"N{i}")
        if verdict is None:  # 판단이 빠진 후보는 코드 기준으로 정하고 경고한다
            warnings.append(f"name_undecided:N{i}")
            keep, reason = (
                key_term_rejection(cand.value, deck_texts) is None,
                "최종 결론이 판단하지 않아 코드 기준으로 결정",
            )
        else:
            keep, reason = verdict.keep, verdict.reason
        decisions.append(
            NameDecision(value=cand.value, source=cand.source, keep=keep, reason=reason)
        )
        if keep:
            kept.append(cand.model_copy(deep=True))

    facts = [f.model_copy(deep=True) for f in rule_facts if f.type not in NAME_TYPES] + kept
    facts.sort(key=lambda f: f.spans[0][0])
    for i, fact in enumerate(facts, 1):
        fact.id = f"CF{i}"
    link_facts_to_key_points(key_points, facts, warnings)
    for kp in key_points:  # Key Point 의 key_terms = 연결된 이름·용어 사실
        kp.key_terms = [f.value for f in facts if f.id in kp.fact_ids and f.type in NAME_TYPES]
    validate_rubric(key_points, facts, roles, warnings)

    return new_rubric(
        script_name,
        slide,
        norm,
        keywords,
        roles,
        review.core_claim,
        key_points,
        facts,
        warnings,
        model,
        final_config=final_config_hash(model),
        draft_warnings=draft.warnings,
        review_changes=review.changes,
        name_decisions=decisions,
    )
