"""리포트가 읽는 대본 전달도 (`content_status` · 장별 상세).

대본 전달도 내용은 AI 가 낸 모양 그대로 전달한다. BE 는 상태를 정하고 확정본(`SCRIPT_COVERAGE`)의
`payload.detail` 을 싣거나 장별 채점에서 이름만 골라 싣는다. 병합 후보(`SCRIPT_COVERAGE_PENDING`)는
읽지 않는다.
"""

import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy.orm import Session

from pitch_coach_backend.module.take.dto import (
    ContentPreviousDTO,
    ContentStatusDTO,
    CoverageFactDTO,
    CoverageKeyPointDTO,
    CoverageSentenceDTO,
    CoverageSlideDTO,
    CoverageUnitDTO,
)
from pitch_coach_backend.module.take.entity import Take
from pitch_coach_backend.module.take.exception import (
    CoverageNotFound,
    CoverageRevisionChanged,
    CoverageSlideNotFound,
    NonExistentTake,
)
from pitch_coach_backend.module.take.repository import TakeRepository
from pitch_coach_backend.module.take.states import ANALYZING, FAILED, FINALIZING, SCORING

# 장별 상세에 싣는 채점 항목의 이름은 응답 DTO 의 필드 이름이다. 저장본에 없는 키는 null 로 채워
# 늘 둔다. 전달 단위(`units`)만 따로 골라 목록으로 채운다
_SENTENCE_KEYS = tuple(key for key in CoverageSentenceDTO.model_fields if key != "units")
_UNIT_KEYS = tuple(CoverageUnitDTO.model_fields)
_KEY_POINT_KEYS = tuple(CoverageKeyPointDTO.model_fields)
_FACT_KEYS = tuple(CoverageFactDTO.model_fields)


def _criteria_version(meta: Any) -> str | None:
    """확정본 `meta.criteria_versions.script_coverage`. 없거나 문자열이 아니면 None."""
    versions = meta.get("criteria_versions") if isinstance(meta, Mapping) else None
    version = versions.get("script_coverage") if isinstance(versions, Mapping) else None
    return version if isinstance(version, str) else None


def _previous(take_repository: TakeRepository, take: Take) -> ContentPreviousDTO | None:
    """바로 앞 Take 의 전달 · 다르게 말함 · 비교 범위. 그 Take 에 상세가 없으면 None 이다 —
    더 앞 Take 를 찾지 않는다."""
    row = take_repository.previous_coverage_summary(take.pitch_id, take.take_number)
    if row is None or not isinstance(row.detail, dict):
        return None
    return ContentPreviousDTO(
        take_id=row.take_id,
        take_number=row.take_number,
        criteria_version=_criteria_version(row.meta),
        delivery=row.detail.get("delivery"),
        contradiction=row.detail.get("contradiction"),
        scope=row.detail.get("scope"),
    )


def content_status(take_repository: TakeRepository, take: Take) -> ContentStatusDTO:
    """리포트의 대본 전달도 상태. 위에서부터 먼저 맞는 것이다.

    | 조건                                                        | state        | reason_code   |
    |-------------------------------------------------------------|--------------|---------------|
    | Take `FAILED`                                               | NOT_MEASURED | TAKE_FAILED   |
    | 확정본에 `payload.detail` 이 있음 (확인 대기 · 보류 중이어도) | MEASURED     | null          |
    | 확정본은 있지만 `payload.detail` 이 없음 (예전 결과)         | NOT_MEASURED | LEGACY_RESULT |
    | 확정본 없음 · `ANALYZING` 이고 `FINALIZING` · `SCORING`      | ANALYZING    | null          |
    | 확정본 없음 · 실패 이유(`coverage_failure_code`)가 있음      | FAILED       | 그 코드       |
    | 그 밖                                                       | NOT_MEASURED | NO_RESULT     |

    채점 중에는 앞 시도의 실패 이유가 남아 있어도 `ANALYZING` 이다 (전체 재시도 중).

    `previous` 도 `MEASURED` 일 때만 싣는다. FE 는 이번 Take 의 `criteria_version` 과 기능 버전이
    같을 때만 비교하는데 그 값이 없으면 쓸 데가 없다. 분석이 끝날 때까지 FE 가 리포트를 2초마다
    다시 읽으므로 그동안 앞 Take 를 읽지 않는다.
    """
    if take.status == FAILED:
        return ContentStatusDTO(state="NOT_MEASURED", reason_code="TAKE_FAILED")
    confirmed = take_repository.coverage_summary(take.id)
    if confirmed is not None:
        if not isinstance(confirmed.detail, dict):
            return ContentStatusDTO(state="NOT_MEASURED", reason_code="LEGACY_RESULT")
        return ContentStatusDTO(
            state="MEASURED",
            revision=confirmed.revision,
            criteria_version=_criteria_version(confirmed.meta),
            summary=confirmed.detail,
            previous=_previous(take_repository, take),
        )
    if take.status == ANALYZING and take.analysis_stage in (FINALIZING, SCORING):
        return ContentStatusDTO(state="ANALYZING")
    if take.coverage_failure_code is not None:
        return ContentStatusDTO(state="FAILED", reason_code=take.coverage_failure_code)
    return ContentStatusDTO(state="NOT_MEASURED", reason_code="NO_RESULT")


