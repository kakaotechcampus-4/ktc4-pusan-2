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
  처리하고 범주별 재현율 · precision · 오탐(끼운 문장 때문에 대본 내용이 후보로 잡힌 수)을 지금 규칙으로 잰다

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
        "recall": round(float(g["caught"].mean()), 4),
        "by_category": g[g["caught"]]["category"].value_counts().to_dict(),
        "missed": g[~g["caught"]][["take_id", "slide", "text"]].to_dict(orient="records"),
    }
mix_df = pd.DataFrame(mix_rows).fillna(0)
candidate_df = pd.DataFrame(
    [{"side": side_of[tk], "category": sp.category, "tokens": sp.tokens} for tk, _, sp in natural]
)
mix_cols = ["script", "procedural", "other_slide", "off_script"]
natural_result = {
    "n_candidates": n_candidates,
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
                    found.append(
                        {
                            "side": side_of[t.take_id],
                            "key": key,
                            "pred": sp.category,
                            "label": label_of.get(key, "no_entry"),
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
        caught = {f["key"] for f in g}
        row = {
            "n_candidates": len(g),
            "n_usable": len(usable),
            "n_label_null": sum(f["label"] is None for f in g),
            "n_not_in_label_file": sum(f["label"] == "no_entry" for f in g),
            "outside_precision": round(len(outside) / len(usable), 4) if usable else None,
            "outside_recall_of_labeled": round(
                len({k for k in labeled_outside if k in caught and side_of[k[0]] == side})
                / max(sum(1 for k in labeled_outside if side_of[k[0]] == side), 1),
                4,
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
            s: (v["n_usable"], v["outside_precision"], v["outside_recall_of_labeled"])
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


runs = []
for ti, t in enumerate(takes):
    for e in evaluations[t.take_id]:
        rubric = rubrics[(t.script_name, e.slide_number)]
        others = {n: r for n, r in OTHER[t.script_name].items() if n != e.slide_number}
        base_norm, _, _, base_off = process(rubric, others, e.stt_text)
        sents = [s.text for s in base_norm.sentences]
        for rnd in range(INJECTION_ROUNDS):
            category = CATEGORIES[(e.slide_number + rnd + ti) % len(CATEGORIES)]
            rng = random.Random(f"{t.take_id}/{e.slide_number}/{rnd}")
            text = pick_sentence(rng, t.script_name, e.slide_number, category)
            if text is None:
                continue
            at = rng.randint(0, len(sents))
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
                "found": rng_ is not None,
                "detected": False,
                "pred": None,
                "pred_other_slide": None,
                "inject_tokens": 0,
                "covered_ratio": None,
                "extra_spans": None,
            }
            runs.append(run)
            if rng_ is None:
                continue
            inj = [t_ for t_ in stt_pt if t_.start >= rng_[0] and t_.end <= rng_[1]]
            run["inject_tokens"] = len(inj)
            if not inj:
                continue
            cover = Counter()
            injected_spans = 0
            for sp in off.spans:
                s0, e0 = span_range(stt_norm, sp)
                n_cov = sum(1 for t_ in inj if t_.start >= s0 and t_.end <= e0)
                if n_cov:
                    injected_spans += 1
                    cover[sp.id] += n_cov
            run["extra_spans"] = len(off.spans) - len(base_off.spans) - injected_spans
            if cover:
                top_id, top_n = max(sorted(cover.items()), key=lambda kv: kv[1])
                run["covered_ratio"] = round(top_n / len(inj), 3)
                if top_n / len(inj) >= 0.5:
                    span = next(s for s in off.spans if s.id == top_id)
                    run["detected"] = True
                    run["pred"] = span.category
                    run["pred_other_slide"] = span.other_slide_number
            else:
                run["covered_ratio"] = 0.0
runs_df = pd.DataFrame(runs)
usable = runs_df[runs_df["inject_tokens"] > 0]
print(
    "끼운 횟수",
    len(runs_df),
    "/ 끼운 글을 못 찾음",
    int((~runs_df["found"]).sum()),
    "/ 형태소 0",
    int((runs_df["found"] & (runs_df["inject_tokens"] == 0)).sum()),
)


def injection_summary(g: pd.DataFrame) -> dict:
    """범주별 재현율 · precision · 오탐. g 는 형태소가 있는 주입만 든다."""
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
    precision = {}
    for pred in ("procedural", "other_slide", "off_script"):
        p = g[g["detected"] & (g["pred"] == pred)]
        precision[pred] = {
            "n_predicted": len(p),
            "precision": round(float((p["truth"] == pred).mean()), 4) if len(p) else None,
            "truth_counts": p["truth_fine"].value_counts().to_dict(),
        }
    return {
        "n": len(g),
        "recall": recall,
        "precision": precision,
        "other_slide_points_to_source": None,
        "false_positive_spans": {
            "total_extra": int(g["extra_spans"].sum()),
            "runs_with_extra": int((g["extra_spans"] > 0).sum()),
            "mean_per_run": round(float(g["extra_spans"].mean()), 4),
        },
    }


injection_result = {
    "rounds": INJECTION_ROUNDS,
    "note": "장마다 범주 4개를 한 번씩, 한 번에 문장 하나만 끼움 (같은 장의 주입은 서로 독립인 별도 실행)",
    "runs": {
        "total": len(runs_df),
        "not_found": int((~runs_df["found"]).sum()),
        "no_tokens": int((runs_df["found"] & (runs_df["inject_tokens"] == 0)).sum()),
    },
    **{side: injection_summary(usable[usable["side"] == side]) for side in ("dev", "test")},
}
# other_slide 로 예측한 것 중 끼운 문장의 출처 장을 가리키는지는 문장 출처를 따로 기록하지 않아 보지 않는다
for side in ("dev", "test"):
    injection_result[side].pop("other_slide_points_to_source")
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
