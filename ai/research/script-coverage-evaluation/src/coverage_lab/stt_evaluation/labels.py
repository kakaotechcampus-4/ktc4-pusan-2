"""정답 라벨과 비교 (성능 측정).

정답 라벨은 가상 STT 를 만들 때 대본 문장마다 어떻게 말했는지 기록해 둔 것이다 (`data/virtual/stt_labels/`).
평가 파이프라인은 라벨을 보지 않고, 여기서 채점 결과와 비교할 때만 읽는다.

- 문장 정답: 라벨 그대로 (verbatim · paraphrased → said, partial, missing, contradicted)
- Key Point 정답: 근거 문장 라벨로 계산한 근사값 (`truth_key_point`)
- 사실 정답: 문장 라벨의 값 목록으로 계산 (`truth_fact`)
- 방법 세 가지: 규칙만 (LLM 없이 규칙 결과로만) / LLM 1차 / 최종 (충돌 검사 + 교차 검증 뒤)
"""

import json
import re
import sqlite3

import pandas as pd

from script_coverage.shared.facts import CriticalFact, extract_critical_facts
from script_coverage.shared.rubric import NAME_TYPES, SCORE_WEIGHT
from script_coverage.shared.text import compact, normalize_script
from script_coverage.stt_evaluation.config import STATUS_SCORE
from script_coverage.stt_evaluation.merge import aggregate_status
from script_coverage.stt_evaluation.schemas import (
    SentenceResult,
    SimilarItem,
    SlideEvaluation,
    Take,
)
from script_coverage.stt_evaluation.similar import _plain

from ..paths import LABEL_DIR
from ..store import load_rubric

# 정답 라벨: 가상 STT 를 만들 때 대본 문장마다 어떻게 말했는지 기록해 둔 것 (평가 파이프라인은 보지 않는다)
LABEL_STATUS = {
    "verbatim": "covered",
    "paraphrased": "covered",
    "partial": "partial",
    "missing": "missing",
    "contradicted": "contradicted",
}
LABEL_SENTENCE = {
    "verbatim": "said",
    "paraphrased": "said",
    "partial": "partial",
    "missing": "missing",
    "contradicted": "contradicted",
}
ORDER = ["covered", "partial", "missing", "contradicted"]
SENTENCE_ORDER = ["said", "partial", "missing", "contradicted"]
FACT_TRUTH_ORDER = ["matched", "approximate", "asr", "mismatched", "missing"]
FACT_PRED_ORDER = ["matched", "approximate", "held", "mismatched", "missing"]
# 예측 이름: sound_alike(발음이 비슷한 말 — 판단 보류) → held
FACT_PRED = {
    "matched": "matched",
    "approximate": "approximate",
    "sound_alike": "held",
    "mismatched": "mismatched",
    "missing": "missing",
    "unverified": "missing",
}


def load_labels(take_id: str) -> dict[int, dict[int, dict]]:
    data = json.loads((LABEL_DIR / f"{take_id}.json").read_text(encoding="utf-8"))
    return {s["slide_number"]: {x["index"]: x for x in s["sentences"]} for s in data["slides"]}


def truth_key_point(sentence_indices: list[int], labels: dict[int, dict]) -> str:
    """문장 라벨 → Key Point 정답. 모순이 하나라도 있으면 contradicted, 모두 전달이면 covered, 모두 빠졌으면 missing, 나머지 partial.
    라벨은 발표자가 '실제로 말한 것' 기준이라, 음성 인식이 잘못 적은 문장도 전달(verbatim)이다."""
    statuses = [LABEL_STATUS[labels[i]["status"]] for i in sentence_indices if i in labels]
    if not statuses:
        return "missing"
    if "contradicted" in statuses:
        return "contradicted"
    if all(s == "covered" for s in statuses):
        return "covered"
    if all(s == "missing" for s in statuses):
        return "missing"
    return "partial"


def _same_value(fact: CriticalFact, surface: str) -> bool:
    if fact.type in NAME_TYPES:
        return (
            compact(fact.value)[0] in compact(surface)[0]
            or compact(surface)[0] in compact(fact.value)[0]
        )
    return any(
        (f.type, f.normalized) == (fact.type, fact.normalized)
        for f in extract_critical_facts(normalize_script(surface))
    )


