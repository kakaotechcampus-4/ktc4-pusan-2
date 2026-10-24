import logging
import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import httpx2
from sqlalchemy.orm import Session

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.module.pitch.dto import (
    HighlightDTO,
    NewerScriptDTO,
    ParsedScriptDTO,
    ParseRequestedDTO,
    ParseTicket,
    PitchListItemDTO,
    PitchListResponseDTO,
    PitchSaveRequestDTO,
    PrepareDTO,
    PresentationDetailDTO,
    PresentationVersionDTO,
    ScriptCreatedDTO,
    ScriptDetailDTO,
    ScriptParseErrorCode,
    ScriptSlideDTO,
    StandardParseResponseDTO,
    StandardTextDTO,
    StandardTextResponseDTO,
    UploadPresentationResultDTO,
    VersionDTO,
    Versioned,
    VersionSummaryDTO,
)
from pitch_coach_backend.module.pitch.entity import (
    CoachingPlan,
    Pitch,
    PresentationVersion,
    ScriptParseStatus,
    ScriptSlide,
    ScriptVersion,
    Standards,
)
from pitch_coach_backend.module.pitch.exception import (
    InvalidPresentation,
    InvalidScript,
    NonExistentPitch,
    NonExistentPresentationVersion,
    NonExistentScript,
    ScriptAlreadyParsed,
    ScriptParseInProgress,
    ScriptReuploadRequired,
    StandardParseFailed,
)
from pitch_coach_backend.module.pitch.presentation_file import (
    MAX_PRESENTATION_BYTES,
    count_pdf_pages,
    file_size,
)
from pitch_coach_backend.module.pitch.repository import PitchRepository
from pitch_coach_backend.module.pitch.s3_service import (
    delete_prefix,
    generate_presigned_url,
    upload,
)
from pitch_coach_backend.module.pitch.script_parser import ParsedScript
from pitch_coach_backend.module.take.dto import TakeSummaryDTO
from pitch_coach_backend.module.take.repository import TakeRepository

logger = logging.getLogger(__name__)

# 평가 기준 나누기는 LLM 한 번 호출이라 기본값(5초)으로는 모자라다. 대본 파싱과 같은 값
STANDARD_PARSE_TIMEOUT = httpx2.Timeout(30.0, connect=5.0)

# 대본 한 편의 글자 수 상한. 1시간 발표도 2만 자 안팎이라 넉넉하고, LLM 한 번에 넣을 수 있는 크기다
MAX_SCRIPT_CHARS = 50_000
# 요청 후 이만큼 지나도 PENDING 이면 만료(FAILED)로 본다. BE 가 재시작되면 백그라운드 작업이
# 사라져 PENDING 이 영원히 남기 때문이다. 작업의 최악 소요(script_parser 의 타임아웃 × 재시도,
# 약 61초)보다 길어야 한다 — 짧으면 아직 도는 작업을 만료로 보여주고 재시도를 열어 버린다
PARSE_EXPIRE_AFTER = timedelta(seconds=90)
# 발표일은 사용자가 고른 한국 날짜다. 오늘도 한국 날짜로 봐야 0~9시에 오늘 발표가 지난 발표가
# 되지 않는다. 관리자 콘솔(admin/repository.py)과 같은 고정 오프셋이다
KST = timezone(timedelta(hours=9), "KST")

def add_pitch_service(db: Session, user_id: uuid.UUID, pitch_dto: PitchSaveRequestDTO):
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

# 홈 화면 : pitches 목록 조회
def get_all_pitches_service(db: Session, user_id: uuid.UUID) -> PitchListResponseDTO:
    pitch_repository = PitchRepository(db)
    pitches = pitch_repository.get_all_by_user(user_id, datetime.now(KST).date())

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

        pitch_dto = PitchListItemDTO(
            pitch_id=pitch.id,
            pitch_title=pitch.title,
            pitch_time=pitch.time_limit_sec,
            thumbnail_url=None,
            presentation_date=pitch.presentation_date,
            takes=take_summaries
        )

        results.append(pitch_dto)

    return PitchListResponseDTO(pitches=results)

