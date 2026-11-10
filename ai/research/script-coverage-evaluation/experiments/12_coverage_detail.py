"""결과 상세 기준값 정하기: 표현 방식 경계 · 간이 전달률 3단계 경계 · 말의 구성(대본 밖 말) 규칙의 정확도.

가상대본1 = 개발(경계값을 고른다), 가상대본2 = 시험(고른 값을 고정한 채 한 번만 잰다).

- A. 표현 방식 경계 (`EXPRESSION_VERBATIM_MIN` · `EXPRESSION_PARAPHRASE_MAX`): 최종 판정이 said 이고 라벨이 verbatim ·
  paraphrased 인 문장의 `lexical_coverage` 로 두 경계를 0.05 간격 격자에서 고른다 (띠 안 비율 15% 이하에서 띠 밖 일치율 최대)
- B. 간이 전달률 3단계 경계 (`FALLBACK_SENTENCE_LO` · `HI` · `FALLBACK_BAND_LITTLE` · `MOSTLY`): 장마다 라벨 점수 평균(정답)과
  문장 `lexical_coverage` 를 1 · 0.5 · 0 으로 바꿔 평균한 값(간이)을 같은 단계 경계로 나눠 비교한다 (심각한 어긋남 0 먼저, 그다음 단계 일치율)
- C. 말의 구성, 자연 데이터: 저장된 채점으로 지금 규칙(`OFFSCRIPT_IN_SENTENCE` 등 기본 config)의 `build_offscript` 후보를
  `outputs/offscript_candidates.jsonl` 로 내보내고, 정답 라벨(`data/virtual/offscript_labels.json`)과
  (take_id, 장, STT 문장, 구간 글)로 맞춰 예측 범주별 precision 을 개발 · 시험으로 낸다(정답이 script 가 아닌 것 = 대본 밖 말;
  off_script 예측은 aside · unrelated 가 맞음). 라벨이 null 이거나 라벨 파일에 없는 후보는 따로 센다.
  라벨의 `additions`(실제로 끼어든 곁말) 재현율과 범주별 후보 수 · 형태소 몫도 잰다
- C2. 규칙 비교: 모듈 상수(`OFFSCRIPT_IN_SENTENCE` 꺼짐 · 켜짐, `OFFSCRIPT_MIN_TOKENS` 2 · 4 · 6)를 실험 안에서만 바꿔
  같은 precision 을 내고 결과의 `rule_variants` 에 남긴다 (지금 규칙이 꼬리를 끄는 근거)
- D. 말의 구성, 합성 주입: `data/virtual/offscript_injections.json` 의 문장을 STT 문장 경계에 하나씩 끼워 서비스와 같은 순서로
  처리하고 지금 규칙으로 잰다. 끼우기 전 STT 에 같은 문장 글이 이미 있으면 그 주입은 건너뛰고 수만 남긴다
  (`already_in_stt`). 주입 전 후보와 주입 후 후보를 (STT 문장 글 · 구간 글)로 대응시켜, 주입 구간과 겹치지 않으면서
  주입 전에 없던 후보를 '새 오탐'으로 각각 센다(사라진 후보는 따로, 둘을 상쇄하지 않는다). 새 오탐은 정답 범주가
  '대본 내용'이므로 범주별 precision 의 분모에 넣는다. 문장 검출(재현율 · 범주 맞음 · precision)과 별개로 주입 구간(정답)과
  예측 구간의 형태소 겹침으로 범주별 · 전체 구간 precision · recall · F1 을 개발 · 시험으로 낸다(예측 구간 = 주입과 겹친 후보 +
  새 후보; 주입 전부터 있던 후보는 자연 데이터 쪽 지표이므로 뺀다)

- LLM 호출: 없음. 채점 결과는 04_evaluate_takes.py 가 DB 에 남긴 것을 읽는다
- 쓰는 곳: outputs/rubrics.sqlite (읽기 전용으로만 연다), outputs/offscript_candidates.jsonl (C 의 후보), data/virtual/offscript_labels.json (읽기),
  reports/results/coverage_detail.json (결과)
- 먼저 01_build_rubrics.py 로 평가 기준을, 04_evaluate_takes.py 로 채점을 DB 에 만들어 둔다
  (로컬 DB 에 없는 장은 결과의 `missing_slides` 에 적고 건너뛴다)
"""

# %% 준비
import itertools
import json
import random
import re
import sqlite3
from collections import Counter

import pandas as pd

from coverage_lab.datasets import load_take, take_files
from coverage_lab.paths import DATA_DIR, DB_PATH, OUTPUTS_DIR
from coverage_lab.results import write_result
from coverage_lab.store import load_rubric, load_take_evaluations
from coverage_lab.stt_labels import load_labels
from pitch_coach_ai.features.script_coverage.shared.text import normalize_script
from pitch_coach_ai.features.script_coverage.stt_evaluation import config as cfg
from pitch_coach_ai.features.script_coverage.stt_evaluation import speech_mix
from pitch_coach_ai.features.script_coverage.stt_evaluation.align import (
    align_sentences,
    positioned_tokens,
)
from pitch_coach_ai.features.script_coverage.stt_evaluation.normalize import normalize_stt
from pitch_coach_ai.features.script_coverage.stt_evaluation.speech_mix import (
    build_offscript,
    build_speech_mix,
)

pd.set_option("display.width", 250)
pd.set_option("display.max_columns", None)

DEV, TEST = "가상대본1", "가상대본2"
MAX_BAND_RATIO = 0.15  # A: 띠 안(구분 어려움) 비율 상한 (결과를 보기 전에 정함)
INJECTION_ROUNDS = 4  # D: 장마다 범주 4개를 모두 한 번씩 끼운다 (한 번에 문장 하나)
CATEGORIES = ("procedural", "related_aside", "unrelated", "other_slide")
OTHER_SLIDE_MIN_SOURCE_TOKENS = 5  # D: 다른 장 대본에서 가져올 문장의 최소 형태소 수

