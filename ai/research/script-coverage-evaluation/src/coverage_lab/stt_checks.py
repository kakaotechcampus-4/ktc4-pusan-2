"""충돌 조건 점검과 사용자 확인 뒤 정확도 (정답 라벨 기준).

- 충돌 조건 점검: 충돌 조건이 LLM 1차 판정의 실수를 제대로 가리키는지 본다
- 사용자 확인 뒤 정확도: **정답 라벨로 사용자 답을 대신**해 비슷한 말을 확인한 뒤 다시 계산하고 정확도를 비교한다 (LLM 호출 없음)
"""

import sqlite3
from collections import Counter
from itertools import combinations

import pandas as pd

from script_coverage.stt_evaluation.merge import CONFLICT_TEXT
from script_coverage.stt_evaluation.schemas import SlideEvaluation, Take

from .store import confirm_similar_item, confirmed_evaluation
from .stt_labels import SENTENCE_ORDER, fact_accuracy, kp_accuracy, label_pairs, pair_match


def conflict_check(sentence_table: pd.DataFrame) -> pd.DataFrame:
    """충돌 조건별 발동 · 단독 · 1차 오답 · 단독 1차 오답 · 바로잡음 · 망침 (정답 라벨 기준)."""
    wrong = sentence_table["LLM 1차"] != sentence_table["정답"]
    right_after = sentence_table["최종"] == sentence_table["정답"]
    sole = sentence_table["충돌"].apply(len) == 1
    rows = []
    for c in CONFLICT_TEXT:
        fired = sentence_table["충돌"].apply(lambda conflicts, c=c: c in conflicts)
        rows.append(
            {
                "충돌": c,
                "발동": int(fired.sum()),
                "단독": int((fired & sole).sum()),
                "1차 오답": int((fired & wrong).sum()),
                "단독 1차 오답": int((fired & sole & wrong).sum()),
                "바로잡음": int((fired & wrong & right_after).sum()),
                "망침": int((fired & ~wrong & ~right_after).sum()),
            }
        )
    return pd.DataFrame(rows).set_index("충돌")


def first_status_check(sentence_table: pd.DataFrame) -> pd.DataFrame:
    """1차 판정별 문장 수 · 1차 오답 · 충돌로 다시 확인한 문장 · 그중 1차 오답. 오답이 있는데 다시 확인하지 않는 판정이 검사의 빈틈이다."""
    rows = []
    for status in SENTENCE_ORDER:
        t = sentence_table[sentence_table["LLM 1차"] == status]
        wrong, checked = t["LLM 1차"] != t["정답"], t["충돌"].apply(bool).astype(bool)
        rows.append(
            {
                "LLM 1차": status,
                "문장": len(t),
                "1차 오답": int(wrong.sum()),
                "다시 확인": int(checked.sum()),
                "다시 확인한 오답": int((wrong & checked).sum()),
            }
        )
    return pd.DataFrame(rows).set_index("LLM 1차")


def confidence_check(sentence_table: pd.DataFrame) -> pd.DataFrame:
    """LLM 확신도별 문장 수 · 1차 오답. contradicted 는 확신도와 상관없이 다시 확인하므로 뺀 값도 함께 본다."""
    rows = []
    for confidence, t in sentence_table.groupby("확신도"):
        wrong, other = t["LLM 1차"] != t["정답"], t["LLM 1차"] != "contradicted"
        rows.append(
            {
                "확신도": confidence,
                "문장": len(t),
                "1차 오답": int(wrong.sum()),
                "contradicted 제외 문장": int(other.sum()),
                "contradicted 제외 1차 오답": int((wrong & other).sum()),
            }
        )
    return pd.DataFrame(rows).set_index("확신도")


def conflict_pairs(sentence_table: pd.DataFrame) -> pd.DataFrame:
    """한 문장에서 함께 뜬 충돌 조건 쌍과 그 문장 수."""
    pairs = Counter(
        p for conflicts in sentence_table["충돌"] for p in combinations(sorted(conflicts), 2)
    )
    return pd.DataFrame(
        [{"조건 1": a, "조건 2": b, "함께 뜬 문장": n} for (a, b), n in pairs.most_common()]
    )


def unchecked_errors(sentence_table: pd.DataFrame) -> pd.DataFrame:
    """충돌 검사가 다시 확인하지 않은 1차 오답."""
    return sentence_table[
        (sentence_table["LLM 1차"] != sentence_table["정답"])
        & ~sentence_table["충돌"].apply(bool).astype(bool)
    ][["take_id", "slide", "sentence", "정답", "LLM 1차", "확신도", "단어 비율", "근거 단어 비율"]]


LABEL_ANSWER = {
    "asr_error": "as_script",
    "speaker_error": "as_stt",
}  # 정답 라벨 → 사용자 답 (라벨에 없는 항목은 답하지 않음)


def confirm_with_labels(
    conn: sqlite3.Connection,
    takes: list[Take],
    all_evaluations: dict[str, list[SlideEvaluation]],
) -> tuple[Counter, dict[str, list[SlideEvaluation]]]:
    """정답 라벨로 사용자 답을 대신해 비슷한 말을 확인하고(DB 에 저장), 확인을 반영한 평가를 연습별로 돌려준다.

    라벨이 인식 오류로 적은 항목은 as_script, 발표자가 바꿔 말한 항목은 as_stt 로 답한다.
    """
    answered = Counter()
    confirmed_by_take = {}
    for take in takes:
        pairs = label_pairs(take.take_id)
        for e in all_evaluations[take.take_id]:
            for it in e.similar_items:
                cause = next(
                    (
                        c
                        for c, script, stt in pairs.get(e.slide_number, [])
                        if pair_match(it, script, stt)
                    ),
                    None,
                )
                if cause in LABEL_ANSWER:
                    confirm_similar_item(
                        conn, take.take_id, e.slide_number, it.item_id, LABEL_ANSWER[cause]
                    )
                    answered[LABEL_ANSWER[cause]] += 1
        confirmed_by_take[take.take_id] = [
            confirmed_evaluation(conn, take.take_id, e.slide_number)
            for e in all_evaluations[take.take_id]
        ]
    return answered, confirmed_by_take


def accuracy_row(
    sent: pd.DataFrame, kp: pd.DataFrame, fact: pd.DataFrame, slide: pd.DataFrame
) -> dict:
    return {
        "대본 문장 정확도": kp_accuracy(sent, "최종")["정확도"],
        "Key Point 정확도": kp_accuracy(kp, "최종")["정확도"],
        "Key Point 심각한 오판 비율": kp_accuracy(kp, "최종")["심각한 오판 비율"],
        "사실 정확도 (보류 제외)": fact_accuracy(fact),
        "보류한 사실": int((fact["최종"] == "held").sum()),
        "슬라이드 내용 점수 평균 오차": round(
            float((slide["최종 coverage"] - slide["정답 coverage"]).abs().mean()), 3
        ),
    }
