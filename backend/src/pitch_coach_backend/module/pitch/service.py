import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pitch_coach_backend.module.take.dto import TakeSummaryDTO
from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch.dto import (
    AllPitchesDTO,
    HighlightDTO,
    ParseRequestedDTO,
    ParseTicket,
    PitchDTO,
    PitchesDTO,
    PresentationDetailDTO,
    ScriptCreatedDTO,
    ScriptDetailDTO,
    ScriptParseErrorCode,
    ScriptSlideDTO,
    UploadPresentationResultDTO,
    VersionDTO,
    VersionSummaryDTO,
    Versioned,
)
from pitch_coach_backend.module.pitch.entity import (
    Pitch,
    PresentationVersion,
    ScriptParseStatus,
    ScriptSlide,
    ScriptVersion,
)
from pitch_coach_backend.module.pitch.exception import (
    InvalidScript,
    NonExistentPitch,
    NonExistentScript,
    ScriptAlreadyParsed,
    ScriptParseInProgress,
    ScriptReuploadRequired,
)
from pitch_coach_backend.module.pitch.repository import PitchRepository
from pitch_coach_backend.module.pitch.s3_service import generate_presigned_url, upload
from pitch_coach_backend.module.pitch.script_parser import ParsedScript
from typing import Iterable

# 대본 한 편의 글자 수 상한. 1시간 발표도 2만 자 안팎이라 넉넉하고, LLM 한 번에 넣을 수 있는 크기다
MAX_SCRIPT_CHARS = 50_000
# 요청 후 이만큼 지나도 PENDING 이면 만료(FAILED)로 본다. BE 가 재시작되면 백그라운드 작업이
# 사라져 PENDING 이 영원히 남기 때문이다. 작업의 최악 소요(script_parser 의 타임아웃 × 재시도,
# 약 71초)보다 길어야 한다 — 짧으면 아직 도는 작업을 만료로 보여주고 재시도를 열어 버린다
PARSE_EXPIRE_AFTER = timedelta(seconds=90)

def add_pitch_service(db: Session, user_id: uuid.UUID, pitch_dto: PitchDTO):
    new_pitch = Pitch(
        user_id=user_id,
        title=pitch_dto.title,
        time_limit_sec=pitch_dto.time_limit_sec,
        presentation_date=pitch_dto.presentation_date,
        upper_deviation=pitch_dto.upper_deviation,
        lower_deviation=pitch_dto.lower_deviation
    )

    pitch_repository = PitchRepository(db)
    saved_pitch = pitch_repository.save(new_pitch)
    db.commit()

    return saved_pitch.id

def get_pitch_service(db: Session, pitch_id: uuid.UUID):
    pitch_repository = PitchRepository(db)
    existing_pitch = pitch_repository.get_by_id(pitch_id)

    if not existing_pitch:
        raise NonExistentPitch()

    return existing_pitch

# 홈 화면 : pitches 목록 조회
def get_all_pitches_service(db: Session, user_id: uuid.UUID) -> AllPitchesDTO:
    pitch_repository = PitchRepository(db)
    pitches = pitch_repository.get_all_by_user(user_id)

    rows_by_pitch = defaultdict(list)
    for row in pitch_repository.get_takes_with_scores_in_pitches([p.id for p in pitches]):
        rows_by_pitch[row.Take.pitch_id].append(row)

    results = []
    for pitch in pitches:
        take_summaries = []
        # Take, Score로 이루어진 row.
        takes = rows_by_pitch[pitch.id]

        for t in range(len(takes)):
            take = takes[t].Take
            take_score = takes[t].score
            d = None

            if t > 0:
                previous_take_score = takes[t - 1].score
                if take_score is not None and previous_take_score is not None:
                    d = take_score - previous_take_score

            take_summary = TakeSummaryDTO(
                take_id=take.id,
                take_version=take.take_number,
                take_elapsed=take.duration_sec,
                take_time=take.goal_time_sec,
                script_mode=take.script_mode,
                score=take_score,
                delta=d,
                created_at = take.created_at
            )

            take_summaries.append(take_summary)

        pitch_dto = PitchesDTO(
            pitch_id=pitch.id,
            pitch_title=pitch.title,
            pitch_time=pitch.time_limit_sec,
            thumbnail_url=None,
            pitch_deadline=pitch.presentation_date,
            takes=take_summaries
        )

        results.append(pitch_dto)

    return AllPitchesDTO(pitches=results)