# 연구 DB 는 다른 실험이 쓰는 중이라 읽기 전용으로만 연다
conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
takes = [load_take(p) for p in take_files()]
side_of = {t.take_id: "dev" if t.script_name == DEV else "test" for t in takes}
labels = {t.take_id: load_labels(t.take_id) for t in takes}
evaluations = {t.take_id: load_take_evaluations(conn, t) for t in takes}
rubrics = {
    (t.script_name, n): load_rubric(conn, t.script_name, n)
    for t in takes
    for n in (s.slide_number for s in t.slides)
}
missing_slides = sorted(
    {
        f"{t.script_name} 슬라이드 {s.slide_number}"
        for t in takes
        for s in t.slides
        if rubrics[(t.script_name, s.slide_number)] is None
    }
)
not_scored = sorted(
    {
        f"{t.take_id} 슬라이드 {s.slide_number}"
        for t in takes
        for s in t.slides
        if rubrics[(t.script_name, s.slide_number)] is not None
        and s.slide_number not in {e.slide_number for e in evaluations[t.take_id]}
    }
)
print(
    "평가 기준이 없어 건너뛴 장:",
    missing_slides,
    "/ 기준은 있으나 채점이 없는 (연습, 장):",
    not_scored,
)

# 문장 표: 채점 대상(skip 아님) 중 라벨이 있는 문장
rows = []
for t in takes:
    for e in evaluations[t.take_id]:
        for s in e.sentences:
            label = labels[t.take_id][e.slide_number].get(s.sentence_index)
            if label is None or s.role == "skip":
                continue
            rows.append(
                {
                    "side": side_of[t.take_id],
                    "take_id": t.take_id,
                    "slide": e.slide_number,
                    "label": label["status"],
                    "final": s.status,
                    "lexical": s.lexical_coverage,
                }
            )
sentences = pd.DataFrame(rows)
print(sentences.groupby(["side", "label", "final"]).size())


def grid(lo: float, hi: float, step: float) -> list[float]:
    n = round((hi - lo) / step)
    return [round(lo + i * step, 2) for i in range(n + 1)]


# %% A. 표현 방식 경계
expr = sentences[
    (sentences["final"] == "said") & sentences["label"].isin(["verbatim", "paraphrased"])
]
EXPR_LABELS = ("verbatim", "paraphrased")


def expression_eval(frame: pd.DataFrame, verb_min: float, para_max: float) -> dict:
    """경계 한 쌍의 결과: 띠 밖 일치율, 띠 안 비율, 혼동표 (행 = 라벨, 열 = 예측)."""
    pred = frame["lexical"].map(
        lambda v: (
            "verbatim_like" if v >= verb_min else "paraphrased" if v < para_max else "undetermined"
        )
    )
    outside = pred != "undetermined"
    match = pred == frame["label"].map({"verbatim": "verbatim_like", "paraphrased": "paraphrased"})
    per_class = [
        float(match[outside & (frame["label"] == lb)].mean())
        if (outside & (frame["label"] == lb)).any()
        else float("nan")
        for lb in EXPR_LABELS
    ]
    confusion = (
        pd.crosstab(frame["label"], pred)
        .reindex(
            index=list(EXPR_LABELS),
            columns=["verbatim_like", "undetermined", "paraphrased"],
            fill_value=0,
        )
        .to_dict(orient="index")
    )
    return {
        "n": len(frame),
        "n_outside": int(outside.sum()),
        "accuracy_outside": round(float(match[outside].mean()), 4) if outside.any() else None,
        "balanced_accuracy_outside": round(float(pd.Series(per_class).mean()), 4),
        "band_ratio": round(float((~outside).mean()), 4),
        "confusion": confusion,
    }


dev_expr = expr[expr["side"] == "dev"]
test_expr = expr[expr["side"] == "test"]
candidates = []
for para_max, verb_min in itertools.product(grid(0.0, 1.0, 0.05), repeat=2):
    if para_max > verb_min:
        continue
    r = expression_eval(dev_expr, verb_min, para_max)
    if r["band_ratio"] <= MAX_BAND_RATIO and r["accuracy_outside"] is not None:
        candidates.append((r["accuracy_outside"], -r["band_ratio"], verb_min, para_max, r))
# 일치율 최대 → 띠가 좁은 쪽 → 지금 값에 가까운 쪽
best = max(
    candidates,
    key=lambda c: (
        c[0],
        c[1],
        -abs(c[2] - cfg.EXPRESSION_VERBATIM_MIN) - abs(c[3] - cfg.EXPRESSION_PARAPHRASE_MAX),
    ),
)
chosen_expr = {"EXPRESSION_VERBATIM_MIN": best[2], "EXPRESSION_PARAPHRASE_MAX": best[3]}
current_expr = {
    "EXPRESSION_VERBATIM_MIN": cfg.EXPRESSION_VERBATIM_MIN,
    "EXPRESSION_PARAPHRASE_MAX": cfg.EXPRESSION_PARAPHRASE_MAX,
}
top5 = sorted(candidates, key=lambda c: (c[0], c[1]), reverse=True)[:5]
expression_result = {
    "n": {"dev": len(dev_expr), "test": len(test_expr)},
    "objective": f"띠 안 비율 <= {MAX_BAND_RATIO} 에서 띠 밖 일치율 최대 (같으면 띠가 좁은 쪽)",
    "chosen": chosen_expr,
    "chosen_dev": best[4],
    "chosen_test": expression_eval(test_expr, best[2], best[3]),
    "current": current_expr,
    "current_dev": expression_eval(
        dev_expr, cfg.EXPRESSION_VERBATIM_MIN, cfg.EXPRESSION_PARAPHRASE_MAX
    ),
    "current_test": expression_eval(
        test_expr, cfg.EXPRESSION_VERBATIM_MIN, cfg.EXPRESSION_PARAPHRASE_MAX
    ),
    "dev_top5": [
        {
            "verbatim_min": c[2],
            "paraphrase_max": c[3],
            "accuracy_outside": c[0],
            "band_ratio": -c[1],
        }
        for c in top5
    ],
}
print(
    json.dumps(
        {
            k: expression_result[k]
            for k in ("chosen", "chosen_dev", "chosen_test", "current_dev", "current_test")
        },
        ensure_ascii=False,
        indent=1,
    )
)

