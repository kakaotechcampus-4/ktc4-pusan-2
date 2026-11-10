"""대본 전달도 상세 계약(#1152)의 응답 예시. 예시의 `take-123` 대신 실제 Take UUID 를 쓴다.

기존 채점 모양(`tests/test_take_ai_validation.coverage`)에 장 상세 · 대본 밖 구간 · 간이 값 ·
Take 상세를 붙였다.
"""

import copy
import uuid
from typing import Any

CRITERIA_VERSION = "script_coverage-1.2+abc123"
META = {
    "schema_version": "1.0",
    "feature_version": "script_coverage-1.2",
    "criteria_versions": {"script_coverage": CRITERIA_VERSION},
    "model": "fake-model",
}


def _metric(value: float | None, state: str, *reasons: str, evaluated=0, excluded=0) -> dict:
    return {
        "value": value,
        "state": state,
        "reason_codes": list(reasons),
        "evaluated": evaluated,
        "excluded": excluded,
    }


def _counts() -> dict[str, int]:
    return {"covered": 0, "partial": 0, "missing": 0, "contradicted": 0}


FALLBACK = {
    "version": 1,
    "method": "lexical",
    "delivery_band": "mostly",
    "lexical_coverage": 0.82,
    "facts": None,
    "fact_accuracy": None,
    "script_recall": _metric(0.7, "estimated", evaluated=30),
    "speech_mix": None,
    "stt_integrity": "unknown",
    "reason_codes": ["LLM_TIMEOUT"],
}

# '모든 대상 장이 실패해도' 예시의 맨 위 detail
ALL_FAILED_DETAIL = {
    "version": 1,
    "delivery": _metric(None, "failed", "SLIDES_EXCLUDED", excluded=4),
    "zero_kind": None,
    "contradiction": _metric(None, "failed", "SLIDES_EXCLUDED", excluded=4),
    "status_shares": None,
    "key_points": {"critical": _counts(), "high": _counts(), "normal": _counts()},
    "critical_gaps": [],
    "legacy": {"content_coverage_raw": None},
    "facts": {
        "matched": 0,
        "approximate": 0,
        "mismatched": 0,
        "missing": 0,
        "pending": 0,
        "accuracy": _metric(None, "failed", "SLIDES_EXCLUDED"),
        "accuracy_range": None,
    },
    "expression": {"verbatim_like": 0, "paraphrased": 0, "undetermined": 0},
    "fidelity": {
        "recall": _metric(None, "failed", "SLIDES_EXCLUDED"),
        "precision": _metric(None, "failed", "SLIDES_EXCLUDED"),
    },
    "speech_mix": {
        "state": "failed",
        "reason_codes": ["SLIDES_EXCLUDED"],
        "tokens": 0,
        "categories": dict.fromkeys(
            (
                "script",
                "other_slide",
                "procedural",
                "off_script",
                "related_aside",
                "unrelated",
                "undetermined",
            ),
            0,
        ),
        "display": [],
        "repetitions": {"count": 0, "confidence": "low"},
    },
    "scope": {
        "target_slides": 1,
        "scored_slides": 0,
        "estimated_slides": 1,
        "provisional_slides": 0,
        "excluded": [{"slide_number": 1, "reason_code": "LLM_TIMEOUT"}],
    },
    "slides": [
        {
            "slide_number": 1,
            "state": "estimated",
            "reason_codes": ["LLM_TIMEOUT"],
            "stt_integrity": "unknown",
            "rubric_quality": "full",
            "delivery": None,
            "zero_kind": None,
            "contradiction": None,
            "status_shares": None,
            "critical_gap_count": 0,
            "delivery_band": "mostly",
            "speech_mix": None,
        }
    ],
}