# Protocol(해당 타입만 가지고 있다면 Versioned 타입으로 간주)로 통일
# Versioned 타입을 가진 객체들을 VersionDTO로 변환
def to_version_dtos(rows: Iterable[Versioned]) -> list[VersionDTO]:
    return [VersionDTO(id=row.id, version=row.version) for row in rows]


# 발표 자료 버전 들고오기
def get_pitch_datas(db: Session, pitch_id: uuid.UUID):
    pitch_repository = PitchRepository(db)

    return VersionSummaryDTO(
        pitch_id=pitch_id,
        presentation_versions=[
            _presentation_dto(row) for row in pitch_repository.get_presentations(pitch_id)
        ],
        script_versions=to_version_dtos(pitch_repository.get_scripts(pitch_id)),
        evaluation_versions=to_version_dtos(pitch_repository.get_evaluations(pitch_id)),
    )

def _presentation_dto(row: PresentationVersion) -> PresentationVersionDTO:
    return PresentationVersionDTO(id=row.id, version=row.version, page_count=row.page_count)


# 장치 점검 화면. 홈에서 바로 들어오거나 새로고침해도 Take 에 넣을 버전을 알 수 있게 한다
def get_prepare_service(db: Session, pitch_id: uuid.UUID) -> PrepareDTO:
    pitch_repository = PitchRepository(db)
    pitch = pitch_repository.get_by_id(pitch_id)

    if not pitch:
        raise NonExistentPitch()

    script = pitch_repository.get_latest_parsed_script(pitch_id)
    slide_count = pitch_repository.count_slides(script.id) if script else None
    # FE 매핑 확인과 같은 규칙이다. 장 수와 쪽수가 같은 가장 최근 발표자료, 없으면 가장 최근 것
    presentation = None
    if slide_count:
        presentation = pitch_repository.get_latest_presentation(pitch_id, page_count=slide_count)
    if presentation is None:
        presentation = pitch_repository.get_latest_presentation(pitch_id)
    latest_script = pitch_repository.get_latest_script(pitch_id)
    latest_take = TakeRepository(db).get_latest_take_in_pitch(pitch_id)

    newer_script = None
    if latest_script is not None and (script is None or latest_script.version > script.version):
        status, error = _effective_status(latest_script, datetime.now(UTC))
        newer_script = NewerScriptDTO(
            id=latest_script.id,
            version=latest_script.version,
            parse_status=status,
            error_code=error,
        )

    return PrepareDTO(
        pitch_id=pitch.id,
        title=pitch.title,
        time_limit_sec=pitch.time_limit_sec,
        script=(
            ParsedScriptDTO(id=script.id, version=script.version, slide_count=slide_count or 0)
            if script
            else None
        ),
        presentation=_presentation_dto(presentation) if presentation else None,
        newer_script=newer_script,
        next_take_number=latest_take.take_number + 1 if latest_take else 1,
        default_script_mode=latest_take.script_mode if latest_take else "HIGHLIGHT",
    )

def update_pitch_service(db: Session, pitch_id: uuid.UUID, pitch_dto):
    pitch_repository = PitchRepository(db)
    existing_pitch = pitch_repository.get_by_id(pitch_id)

    if not existing_pitch:
        raise NonExistentPitch()

    existing_pitch.title = pitch_dto.title
    existing_pitch.time_limit_sec = pitch_dto.time_limit_sec
    existing_pitch.presentation_date = pitch_dto.presentation_date
    existing_pitch.upper_deviation = pitch_dto.upper_deviation
    existing_pitch.lower_deviation = pitch_dto.lower_deviation

    updated_pitch = pitch_repository.save(existing_pitch)
    db.commit()

    return updated_pitch.id