def truth_fact(fact: CriticalFact, labels: dict[int, dict]) -> str:
    """문장 라벨 → 사실 정답: matched / approximate(어림해 말함) / asr(맞게 말했지만 인식 오류) / mismatched(틀리게 말함) / missing."""
    outcomes = []
    for i in set(fact.sentence_indices):
        label = labels.get(i)
        if label is None:
            continue
        if any(_same_value(fact, c["script"]) for c in label.get("asr_errors", [])):
            outcomes.append("asr")
        elif any(_same_value(fact, c["script"]) for c in label.get("approximated_values", [])):
            outcomes.append("approximate")
        elif any(_same_value(fact, c["script"]) for c in label["changed_values"]):
            outcomes.append("mismatched")
        elif label["status"] == "missing" or any(
            _same_value(fact, v) for v in label["dropped_values"]
        ):
            outcomes.append("missing")
        else:
            outcomes.append("matched")
    return next(
        (o for o in ("matched", "asr", "approximate", "mismatched") if o in outcomes), "missing"
    )


def rule_only_sentence(s: SentenceResult) -> str:
    """비교용 기준선: LLM 없이 규칙 결과(문장의 단어 비율 + 사실 검증)만으로 문장 판정. 판단 보류(sound_alike) 수치는 보지 않는다."""
    statuses = set(s.fact_statuses.values())
    if "mismatched" in statuses:
        return "contradicted"
    if s.lexical_coverage >= 0.7 and not statuses & {"missing", "approximate"}:
        return "said"
    return "partial" if s.lexical_coverage >= 0.3 else "missing"


def coverage_from(statuses_and_importance: list[tuple[str, str]]) -> float:
    weight = sum(SCORE_WEIGHT[imp] for _, imp in statuses_and_importance)
    score = sum(STATUS_SCORE[s] * SCORE_WEIGHT[imp] for s, imp in statuses_and_importance)
    return max(score / weight, 0.0) if weight else 0.0


