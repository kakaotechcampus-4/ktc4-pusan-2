"""Take AI 응답의 저장 가능 여부를 검사하고 FE 에 보일 값을 꺼낸다.

채점 payload는 받은 그대로 보존한다.
"""

import json
import uuid
from collections.abc import Mapping
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from pitch_coach_backend.core.validation import reject_unstorable_json
from pitch_coach_backend.module.take.dto import ReviewResponseDTO
from pitch_coach_backend.module.take.exception import InvalidReviewResponse

SCHEMA_MAJOR = "1"


class CoverageCallError(Exception):
    def __init__(self, reason: str, retryable: bool) -> None:
        super().__init__(reason)
        self.retryable = retryable


class _View(BaseModel):
    model_config = ConfigDict(strict=True, allow_inf_nan=False, extra="ignore")


Ratio = Annotated[float, Field(ge=0, le=1)]
Count = Annotated[int, Field(ge=0)]
SlideNumber = Annotated[int, Field(ge=1)]


class _Scores(_View):
    content_coverage: Ratio | None
    critical_fact_accuracy: Ratio | None
    script_fidelity: Ratio | None
    similar_words: Count
    similar_numbers: Count


class _TakeScores(_Scores):
    scored_ratio: Ratio


class _Evaluation(_View):
    slide_number: SlideNumber
    rubric_id: str
    scores: _Scores


class _Confirmation(_View):
    slide_number: SlideNumber
    item_id: str
    script_text: str
    stt_text: str


class _Error(_View):
    code: str
    message: str


class _Failed(_View):
    slide_number: SlideNumber
    error: _Error


class _Meta(_View):
    schema_version: str
    feature_version: str
    criteria_versions: dict[str, str]
    model: str | None = None


class _Coverage(_View):
    take_id: str
    evaluations: list[_Evaluation]
    take_scores: _TakeScores
    confirmations: list[_Confirmation]
    failed: list[_Failed]
    meta: _Meta


def _supported_schema(version: str) -> None:
    if version.split(".", 1)[0] != SCHEMA_MAJOR:
        raise ValueError("지원하지 않는 AI 응답 버전")


def _storable_json(reply: Any) -> None:
    reject_unstorable_json(reply)
    # JSON의 이스케이프된 고립 surrogate도 PostgreSQL JSONB에는 저장할 수 없다.
    json.dumps(reply, ensure_ascii=False, allow_nan=False).encode("utf-8")


def validate_coverage_reply(reply: Any, take_id: uuid.UUID | str) -> dict:
    try:
        view = _Coverage.model_validate(reply)
        _storable_json(reply)
        _supported_schema(view.meta.schema_version)
        if uuid.UUID(view.take_id) != uuid.UUID(str(take_id)):
            raise ValueError("다른 Take의 채점")
        numbers = [item.slide_number for item in (*view.evaluations, *view.failed)]
        if len(numbers) != len(set(numbers)):
            raise ValueError("장 번호가 중복된 채점")
    except (ValueError, TypeError) as exc:
        # 검증 예외에는 대본·전사가 있을 수 있어 값이나 본문을 메시지에 붙이지 않는다.
        raise CoverageCallError("invalid coverage response", retryable=False) from exc
    # 새 필드와 장별 채점은 rescore에 그대로 돌려보내므로 모델로 다시 직렬화하지 않는다.
    return reply


def _at(values: Any, index: Any) -> Any:
    if isinstance(index, bool) or not isinstance(index, int) or not isinstance(values, list):
        return None
    return values[index] if 0 <= index < len(values) else None


def _script_sentence(rubric: Mapping[str, Any] | None, index: Any) -> str | None:
    for sentence in (rubric or {}).get("sentences") or []:
        if isinstance(sentence, dict) and sentence.get("index") == index:
            text = sentence.get("text")
            return text if isinstance(text, str) else None
    return None


def confirmation_items(
    payload: Mapping[str, Any], rubrics: Mapping[int, Mapping[str, Any] | None]
) -> list[dict]:
    """확인 화면에 보일 항목 (`script-coverage-evaluation.md` 5-3 `confirmations[]`).

    같은 장 채점의 `similar_items[]` 에서 종류와 앞뒤 문장을 찾아 붙인다. 대본 문장은 평가 기준
    `sentences[].text`, 인식한 문장은 채점 `stt_sentences[]` 다. 찾지 못하면 null 로 둔다.
    """
    evaluations = {
        evaluation.get("slide_number"): evaluation
        for evaluation in payload.get("evaluations") or []
        if isinstance(evaluation, dict)
    }
    items = []
    for confirmation in payload.get("confirmations") or []:
        slide_number = confirmation["slide_number"]
        evaluation = evaluations.get(slide_number, {})
        similar = next(
            (
                item
                for item in evaluation.get("similar_items") or []
                if isinstance(item, dict) and item.get("item_id") == confirmation["item_id"]
            ),
            {},
        )
        stt_sentence = _at(evaluation.get("stt_sentences"), similar.get("stt_sentence_index"))
        items.append(
            {
                "slide_number": slide_number,
                "item_id": confirmation["item_id"],
                "kind": similar.get("kind") if isinstance(similar.get("kind"), str) else None,
                "script_text": confirmation["script_text"],
                "stt_text": confirmation["stt_text"],
                "script_sentence": _script_sentence(
                    rubrics.get(slide_number), similar.get("script_sentence_index")
                ),
                "stt_sentence": stt_sentence if isinstance(stt_sentence, str) else None,
            }
        )
    return items


def validate_review_reply(reply: Any, take_id: uuid.UUID) -> ReviewResponseDTO:
    try:
        view = ReviewResponseDTO.model_validate(reply)
        _storable_json(reply)
        _supported_schema(view.meta.schema_version)
        if view.take_id != take_id:
            raise ValueError("다른 Take의 리뷰")
    except (ValueError, TypeError) as exc:
        raise InvalidReviewResponse() from exc
    return view