def delete_pitch_service(db: Session, pitch_id: uuid.UUID) -> list[uuid.UUID]:
    """pitch 를 지운다. Take 와 그 아래 행은 CASCADE 로 같이 지워진다.

    지운 Take 의 id 를 돌려준다. 진행 중이던 Take 의 실시간 STT 는 호출자가 끊는다.
    """
    pitch_repository = PitchRepository(db)
    existing_pitch = pitch_repository.get_by_id(pitch_id)

    if not existing_pitch:
        raise NonExistentPitch()

    # 지운 뒤에는 CASCADE 로 사라져 읽을 수 없다
    take_ids = pitch_repository.get_take_ids(pitch_id)
    pitch_repository.delete(existing_pitch)
    db.commit()

    return take_ids

def delete_pitch_files(pitch_id: uuid.UUID) -> None:
    """S3 에 올린 이 pitch 의 파일을 지운다. pitch 행을 지운 커밋 뒤 백그라운드로 돈다.

    실패해도 삭제 요청은 이미 성공했다. 남은 파일은 로그의 pitch_id 로 찾아 지운다.
    """
    try:
        delete_prefix(f"pitches/{pitch_id}/")
    except Exception:
        logger.exception("pitch 파일 삭제 실패 pitch_id=%s", pitch_id)

def get_pitch_ids_of_user(db: Session, user_id: uuid.UUID) -> list[uuid.UUID]:
    return PitchRepository(db).get_ids_by_user(user_id)

def get_take_ids_of_user(db: Session, user_id: uuid.UUID) -> list[uuid.UUID]:
    return PitchRepository(db).get_take_ids_by_user(user_id)

def upload_presentation_service(db: Session, pitch_id: uuid.UUID, upload_dto):
    pitch_repository = PitchRepository(db)
    presentation_file = upload_dto.presentation_file

    # 확장자 · MIME 은 클라이언트가 정하는 값이라 보지 않고 직접 연다. 잠그기 전에 해서 쪽수를
    # 세는 동안 같은 pitch 의 다른 요청을 막지 않는다
    if file_size(presentation_file.file) > MAX_PRESENTATION_BYTES:
        raise InvalidPresentation()
    page_count = count_pdf_pages(presentation_file.file)
    if page_count is None:
        raise InvalidPresentation()

    # 같은 pitch 에 동시에 올려도 같은 버전 번호를 받지 않게 커밋까지 줄을 세운다.
    # 번호가 S3 키에도 들어가서, 잠그지 않으면 500 에 더해 앞서 올린 파일이 덮어쓰인다
    pitch_repository.lock_for_new_version(pitch_id)

    version = pitch_repository.next_presentation_version(pitch_id)
    presentation_key = upload(
        presentation_file,
        f"pitches/{pitch_id}/presentations/{version}.pdf",
        content_type="application/pdf",
    )

    presentation = PresentationVersion(
        pitch_id=pitch_id,
        version=version,
        file_key=presentation_key,
        page_count=page_count,
    )

    pitch_repository.save_presentation(presentation)
    db.commit()

    return UploadPresentationResultDTO(
        pitch_id=pitch_id,
        presentation_version_id=presentation.id,
        file_url = generate_presigned_url(presentation_key),
        page_count=page_count,
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


def _target_ms(pitch_repository: PitchRepository, pitch_id: uuid.UUID) -> int | None:
    """AI 에 실을 발표 제한 시간(ms). 다시 파싱할 때도 그때의 제한 시간을 싣는다."""
    pitch = pitch_repository.get_by_id(pitch_id)
    if pitch is None:
        raise NonExistentPitch()
    # 계약은 1 이상이다. 입력 검증(#147) 전에 0 이하로 저장된 발표는 AI 가 422 로 거절하지 않게
    # 목표 없이 부른다
    return pitch.time_limit_sec * 1000 if pitch.time_limit_sec > 0 else None


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
    target_ms = _target_ms(pitch_repository, pitch_id)

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
        target_ms=target_ms,
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
    target_ms = _target_ms(pitch_repository, pitch_id)

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
        ParseTicket(
            script_version_id=script.id,
            requested_at=now,
            script_text=script.content,
            target_ms=target_ms,
        ),
    )


