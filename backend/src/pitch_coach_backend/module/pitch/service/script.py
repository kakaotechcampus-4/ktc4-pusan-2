"""대본 검사 · 생성 · 파싱 상태 · 재파싱 · 결과 저장 · 장별 평가 기준 상태 · STT 인식 용어 후보."""

import logging
import uuid
from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch.adapter.script_parser import ParsedScript
from pitch_coach_backend.module.pitch.dto import (
    HighlightDTO,
    ParseRequestedDTO,
    ParseSaved,
    ParseTicket,
    RubricTicket,
    ScriptCreatedDTO,
    ScriptDetailDTO,
    ScriptParseErrorCode,
    ScriptSlideDTO,
)
from pitch_coach_backend.module.pitch.entity import (
    ScriptParseStatus,
    ScriptRubricStatus,
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

logger = logging.getLogger(__name__)


# 대본 한 편의 글자 수 상한. 1시간 발표도 2만 자 안팎이라 넉넉하고, LLM 한 번에 넣을 수 있는 크기다
MAX_SCRIPT_CHARS = 50_000


# 요청 후 이만큼 지나도 PENDING 이면 만료(FAILED)로 본다. BE 가 재시작되면 백그라운드 작업이
# 사라져 PENDING 이 영원히 남기 때문이다. 작업의 최악 소요(script_parser 의 타임아웃 × 재시도,
# 약 61초)보다 길어야 한다 — 짧으면 아직 도는 작업을 만료로 보여주고 재시도를 열어 버린다
PARSE_EXPIRE_AFTER = timedelta(seconds=90)


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
        pitch_id=pitch_id,
    )
    return created, ticket


# ── 대본 파싱 상태 ──────────────────────────────────────────────────────
# 백그라운드 작업(script_parse_runner)이 AI 를 기다리는 동안에는 DB 세션을 쥐고 있지 않는다.
# 아래 complete/fail 은 AI 응답을 받은 뒤 짧은 세션으로 한 번에 끝낸다.
def effective_status(
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
    status, error = effective_status(script, datetime.now(UTC))

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

    status, _ = effective_status(script, now)
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
            pitch_id=pitch_id,
        ),
    )


def resume_parses(db: Session) -> list[ParseTicket]:
    """BE 가 뜰 때 부른다. 만료 전 PENDING 으로 남은 파싱마다 표식을 새로 바꿔 이어받는다.

    표식을 지금으로 바꾸므로 만료(PARSE_EXPIRE_AFTER)도 지금부터 다시 센다. 옛 시각을 그대로 쓰면
    남은 시간이 어댑터 최악 소요보다 짧은 행은 AI 가 답하는 중에 만료로 보인다.
    이미 만료된 행은 두고 FAILED(PARSE_EXPIRED) 로 보여 사용자가 다시 시도하게 한다.
    BE 프로세스가 하나라 앞 작업은 이미 끝났다 (resume_rubrics 와 같다).
    """
    pitch_repository = PitchRepository(db)
    tickets: list[ParseTicket] = []
    now = datetime.now(UTC)
    expired_before = now - PARSE_EXPIRE_AFTER
    for script_version_id, read_at in pitch_repository.get_resumable_parses(expired_before):
        if not pitch_repository.take_over_parse(
            script_version_id, read_at, now, expired_before=expired_before
        ):
            # 그 사이 재시도 · 다른 작업이 바꿨다
            db.rollback()
            continue
        script = pitch_repository.get_script_version(script_version_id)
        assert script is not None and script.content is not None  # 방금 표식을 바꾼 행이다
        tickets.append(
            ParseTicket(
                script_version_id=script.id,
                requested_at=now,
                script_text=script.content,
                target_ms=_target_ms(pitch_repository, script.pitch_id),
                pitch_id=script.pitch_id,
            )
        )
        db.commit()
    return tickets


