"""STT 평가 결과를 표로 보여 주는 도구."""

import json
import re
import sqlite3
from collections import Counter

import pandas as pd
from IPython.display import display

from script_coverage.shared.text import normalize_script
from script_coverage.stt_evaluation.align import align_sentences, positioned_tokens
from script_coverage.stt_evaluation.merge import CONFLICT_TEXT
from script_coverage.stt_evaluation.normalize import normalize_stt
from script_coverage.stt_evaluation.schemas import SlideEvaluation, Take
from script_coverage.stt_evaluation.scoring import take_scores

from .store import load_rubric, load_similar_items

# 실제 STT 는 문장부호가 거의 없어 Kiwi 가 문장을 나눈다. 여러 문장이 한 문장으로 붙으면 문장 정렬과 T번호 근거가 거칠어진다
LONG_SENTENCE = 120  # 이보다 긴 STT 문장(글자 수)은 여러 문장이 붙었을 가능성이 크다
# 뜻이 있을 수도 있어서 지우지 않는 말. 실제 데이터에서 간투사로 많이 쓰이면 `script_coverage/stt_evaluation/fillers.py` 의 FILLER 에 넣을지 검토한다
FILLER_CANDIDATES = ["그", "저", "뭐", "막", "좀", "이제", "그러니까", "약간"]


def segmentation_report(conn: sqlite3.Connection, takes: list[Take]) -> pd.DataFrame:
    """연습마다 STT 문장 분리와 정렬 상태를 요약한다 (규칙만, API 호출 없음)."""
    filler_pattern = re.compile(r"(?<![가-힣])(" + "|".join(FILLER_CANDIDATES) + r")(?![가-힣])")
    rows = []
    for take in takes:
        lengths, windows, fillers = [], Counter(), Counter()
        for s in take.slides:
            rubric = load_rubric(conn, take.script_name, s.slide_number)
            script_norm, stt_norm = normalize_script(rubric.normalized_script), normalize_stt(s.stt)
            lengths += [len(x.text) for x in stt_norm.sentences]
            s_tokens, t_tokens = positioned_tokens(script_norm), positioned_tokens(stt_norm)
            for a in align_sentences(
                script_norm,
                [(t.key, t.sentence) for t in s_tokens],
                stt_norm,
                [(t.key, t.sentence) for t in t_tokens],
                rubric.sentence_roles,
            ):
                windows[len(a.stt_ids)] += 1
            fillers.update(filler_pattern.findall(stt_norm.text))
        aligned = sum(windows.values())
        rows.append(
            {
                "take_id": take.take_id,
                "STT 문장 수": len(lengths),
                "평균 길이": round(sum(lengths) / max(len(lengths), 1), 1),
                "최대 길이": max(lengths, default=0),
                f"{LONG_SENTENCE}자 넘는 문장": sum(n > LONG_SENTENCE for n in lengths),
                "정렬 구간 1 / 2 / 3문장": f"{windows[1]} / {windows[2]} / {windows[3]}",
                "3문장 구간 비율": round(windows[3] / max(aligned, 1), 2),
                "정렬 안 된 대본 문장": windows[0],
                "남은 간투사 후보": ", ".join(f"{w} {n}" for w, n in fillers.most_common(3)) or "-",
            }
        )
    return pd.DataFrame(rows)


def take_summary(
    takes: list[Take], all_evaluations: dict[str, list[SlideEvaluation]]
) -> pd.DataFrame:
    """연습마다 점수와 Key Point 판정 분포."""
    rows = []
    for take in takes:
        evaluations = all_evaluations[take.take_id]
        statuses = Counter(r.status for e in evaluations for r in e.key_points)
        rows.append(
            {
                "take_id": take.take_id,
                "scenario": take.scenario,
                **take_scores(evaluations),
                "covered/partial/missing/contradicted": "/".join(
                    str(statuses[s]) for s in ("covered", "partial", "missing", "contradicted")
                ),
                "검증한 슬라이드": sum(e.verification["required"] for e in evaluations),
                "틀리게 말한 수치": sum(
                    c.status == "mismatched" for e in evaluations for c in e.critical_facts
                ),
            }
        )
    return pd.DataFrame(rows)


def conflict_log(evaluations_by_take: dict[str, list[SlideEvaluation]]) -> pd.DataFrame:
    """충돌 조건별 발동 기록 (라벨 없이). 발동 = 조건이 뜬 문장 수, 단독 = 그 조건 하나만 뜬 문장 수, 판정 바뀜 = 교차 검증 뒤 1차와 달라진 문장 수."""
    sentences = [
        s for evaluations in evaluations_by_take.values() for e in evaluations for s in e.sentences
    ]
    return pd.DataFrame(
        [
            {
                "충돌": c,
                "발동": sum(c in s.conflicts for s in sentences),
                "단독": sum(s.conflicts == [c] for s in sentences),
                "판정 바뀜": sum(
                    c in s.conflicts and s.status != s.first_status for s in sentences
                ),
            }
            for c in CONFLICT_TEXT
        ]
    ).set_index("충돌")