# %% B. 간이 전달률 3단계 경계
LABEL_SCORE = {
    "verbatim": 1.0,
    "paraphrased": 1.0,
    "partial": 0.5,
    "missing": 0.0,
    "contradicted": 0.0,
}
FINAL_SCORE = {"said": 1.0, "partial": 0.5, "missing": 0.0, "contradicted": 0.0}
sentences["truth_score"] = sentences["label"].map(LABEL_SCORE)
sentences["final_score"] = sentences["final"].map(FINAL_SCORE)
slide_keys = ["side", "take_id", "slide"]
slides = sentences.groupby(slide_keys, sort=True)


def to_band(value: float, little: float, mostly: float) -> str:
    return "mostly" if value >= mostly else "little" if value < little else "about_half"


def fallback_value(lexical: pd.Series, lo: float, hi: float) -> float:
    """서비스 fallback.py `_lexical_coverage` 와 같은 식 (문장마다 1 · 0.5 · 0 → 평균, 반올림 3자리)."""
    points = lexical.map(lambda v: 1.0 if v >= hi else 0.5 if v >= lo else 0.0)
    return round(float(points.mean()), 3)


slide_table = slides.agg(
    truth=("truth_score", "mean"), llm=("final_score", "mean"), n_sent=("label", "size")
).reset_index()
slide_table["truth"] = slide_table["truth"].round(3)
slide_table["llm"] = slide_table["llm"].round(3)
lexical_by_slide = {k: g["lexical"] for k, g in slides}
SEVERE_BANDS = {("mostly", "little"), ("little", "mostly")}


def band_eval(frame: pd.DataFrame, lo: float, hi: float, little: float, mostly: float) -> dict:
    fb = [
        fallback_value(lexical_by_slide[(r.side, r.take_id, r.slide)], lo, hi)
        for r in frame.itertuples()
    ]
    truth_band = [to_band(v, little, mostly) for v in frame["truth"]]
    fb_band = [to_band(v, little, mostly) for v in fb]
    n = len(frame)
    return {
        "n_slides": n,
        "agreement": round(sum(a == b for a, b in zip(truth_band, fb_band, strict=False)) / n, 4),
        "severe": sum((a, b) in SEVERE_BANDS for a, b in zip(truth_band, fb_band, strict=False)),
        "mae": round(
            float(sum(abs(a - b) for a, b in zip(frame["truth"], fb, strict=False)) / n), 4
        ),
        "confusion": pd.crosstab(
            pd.Series(truth_band, name="truth"), pd.Series(fb_band, name="fallback")
        )
        .reindex(
            index=["little", "about_half", "mostly"],
            columns=["little", "about_half", "mostly"],
            fill_value=0,
        )
        .to_dict(orient="index"),
    }


def llm_band_eval(frame: pd.DataFrame, little: float, mostly: float) -> dict:
    """참고: LLM 최종 판정 전달률(문장 판정 said 1 · partial 0.5 · 나머지 0 의 평균)의 단계 일치율."""
    truth_band = [to_band(v, little, mostly) for v in frame["truth"]]
    llm_band = [to_band(v, little, mostly) for v in frame["llm"]]
    n = len(frame)
    return {
        "agreement": round(sum(a == b for a, b in zip(truth_band, llm_band, strict=False)) / n, 4),
        "severe": sum((a, b) in SEVERE_BANDS for a, b in zip(truth_band, llm_band, strict=False)),
        "mae": round(
            float(sum(abs(a - b) for a, b in zip(frame["truth"], frame["llm"], strict=False)) / n),
            4,
        ),
    }


dev_slides = slide_table[slide_table["side"] == "dev"].reset_index(drop=True)
test_slides = slide_table[slide_table["side"] == "test"].reset_index(drop=True)
cur = (
    cfg.FALLBACK_SENTENCE_LO,
    cfg.FALLBACK_SENTENCE_HI,
    cfg.FALLBACK_BAND_LITTLE,
    cfg.FALLBACK_BAND_MOSTLY,
)
band_candidates = []
for lo, hi in itertools.product(grid(0.05, 0.95, 0.05), grid(0.1, 1.0, 0.05)):
    if lo >= hi:
        continue
    # 장마다 간이 값은 (lo, hi) 로 한 번만 계산해 (little, mostly) 격자에 재사용한다
    fb = [
        fallback_value(lexical_by_slide[(r.side, r.take_id, r.slide)], lo, hi)
        for r in dev_slides.itertuples()
    ]
    for little, mostly in itertools.product(grid(0.1, 0.9, 0.1), repeat=2):
        if little >= mostly:
            continue
        tb = [to_band(v, little, mostly) for v in dev_slides["truth"]]
        fbb = [to_band(v, little, mostly) for v in fb]
        severe = sum((a, b) in SEVERE_BANDS for a, b in zip(tb, fbb, strict=False))
        agree = sum(a == b for a, b in zip(tb, fbb, strict=False)) / len(tb)
        mae = sum(abs(a - b) for a, b in zip(dev_slides["truth"], fb, strict=False)) / len(tb)
        dist = abs(lo - cur[0]) + abs(hi - cur[1]) + abs(little - cur[2]) + abs(mostly - cur[3])
        band_candidates.append((severe, -agree, mae, dist, lo, hi, little, mostly))