# Protocol(해당 타입만 가지고 있다면 Versioned 타입으로 간주)로 통일
# Versioned 타입을 가진 객체들을 VersionDTO로 변환
def to_version_dtos(rows: Iterable[Versioned]) -> list[VersionDTO]:
    return [VersionDTO(id=row.id, version=row.version) for row in rows]


# 존재 확인
def ensure_pitch_exists(pitch_repository: PitchRepository, pitch_id: uuid.UUID) -> None:
    if not pitch_repository.get_by_id(pitch_id):
        raise NonExistentPitch()


# 발표 자료 버전 들고오기
def get_pitch_datas(db: Session, pitch_id: uuid.UUID):
    pitch_repository = PitchRepository(db)

    return VersionSummaryDTO(
        pitch_id=pitch_id,
        presentation_versions=to_version_dtos(pitch_repository.get_presentations(pitch_id)),
        script_versions=to_version_dtos(pitch_repository.get_scripts(pitch_id)),
        evaluation_versions=to_version_dtos(pitch_repository.get_evaluations(pitch_id)),
    )

def update_pitch_service(db: Session, pitch_id: uuid.UUID, pitch_dto):
    pitch_repository = PitchRepository(db)
    existing_pitch = pitch_repository.get_by_id(pitch_id)

    if not existing_pitch:
        raise NonExistentPitch()

    existing_pitch.title = pitch_dto.title
    existing_pitch.time_limit_sec = pitch_dto.time_limit_sec
    existing_pitch.presentation_date = pitch_dto.presentation_date

    updated_pitch = pitch_repository.save(existing_pitch)
    db.commit()

    return updated_pitch.id

def delete_pitch_service(db: Session, pitch_id: uuid.UUID):
    pitch_repository = PitchRepository(db)
    existing_pitch = pitch_repository.get_by_id(pitch_id)

    if not existing_pitch:
        raise NonExistentPitch()

    pitch_repository.delete(existing_pitch)
    db.commit()

    return pitch_id

def upload_presentation_service(db: Session, pitch_id: uuid.UUID, upload_dto):
    pitch_repository = PitchRepository(db)
    # 같은 pitch 에 동시에 올려도 같은 버전 번호를 받지 않게 커밋까지 줄을 세운다.
    # 번호가 S3 키에도 들어가서, 잠그지 않으면 500 에 더해 앞서 올린 파일이 덮어쓰인다
    pitch_repository.lock_for_new_version(pitch_id)

    version = pitch_repository.next_presentation_version(pitch_id)
    suffix = Path(upload_dto.presentation_file.filename or "").suffix
    presentation_key = upload(
        upload_dto.presentation_file,
        f"pitches/{pitch_id}/presentations/{version}{suffix}"
    )

    presentation = PresentationVersion(
        pitch_id=pitch_id,
        version=version,
        file_key=presentation_key
    )

    pitch_repository.save_presentation(presentation)
    db.commit()

    return UploadPresentationResultDTO(
        pitch_id=pitch_id,
        presentation_version_id=presentation.id,
        file_url = generate_presigned_url(presentation_key)
    )

def validate_script_text(content: str) -> str:
    """대본 원문을 검사한다. 통과하면 그대로 돌려준다 (다듬지 않는다 — 원문 그대로 보관)."""
    if not content.strip():
        raise InvalidScript("대본이 비어 있습니다.")
    if len(content) > MAX_SCRIPT_CHARS:
        raise InvalidScript(f"대본은 {MAX_SCRIPT_CHARS:,}자까지 올릴 수 있습니다.")
    # JSON 은 "\u0000" 을 허용하지만 PostgreSQL TEXT 는 NUL 을 넣지 못해 커밋에서 500 이 난다
    if "\x00" in content:
        raise InvalidScript("대본에 쓸 수 없는 문자(NUL)가 있습니다.")
    return content


