"""LLM 일관성 측정: 같은 대본으로 평가 기준을 여러 벌 만들어, 1차 결과와 최종 결과가 얼마나 흔들리는지 비교한다.

k 번째 벌 = k 번째 1차 분석 응답 → 1차 결과(코드) → 최종 결론 응답 → 최종 결과(코드).
없는 응답만 새로 호출해 캐시에 남기므로 다시 실행하면 호출하지 않는다.
"""

import random
from pathlib import Path

import pandas as pd

from script_coverage.script_analysis.draft import draft_rubric
from script_coverage.script_analysis.final import (
    final_config_hash,
    final_user_message,
    finalize_rubric,
    name_candidates,
    review_final,
)
from script_coverage.script_analysis.keywords import extract_keywords_tfidf
from script_coverage.script_analysis.schemas import FinalReview, SlideSemanticAnalysis
from script_coverage.script_analysis.semantic import analyze_semantics, semantic_config_hash
from script_coverage.shared.facts import extract_critical_facts
from script_coverage.shared.llm_step import (
    LLMCache,
    cache_get,
    cache_put,
    call_in_parallel,
    llm_step,
)
from script_coverage.shared.rubric import NAME_TYPES, SCORE_WEIGHT, EvaluationRubric
from script_coverage.shared.text import normalize_script

from ..cache import SampleCache
from ..datasets import load_script_json


def sample_rubrics(
    path: Path,
    cache: LLMCache,
    semantic_llm,
    final_llm,
    model: str,
    n_samples: int,
):
    """슬라이드마다 1차 결과와 최종 결과를 n_samples 벌씩 만든다.

    0번 1차 분석은 파이프라인이 캐시한 응답이다 (`script.semantic`), 1번부터는 `script.semantic#k` 로 따로 캐시한다.
    없는 LLM 응답만 새로 호출하고 캐시에 저장하므로 다시 실행하면 호출하지 않는다.
    """
    slides = load_script_json(path)
    norms = [normalize_script(s.script) for s in slides]
    deck_texts = [n.text for n in norms]
    keywords = extract_keywords_tfidf(norms)
    facts = [extract_critical_facts(n) for n in norms]
    semantic_config = semantic_config_hash(model)
    caches = [SampleCache(cache, k) for k in range(n_samples)]

    samples: list[dict[int, SlideSemanticAnalysis]] = []
    for norm in norms:
        got = {}
        for k in range(n_samples):
            cached = cache_get(
                caches[k],
                "script.semantic",
                norm.content_hash,
                semantic_config,
                SlideSemanticAnalysis,
            )
            if (
                cached is None and k == 0
            ):  # 0번: semantic_cache 가 우선, 없으면 semantic_samples 의 0번을 쓴다
                cached = cache_get(
                    cache,
                    "script.semantic#0",
                    norm.content_hash,
                    semantic_config,
                    SlideSemanticAnalysis,
                )
            if cached is not None:
                got[k] = cached
        samples.append(got)
    jobs = {
        (i, k): (slides[i], norms[i], semantic_llm)
        for i, got in enumerate(samples)
        for k in range(n_samples)
        if k not in got
    }
    done, errors = call_in_parallel(jobs, analyze_semantics, 4)
    for (
        (i, k),
        sem,
    ) in done.items():  # 실패한 호출이 있어도 성공한 (이미 비용을 낸) 응답은 먼저 캐시에 남긴다
        samples[i][k] = sem
        kind = "script.semantic#0" if k == 0 else "script.semantic"
        cache_put(cache if k == 0 else caches[k], kind, norms[i].content_hash, semantic_config, sem)
    if errors:
        raise RuntimeError(f"1차 분석 호출 실패: {next(iter(errors.values()))}")
    n_calls = len(jobs)

    drafts, inputs = {}, {}
    for i in range(len(slides)):
        for k in range(n_samples):
            draft = draft_rubric(
                path.stem,
                slides[i],
                norms[i],
                facts[i],
                keywords[i],
                samples[i][k],
                deck_texts,
                model,
            )
            candidates = name_candidates(draft, facts[i], norms[i], deck_texts)
            drafts[i, k] = draft
            inputs[i, k] = (
                candidates,
                final_user_message(slides[i], norms[i], draft, facts[i], candidates, deck_texts),
            )
    reviews, errors, final_calls = llm_step(
        "script.final",
        {key: message for key, (_, message) in inputs.items()},
        review_final,
        final_llm,
        final_config_hash(model),
        FinalReview,
        cache=cache,
    )
    if errors:
        raise RuntimeError(f"최종 결론 호출 실패: {next(iter(errors.values()))}")
    n_calls += final_calls

    finals = {
        (i, k): finalize_rubric(
            path.stem, slides[i], norms[i], facts[i], keywords[i],
            drafts[i, k], reviews[i, k], inputs[i, k][0], deck_texts, model,
        )
        for (i, k) in drafts
    }  # fmt: skip
    return slides, norms, drafts, finals, n_calls


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / len(a | b) if a | b else 1.0


