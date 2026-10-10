"""LLM 분기 결과의 품질 지표와, 저장된 평가 기준에서 사실을 꺼내 보는 도구."""

import sqlite3
from collections import Counter

import pandas as pd

from script_coverage.shared.facts import CriticalFact
from script_coverage.shared.rubric import EvaluationRubric

from .store import load_rubric


def facts_of(
    conn: sqlite3.Connection, script_name: str, slide_number: int
) -> dict[str, CriticalFact]:
    """저장된 평가 기준의 Critical Fact 를 정규형(normalized)으로 찾는다."""
    rubric = load_rubric(conn, script_name, slide_number)
    return {f.normalized: f for f in rubric.critical_facts}


def fact_values(rubric: EvaluationRubric) -> set[str]:
    return {f"{f.type}:{f.normalized}" for f in rubric.critical_facts}


def llm_quality_report(rubrics: list[EvaluationRubric]) -> pd.DataFrame:
    """LLM 분기 출력이 채점 기준으로 쓸 만한지 보는 지표. LLM 응답이 바뀌면 값도 바뀐다."""
    kps = [kp for r in rubrics for kp in r.key_points]
    facts = [f for r in rubrics for f in r.critical_facts]
    numeric = [f for f in facts if f.type not in ("proper_noun", "term")]
    importance = Counter(kp.importance for kp in kps)
    rows = {
        "Key Point 수": len(kps),
        "중요도 분포 (critical/high/normal)": f"{importance['critical']}/{importance['high']}/{importance['normal']}",
        "claim 문장이 2개 이상인 슬라이드": sum(
            w.startswith("too_many_claims") for r in rubrics for w in r.warnings
        ),
        "Key Point 에 안 들어간 문장 (skip 제외)": sum(
            len(w.split(":", 1)[1].split(","))
            for r in rubrics
            for w in r.warnings
            if w.startswith("uncovered_sentences")
        ),
        "잘못된 문장 번호 / 역할이 빠진 문장": sum(
            w.startswith(("invalid_sentence_id", "missing_sentence_roles"))
            for r in rubrics
            for w in r.warnings
        ),
        "critical 없는 슬라이드": sum(
            all(kp.importance != "critical" for kp in r.key_points) for r in rubrics
        ),
        "Key Point 에 연결된 수치 사실": f"{sum(bool(f.key_point_ids) for f in numeric)}/{len(numeric)}",
        "LLM 이 보탠 용어(term) 사실": sum(f.source == "llm" for f in facts),
        "대본에 없는 key_term": sum(
            w.startswith("key_term_not_found") for r in rubrics for w in r.warnings
        ),
        "이름·용어 후보 유지/제외 (최종 결론)": f"{sum(d.keep for r in rubrics for d in r.name_decisions)}/{sum(not d.keep for r in rubrics for d in r.name_decisions)}",
        "1차 검증 경고 수 → 최종 경고 수": f"{sum(len(r.draft_warnings) for r in rubrics)} → {sum(len(r.warnings) for r in rubrics)}",
        "최종 결론이 1차에서 바꾼 항목": sum(len(r.review_changes) for r in rubrics),
        "critical 이 2개 이상인 슬라이드": sum(
            w.startswith("too_many_critical") for r in rubrics for w in r.warnings
        ),
        "대본에 없는 수치를 말한 Key Point": sum(
            w.startswith("unsupported_number") for r in rubrics for w in r.warnings
        ),
        "전달 단위 수 (나눈 문장당 평균)": "{} ({:.2f})".format(
            sum(len(r.content_units) for r in rubrics),
            sum(len(r.content_units) for r in rubrics)
            / max(sum(len({u.sentence_index for u in r.content_units}) for r in rubrics), 1),
        ),
        "전달 단위를 나누지 못한 문장": sum(
            w.startswith("units_not_split") for r in rubrics for w in r.warnings
        ),
        "대본에 없어 버린 전달 단위": sum(
            w.startswith("unit_quote_not_found") for r in rubrics for w in r.warnings
        ),
        "표현을 고쳐 옮겨 형태소로 찾은 전달 단위": sum(
            w.startswith("unit_quote_loose") for r in rubrics for w in r.warnings
        ),
        "단위에 안 들어가 코드가 보탠 수치·이름": sum(
            w.startswith("unit_missing_fact") for r in rubrics for w in r.warnings
        ),
    }
    return pd.DataFrame(rows.items(), columns=["지표", "값"])
