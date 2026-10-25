"""평가 기준 저장 · 나누기 · 조회. Take 에 고정한 버전의 기준도 여기서 읽는다."""

import asyncio
import uuid
from typing import Any

from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch.adapter.standard_parser import parse_standard_text
from pitch_coach_backend.module.pitch.dto import (
    StandardAIResponseDTO,
    StandardTextDTO,
    StandardTextResponseDTO,
)
from pitch_coach_backend.module.pitch.entity import StandardAnalyses, Standards
from pitch_coach_backend.module.pitch.exception import NonExistentEvaluation, NonExistentPitch
from pitch_coach_backend.module.pitch.repository import PitchRepository


def add_pitch_standard_service(
    db: Session, pitch_id: uuid.UUID, standard_text_dto: StandardTextDTO
):
    # 인가 조회가 빌린 커넥션부터 반환한다. AI 응답을 기다릴 때 DB 트랜잭션을 두지 않는다.
    db.commit()
    standards_result = divide_standard_text(standard_text_dto)

    pitch_repository = PitchRepository(db)
    if not pitch_repository.lock_for_new_version(pitch_id):
        raise NonExistentPitch()
    version = pitch_repository.next_standard_version(pitch_id)
    for position, title in enumerate(standards_result.display_criteria, start=1):
        pitch_repository.save_standard(
            Standards(
                pitch_id=pitch_id, version=version, position=position, title=title
            )
        )
    pitch_repository.save_standard_analysis(
        StandardAnalyses(
            pitch_id=pitch_id,
            version=version,
            values=standards_result.values,
            targets=standards_result.targets,
            script_used=standards_result.values.get("script_used"),
            excluded=standards_result.excluded or [],
            meta=standards_result.meta,
        )
    )
    db.commit()

    return StandardTextResponseDTO(
        pitch_id=pitch_id,
        standards=[
            {"standard": standard} for standard in standards_result.display_criteria
        ],
        except_standard=", ".join(standards_result.excluded or []) or None,
    )


def divide_standard_text(standard_text_dto: StandardTextDTO) -> StandardAIResponseDTO:
    """동기 HTTP 작업 스레드에서 전체 시간 제한이 있는 AI 어댑터를 부른다."""
    return asyncio.run(parse_standard_text(standard_text_dto.standard_text))


def get_standard_detail(db: Session, pitch_id: uuid.UUID, evaluation_version: int):
    pitch_repository = PitchRepository(db)
    standards = pitch_repository.get_evaluations_by_version(
        pitch_id, evaluation_version
    )
    if (
        not standards
        and pitch_repository.get_standard_analysis(pitch_id, evaluation_version) is None
    ):
        raise NonExistentEvaluation()

    return {
        "pitch_id": pitch_id,
        "evaluation_version": evaluation_version,
        "evaluations": [
            {"order": standards[s].position, "standard": standards[s].title}
            for s in range(len(standards))
        ],
    }


def latest_analysis_version(db: Session, pitch_id: uuid.UUID) -> int | None:
    """분석까지 끝난 평가 기준의 가장 최근 버전. Take 를 시작할 때 그 Take 에 고정한다."""
    return PitchRepository(db).get_latest_analysis_version(pitch_id)


def script_used(db: Session, pitch_id: uuid.UUID, version: int | None) -> bool | None:
    """그 버전의 평가 기준이 대본을 쓰는가. 버전이 없으면 None."""
    if version is None:
        return None
    return PitchRepository(db).get_script_used(pitch_id, version)


def criteria_for_version(
    db: Session, pitch_id: uuid.UUID, version: int | None
) -> dict[str, Any] | None:
    """Take 에 고정한 버전의 표시 기준 · 숫자 목표. null 은 끝까지 '기준 없음'이다."""
    if version is None:
        return None
    repository = PitchRepository(db)
    analysis = repository.get_standard_analysis(pitch_id, version)
    return {
        "display_criteria": [
            s.title for s in repository.get_standards_in_version(pitch_id, version)
        ],
        "targets": analysis.targets if analysis is not None else [],
        "script_used": analysis.script_used if analysis is not None else None,
    }