def all_failed(take_id: uuid.UUID) -> dict[str, Any]:
    """모든 대상 장이 실패했지만 간이 값이 있어 200 으로 온 응답 (#1152 예시 그대로)."""
    return {
        "take_id": str(take_id),
        "evaluations": [],
        "take_scores": {
            "content_coverage": None,
            "critical_fact_accuracy": None,
            "script_fidelity": None,
            "similar_words": 0,
            "similar_numbers": 0,
            "scored_ratio": 0.0,
        },
        "confirmations": [],
        "failed": [
            {
                "slide_number": 1,
                "error": {"code": "LLM_TIMEOUT", "message": "LLM 응답 시간이 초과되었습니다"},
                "fallback": copy.deepcopy(FALLBACK),
            }
        ],
        "detail": copy.deepcopy(ALL_FAILED_DETAIL),
        "meta": copy.deepcopy(META),
    }


STT_INCOMPLETE_SLIDES = (4, 6, 7)


def _stt_incomplete_slide(slide_number: int) -> dict[str, Any]:
    return {
        "slide_number": slide_number,
        "state": "failed",
        "reason_codes": ["STT_INCOMPLETE"],
        "stt_integrity": "incomplete",
        "rubric_quality": "full",
        "delivery": None,
        "zero_kind": None,
        "contradiction": None,
        "status_shares": None,
        "critical_gap_count": 0,
        "delivery_band": None,
        "speech_mix": None,
    }


def all_stt_incomplete(take_id: uuid.UUID) -> dict[str, Any]:
    """대상 장이 모두 STT 끊김이라 채점도 간이 값도 없이 200 으로 온 응답 (#1158).

    AI 의 `evaluate_take` 에 4 · 6 · 7장 STT 를 비우고 끊김 시간을 준 요청을 넣어 얻은 모양이다.
    `ALL_FAILED_DETAIL` 과는 전달 · 다르게 말함 · 비교 범위 · 장 상세만 다르다. LLM 을 부르지 않아
    `meta.model` 이 null 이다.
    """
    numbers = STT_INCOMPLETE_SLIDES
    excluded = len(numbers)
    detail = copy.deepcopy(ALL_FAILED_DETAIL)
    detail.update(
        delivery=_metric(None, "failed", "SLIDES_EXCLUDED", excluded=excluded),
        contradiction=_metric(None, "failed", "SLIDES_EXCLUDED", excluded=excluded),
        scope={
            "target_slides": excluded,
            "scored_slides": 0,
            "estimated_slides": 0,
            "provisional_slides": 0,
            "excluded": [{"slide_number": n, "reason_code": "STT_INCOMPLETE"} for n in numbers],
        },
        slides=[_stt_incomplete_slide(n) for n in numbers],
    )
    return {
        "take_id": str(take_id),
        "evaluations": [],
        "take_scores": {
            "content_coverage": None,
            "critical_fact_accuracy": None,
            "script_fidelity": None,
            "similar_words": 0,
            "similar_numbers": 0,
            "scored_ratio": 0.0,
        },
        "confirmations": [],
        "failed": [
            {
                "slide_number": n,
                "error": {
                    "code": "STT_INCOMPLETE",
                    "message": f"{n}번 장: 음성 인식이 끊겨 이 장을 채점하지 않았습니다.",
                },
                "fallback": None,
            }
            for n in numbers
        ],
        "detail": detail,
        "meta": {**copy.deepcopy(META), "model": None},
    }


SLIDE_DETAIL = {
    "version": 1,
    "state": "ok",
    "reason_codes": [],
    "stt_integrity": "complete",
    "rubric_quality": "full",
    "delivery": _metric(1.0, "ok", evaluated=1),
    "zero_kind": None,
}
OFFSCRIPT = {
    "version": 1,
    "state": "ok",
    "reason_codes": [],
    "spans": [
        {
            "id": "O1",
            "stt_sentence_index": 0,
            "start": 0,
            "end": 5,
            "category": "procedural",
            "display_category": "procedural",
            "source": "rule",
            "other_slide_number": None,
            "tokens": 2,
        }
    ],
}
TAKE_DETAIL = {
    "version": 1,
    "delivery": _metric(0.5, "provisional", "SLIDES_EXCLUDED", evaluated=3, excluded=4),
    "contradiction": _metric(0.333, "provisional", "SLIDES_EXCLUDED", evaluated=3, excluded=4),
    "scope": {
        "target_slides": 4,
        "scored_slides": 1,
        "estimated_slides": 1,
        "provisional_slides": 0,
        "excluded": [
            {"slide_number": 4, "reason_code": "LLM_TIMEOUT"},
            {"slide_number": 5, "reason_code": "STT_INCOMPLETE"},
            {"slide_number": 6, "reason_code": "RUBRIC_MISSING"},
        ],
    },
    "slides": [{"slide_number": 3, "state": "scored"}],
}