def create_script_service(
    db: Session, pitch_id: uuid.UUID, content: str
) -> tuple[ScriptCreatedDTO, ParseTicket]:
    """대본 새 버전을 만들고 PENDING 으로 커밋한다. 슬라이드 분리는 응답 뒤 백그라운드에서 한다.

    버전은 pitch 안에서 max(version) + 1 로 자동으로 매긴다. 발표자료 버전과는 따로 센다.
    """
    content = validate_script_text(content)
    pitch_repository = PitchRepository(db)
    # 같은 pitch 에 동시에 올려도 같은 버전 번호를 받지 않게 커밋까지 줄을 세운다
    pitch_repository.lock_for_new_version(pitch_id)

    script = ScriptVersion(
        pitch_id=pitch_id,
        version=pitch_repository.next_script_version(pitch_id),
        # 원문은 DB 에 둔다 (S3 에 올리지 않는다 — entity 설명 참고)
        content=content,
        parse_status=ScriptParseStatus.PENDING,
        parse_requested_at=datetime.now(UTC),
    )
    pitch_repository.save_script(script)
    db.commit()

    created = ScriptCreatedDTO(
        script_version_id=script.id,
        version=script.version,
        parse_status=ScriptParseStatus.PENDING,
    )
    ticket = ParseTicket(
        script_version_id=script.id,
        requested_at=script.parse_requested_at,
        script_text=content,
    )
    return created, ticket


# ── 대본 파싱 상태 ──────────────────────────────────────────────────────
# 백그라운드 작업(script_parse_runner)이 AI 를 기다리는 동안에는 DB 세션을 쥐고 있지 않는다.
# 아래 complete/fail 은 AI 응답을 받은 뒤 짧은 세션으로 한 번에 끝낸다.


def _effective_status(
    script: ScriptVersion, now: datetime
) -> tuple[ScriptParseStatus, ScriptParseErrorCode | None]:
    """DB 상태에 만료를 얹은 값. 오래된 PENDING 은 FAILED(PARSE_EXPIRED) 로 보여준다.

    DB 에 쓰지 않고 읽을 때 계산한다 — 조회(GET)가 상태를 바꾸지 않고, 만료를 쓰는 별도
    정리 작업도 필요 없다. 재시도(restart_parse)도 같은 기준으로 만료를 판단한다.
    """
    status = ScriptParseStatus(script.parse_status)
    if status is ScriptParseStatus.PENDING and script.parse_requested_at < now - PARSE_EXPIRE_AFTER:
        return ScriptParseStatus.FAILED, ScriptParseErrorCode.PARSE_EXPIRED
    return status, _error_code(script.parse_error)


def _error_code(value: str | None) -> ScriptParseErrorCode | None:
    # 모르는 값이 DB 에 있어도(코드를 되돌린 뒤 등) 조회가 500 이 되지 않게 한다
    if value is None:
        return None
    try:
        return ScriptParseErrorCode(value)
    except ValueError:
        return ScriptParseErrorCode.INTERNAL_ERROR


def _get_script_or_raise(
    pitch_repository: PitchRepository, pitch_id: uuid.UUID, script_version_id: uuid.UUID
) -> ScriptVersion:
    # pitch 소유는 controller 의 OwnedPitch 가 확인했다. 여기서는 그 pitch 의 대본인지만 본다
    script = pitch_repository.get_script_in_pitch(pitch_id, script_version_id)
    if script is None:
        raise NonExistentScript()
    return script


def get_script_detail(
    db: Session, pitch_id: uuid.UUID, script_version_id: uuid.UUID
) -> ScriptDetailDTO:
    pitch_repository = PitchRepository(db)
    script = _get_script_or_raise(pitch_repository, pitch_id, script_version_id)
    status, error = _effective_status(script, datetime.now(UTC))

    slides: list[ScriptSlideDTO] = []
    if status is ScriptParseStatus.DONE:
        slides = [
            ScriptSlideDTO(
                slide_number=slide.slide_number,
                content=slide.full_content,
                keywords=slide.keywords or [],
                highlights=[
                    HighlightDTO(start=start, end=end) for start, end in slide.highlights or []
                ],
            )
            for slide in pitch_repository.get_slides(script.id)
        ]

    return ScriptDetailDTO(
        script_version_id=script.id,
        version=script.version,
        original_content=script.content,
        parse_status=status,
        segmented=script.segmented if status is ScriptParseStatus.DONE else None,
        slides=slides,
        terms=(script.terms or []) if status is ScriptParseStatus.DONE else [],
        error_code=error,
    )