band_candidates.sort()
unconstrained = band_candidates[0]
# 단계 경계가 0.5 를 사이에 두지 않으면(예: MOSTLY 0.3) 모든 장이 mostly 로 몰려 의미 없는 해가 되므로,
# 'about_half' 가 0.5 를 품도록 LITTLE <= 0.4 · MOSTLY >= 0.6 으로 제한한다
band_candidates = [c for c in band_candidates if c[6] <= 0.4 and c[7] >= 0.6]
b = band_candidates[0]
chosen_band = (b[4], b[5], b[6], b[7])
fallback_result = {
    "n_slides": {"dev": len(dev_slides), "test": len(test_slides)},
    "objective": "개발에서 심각한 어긋남(정답 mostly <-> 간이 little) 최소 -> 단계 일치율 최대 -> MAE 최소 -> 지금 값에 가까운 쪽",
    "constraint": "LITTLE <= 0.4, MOSTLY >= 0.6 (제한 없이 고르면 아래 unconstrained_best 처럼 모든 장이 mostly 로 몰린다)",
    "unconstrained_best": dict(
        zip(
            ("severe", "neg_agreement", "mae", "dist", "LO", "HI", "LITTLE", "MOSTLY"),
            unconstrained,
            strict=False,
        )
    ),
    "chosen": dict(zip(("LO", "HI", "LITTLE", "MOSTLY"), chosen_band, strict=False)),
    "chosen_dev": band_eval(dev_slides, *chosen_band),
    "chosen_test": band_eval(test_slides, *chosen_band),
    "current": dict(zip(("LO", "HI", "LITTLE", "MOSTLY"), cur, strict=False)),
    "current_dev": band_eval(dev_slides, *cur),
    "current_test": band_eval(test_slides, *cur),
    "llm_final_reference": {
        "at_current_bands": {
            "dev": llm_band_eval(dev_slides, cur[2], cur[3]),
            "test": llm_band_eval(test_slides, cur[2], cur[3]),
        },
        "at_chosen_bands": {
            "dev": llm_band_eval(dev_slides, chosen_band[2], chosen_band[3]),
            "test": llm_band_eval(test_slides, chosen_band[2], chosen_band[3]),
        },
    },
    "dev_top5": [
        {
            "LO": c[4],
            "HI": c[5],
            "LITTLE": c[6],
            "MOSTLY": c[7],
            "severe": c[0],
            "agreement": -c[1],
            "mae": round(c[2], 4),
        }
        for c in band_candidates[:5]
    ],
    "dev_truth_rate_summary": dev_slides["truth"].describe().round(3).to_dict(),
}
print(
    json.dumps(
        {
            k: fallback_result[k]
            for k in (
                "chosen",
                "chosen_dev",
                "chosen_test",
                "current_dev",
                "current_test",
                "llm_final_reference",
            )
        },
        ensure_ascii=False,
        indent=1,
    )
)

# %% C / D 공통: 서비스와 같은 순서로 처리하는 함수
OTHER = {}  # {(대본, 장): 같은 대본의 다른 장 평가 기준}
for (name, n), rubric in rubrics.items():
    OTHER.setdefault(name, {})
    if rubric is not None:
        OTHER[name][n] = rubric


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def process(rubric, other_rubrics, stt_text: str):
    """STT 글 → (정규화 STT, 형태소, 대본 밖 구간 목록). 서비스 core 와 같은 순서다."""
    script_norm = normalize_script(rubric.normalized_script)
    stt_norm = normalize_stt(stt_text)
    script_pt, stt_pt = positioned_tokens(script_norm), positioned_tokens(stt_norm)
    alignments = align_sentences(
        script_norm,
        [(t.key, t.sentence) for t in script_pt],
        stt_norm,
        [(t.key, t.sentence) for t in stt_pt],
        rubric.sentence_roles,
    )
    offscript = build_offscript(rubric, stt_norm, stt_pt, alignments, other_rubrics)
    return stt_norm, stt_pt, alignments, offscript


def span_range(stt_norm, span) -> tuple[int, int]:
    """구간의 정규화 STT 전체 글 기준 위치."""
    base = stt_norm.sentences[span.stt_sentence_index].start
    return base + span.start, base + span.end


def span_text(stt_norm, span) -> str:
    s, e = span_range(stt_norm, span)
    return stt_norm.text[s:e]


