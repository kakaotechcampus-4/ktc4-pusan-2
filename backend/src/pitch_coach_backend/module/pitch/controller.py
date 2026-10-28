
import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, UploadFile, status
from sqlalchemy.orm import Session

from pitch_coach_backend.core.database import get_db
from pitch_coach_backend.module import hooks
from pitch_coach_backend.module.auth.dependencies import CurrentUser
from pitch_coach_backend.module.pitch.dependencies import OwnedPitch, ScriptParseRunnerDep
from pitch_coach_backend.module.pitch.dto import (
    ParseRequestedDTO,
    PitchSaveRequestDTO,
    PrepareDTO,
    ScriptCreatedDTO,
    ScriptCreateDTO,
    ScriptDetailDTO,
    StandardsSaveRequestDTO,
    StandardTextDTO,
    StandardTextResponseDTO,
    UploadPresentationDTO,
)
from pitch_coach_backend.module.pitch.service.pitch import (
    add_pitch_service,
    delete_pitch_files,
    delete_pitch_service,
    get_all_pitches_service,
    get_pitch_datas,
    get_prepare_service,
    pitch_timing,
    update_pitch_service,
)
from pitch_coach_backend.module.pitch.service.presentation import (
    get_presentation_detail,
    upload_presentation_service,
)
from pitch_coach_backend.module.pitch.service.script import (
    create_script_service,
    get_script_detail,
    request_reparse,
)
from pitch_coach_backend.module.pitch.service.standard import (
    add_pitch_standard_service,
    get_standard_detail,
    save_pitch_standards_service,
)

router = APIRouter(prefix="/pitches", tags=["Pitch"])

@router.get("/")
def get_pitches(
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)]
):
    all_pitches = get_all_pitches_service(db, current_user.id)
    return all_pitches


# 사이드바
# /api/pitches/{pitch_id}/resources로 변경
@router.get("/{pitch_id}/resources")
def get_pitch_summaries(
    pitch_id: OwnedPitch,
    db: Annotated[Session, Depends(get_db)]
):
    pitch_summaries = get_pitch_datas(db, pitch_id)
    return pitch_summaries

# 장치 점검 화면. 이번 연습에 쓸 버전 · 다음 Take 번호 · 지난번 대본 표시 방식
@router.get("/{pitch_id}/prepare", response_model=PrepareDTO)
def get_prepare(
    pitch_id: OwnedPitch,
    db: Annotated[Session, Depends(get_db)]
):
    return get_prepare_service(db, pitch_id)

@router.get("/{pitch_id}/presentations/{presentation_id}")
def get_presentation(
    
    pitch_id: OwnedPitch,
    presentation_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)]
):
    # 없으면 service 가 NonExistentPresentationVersion(404) 을 던진다
    return get_presentation_detail(db, pitch_id, presentation_id)

@router.post("/add")
def add_pitch(
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    pitch_dto: PitchSaveRequestDTO
):
    result = add_pitch_service(db, current_user.id, pitch_dto)
    return {"message": "Pitch added successfully", "pitch_id": result}

@router.patch("/update/{pitch_id}")
def update_pitch(
    pitch_id: OwnedPitch,
    db: Annotated[Session, Depends(get_db)],
    pitch_dto: PitchSaveRequestDTO,
):
    before = pitch_timing(db, pitch_id)
    result = update_pitch_service(db, pitch_id, pitch_dto)
    # 제한 시간 · 허용 범위가 바뀌면 다음 Take 의 코칭 계획을 다시 만든다 (coach-agent.md 5-1).
    # 백그라운드 작업이라 응답을 기다리게 하지 않는다
    if pitch_timing(db, pitch_id) != before:
        hooks.coaching().plan_next_take(pitch_id)
    return {"message": "Pitch updated successfully", "pitch_id": result}