def request_reparse(
    db: Session, pitch_id: uuid.UUID, script_version_id: uuid.UUID
) -> tuple[ParseRequestedDTO, ParseTicket]:
    """FAILED(만료 포함) 대본을 다시 PENDING 으로 돌리고 작업 표를 만든다."""
    pitch_repository = PitchRepository(db)
    script = _get_script_or_raise(pitch_repository, pitch_id, script_version_id)
    now = datetime.now(UTC)

    status, _ = _effective_status(script, now)
    if status is ScriptParseStatus.DONE:
        raise ScriptAlreadyParsed()
    if status is ScriptParseStatus.PENDING:
        raise ScriptParseInProgress()

    # 원문은 올릴 때 검사해 DB 에 둔 것을 그대로 쓴다.
    # 파싱 기능 전에 올라온 대본은 원문이 S3 파일로만 있어(.docx 도 있음) 다시 올려야 한다
    if script.content is None:
        raise ScriptReuploadRequired()

    # 위 검사는 빠른 거절용이고, 실제 판정은 이 조건부 UPDATE 다.
    # 버튼을 두 번 눌러 요청 둘이 동시에 여기까지 와도 하나만 1행을 바꾼다
    if not pitch_repository.restart_parse(
        script.id, now, expired_before=now - PARSE_EXPIRE_AFTER
    ):
        # 진 이유를 다시 읽어 답한다:
        # 다른 재시도가 먼저 PENDING 으로 바꿨거나, 그 사이 작업이 끝났다
        db.rollback()
        db.refresh(script)
        if script.parse_status == ScriptParseStatus.DONE:
            raise ScriptAlreadyParsed()
        raise ScriptParseInProgress()
    db.commit()

    return (
        ParseRequestedDTO(script_version_id=script.id, parse_status=ScriptParseStatus.PENDING),
        ParseTicket(script_version_id=script.id, requested_at=now, script_text=script.content),
    )


def complete_parse(db: Session, ticket: ParseTicket, parsed: ParsedScript) -> bool:
    """AI 결과를 저장하고 DONE. 이 작업이 이미 무효(재시도로 교체·만료 후 재시작)면 False."""
    pitch_repository = PitchRepository(db)

    # 상태 UPDATE 를 먼저 한다. 이기면 이 행이 잠겨서 슬라이드를 넣는 동안 다른 작업이 못 끼어든다.
    # PENDING → DONE 은 이 한 번뿐이고 DONE 은 다시 파싱하지 않으므로,
    # 넣기 전에 지울 슬라이드는 없다
    if not pitch_repository.mark_parse_done(
        ticket.script_version_id,
        ticket.requested_at,
        expired_before=datetime.now(UTC) - PARSE_EXPIRE_AFTER,
        segmented=parsed.segmented,
        terms=list(parsed.terms),
    ):
        db.rollback()
        return False

    pitch_repository.add_slides(
        [
            ScriptSlide(
                script_version_id=ticket.script_version_id,
                slide_number=slide.slide_number,
                full_content=slide.content,
                keywords=list(slide.keywords),
                highlights=[list(span) for span in slide.highlights],
            )
            for slide in parsed.slides
        ],
    )
    db.commit()
    return True


def fail_parse(db: Session, ticket: ParseTicket, error_code: ScriptParseErrorCode) -> bool:
    """FAILED 로 바꾼다. 이 작업이 이미 무효면 아무것도 안 하고 False."""
    pitch_repository = PitchRepository(db)
    if not pitch_repository.mark_parse_failed(
        ticket.script_version_id,
        ticket.requested_at,
        error_code,
        expired_before=datetime.now(UTC) - PARSE_EXPIRE_AFTER,
    ):
        db.rollback()
        return False
    db.commit()
    return True

def get_presentation_detail(db: Session, pitch_id: uuid.UUID, presentation_version_id: uuid.UUID):
    pitch_repository = PitchRepository(db)
    presentation_version = pitch_repository.get_presentation_detail(pitch_id, presentation_version_id)

    if not presentation_version:
        raise NonExistentPitch()

    return PresentationDetailDTO(
        pitch_id=pitch_id,
        presentation_version_id=presentation_version.id,
        version=presentation_version.version,
        file_url=generate_presigned_url(presentation_version.file_key),
        description=presentation_version.description,
        created_at=presentation_version.created_at.date()
    )
def add_pitch_standard_service(db: Session, pitch_id: uuid.UUID, standard_text_dto):
    pitch_repository = PitchRepository(db)
    # 평가 기준 분할 로직
    # standards_result =

    # for standard in standards_result.standards:
    #    pitch_repository.save_standard(pitch_id, standard)

    # return StandardTextResponseDTO(
    #     pitch_id=pitch_id,
    #     standards=[{"standard": standard} for standard in standards_result.standards],
    #     except_standard=standards_result.except_standard
    # )