def complete_parse(db: Session, ticket: ParseTicket, parsed: ParsedScript) -> bool:
    """AI 결과를 저장하고 DONE. 이 작업이 이미 무효(재시도로 교체·만료 후 재시작)면 False."""
    pitch_repository = PitchRepository(db)
    parse_meta = None
    if parsed.meta is not None:
        parse_meta = {**asdict(parsed.meta), "target_ms": ticket.target_ms}

    # 상태 UPDATE 를 먼저 한다. 이기면 이 행이 잠겨서 슬라이드를 넣는 동안 다른 작업이 못 끼어든다.
    # PENDING → DONE 은 이 한 번뿐이고 DONE 은 다시 파싱하지 않으므로,
    # 넣기 전에 지울 슬라이드는 없다
    if not pitch_repository.mark_parse_done(
        ticket.script_version_id,
        ticket.requested_at,
        expired_before=datetime.now(UTC) - PARSE_EXPIRE_AFTER,
        segmented=parsed.segmented,
        terms=list(parsed.terms),
        parse_meta=parse_meta,
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
                script_chars=slide.script_chars,
                expected_ms=slide.expected_ms,
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


def stt_keyterm_candidates(
    db: Session, pitch_id: uuid.UUID, script_version_id: uuid.UUID
) -> list[str]:
    """리허설 STT 에 넘길 대본 용어. 우선순위 순이고, 한도에 맞춰 자르는 건 호출자 몫이다.

    realtime 이 WebSocket 인가 중에 부른다 (호출자가 run_in_threadpool 로 감싼다).
    파싱이 끝나지 않은 대본은 빈 목록 — 용어가 없어도 STT 는 돈다.

    terms 만 쓴다 — STT 가 틀리기 쉬운 고유명사로, 파서가 우선순위 순으로 준다.
    슬라이드 keywords 는 발음 잡기용이 아니라 넣지 않는다. 이미 잘 받아 적는 일반 단어라
    boosting 하면 말하지 않은 단어가 전사에 끼어든다 (fillers.py 의 T3 와 같은 이유).
    """
    script = PitchRepository(db).get_script_in_pitch(pitch_id, script_version_id)
    if script is None or script.parse_status != ScriptParseStatus.DONE:
        return []
    return [term for term in script.terms or [] if isinstance(term, str)]


# 각 발표자료 버전의 상세 정보.
def get_presentation_detail(db: Session, pitch_id: uuid.UUID, presentation_version_id: uuid.UUID):
    pitch_repository = PitchRepository(db)
    presentation_version = pitch_repository.get_presentation_detail(
        pitch_id, presentation_version_id
    )

    if not presentation_version:
        raise NonExistentPresentationVersion()

    return PresentationDetailDTO(
        pitch_id=pitch_id,
        presentation_version_id=presentation_version.id,
        version=presentation_version.version,
        file_url=generate_presigned_url(presentation_version.file_key),
        page_count=presentation_version.page_count,
        description=presentation_version.description,
        created_at=presentation_version.created_at.date()
    )

def add_pitch_standard_service(
        db: Session, pitch_id: uuid.UUID, standard_text_dto: StandardTextDTO):
    # AI 를 먼저 부르고 잠금은 그 뒤에 잡는다 — LLM 을 기다리는 동안 pitch 행을 쥐고 있지 않게
    standards_result = divide_standard_text(standard_text_dto)

    pitch_repository = PitchRepository(db)
    pitch_repository.lock_for_new_version(pitch_id)
    version = pitch_repository.next_standard_version(pitch_id)
    for position, title in enumerate(standards_result.standards, start=1):
        pitch_repository.save_standard(
            Standards(pitch_id=pitch_id, version=version, position=position, title=title)
        )
    db.commit()

    return StandardTextResponseDTO(
         pitch_id=pitch_id,
         standards=[{"standard": standard} for standard in standards_result.standards],
         except_standard=standards_result.except_standard
    )

def divide_standard_text(standard_text_dto: StandardTextDTO) -> StandardParseResponseDTO:
    """평가 기준 텍스트를 AI 서버로 보내 항목으로 나눈다.

    AI 응답(ai/research/evaluation-criteria 의 스키마)에서 display_criteria 를 항목으로,
    excluded(기준으로 쓰지 못한 항목 이름 목록)를 쉼표로 이어 except_standard 로 쓴다.
    AI 가 실패하거나 응답 모양이 다르면 StandardParseFailed(502).
    """
    try:
        with httpx2.Client(timeout=STANDARD_PARSE_TIMEOUT) as client:
            response = client.post(
                f"{settings.ai_base_url}/evaluation-criteria/parse",
                json={"standard_text": standard_text_dto.standard_text}
            )
            response.raise_for_status()
            result = response.json()

        return StandardParseResponseDTO(
            standards=result["display_criteria"],
            except_standard=", ".join(result.get("excluded") or []) or None,
        )
    # ValueError: JSON 이 아님·pydantic 검증 실패, KeyError·TypeError: 응답 모양이 다름
    except (httpx2.HTTPError, ValueError, KeyError, TypeError) as e:
        raise StandardParseFailed() from e

def get_standard_detail(db: Session, pitch_id: uuid.UUID, evaluation_version: int):
    pitch_repository = PitchRepository(db)
    standards = pitch_repository.get_evaluations_by_version(pitch_id, evaluation_version)

    return {
        "pitch_id": pitch_id,
        "evaluation_version": evaluation_version,
        "evaluations":
        [{
            "order": standards[s].position,
            "standard": standards[s].title
        } for s in range(len(standards))],
    }


def lock_for_new_version(db: Session, pitch_id: uuid.UUID) -> None:
    """발표 행을 잠근다 (`PitchRepository.lock_for_new_version`). Take 를 시작할 때도 잡는다."""
    PitchRepository(db).lock_for_new_version(pitch_id)

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


def find_pitch(db: Session, pitch_id: uuid.UUID) -> Pitch | None:
    return PitchRepository(db).get_by_id(pitch_id)


def find_script_version(db: Session, script_version_id: uuid.UUID) -> ScriptVersion | None:
    return PitchRepository(db).get_script_version(script_version_id)

def script_slides(db: Session, script_version_id: uuid.UUID) -> list[ScriptSlide]:
    """대본 버전의 장, 장 번호 순서."""
    return list(PitchRepository(db).get_slides(script_version_id))


def latest_parsed_script(db: Session, pitch_id: uuid.UUID) -> ScriptVersion | None:
    """파싱이 끝난 가장 최근 대본 버전. 다음 Take 가 쓸 대본이다."""
    return PitchRepository(db).get_latest_parsed_script(pitch_id)

def chosen_coaching_plan(
    db: Session,
    pitch_id: uuid.UUID,
    *,
    source_take_id: uuid.UUID | None,
    script_version_id: uuid.UUID | None,
    target_ms: int | None,
    min_ms: int | None,
    max_ms: int | None,
) -> CoachingPlan | None:
    """바탕 값이 모두 같은 계획 중 요청 시각이 가장 늦은 것 (coach-agent.md 12-2 계획 고르기)."""
    return PitchRepository(db).get_chosen_coaching_plan(
        pitch_id,
        source_take_id=source_take_id,
        script_version_id=script_version_id,
        target_ms=target_ms,
        min_ms=min_ms,
        max_ms=max_ms,
    )

def add_coaching_plan(
    db: Session,
    pitch_id: uuid.UUID,
    *,
    source_take_id: uuid.UUID | None,
    script_version_id: uuid.UUID | None,
    target_ms: int | None,
    min_ms: int | None,
    max_ms: int | None,
    requested_at: datetime,
    payload: dict,
    meta: dict,
) -> bool:
    """계획 응답 하나를 한 행으로 더한다. 발표가 그 사이 지워졌으면 넣지 않고 False."""
    return PitchRepository(db).add_coaching_plan(
        CoachingPlan(
            pitch_id=pitch_id,
            source_take_id=source_take_id,
            script_version_id=script_version_id,
            target_ms=target_ms,
            min_ms=min_ms,
            max_ms=max_ms,
            requested_at=requested_at,
            payload=payload,
            meta=meta,
        )
    )