def build_tables(
    conn: sqlite3.Connection,
    takes: list[Take],
    evaluations_by_take: dict[str, list[SlideEvaluation]],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """채점 결과 한 벌 → (Key Point 표, 사실 표, 슬라이드 표, 문장 표). 각 행에 정답과 방법별 예측을 둔다."""
    kp_rows, fact_rows, slide_rows, sentence_rows = [], [], [], []
    for take in takes:
        labels = load_labels(take.take_id)
        for e in evaluations_by_take.get(take.take_id, []):
            rubric = load_rubric(conn, take.script_name, e.slide_number)
            kps = {kp.id: kp for kp in rubric.key_points}
            facts = {f.id: f for f in rubric.critical_facts}
            rule_only = {s.sentence_index: rule_only_sentence(s) for s in e.sentences}
            for s in e.sentences:
                label = labels[e.slide_number].get(s.sentence_index)
                if label is not None:
                    sentence_rows.append(
                        {
                            "take_id": take.take_id,
                            "scenario": take.scenario,
                            "slide": e.slide_number,
                            "sentence": s.sentence_index,
                            "정답": LABEL_SENTENCE[label["status"]],
                            "규칙만": rule_only[s.sentence_index],
                            "LLM 1차": s.first_status,
                            "최종": s.status,
                            "검증": s.verified,
                            "충돌": s.conflicts,
                            "확신도": s.confidence,
                            "단어 비율": s.lexical_coverage,
                            "근거 단어 비율": s.evidence_coverage,
                        }
                    )
            truths = {}
            for r in e.key_points:
                truth = truth_key_point(
                    kps[r.key_point_id].sentence_indices, labels[e.slide_number]
                )
                truths[r.key_point_id] = truth
                kp_rows.append(
                    {
                        "take_id": take.take_id,
                        "scenario": take.scenario,
                        "slide": e.slide_number,
                        "kp": r.key_point_id,
                        "정답": truth,
                        "규칙만": aggregate_status(
                            [rule_only[i] for i in r.sentence_indices if i in rule_only]
                        ),
                        "LLM 1차": r.first_status,
                        "최종": r.status,
                        "검증": r.verified,
                    }
                )
            for c in e.critical_facts:
                fact_rows.append(
                    {
                        "take_id": take.take_id,
                        "scenario": take.scenario,
                        "slide": e.slide_number,
                        "fact_id": c.fact_id,
                        "fact": c.value,
                        "type": c.type,
                        "STT": c.stt_value,
                        "정답": truth_fact(facts[c.fact_id], labels[e.slide_number]),
                        "최종": FACT_PRED[c.status],
                    }
                )
            slide_rows.append(
                {
                    "take_id": take.take_id,
                    "scenario": take.scenario,
                    "slide": e.slide_number,
                    "정답 coverage": coverage_from(
                        [(truths[r.key_point_id], r.importance) for r in e.key_points]
                    ),
                    "LLM 1차 coverage": coverage_from(
                        [(r.first_status, r.importance) for r in e.key_points]
                    ),
                    "최종 coverage": e.scores["content_coverage"],
                }
            )
    return (
        pd.DataFrame(kp_rows),
        pd.DataFrame(fact_rows),
        pd.DataFrame(slide_rows),
        pd.DataFrame(sentence_rows),
    )


SEVERE = {
    ("covered", "missing"),
    ("covered", "contradicted"),
    ("missing", "covered"),
    ("contradicted", "covered"),
    ("said", "missing"),
    ("said", "contradicted"),
    ("missing", "said"),
    ("contradicted", "said"),
}


def kp_accuracy(kp_table: pd.DataFrame, column: str) -> dict:
    exact = (kp_table[column] == kp_table["정답"]).mean()
    severe = kp_table.apply(lambda row: (row[column], row["정답"]) in SEVERE, axis=1).mean()
    return {"정확도": round(float(exact), 3), "심각한 오판 비율": round(float(severe), 3)}


def fact_accuracy(fact_table: pd.DataFrame) -> float:
    """사실 정확도 (판단 보류 held 는 빼고). 인식 오류(asr)인 이름을 5장 이름 확인이 '말함(matched)'으로 받아들인 경우도 맞음 (감점 없음)."""
    rows = fact_table[fact_table["최종"] != "held"]
    ok = (rows["정답"] == rows["최종"]) | ((rows["정답"] == "asr") & (rows["최종"] == "matched"))
    return round(float(ok.mean()), 3)


# ── 비슷한 말 평가 ─────────────────────────────────────
LABEL_CAUSE = {
    "asr_errors": "asr_error",
    "changed_values": "speaker_error",
    "approximated_values": "approximation",
}


def label_pairs(take_id: str) -> dict[int, list[tuple[str, str, str]]]:
    """슬라이드 → [(원인, 대본 표현, STT 표현)]. 라벨의 인식 오류 / 발표자가 바꿔 말함 / 어림."""
    data = json.loads((LABEL_DIR / f"{take_id}.json").read_text(encoding="utf-8"))
    return {
        s["slide_number"]: [
            (LABEL_CAUSE[key], c["script"], c["stt"])
            for x in s["sentences"]
            for key in LABEL_CAUSE
            for c in x.get(key, [])
        ]
        for s in data["slides"]
    }


def pair_match(item: SimilarItem, script: str, stt: str) -> bool:
    """비슷한 말 항목이 라벨의 (대본 표현, STT 표현) 쌍과 같은 자리인지."""
    a, b, c, d = _plain(item.script_text), _plain(script), _plain(item.stt_text), _plain(stt)
    return bool(a and c) and (a in b or b in a) and (c in d or d in c)


def similar_tables(
    takes: list[Take], evaluations_by_take: dict[str, list[SlideEvaluation]]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(항목 표, 라벨 표). 항목 표: 비슷한 말마다 실제 원인(라벨에 없으면 none) / 규칙 추정.
    라벨 표: 라벨의 인식 오류마다 비슷한 말로 보류했는지. 영문 이름은 이름 확인(LLM)이 따로 봐서 뺀다."""
    item_rows, label_rows = [], []
    for take in takes:
        pairs = label_pairs(take.take_id)
        for e in evaluations_by_take.get(take.take_id, []):
            slide_pairs = pairs.get(e.slide_number, [])
            for it in e.similar_items:
                truth = next(
                    (cause for cause, script, stt in slide_pairs if pair_match(it, script, stt)),
                    "none",
                )
                item_rows.append(
                    {
                        "take_id": take.take_id,
                        "slide": e.slide_number,
                        "종류": it.kind,
                        "대본": it.script_text,
                        "STT": it.stt_text,
                        "실제": truth,
                        "규칙 추정": it.rule_guess,
                    }
                )
            for cause, script, stt in slide_pairs:
                if cause != "asr_error" or re.search(r"[A-Za-z]", script):
                    continue
                hit = any(pair_match(it, script, stt) for it in e.similar_items)
                label_rows.append(
                    {
                        "take_id": take.take_id,
                        "slide": e.slide_number,
                        "대본": script,
                        "STT": stt,
                        "보류함": hit,
                    }
                )
    return pd.DataFrame(item_rows), pd.DataFrame(label_rows)


def similar_accuracy(
    takes: list[Take], evaluations_by_take: dict[str, list[SlideEvaluation]]
) -> dict:
    items, labels = similar_tables(takes, evaluations_by_take)
    return {
        "인식 오류 보류율": round(float(labels["보류함"].mean()), 3)
        if len(labels)
        else float("nan"),
        "보류 중 실제 인식 오류": round(float((items["실제"] == "asr_error").mean()), 3)
        if len(items)
        else float("nan"),
    }