def rubric_view(rubric: EvaluationRubric):
    """비교에 쓰는 요약: 문장 역할, (근거 문장 집합, 중요도) 목록, 이름·용어 사실 집합."""
    return (
        rubric.sentence_roles,
        [(set(kp.sentence_indices), kp.importance) for kp in rubric.key_points],
        {f.normalized for f in rubric.critical_facts if f.type in NAME_TYPES},
    )


def compare_views(views: list) -> dict:
    """0번을 기준으로 나머지와 비교한다. Key Point 는 근거 문장 집합이 절반 이상 겹치면 같은 것으로 본다."""
    base_roles, base_kps, base_names = views[0]
    pairs = matched = agreed = critical_same = role_same = role_total = 0
    for roles, kps, _ in views[1:]:
        for ids, importance in base_kps:
            pairs += 1
            best = max(kps, key=lambda kp: _jaccard(ids, kp[0]), default=(set(), None))
            if ids and _jaccard(ids, best[0]) >= 0.5:
                matched += 1
                agreed += best[1] == importance
        base_crit = [ids for ids, imp in base_kps if imp == "critical"]
        other_crit = [ids for ids, imp in kps if imp == "critical"]
        critical_same += (not base_crit and not other_crit) or any(
            _jaccard(a, b) >= 0.5 for a in base_crit for b in other_crit
        )
        role_total += len(base_roles)
        role_same += sum(a == b for a, b in zip(base_roles, roles, strict=False))
    return {
        "KP 수": "/".join(str(len(kps)) for _, kps, _ in views),
        "critical 수": "/".join(
            str(sum(imp == "critical" for _, imp in kps)) for _, kps, _ in views
        ),
        "문장 역할 일치율": role_same / role_total if role_total else 1.0,
        "KP 대응률": matched / pairs if pairs else 1.0,
        "중요도 일치율": agreed / matched if matched else 1.0,
        "critical 위치 일치": critical_same / (len(views) - 1),
        "이름·용어 일치": sum(_jaccard(base_names, names) for _, _, names in views[1:])
        / (len(views) - 1),
    }


def score_spread(views: list, n_sentences: int, trials: int = 300, seed: int = 0) -> list[float]:
    """같은 가상 발화를 각 기준으로 채점했을 때 점수 차이(최대 - 최소, 100점 만점).

    문장마다 80% 확률로 말했다고 가정하고, Key Point 는 근거 문장을 다 말하면 1, 일부면 0.5, 하나도 안 말하면 0 으로
    중요도 가중 평균을 낸다. 실제 채점 방식이 아니라 기준이 흔들릴 때 점수가 얼마나 달라지는지 가늠하는 용도다.
    """
    rng = random.Random(seed)
    spreads = []
    for _ in range(trials):
        said = [rng.random() < 0.8 for _ in range(n_sentences)]
        scores = []
        for _, kps, _ in views:
            num = den = 0.0
            for ids, importance in kps:
                if not ids:
                    continue
                ratio = sum(said[j] for j in ids) / len(ids)
                num += (1.0 if ratio == 1 else 0.0 if ratio == 0 else 0.5) * SCORE_WEIGHT[
                    importance
                ]
                den += SCORE_WEIGHT[importance]
            scores.append(100 * num / den if den else 0.0)
        spreads.append(max(scores) - min(scores))
    return spreads


def measure_consistency(
    paths: list[Path],
    cache: LLMCache,
    semantic_llm,
    final_llm,
    model: str,
    n_samples: int,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    """대본마다 평가 기준을 n_samples 벌 만들어 비교한다. (슬라이드별 표, 단계별 요약 표, 대본별 추가 호출 수)"""
    rows, spreads = [], {"1차": [], "최종": []}
    calls = {}
    for path in paths:
        slides, norms, drafts, finals, n_calls = sample_rubrics(
            path, cache, semantic_llm, final_llm, model, n_samples
        )
        calls[path.name] = n_calls
        for i, slide in enumerate(slides):
            for stage, rubrics_by_key in (("1차", drafts), ("최종", finals)):
                views = [rubric_view(rubrics_by_key[i, k]) for k in range(n_samples)]
                spread = score_spread(views, len(norms[i].sentences))
                spreads[stage] += spread
                rows.append(
                    {
                        "file": path.stem,
                        "slide": slide.slide_number,
                        "단계": stage,
                        **compare_views(views),
                        "점수 차이 평균": sum(spread) / len(spread),
                    }
                )

    consistency_table = pd.DataFrame(rows)
    rate_cols = [
        "문장 역할 일치율",
        "KP 대응률",
        "중요도 일치율",
        "critical 위치 일치",
        "이름·용어 일치",
    ]
    summary = consistency_table.groupby("단계")[rate_cols].mean()
    for stage, values in spreads.items():
        values = sorted(values)
        summary.loc[stage, "점수 차이 평균"] = sum(values) / len(values)
        summary.loc[stage, "점수 차이 중앙값"] = values[len(values) // 2]
        summary.loc[stage, "점수 차이 상위 10%"] = values[int(0.9 * (len(values) - 1))]
    return consistency_table, summary, calls