def complete_parse(db: Session, ticket: ParseTicket, parsed: ParsedScript) -> ParseSaved:
    """AI 결과를 저장하고 DONE. 이 작업이 이미 무효(재시도로 교체·만료 후 재시작)면 STALE.

    원문은 버전 안에서 바뀌지 않으므로 같은 버전을 다시 파싱하면 같은 장이 나와야 한다.
    장별 결과가 이미 저장돼 있으면 전체 (장 번호, 대본) 를 번호순으로 비교해 같으면 저장본과
    그 평가 기준을 그대로 두고, 다르면 적용하지 않고 REPARSE_MISMATCH 를 남긴다 — 그 버전으로 이미
    채점한 Take 가 다른 장을 가리키게 되지 않게 (docs/ai/architecture.md 1-7).
    지금은 DONE 대본의 재파싱을 막고 있어(ScriptAlreadyParsed) 저장본이 있는 경우는 생기지 않는다.
    적용하거나 재사용하면 평가 기준 첫 작업도 같은 트랜잭션에서 PENDING 으로 둔다 (_queue_rubric).
    """
    pitch_repository = PitchRepository(db)
    # 행을 먼저 잠근다. 슬라이드를 넣는 동안 다른 작업이 못 끼어든다
    script = pitch_repository.lock_pending_parse(
        ticket.script_version_id,
        ticket.requested_at,
        expired_before=datetime.now(UTC) - PARSE_EXPIRE_AFTER,
    )
    if script is None:
        db.rollback()
        return ParseSaved.STALE

    script.parse_status = ScriptParseStatus.DONE
    stored = [
        (slide.slide_number, slide.full_content) for slide in pitch_repository.get_slides(script.id)
    ]
    if stored:
        # 문자열은 더 다듬지 않고 그대로 비교한다
        fresh = sorted((slide.slide_number, slide.content) for slide in parsed.slides)
        if stored == fresh:
            script.parse_error = None
            _queue_rubric(script, ticket.requested_at)
            db.commit()
            return ParseSaved.REUSED
        script.parse_error = ScriptParseErrorCode.REPARSE_MISMATCH
        db.commit()
        logger.warning(
            "다시 파싱한 대본이 저장본과 달라 적용하지 않음 script_version_id=%s", script.id
        )
        return ParseSaved.MISMATCH

    parse_meta = None
    if parsed.meta is not None:
        parse_meta = {**asdict(parsed.meta), "target_ms": ticket.target_ms}
    script.segmented = parsed.segmented
    script.terms = list(parsed.terms)
    script.parse_meta = parse_meta
    script.parse_error = None
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
    _queue_rubric(script, ticket.requested_at)
    db.commit()
    return ParseSaved.APPLIED


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


# ── 장별 평가 기준 (/scripts/rubric) ─────────────────────────────────────
# 백그라운드 작업(script_rubric_runner)이 단계마다 짧은 세션으로 부른다.
# 순서와 조건은 docs/ai/architecture.md 1-7, 저장 칸은 script-coverage-evaluation.md 12-1
def _queue_rubric(script: ScriptVersion, requested_at: datetime) -> None:
    """평가 기준을 만든 적 없는 버전이면 첫 작업을 PENDING 으로 둔다. 잠근 행에 부른다.

    파싱 결과와 같은 트랜잭션에서 한다. 커밋 직후 BE 가 죽어도 상태가 null 로 남지 않아 다시 뜰 때
    이어받는다. 표식은 그 파싱 작업의 표식을 그대로 쓴다 — 파싱 작업이 이어서 이 표식으로
    작업을 연다 (begin_rubric). 이미 시작했던 버전의 작업 · 한도 · 성공 기준은 건드리지 않는다.
    """
    if script.rubric_status is None:
        script.rubric_status = ScriptRubricStatus.PENDING
        script.rubric_requested_at = requested_at


def begin_rubric(
    db: Session, script_version_id: uuid.UUID, requested_at: datetime
) -> RubricTicket | None:
    """complete_parse 가 PENDING 으로 둔 첫 작업을 연다. 그 사이 다른 작업이 됐으면 None."""
    pitch_repository = PitchRepository(db)
    script = pitch_repository.lock_rubric_job(script_version_id, requested_at)
    if script is None:
        db.rollback()
        return None
    ticket = _rubric_ticket(pitch_repository, script, resumed=False)
    db.commit()
    return ticket