@router.delete("/delete/{pitch_id}")
def delete_pitch(
    pitch_id: OwnedPitch,
    db: Annotated[Session, Depends(get_db)],
    background_tasks: BackgroundTasks,
):
    take_ids = delete_pitch_service(db, pitch_id)
    # 진행 중이던 Take 가 있으면 실시간 STT(Deepgram) · 코치도 끊는다
    hooks.live_takes().discard(take_ids)
    # 발표자료 파일은 CASCADE 로 지워지지 않는다. 응답을 늦추지 않게 보낸 뒤 지운다
    background_tasks.add_task(delete_pitch_files, pitch_id)
    return {"message": "Pitch deleted successfully", "pitch_id": pitch_id}

@router.post("/add/{pitch_id}/presentation")
def upload_presentation(
    pitch_id: OwnedPitch,
    db: Annotated[Session, Depends(get_db)],
    presentation_file: UploadFile
):
    upload_presentation_dto = UploadPresentationDTO(
        presentation_file=presentation_file
    )

    result = upload_presentation_service(db, pitch_id, upload_presentation_dto)

    return {
        "message": "Presentation uploaded successfully",
        "presentation": result
    }

@router.post("/add/{pitch_id}/standards", response_model=StandardTextResponseDTO)
def post_pitch_standard_text(
    pitch_id: OwnedPitch,
    db: Annotated[Session, Depends(get_db)],
    standard_text: StandardTextDTO
):
    return add_pitch_standard_service(db, pitch_id, standard_text)

# 사용자가 고친 표시 기준 저장. 다시 분석한 문장을 새 버전으로 저장한다
# (evaluation-criteria.md 12절)
@router.post("/{pitch_id}/standards", response_model=StandardTextResponseDTO)
def save_pitch_standards(
    pitch_id: OwnedPitch,
    db: Annotated[Session, Depends(get_db)],
    standards: StandardsSaveRequestDTO,
):
    return save_pitch_standards_service(db, pitch_id, standards)


# 대본 새 버전. 202: 원문 저장까지만 하고 돌려준다. 슬라이드 분리(AI)는 응답 뒤 백그라운드에서
# 돌고, FE 는 받은 script_version_id 로 GET /{pitch_id}/scripts/{id} 를 폴링한다
@router.post(
    "/{pitch_id}/scripts",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ScriptCreatedDTO,
)
def create_script(
    pitch_id: OwnedPitch,
    script_dto: ScriptCreateDTO,
    db: Annotated[Session, Depends(get_db)],
    background_tasks: BackgroundTasks,
    parse_runner: ScriptParseRunnerDep,
):
    result, parse_ticket = create_script_service(db, pitch_id, script_dto.content)
    # 커밋이 끝난 뒤에 예약된다. 작업이 PENDING 행을 못 찾는 일은 없다
    background_tasks.add_task(parse_runner.run, parse_ticket)
    return result


# 대본 파싱 상태 + 결과. FE 가 1초마다 부른다. DONE 이면 슬라이드까지 같이 나간다
@router.get("/{pitch_id}/scripts/{script_version_id}", response_model=ScriptDetailDTO)
def get_script(
    pitch_id: OwnedPitch,
    script_version_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
):
    return get_script_detail(db, pitch_id, script_version_id)


# FAILED 인 대본을 다시 파싱한다. 원문은 올릴 때 DB 에 저장한 것을 쓴다
@router.post(
    "/{pitch_id}/scripts/{script_version_id}/parse",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=ParseRequestedDTO,
)
def reparse_script(
    pitch_id: OwnedPitch,
    script_version_id: uuid.UUID,
    db: Annotated[Session, Depends(get_db)],
    background_tasks: BackgroundTasks,
    parse_runner: ScriptParseRunnerDep,
):
    result, parse_ticket = request_reparse(db, pitch_id, script_version_id)
    background_tasks.add_task(parse_runner.run, parse_ticket)
    return result

@router.get("/{pitch_id}/evaluations/{evaluation_version}")
def get_evaluation(
    pitch_id: OwnedPitch,
    evaluation_version: int,
    db: Annotated[Session, Depends(get_db)],
):
    evaluation = get_standard_detail(db, pitch_id, evaluation_version)
    return evaluation