# %% C. 자연 데이터: 후보 구간 내보내기와 곁말 재현율
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
candidate_path = OUTPUTS_DIR / "offscript_candidates.jsonl"
natural = []  # 후보 구간 (재현율 계산용)
mix_rows = []  # Take 별 범주 형태소 합
addition_rows = []
n_candidates = 0
with candidate_path.open("w", encoding="utf-8") as out:
    for t in takes:
        side = side_of[t.take_id]
        take_mix = Counter()
        take_tokens = 0
        label_data = json.loads(
            (DATA_DIR / "stt_labels" / f"{t.take_id}.json").read_text(encoding="utf-8")
        )
        additions_by_slide = {s["slide_number"]: s["additions"] for s in label_data["slides"]}
        for e in evaluations[t.take_id]:
            rubric = rubrics[(t.script_name, e.slide_number)]
            others = {n: r for n, r in OTHER[t.script_name].items() if n != e.slide_number}
            stt_norm, stt_pt, alignments, offscript = process(rubric, others, e.stt_text)
            assert [s.text for s in stt_norm.sentences] == e.stt_sentences, (
                "저장된 STT 문장과 다르다"
            )
            assert [(a.sentence_index, a.stt_ids) for a in alignments] == [
                (a.sentence_index, a.stt_ids) for a in e.alignments
            ], "저장된 정렬과 다르다"
            aligned = {j for a in alignments for j in a.stt_ids}
            mix = build_speech_mix(offscript, stt_pt, None)
            take_mix.update(mix.categories)
            take_tokens += mix.tokens
            tok_by_span = {}
            for sp in offscript.spans:
                s0, e0 = span_range(stt_norm, sp)
                tok_by_span[sp.id] = {t_.key for t_ in stt_pt if t_.start >= s0 and t_.end <= e0}
                record = {
                    "take_id": t.take_id,
                    "slide": e.slide_number,
                    "span_id": sp.id,
                    "category": sp.category,
                    "span_text": span_text(stt_norm, sp),
                    "stt_sentence": stt_norm.sentences[sp.stt_sentence_index].text,
                    "aligned": sp.stt_sentence_index in aligned,
                    "other_slide_number": sp.other_slide_number,
                    "tokens": sp.tokens,
                    "label": None,  # 사람이 달 자리: related_aside / unrelated / script / other
                }
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
                n_candidates += 1
                natural.append((t.take_id, e.slide_number, sp))
            for text in additions_by_slide.get(e.slide_number, []):
                add_keys = {x.key for x in positioned_tokens(normalize_script(text))}
                best_span, best_ov = None, 0.0
                for sp in offscript.spans:
                    st_text = compact(span_text(stt_norm, sp))
                    ct = compact(text)
                    ov = len(add_keys & tok_by_span[sp.id]) / len(add_keys) if add_keys else 0.0
                    substring = bool(st_text) and (ct in st_text or st_text in ct)
                    if (substring or ov >= 0.5) and max(ov, 1.0 if substring else 0.0) > best_ov:
                        best_span, best_ov = sp, max(ov, 1.0 if substring else 0.0)
                addition_rows.append(
                    {
                        "side": side,
                        "take_id": t.take_id,
                        "slide": e.slide_number,
                        "text": text,
                        "caught": best_span is not None,
                        "category": best_span.category if best_span else None,
                    }
                )
        mix_rows.append(
            {"side": side, "take_id": t.take_id, "tokens": take_tokens, **dict(take_mix)}
        )
print("후보 구간", n_candidates, "개 ->", candidate_path)

additions = pd.DataFrame(addition_rows)
recall_by_side = {}
for side, g in additions.groupby("side"):
    recall_by_side[side] = {
        "n": len(g),
        "caught": int(g["caught"].sum()),
        "additions_caught_ratio": round(float(g["caught"].mean()), 4),
        "by_category": g[g["caught"]]["category"].value_counts().to_dict(),
        "missed": g[~g["caught"]][["take_id", "slide", "text"]].to_dict(orient="records"),
    }
mix_df = pd.DataFrame(mix_rows).fillna(0)
candidate_df = pd.DataFrame(
    [{"side": side_of[tk], "category": sp.category, "tokens": sp.tokens} for tk, _, sp in natural]
)
mix_cols = ["script", "procedural", "other_slide", "off_script"]
RECALL_DEFINITIONS = {
    "additions_caught_ratio": (
        "라벨 additions(실제로 끼어든 곁말 문장) 중 어떤 후보 구간에 잡힌 비율 "
        "(형태소 겹침 0.5 이상 또는 글 포함). offscript_natural.addition_recall 에 있다"
    ),
    "labeled_outside_caught_ratio": (
        "라벨 파일의 정답(두 라벨러가 같은 것 + 어긋나 다시 판정한 것)이 대본 밖 말(aside · unrelated · procedural · other_slide)인 것에서, "
        "지금 규칙이 후보로 잡은 비율. offscript_natural_precision 에 있다"
    ),
}
natural_result = {
    "n_candidates": n_candidates,
    "recall_definitions": RECALL_DEFINITIONS,
    "candidates_by_category": {
        side: g["category"].value_counts().to_dict() for side, g in candidate_df.groupby("side")
    },
    "addition_recall": recall_by_side,
    "take_mean_token_share": {
        side: {c: round(float((g[c] / g["tokens"]).mean()), 4) for c in mix_cols if c in g.columns}
        for side, g in mix_df.groupby("side")
    },
    "take_token_share_range": {
        side: {
            c: [
                round(float((g[c] / g["tokens"]).min()), 4),
                round(float((g[c] / g["tokens"]).max()), 4),
            ]
            for c in mix_cols
            if c in g.columns
        }
        for side, g in mix_df.groupby("side")
    },
}
print(
    json.dumps(
        {
            k: {
                s: (
                    v
                    if k != "addition_recall"
                    else {kk: vv for kk, vv in v.items() if kk != "missed"}
                )
                for s, v in d.items()
            }
            if isinstance(d, dict) and k != "n_candidates"
            else d
            for k, d in natural_result.items()
        },
        ensure_ascii=False,
        indent=1,
    )
)

# %% C2. 자연 데이터 precision과 규칙 비교 (정답 라벨 `offscript_labels.json`)

label_file = json.loads((DATA_DIR / "offscript_labels.json").read_text(encoding="utf-8"))
label_of = {
    (x["take_id"], x["slide_number"], x["stt_sentence"], x["span_text"]): x["label"]
    for x in label_file["items"]
}
# 짝 없는 문장 후보는 문장 하나에 구간 하나다. 구간이 '내용 형태소 범위'에서 '문장 전체'로 바뀌어 구간 글이
# 라벨 파일과 다를 수 있으므로, 그 후보는 (take, 장, 문장 글)로도 라벨 항목을 찾는다
label_key_by_sentence = {
    (x["take_id"], x["slide_number"], x["stt_sentence"]): (
        x["take_id"],
        x["slide_number"],
        x["stt_sentence"],
        x["span_text"],
    )
    for x in label_file["items"]
    if not x["aligned"]
}