def _objects(value: Any) -> list[Mapping[str, Any]]:
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _pick(item: Mapping[str, Any], keys: Iterable[str]) -> dict[str, Any]:
    return {key: item.get(key) for key in keys}


def _sentence(item: Mapping[str, Any]) -> dict[str, Any]:
    units = [_pick(unit, _UNIT_KEYS) for unit in _objects(item.get("units"))]
    return {**_pick(item, _SENTENCE_KEYS), "units": units}


def slide_detail(
    db: Session,
    take_id: uuid.UUID,
    user_id: uuid.UUID,
    slide_number: int,
    revision: int | None,
) -> CoverageSlideDTO:
    """확정본에서 장 하나의 상세. FE 가 리포트에서 장을 펼칠 때 부른다.

    `revision` 을 주면 지금 확정본의 revision 과 같을 때만 답한다 — 리포트를 읽은 뒤 확인 답 ·
    다시 채점으로 확정본이 바뀌었으면 `CoverageRevisionChanged`(409) 다. 바뀐 확정본에서는 그 장이
    있는지도 달라지므로 장을 찾기 전에 본다.

    본문에 사용자 발화(`stt_sentences`)가 있다. 그 Take 의 주인만 읽고 로그에 남기지 않는다.
    """
    take_repository = TakeRepository(db)
    if take_repository.get_owned(take_id, user_id) is None:
        raise NonExistentTake()
    row = take_repository.coverage_slide(take_id, slide_number)
    if row is None:
        raise CoverageNotFound()
    if revision is not None and revision != row.revision:
        raise CoverageRevisionChanged()
    common = {"take_id": take_id, "revision": row.revision, "slide_number": slide_number}
    if isinstance(row.evaluation, Mapping):
        evaluation = row.evaluation
        stt_sentences = evaluation.get("stt_sentences")
        return CoverageSlideDTO(
            **common,
            state="SCORED",
            detail=evaluation.get("detail"),
            offscript=evaluation.get("offscript"),
            failure=None,
            sentences=[_sentence(item) for item in _objects(evaluation.get("sentences"))],
            key_points=[
                _pick(item, _KEY_POINT_KEYS) for item in _objects(evaluation.get("key_points"))
            ],
            critical_facts=[
                _pick(item, _FACT_KEYS) for item in _objects(evaluation.get("critical_facts"))
            ],
            stt_sentences=stt_sentences if isinstance(stt_sentences, list) else [],
        )
    if isinstance(row.failed, Mapping):
        fallback = row.failed.get("fallback")
        return CoverageSlideDTO(
            **common,
            state="FAILED" if fallback is None else "ESTIMATED",
            detail=None,
            offscript=None,
            failure={"error": row.failed.get("error"), "fallback": fallback},
            sentences=[],
            key_points=[],
            critical_facts=[],
            stt_sentences=[],
        )
    raise CoverageSlideNotFound()
