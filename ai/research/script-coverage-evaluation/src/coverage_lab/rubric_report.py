"""평가 기준(EvaluationRubric)을 표로 보여 주는 도구."""

from collections import Counter

import pandas as pd
from IPython.display import display

from script_coverage.shared.facts import CriticalFact
from script_coverage.shared.rubric import EvaluationRubric


def rubric_summary(rubrics: list[EvaluationRubric]) -> pd.DataFrame:
    """슬라이드별 평가 기준 한 줄 요약."""
    rows = []
    for r in rubrics:
        importance = Counter(kp.importance for kp in r.key_points)
        rows.append(
            {
                "slide": r.meta.slide_number,
                "KP": len(r.key_points),
                "critical/high/normal": f"{importance['critical']}/{importance['high']}/{importance['normal']}",
                "facts(rule/llm)": f"{sum(f.source == 'rule' for f in r.critical_facts)}/{sum(f.source == 'llm' for f in r.critical_facts)}",
                "1차 경고": len(r.draft_warnings),
                "최종 결론이 바꾼 점": len(r.review_changes),
                "전달 단위": len(r.content_units),
                "최종 경고": "; ".join(r.warnings),
                "top keywords": ", ".join(k.term for k in r.keywords[:4]),
            }
        )
    return pd.DataFrame(rows)


def facts_table(facts: list[CriticalFact]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "id": f.id,
                "type": f.type,
                "value": f.value,
                "normalized": f.normalized,
                "qualifier": f.qualifier,
                "count": len(f.spans),
                "sentences": f.sentence_indices,
            }
            for f in facts
        ]
    )


def key_points_table(rubric: EvaluationRubric) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "id": kp.id,
                "importance": kp.importance,
                "content": kp.content,
                "sentences": kp.sentence_indices,
                "key_terms": kp.key_terms,
                "facts": kp.fact_ids,
            }
            for kp in rubric.key_points
        ]
    )


def linked_facts_table(rubric: EvaluationRubric) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "id": f.id,
                "type": f.type,
                "value": f.value,
                "normalized": f.normalized,
                "qualifier": f.qualifier,
                "source": f.source,
                "key_points": f.key_point_ids,
                "importance": f.importance,
            }
            for f in rubric.critical_facts
        ]
    )


def units_table(rubric: EvaluationRubric) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"id": u.id, "단위": u.text, "대본 표현": u.quote, "사실": u.fact_ids, "출처": u.source}
            for u in rubric.content_units
        ]
    )


def show_rubric(rubric: EvaluationRubric) -> None:
    m = rubric.meta
    print(f"[{m.script_name} / 슬라이드 {m.slide_number}]")
    print("문장 역할:", ", ".join(f"S{i} {role}" for i, role in enumerate(rubric.sentence_roles)))
    print("Core Claim:", rubric.core_claim)
    display(key_points_table(rubric))
    display(linked_facts_table(rubric))
    print("전달 단위 (STT 평가가 단위마다 말했는지 판정한다)")
    display(units_table(rubric))
    print("Keywords:", ", ".join(f"{k.term}({k.score:.2f})" for k in rubric.keywords))
    print("1차 검증 경고:", rubric.draft_warnings or "-")
    print("최종 결론이 바꾼 점:", rubric.review_changes or "-")
    print(
        "이름·용어 결정:",
        [f"{d.value}={'유지' if d.keep else '제외'}({d.reason})" for d in rubric.name_decisions]
        or "-",
    )
    print("최종 경고:", rubric.warnings or "-")