def label_key(key: tuple) -> tuple | None:
    """후보 키 → 라벨 파일 항목 키 (없으면 None)."""
    if key in label_of:
        return key
    return label_key_by_sentence.get(key[:3])


n_null = sum(1 for v in label_of.values() if v is None)
OUTSIDE_RIGHT = {  # 예측 범주 -> 그 범주로 맞는 정답
    "off_script": {"aside", "unrelated"},
    "procedural": {"procedural"},
    "other_slide": {"other_slide"},
}
labeled_outside = {k for k, v in label_of.items() if v not in (None, "script", "unclear")}


def collect(in_sentence: bool, min_tokens: int) -> list[dict]:
    """상수를 잠깐 바꿔 모든 장의 후보 구간을 뽑는다 (끝나면 원래 값으로 되돌린다)."""
    saved = (speech_mix.OFFSCRIPT_IN_SENTENCE, speech_mix.OFFSCRIPT_MIN_TOKENS)
    speech_mix.OFFSCRIPT_IN_SENTENCE, speech_mix.OFFSCRIPT_MIN_TOKENS = in_sentence, min_tokens
    try:
        found = []
        for t in takes:
            for e in evaluations[t.take_id]:
                rubric = rubrics[(t.script_name, e.slide_number)]
                others = {n: r for n, r in OTHER[t.script_name].items() if n != e.slide_number}
                stt_norm, _, _, off = process(rubric, others, e.stt_text)
                for sp in off.spans:
                    key = (
                        t.take_id,
                        e.slide_number,
                        stt_norm.sentences[sp.stt_sentence_index].text,
                        span_text(stt_norm, sp),
                    )
                    lk = label_key(key)
                    found.append(
                        {
                            "side": side_of[t.take_id],
                            "key": key,
                            "label_key": lk,
                            "pred": sp.category,
                            "label": label_of[lk] if lk is not None else "no_entry",
                        }
                    )
        return found
    finally:
        speech_mix.OFFSCRIPT_IN_SENTENCE, speech_mix.OFFSCRIPT_MIN_TOKENS = saved


def precision_report(found: list[dict]) -> dict:
    """후보 목록 -> 개발 · 시험별 범주별 precision. 라벨이 null 이거나 라벨 파일에 없는 후보는 따로 센다."""
    out = {}
    for side in ("dev", "test"):
        g = [f for f in found if f["side"] == side]
        usable = [f for f in g if f["label"] not in (None, "no_entry", "unclear")]
        outside = [f for f in usable if f["label"] != "script"]
        caught = {f["label_key"] for f in g if f["label_key"] is not None}
        row = {
            "n_candidates": len(g),
            "n_usable": len(usable),
            "n_label_null": sum(f["label"] is None for f in g),
            "n_not_in_label_file": sum(f["label"] == "no_entry" for f in g),
            "outside_precision": round(len(outside) / len(usable), 4) if usable else None,
            "labeled_outside_caught_ratio": round(
                len({k for k in labeled_outside if k in caught and side_of[k[0]] == side})
                / max(sum(1 for k in labeled_outside if side_of[k[0]] == side), 1),
                4,
            ),
            "n_label_mismatch_excluded": sum(
                f["label"] in (None, "no_entry", "unclear") for f in g
            ),
            "n_labeled_outside": sum(1 for k in labeled_outside if side_of[k[0]] == side),
            "by_predicted": {},
        }
        for pred, right in OUTSIDE_RIGHT.items():
            p = [f for f in usable if f["pred"] == pred]
            row["by_predicted"][pred] = {
                "n": len(p),
                "right": sum(f["label"] in right for f in p),
                "precision": round(sum(f["label"] in right for f in p) / len(p), 4) if p else None,
                "labels": dict(Counter(f["label"] for f in p)),
            }
        out[side] = row
    return out


natural_precision = precision_report(
    collect(speech_mix.OFFSCRIPT_IN_SENTENCE, speech_mix.OFFSCRIPT_MIN_TOKENS)
)
rule_variants = {}
for in_sentence, min_tokens in [(False, 2), (True, 2), (True, 4), (True, 6)]:
    name = f"in_sentence={in_sentence},min_tokens={min_tokens}"
    rule_variants[name] = precision_report(collect(in_sentence, min_tokens))
assert (speech_mix.OFFSCRIPT_IN_SENTENCE, speech_mix.OFFSCRIPT_MIN_TOKENS) == (
    cfg.OFFSCRIPT_IN_SENTENCE,
    cfg.OFFSCRIPT_MIN_TOKENS,
)
labels_result = {
    "note": label_file["note"],
    "agreement": label_file["agreement"],
    "n_items": len(label_file["items"]),
    "n_label_null": n_null,
    "label_counts": dict(Counter(v for v in label_of.values())),
}
print(
    json.dumps(
        {"current": cfg.OFFSCRIPT_IN_SENTENCE, "precision": natural_precision},
        ensure_ascii=False,
        indent=1,
    )
)
for name, r in rule_variants.items():
    print(
        name,
        {
            s: (v["n_usable"], v["outside_precision"], v["labeled_outside_caught_ratio"])
            for s, v in r.items()
        },
    )

# %% D. 합성 주입
inject = json.loads((DATA_DIR / "offscript_injections.json").read_text(encoding="utf-8"))
other_sources = {}  # {(대본, 장): [(문장 글, 형태소 수)]} 다른 장에서 가져올 수 있는 문장 (skip 아니고 형태소 5개 이상)
for name, by_slide in OTHER.items():
    for n, rubric in by_slide.items():
        norm = normalize_script(rubric.normalized_script)
        count = Counter(t_.sentence for t_ in positioned_tokens(norm))
        other_sources[(name, n)] = [
            s.text
            for s in norm.sentences
            if rubric.sentence_roles[s.index] != "skip"
            and count[s.index] >= OTHER_SLIDE_MIN_SOURCE_TOKENS
        ]