def similar_report(conn: sqlite3.Connection, takes: list[Take], take_id: str) -> pd.DataFrame:
    """비슷한 말 목록 (DB `similar_items` 에서 읽음). 리뷰 agent 는 이 위치로 대본·STT·녹음 구간을 찾아 사용자에게 확인한다."""
    df = load_similar_items(conn, take_id)
    raw = {s.slide_number: s.stt for s in next(t for t in takes if t.take_id == take_id).slides}
    return pd.DataFrame(
        [
            {
                "slide": r.slide_number,
                "종류": "수치" if r.kind == "number" else "단어",
                "대본": r.script_text,
                "STT": r.stt_text,
                "대본 위치": f"S{r.script_sentence_index} [{r.script_start}:{r.script_end}]",
                "STT 위치": f"T{r.stt_sentence_index} [{r.stt_start}:{r.stt_end}]",
                "원본 STT 위치": (
                    f"[{int(r.stt_raw_start)}:{int(r.stt_raw_end)}] '{raw[r.slide_number][int(r.stt_raw_start) : int(r.stt_raw_end)]}'"
                    if pd.notna(r.stt_raw_start)
                    else "-"
                ),
                "Key Point": ", ".join(json.loads(r.key_point_ids)),
                "규칙 추정": r.rule_guess,
                "규칙 신호": " / ".join(json.loads(r.signals)[-1:]),
            }
            for r in df.itertuples()
        ]
    )


def similar_counts(conn: sqlite3.Connection) -> pd.DataFrame:
    """연습마다 비슷한 말 개수 (비율에서 뺀 판단 보류 항목)."""
    return pd.read_sql_query(
        "SELECT take_id, SUM(kind = 'word') AS 비슷한_단어, SUM(kind = 'number') AS 비슷한_수치 FROM similar_items GROUP BY take_id",
        conn,
    )


def show_evaluation(
    all_evaluations: dict[str, list[SlideEvaluation]], take_id: str, slide_number: int
) -> None:
    e = next(x for x in all_evaluations[take_id] if x.slide_number == slide_number)
    print(
        f"[{take_id} / 슬라이드 {slide_number}] 점수:",
        {k: v for k, v in e.scores.items() if not k.startswith("_")},
    )
    for i, text in enumerate(e.stt_sentences):
        print(f"  [T{i}] {text}")
    print("대본 문장별 판정")
    display(
        pd.DataFrame(
            [
                {
                    "문장": f"S{s.sentence_index}",
                    "역할": s.role,
                    "1차": s.first_status,
                    "최종": s.status,
                    "검증": s.verified,
                    "근거": s.evidence,
                    "단어 비율": s.lexical_coverage,
                    "충돌": ", ".join(s.conflicts),
                    "이유": s.reason,
                }
                for s in e.sentences
            ]
        )
    )
    unit_rows = [
        {
            "문장": f"S{s.sentence_index}",
            "단위": u.unit_id,
            "내용": u.text,
            "1차": u.first_status,
            "최종": u.status,
        }
        for s in e.sentences
        if s.status != "said" or s.first_status != "said"
        for u in s.units
    ]
    if unit_rows:
        print("전달 단위 판정 (said 가 아닌 문장 — 문장 판정은 이 판정을 모은 것)")
        display(pd.DataFrame(unit_rows))
    print("Key Point 판정 (근거 문장 판정을 모은 것)")
    display(
        pd.DataFrame(
            [
                {
                    "id": r.key_point_id,
                    "importance": r.importance,
                    "근거 문장": [f"S{i}" for i in r.sentence_indices],
                    "1차": r.first_status,
                    "최종": r.status,
                    "검증": r.verified,
                    "내용": r.content,
                }
                for r in e.key_points
            ]
        )
    )
    display(
        pd.DataFrame(
            [
                {
                    "id": c.fact_id,
                    "type": c.type,
                    "대본": c.value,
                    "결과": c.status,
                    "STT": c.stt_value,
                    "원인": c.cause or "",
                    "note": c.cause_reason or c.note,
                }
                for c in e.critical_facts
            ]
        )
    )
    if e.similar_items:
        print("비슷한 말 (비율에서 뺌 — 리뷰 agent 가 사용자에게 확인)")
        display(
            pd.DataFrame(
                [
                    {
                        "id": it.item_id,
                        "종류": it.kind,
                        "대본": it.script_text,
                        "STT": it.stt_text,
                        "대본 위치": f"S{it.script_sentence_index} {list(it.script_span)}",
                        "STT 위치": f"T{it.stt_sentence_index} {list(it.stt_span)}",
                        "Key Point": ", ".join(it.key_point_ids),
                        "규칙 추정": it.rule_guess,
                    }
                    for it in e.similar_items
                ]
            )
        )