def scored(take_id: uuid.UUID) -> dict[str, Any]:
    """3장 채점 · 4장 간이 값 · 5장 간이 값 없음 · 6장 예전 실패 항목(`fallback` 키 없음)."""
    scores = {
        "content_coverage": 0.81,
        "critical_fact_accuracy": 1.0,
        "script_fidelity": 0.74,
        "similar_words": 0,
        "similar_numbers": 0,
    }
    return {
        "take_id": str(take_id),
        "evaluations": [
            {
                "slide_number": 3,
                "rubric_id": "rubric-3",
                "scores": scores,
                "stt_integrity": "complete",
                "sentences": [
                    {
                        "sentence_index": 0,
                        "text": "지난 분기 매출은 37% 늘었습니다.",
                        "role": "claim",
                        "status": "said",
                        "first_status": "said",
                        "expression_mode": "paraphrased",
                        "reason": "같은 뜻으로 말했다",
                        "evidence": [0],
                        "confidence": 0.9,
                        "units": [
                            {
                                "unit_id": "S0-U1",
                                "text": "매출 37% 증가",
                                "fact_ids": ["CF1"],
                                "status": "said",
                                "first_status": "said",
                                "raised_by_rule": False,
                            }
                        ],
                    },
                    # 예전 결과: 표현 구분 · 전달 단위가 없다
                    {
                        "sentence_index": 1,
                        "text": "감사합니다.",
                        "role": "closing",
                        "reason": None,
                        "evidence": [],
                    },
                ],
                "key_points": [
                    {
                        "key_point_id": "KP1",
                        "content": "지난 분기 매출 37% 증가",
                        "importance": "critical",
                        "sentence_indices": [0],
                        "status": "covered",
                        "reason": "근거 문장에서 말했다",
                        "evidence": [0],
                        "evidence_text": "지난 분기 매출은 삼십칠 퍼센트 늘었는데요",
                    }
                ],
                "critical_facts": [
                    {
                        "fact_id": "CF1",
                        "type": "percentage",
                        "value": "37%",
                        "normalized": "37%",
                        "stt_value": "37%",
                        "status": "matched",
                        "stt_ids": [0],
                    }
                ],
                "similar_items": [],
                "verification": {},
                "stt_sentences": ["안녕하세요 지난 분기 매출은 삼십칠 퍼센트 늘었는데요"],
                "stt_text": "안녕하세요 지난 분기 매출은 삼십칠 퍼센트 늘었는데요",
                "applied_answers": {},
                "detail": copy.deepcopy(SLIDE_DETAIL),
                "offscript": copy.deepcopy(OFFSCRIPT),
            }
        ],
        "take_scores": {**scores, "scored_ratio": 0.25},
        "confirmations": [],
        "failed": [
            {
                "slide_number": 4,
                "error": {"code": "LLM_TIMEOUT", "message": "LLM 응답 시간이 초과되었습니다"},
                "fallback": copy.deepcopy(FALLBACK),
            },
            {
                "slide_number": 5,
                "error": {"code": "STT_INCOMPLETE", "message": "음성 인식이 끊겼습니다"},
                "fallback": None,
            },
            {
                "slide_number": 6,
                "error": {"code": "RUBRIC_MISSING", "message": "평가 기준 없음"},
            },
        ],
        "detail": copy.deepcopy(TAKE_DETAIL),
        "meta": copy.deepcopy(META),
    }