def pick_sentence(rng: random.Random, name: str, slide: int, category: str) -> str | None:
    if category in ("procedural", "unrelated"):
        return rng.choice(inject[category])
    if category == "related_aside":
        return rng.choice(inject["related_aside"][name][str(slide)])
    pool = [
        s
        for (nm, n), lst in sorted(other_sources.items())
        if nm == name and n != slide
        for s in lst
    ]
    return rng.choice(pool) if pool else None


def find_range(text: str, sentence: str) -> tuple[int, int] | None:
    """공백 무시로 sentence 가 text 안에서 차지하는 위치."""
    pattern = r"\s*".join(map(re.escape, compact(sentence)))
    m = re.search(pattern, text)
    return (m.start(), m.end()) if m else None


def truth_of(category: str) -> str:
    return "off_script" if category in ("related_aside", "unrelated") else category


def span_key(stt_norm, sp) -> tuple[str, str]:
    """주입 전 · 후 후보를 대응시키는 키: (STT 문장 글, 구간 글)."""
    return stt_norm.sentences[sp.stt_sentence_index].text, span_text(stt_norm, sp)


def token_ids(stt_pt, lo: int, hi: int) -> set[int]:
    return {i for i, t_ in enumerate(stt_pt) if t_.start >= lo and t_.end <= hi}


runs = []
for ti, t in enumerate(takes):
    for e in evaluations[t.take_id]:
        rubric = rubrics[(t.script_name, e.slide_number)]
        others = {n: r for n, r in OTHER[t.script_name].items() if n != e.slide_number}
        base_norm, _, _, base_off = process(rubric, others, e.stt_text)
        base_keys = Counter(span_key(base_norm, sp) for sp in base_off.spans)
        sents = [s.text for s in base_norm.sentences]
        for rnd in range(INJECTION_ROUNDS):
            category = CATEGORIES[(e.slide_number + rnd + ti) % len(CATEGORIES)]
            rng = random.Random(f"{t.take_id}/{e.slide_number}/{rnd}")
            text = pick_sentence(rng, t.script_name, e.slide_number, category)
            if text is None:
                continue
            at = rng.randint(0, len(sents))
            if find_range(base_norm.text, text) is not None:
                # 원래 STT 에 같은 문장이 이미 있으면 처음 나온 위치를 정답으로 쓰게 되므로 건너뛴다
                runs.append(
                    {
                        "side": side_of[t.take_id],
                        "truth_fine": category,
                        "already_in_stt": True,
                        "found": False,
                        "inject_tokens": 0,
                    }
                )
                continue
            new_text = " ".join([*sents[:at], text, *sents[at:]])
            stt_norm, stt_pt, _, off = process(rubric, others, new_text)
            rng_ = find_range(stt_norm.text, text)
            run = {
                "side": side_of[t.take_id],
                "take_id": t.take_id,
                "slide": e.slide_number,
                "truth_fine": category,
                "truth": truth_of(category),
                "text": text,
                "already_in_stt": False,
                "found": rng_ is not None,
                "detected": False,
                "pred": None,
                "pred_other_slide": None,
                "inject_tokens": 0,
                "covered_ratio": None,
                "new_false": [],  # 주입과 겹치지 않고 주입 전에 없던 후보의 범주
                "removed": 0,  # 주입 전에 있었으나 주입 후에 없는 후보 수
                "tok_truth": 0,
                "tok_pred": Counter(),  # 예측 범주별 형태소 수 (주입과 겹친 후보 + 새 후보)
                "tok_hit": Counter(),  # 그중 주입 형태소와 겹친 수 (예측 범주별)
            }
            runs.append(run)
            if rng_ is None:
                continue
            inj_ids = token_ids(stt_pt, *rng_)
            run["inject_tokens"] = len(inj_ids)
            if not inj_ids:
                continue
            run["tok_truth"] = len(inj_ids)
            new_keys = Counter()
            cover = Counter()
            for sp in off.spans:
                s0, e0 = span_range(stt_norm, sp)
                sp_ids = token_ids(stt_pt, s0, e0)
                n_cov = len(sp_ids & inj_ids)
                key = span_key(stt_norm, sp)
                new_keys[key] += 1
                if n_cov:
                    cover[sp.id] += n_cov
                elif base_keys[key] >= new_keys[key]:
                    continue  # 주입 전부터 있던 후보
                else:
                    run["new_false"].append(sp.category)
                run["tok_pred"][sp.category] += len(sp_ids)
                run["tok_hit"][sp.category] += n_cov
            run["removed"] = sum(max(c - new_keys[k], 0) for k, c in base_keys.items())
            if cover:
                top_id, top_n = max(sorted(cover.items()), key=lambda kv: kv[1])
                run["covered_ratio"] = round(top_n / len(inj_ids), 3)
                if top_n / len(inj_ids) >= 0.5:
                    span = next(s for s in off.spans if s.id == top_id)
                    run["detected"] = True
                    run["pred"] = span.category
                    run["pred_other_slide"] = span.other_slide_number
            else:
                run["covered_ratio"] = 0.0
runs_df = pd.DataFrame(runs)
skipped = runs_df[runs_df["already_in_stt"]]
runs_df = runs_df[~runs_df["already_in_stt"]]
usable = runs_df[runs_df["inject_tokens"] > 0]
print(
    "끼운 횟수",
    len(runs_df),
    "/ 원래 STT 에 이미 있어 건너뜀",
    len(skipped),
    "/ 끼운 글을 못 찾음",
    int((~runs_df["found"]).sum()),
    "/ 형태소 0",
    int((runs_df["found"] & (runs_df["inject_tokens"] == 0)).sum()),
)