def resume_rubrics(db: Session) -> list[RubricTicket]:
    """BE 가 뜰 때 부른다. PENDING 으로 남은 작업마다 표식을 새로 바꿔 이어받는다.

    BE 프로세스가 하나라 앞 작업은 이미 끝났다. 여러 프로세스로 늘리면 표식 교체만으로는 둘이
    차례로 이어받는 것을 막지 못하므로 그때 방법을 따로 정한다 (architecture.md 1-7).
    """
    pitch_repository = PitchRepository(db)
    tickets: list[RubricTicket] = []
    for script_version_id, read_at in pitch_repository.get_pending_rubrics():
        now = datetime.now(UTC)
        if not pitch_repository.take_over_rubric(script_version_id, read_at, now):
            db.rollback()
            continue
        script = pitch_repository.lock_rubric_job(script_version_id, now)
        assert script is not None  # 방금 표식을 바꾼 행이다
        tickets.append(_rubric_ticket(pitch_repository, script, resumed=True))
        db.commit()
    return tickets


def _rubric_ticket(
    pitch_repository: PitchRepository, script: ScriptVersion, *, resumed: bool
) -> RubricTicket:
    slides = pitch_repository.get_slides(script.id)
    assert script.rubric_requested_at is not None  # PENDING 인 작업이다
    return RubricTicket(
        script_version_id=script.id,
        pitch_id=script.pitch_id,
        requested_at=script.rubric_requested_at,
        slides=tuple((slide.slide_number, slide.full_content) for slide in slides),
        missing=tuple(slide.slide_number for slide in slides if slide.rubric is None),
        retry_used=script.rubric_retry_used,
        resumed=resumed,
    )


def use_rubric_retry(db: Session, ticket: RubricTicket) -> bool:
    """재시도를 보내기 전에 한도를 썼다고 커밋한다. 이미 썼거나 이 작업이 무효면 False.

    보낸 뒤 BE 가 재시작해도 같은 재시도를 다시 보내지 않게 한다.
    """
    script = PitchRepository(db).lock_rubric_job(ticket.script_version_id, ticket.requested_at)
    if script is None or script.rubric_retry_used:
        db.rollback()
        return False
    script.rubric_retry_used = True
    db.commit()
    return True


def save_rubrics(
    db: Session,
    ticket: RubricTicket,
    rubrics: Mapping[int, dict[str, Any]],
    rubric_meta: dict[str, Any]) -> bool:
    """응답 하나의 장별 기준을 한 트랜잭션으로 저장한다. 이 작업이 무효면 버리고 False.

    기준이 이미 있는 장은 건너뛴다 (첫 성공본을 덮지 않는다).
    """
    pitch_repository = PitchRepository(db)
    if pitch_repository.lock_rubric_job(ticket.script_version_id, ticket.requested_at) is None:
        db.rollback()
        return False
    for slide_number, rubric in rubrics.items():
        pitch_repository.save_rubric(ticket.script_version_id, slide_number, rubric, rubric_meta)
    db.commit()
    return True


def finish_rubric(db: Session, ticket: RubricTicket) -> ScriptRubricStatus | None:
    """저장된 장별 기준으로 상태를 확정한다. 이 작업이 무효면 None."""
    pitch_repository = PitchRepository(db)
    script = pitch_repository.lock_rubric_job(ticket.script_version_id, ticket.requested_at)
    if script is None:
        db.rollback()
        return None
    slides = pitch_repository.get_slides(script.id)
    made = sum(slide.rubric is not None for slide in slides)
    if made == len(slides):
        status = ScriptRubricStatus.DONE
    elif made == 0:
        status = ScriptRubricStatus.FAILED
    else:
        status = ScriptRubricStatus.PARTIAL
    script.rubric_status = status
    db.commit()
    return status


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


def find_script_version(db: Session, script_version_id: uuid.UUID) -> ScriptVersion | None:
    return PitchRepository(db).get_script_version(script_version_id)


def rubric_pending(db: Session, script_version_id: uuid.UUID) -> bool:
    script = PitchRepository(db).get_script_version(script_version_id)
    return script is not None and script.rubric_status == ScriptRubricStatus.PENDING


def script_slides(db: Session, script_version_id: uuid.UUID) -> list[ScriptSlide]:
    """대본 버전의 장, 장 번호 순서."""
    return list(PitchRepository(db).get_slides(script_version_id))


def latest_parsed_script(db: Session, pitch_id: uuid.UUID) -> ScriptVersion | None:
    """파싱이 끝난 가장 최근 대본 버전. 다음 Take 가 쓸 대본이다."""
    return PitchRepository(db).get_latest_parsed_script(pitch_id)
