"""반복 채점 일관성: 같은 STT 를 여러 번 채점해 LLM 판정이 채점할 때마다 얼마나 달라지는지 본다.

0번은 처음 채점(DB 에 저장된 것)이고, 1번부터는 LLM 의미 평가와 교차 검증을 다시 부른다.
응답은 `stt.semantic#k` · `stt.verifier#k` 로 캐시해 다시 실행하면 호출하지 않는다.
"""

import sqlite3
from collections import Counter, defaultdict

import pandas as pd

from script_coverage.stt_evaluation.schemas import SlideEvaluation, Take
from script_coverage.stt_evaluation.scoring import take_scores

from .llm import Settings
from .runs import run_take_evaluation
from .store import load_rubrics
from .stt_labels import build_tables, fact_accuracy, kp_accuracy, similar_accuracy


def spread(values: list[float]) -> float:
    return max(values) - min(values) if values else 0.0


def check_baseline(
    conn: sqlite3.Connection,
    takes: list[Take],
    all_evaluations: dict[str, list[SlideEvaluation]],
) -> None:
    """반복 채점 전에 0번(처음 채점)이 온전한지 본다. 문제가 있으면 LLM 을 부르기 전에 멈춘다.

    모든 연습의 모든 슬라이드에 저장된 평가가 있어야 하고, 그 평가가 쓴 평가 기준(`rubric_id`)이 지금 DB 의 기준과 같아야 한다.
    아니면 반복 채점은 다른 입력을 채점한 결과를 0번과 비교하게 된다.
    """
    if not takes:
        raise RuntimeError("채점할 연습이 없다 — data/virtual/stt 를 확인한다")
    problems = []
    for take in takes:
        rubrics = load_rubrics(conn, take.script_name)
        saved = {e.slide_number: e for e in all_evaluations.get(take.take_id, [])}
        for s in take.slides:
            evaluation, rubric = saved.get(s.slide_number), rubrics.get(s.slide_number)
            if evaluation is None:
                problems.append(f"{take.take_id} 슬라이드 {s.slide_number}: 처음 채점 결과가 없다")
            elif rubric is None or rubric.meta.rubric_id != evaluation.rubric_id:
                problems.append(
                    f"{take.take_id} 슬라이드 {s.slide_number}: 처음 채점 뒤 평가 기준이 바뀌었거나 없다"
                )
    if problems:
        shown = "; ".join(problems[:5]) + (
            f" … 외 {len(problems) - 5}건" if len(problems) > 5 else ""
        )
        raise RuntimeError(
            f"반복 채점의 기준(0번)이 온전하지 않다 — 04_evaluate_takes.py 를 먼저 (다시) 실행한다. {shown}"
        )


def repeat_runs(
    conn: sqlite3.Connection,
    takes: list[Take],
    settings: Settings,
    all_evaluations: dict[str, list[SlideEvaluation]],
    n_samples: int,
    *,
    llms: dict | None = None,
) -> tuple[dict[int, dict[str, list[SlideEvaluation]]], Counter]:
    """같은 STT 를 n_samples 번 채점한다. (채점 번호 → 연습별 평가, 이번 실행에서 실제로 부른 API 호출 수)

    LLM 을 부르기 전에 `check_baseline` 으로 0번이 온전한지 먼저 본다.
    """
    check_baseline(conn, takes, all_evaluations)
    runs = {0: all_evaluations}
    extra_calls = Counter()
    for k in range(1, n_samples):
        runs[k] = {}
        for take in takes:
            evaluations, stats = run_take_evaluation(
                conn=conn, take=take, settings=settings, sample=k, llms=llms
            )
            if stats["failed"]:
                raise RuntimeError(
                    f"반복 채점 {k} / {take.take_id} 실패: {next(iter(stats['failed'].values()))}"
                )
            runs[k][take.take_id] = evaluations
            extra_calls["semantic"] += stats["semantic_calls"]
            extra_calls["verifier"] += stats["verifier_calls"]
    return runs, extra_calls


def stability_summary(
    runs: dict[int, dict[str, list[SlideEvaluation]]], n_samples: int
) -> tuple[pd.DataFrame, dict]:
    """채점 사이 일관성 표(한 열)와, 채점마다 달라진 Key Point 판정 {(take_id, slide, kp): [판정 …]}."""
    # 같은 Key Point · 수치 · 슬라이드의 결과를 채점별로 모은다
    kp_status, first_status, sentence_status, slide_cov, take_cov, take_fact = (
        defaultdict(list) for _ in range(6)
    )
    for _, by_take in runs.items():
        for take_id, evaluations in by_take.items():
            for e in evaluations:
                for s in e.sentences:
                    sentence_status[take_id, e.slide_number, s.sentence_index].append(s.status)
                for r in e.key_points:
                    kp_status[take_id, e.slide_number, r.key_point_id].append(r.status)
                    first_status[take_id, e.slide_number, r.key_point_id].append(r.first_status)
                slide_cov[take_id, e.slide_number].append(100 * e.scores["content_coverage"])
            scores = take_scores(evaluations)
            take_cov[take_id].append(100 * scores["content_coverage"])
            take_fact[take_id].append(100 * (scores["critical_fact_accuracy"] or 0))

    def agree(groups: dict) -> float:
        full = [v for v in groups.values() if len(v) == n_samples]
        return round(sum(len(set(v)) == 1 for v in full) / len(full), 3) if full else float("nan")

    slide_spreads = sorted(spread(v) for v in slide_cov.values())
    table = pd.DataFrame(
        [
            {
                "대본 문장 판정 일치율 (최종)": agree(sentence_status),
                "Key Point 판정 일치율 (LLM 1차)": agree(first_status),
                "Key Point 판정 일치율 (최종)": agree(kp_status),
                "슬라이드 내용 점수 차이 평균": round(sum(slide_spreads) / len(slide_spreads), 2),
                "슬라이드 내용 점수 차이 최대": round(slide_spreads[-1], 2),
                "발표 전체 내용 점수 차이 최대": round(
                    max(spread(v) for v in take_cov.values()), 2
                ),
                "발표 전체 수치 점수 차이 최대": round(
                    max(spread(v) for v in take_fact.values()), 2
                ),
            }
        ]
    ).T.rename(columns={0: "값"})
    unstable = {key: v for key, v in kp_status.items() if len(set(v)) > 1}
    return table, unstable


def accuracy_by_run(
    conn: sqlite3.Connection,
    takes: list[Take],
    runs: dict[int, dict[str, list[SlideEvaluation]]],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """채점마다 정답 라벨과 비교한 정확도 표, 그리고 모든 채점의 문장 표를 합친 표 (충돌 조건 점검 합산용)."""
    rows, sentence_tables = [], []
    for k, by_take in runs.items():
        kp_k, fact_k, _, sent_k = build_tables(conn, takes, by_take)
        sentence_tables.append(sent_k)
        rows.append(
            {
                "채점": k,
                "문장 최종": kp_accuracy(sent_k, "최종")["정확도"],
                **{f"KP {col}": kp_accuracy(kp_k, col)["정확도"] for col in ("LLM 1차", "최종")},
                "KP 심각한 오판": kp_accuracy(kp_k, "최종")["심각한 오판 비율"],
                "사실 정확도 (보류 제외)": fact_accuracy(fact_k),
                **similar_accuracy(takes, by_take),
            }
        )
    return pd.DataFrame(rows).set_index("채점"), pd.concat(sentence_tables, ignore_index=True)


def unstable_table(unstable: dict) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"take_id": t, "slide": s, "kp": kp, "판정": " / ".join(v)}
            for (t, s, kp), v in unstable.items()
        ]
    )