def prf(tp: int, n_pred: int, n_truth: int) -> dict:
    p = tp / n_pred if n_pred else None
    r = tp / n_truth if n_truth else None
    f1 = 2 * p * r / (p + r) if p and r else (0.0 if p is not None and r is not None else None)
    return {
        "precision": round(p, 4) if p is not None else None,
        "recall": round(r, 4) if r is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
        "tp_tokens": tp,
        "pred_tokens": n_pred,
        "truth_tokens": n_truth,
    }


def span_metrics(g: pd.DataFrame) -> dict:
    """구간 단위(형태소 겹침) precision · recall · F1.

    정답 = 주입한 문장의 형태소(범주 = truth), 예측 = 주입과 겹친 후보 + 새 후보의 형태소(범주 = 후보 범주).
    범주별은 정답 범주와 예측 범주가 같은 형태소만 맞는 것으로 센다. overall 은 범주까지 맞아야 하고,
    overall_any_category 는 범주를 보지 않고 대본 밖 후보에 든 형태소만 본다.
    """
    out = {}
    for pred in ("procedural", "other_slide", "off_script"):
        truth_n = int(g.loc[g["truth"] == pred, "tok_truth"].sum())
        pred_n = sum(c[pred] for c in g["tok_pred"])
        hit = sum(c[pred] for c, tr in zip(g["tok_hit"], g["truth"], strict=True) if tr == pred)
        out[pred] = prf(hit, pred_n, truth_n)
    all_pred = sum(sum(c.values()) for c in g["tok_pred"])
    all_hit_any = sum(sum(c.values()) for c in g["tok_hit"])
    all_hit_right = sum(c[tr] for c, tr in zip(g["tok_hit"], g["truth"], strict=True))
    all_truth = int(g["tok_truth"].sum())
    out["overall"] = prf(all_hit_right, all_pred, all_truth)
    out["overall_any_category"] = prf(all_hit_any, all_pred, all_truth)
    return out


def injection_summary(g: pd.DataFrame, all_runs: pd.DataFrame) -> dict:
    """범주별 재현율 · precision · 새 오탐 · 구간 지표. g 는 형태소가 있는 주입만 든다."""
    recall = {}
    for fine in CATEGORIES:
        h = g[g["truth_fine"] == fine]
        if h.empty:
            continue
        want = truth_of(fine)
        recall[fine] = {
            "n": len(h),
            "detected": round(float(h["detected"].mean()), 4),
            "category_right": round(float((h["detected"] & (h["pred"] == want)).mean()), 4),
            "predicted_as": h[h["detected"]]["pred"].value_counts().to_dict(),
        }
    new_false = Counter(c for lst in g["new_false"] for c in lst)
    precision = {}
    for pred in ("procedural", "other_slide", "off_script"):
        p = g[g["detected"] & (g["pred"] == pred)]
        right = int((p["truth"] == pred).sum())
        denom = len(p) + new_false[pred]  # 새 후보는 정답이 '대본 내용'이므로 틀린 예측
        precision[pred] = {
            "n_predicted": len(p),
            "n_new_false": new_false[pred],
            "precision": round(right / denom, 4) if denom else None,
            "precision_without_new_false": round(right / len(p), 4) if len(p) else None,
            "truth_counts": p["truth_fine"].value_counts().to_dict(),
        }
    return {
        "n": len(g),
        "already_in_stt_skipped": int((all_runs["already_in_stt"]).sum()),
        "recall": recall,
        "precision": precision,
        "new_false_positives": {
            "total": int(sum(new_false.values())),
            "by_category": dict(new_false),
            "runs_with_new": int((g["new_false"].map(len) > 0).sum()),
            "mean_per_run": round(float(g["new_false"].map(len).mean()), 4),
            "removed_total": int(g["removed"].sum()),
            "runs_with_removed": int((g["removed"] > 0).sum()),
        },
        "span_metrics": span_metrics(g),
    }


injection_result = {
    "rounds": INJECTION_ROUNDS,
    "note": "장마다 범주 4개를 한 번씩, 한 번에 문장 하나만 끼움 (같은 장의 주입은 서로 독립인 별도 실행)",
    "definitions": {
        "new_false_positive": "주입 구간과 겹치지 않고, (STT 문장 글, 구간 글)로 대응시킨 주입 전 후보에 없던 후보. 사라진 후보와 상쇄하지 않는다",
        "precision": "범주별: 그 범주로 검출된 주입 중 정답 범주 / (그 수 + 그 범주의 새 오탐)",
        "span_metrics": "정답 = 주입 문장 형태소, 예측 = 주입과 겹친 후보 + 새 후보의 형태소; 범주 일치 필요",
    },
    "runs": {
        "total": len(runs_df),
        "already_in_stt_skipped": len(skipped),
        "already_in_stt_by_category": skipped["truth_fine"].value_counts().to_dict(),
        "not_found": int((~runs_df["found"]).sum()),
        "no_tokens": int((runs_df["found"] & (runs_df["inject_tokens"] == 0)).sum()),
    },
    **{
        side: injection_summary(usable[usable["side"] == side], skipped[skipped["side"] == side])
        for side in ("dev", "test")
    },
}
print(json.dumps(injection_result, ensure_ascii=False, indent=1))

# %% 결과 저장
path = write_result(
    "coverage_detail",
    "none (LLM 호출 없음)",
    {
        "split": {"dev": DEV, "test": TEST},
        "missing_slides": {"no_rubric": missing_slides, "not_scored": not_scored},
        "expression": expression_result,
        "fallback_band": fallback_result,
        "labels": labels_result,
        "offscript_natural": natural_result,
        "offscript_natural_precision": natural_precision,
        "rule_variants": rule_variants,
        "offscript_injection": injection_result,
    },
)
print("저장:", path)
