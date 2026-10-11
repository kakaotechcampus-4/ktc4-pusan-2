"""점수 계산: LLM 은 점수를 매기지 않는다. 판정과 규칙 결과로 코드가 계산한다."""

from ..shared.rubric import SCORE_WEIGHT
from .config import FACT_SCORE, STATUS_SCORE
from .schemas import FactCheck, KeyPointResult, SimilarItem


def slide_scores(
    results: list[KeyPointResult],
    fact_checks: list[FactCheck],
    fidelity: dict,
    items: list[SimilarItem],
) -> dict:
    """Score Engine (코드): LLM 은 점수를 매기지 않는다. 판정과 규칙 결과로 점수를 계산한다.

    - content_coverage = Σ(판정 점수 × 중요도 가중치) / Σ(중요도 가중치). 모순(-0.5) 때문에 음수가 되면 0
    - critical_fact_accuracy = Σ(사실 점수 × 가중치) / Σ(가중치). 맞게 말함 1, 어림해 말함 0.5, 빠짐·틀림 0.
      발음이 비슷한 다른 말로 나온 수치·이름(sound_alike)은 판단을 보류하므로 분자·분모에서 뺀다 (사실이 없으면 None)
    - script_fidelity = 대본 내용 형태소가 같은 순서로 나온 정도 (`script_fidelity`). 비슷한 말 자리의 형태소는 양쪽에서 뺀다
    - similar_words / similar_numbers = 비율에서 뺀(아직 확인하지 않은) 비슷한 말의 개수. 사용자가 확인하면 `confirm.py` 에서 다시 계산한다
    """
    weight = sum(SCORE_WEIGHT[r.importance] for r in results)
    coverage = (
        sum(STATUS_SCORE[r.status] * SCORE_WEIGHT[r.importance] for r in results) / weight
        if weight
        else 0.0
    )
    counted = [c for c in fact_checks if c.status != "sound_alike"]
    fact_weight = sum(SCORE_WEIGHT[c.importance] for c in counted)
    fact_hit = sum(SCORE_WEIGHT[c.importance] * FACT_SCORE.get(c.status, 0.0) for c in counted)
    return {
        "content_coverage": round(max(coverage, 0.0), 3),
        "critical_fact_accuracy": round(fact_hit / fact_weight, 3) if fact_weight else None,
        "script_fidelity": fidelity["fidelity"],
        "similar_words": sum(it.kind == "word" for it in items),
        "similar_numbers": sum(it.kind == "number" for it in items),
        "_weights": {
            "key_points": weight,
            "facts": fact_weight,
            "script_tokens": fidelity.get("script_tokens", 0),
        },
    }


def take_scores(slide_evaluations: list) -> dict:
    """연습 한 번(발표 전체) 점수 = 슬라이드 점수의 가중 평균 (Key Point 가중치 / 사실 가중치 / 대본 형태소 수)."""

    def weighted(key: str, weight_key: str):
        pairs = [
            (e.scores[key], e.scores["_weights"][weight_key])
            for e in slide_evaluations
            if e.scores[key] is not None
        ]
        total = sum(w for _, w in pairs)
        return round(sum(v * w for v, w in pairs) / total, 3) if total else None

    return {
        "content_coverage": weighted("content_coverage", "key_points"),
        "critical_fact_accuracy": weighted("critical_fact_accuracy", "facts"),
        "script_fidelity": weighted("script_fidelity", "script_tokens"),
        "similar_words": sum(e.scores["similar_words"] for e in slide_evaluations),
        "similar_numbers": sum(e.scores["similar_numbers"] for e in slide_evaluations),
    }
