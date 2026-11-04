"""발표(pitch)와 그 재료 테이블.

발표자료(PresentationVersion)와 대본(ScriptVersion)은 pitch 에 종속된
버전 이력이다. 둘 다 version 이 pitch 안에서만 1부터 세는 번호라서
DB 시퀀스를 쓰지 않고 애플리케이션이 채운다.
"""

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from pitch_coach_backend.core.database import (
    Base,
    CreatedAtMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)


class Pitch(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """발표 하나. 연습(Take)은 모두 이 아래에 달린다."""

    __tablename__ = "pitches"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(50), nullable=False)
    # 제한 시간. 초 단위로 저장해 화면에서 분·초로 환산한다.
    time_limit_sec: Mapped[int] = mapped_column(Integer, nullable=False)
    presentation_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    upper_deviation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lower_deviation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

class Standards(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """발표 표준. 발표를 평가할 때 기준이 되는 표준 발표를 저장한다."""

    __tablename__ = "standards"
    __table_args__ = (
        UniqueConstraint(
            "pitch_id", "version", "position", name="uq_standards_pitch_version_position"
        ),
    )

    # 자식 쪽에 외래키를 건다. 
    pitch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pitches.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(50), nullable=False)

class StandardAnalyses(UUIDPrimaryKeyMixin, TimestampMixin, Base):

    __tablename__ = "standard_analyses"
    __table_args__ = (
        UniqueConstraint("pitch_id", "version", name="uq_standard_analyses_pitch_version"),
    )

    pitch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pitches.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    targets: Mapped[Any] = mapped_column(JSONB, nullable=False)
    script_used: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    excluded: Mapped[Any] = mapped_column(JSONB, nullable=False)
    values: Mapped[Any | None] = mapped_column(JSONB, nullable=True)
    meta: Mapped[Any] = mapped_column(JSONB, nullable=False)

class PresentationVersion(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """업로드된 발표자료 파일의 한 버전. 한 번 올리면 수정하지 않는다."""

    __tablename__ = "presentation_versions"
    __table_args__ = (
        # 같은 pitch 안에서만 version 이 유일하다.
        # pitch 가 다르면 version 이 겹쳐도 된다.
        UniqueConstraint("pitch_id", "version", name="uq_presentation_versions_pitch_version"),
    )

    pitch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pitches.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    file_key: Mapped[str] = mapped_column(String(255), nullable=False)
    # PDF 쪽수. 대본 장 수와 같아야 짝이다. 이 칸이 생기기 전에 올린 행은
    # dev/backfill-presentation-page-count.py 로 채우기 전까지 비어 있다
    page_count: Mapped[int | None] = mapped_column(Integer)
    thumbnail_key: Mapped[str | None] = mapped_column(String(255))
    # 사용자가 남기는 변경 메모. 없어도 된다.
    description: Mapped[str | None] = mapped_column(Text)


class ScriptParseStatus(StrEnum):
    """대본을 AI 로 슬라이드 단위로 나누는 작업의 상태. FE 가 폴링으로 본다."""

    PENDING = "PENDING"
    DONE = "DONE"
    FAILED = "FAILED"


class ScriptRubricStatus(StrEnum):
    """대본 버전의 장별 평가 기준(`/scripts/rubric`) 만들기 상태. null 은 아직 시작하지 않음."""

    PENDING = "PENDING"
    DONE = "DONE"  # 모든 장 성공
    PARTIAL = "PARTIAL"  # 다시 부른 뒤에도 실패한 장이 남음
    FAILED = "FAILED"  # 모든 장 실패


class ScriptVersion(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """대본의 한 버전. 원문은 content, AI 가 슬라이드 단위로 나눈 결과는 ScriptSlide 에 있다.

    원문을 S3 가 아니라 DB 에 두는 이유: 대본은 5만 자 이하 텍스트라 TEXT 컬럼으로 충분하고,
    슬라이드와 한 트랜잭션으로 저장돼 짝 없는 파일이 생기지 않으며, 재시도·편집 화면이
    네트워크 호출 없이 바로 읽는다. ScriptSlide.full_content 는 AI 가 구분자·제목을 빼고
    공백을 합친 글이라 원문을 대신할 수 없다.
    """

    __tablename__ = "script_versions"
    __table_args__ = (
        UniqueConstraint("pitch_id", "version", name="uq_script_versions_pitch_version"),
        # PG ENUM 대신 문자열 + CHECK. 값이 늘어도 ALTER TYPE 없이 제약만 바꾸면 된다
        CheckConstraint(
            "parse_status IN ('PENDING', 'DONE', 'FAILED')",
            name="ck_script_versions_parse_status",
        ),
        CheckConstraint(
            "rubric_status IN ('PENDING', 'DONE', 'PARTIAL', 'FAILED')",
            name="ck_script_versions_rubric_status",
        ),
    )

    pitch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pitches.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    # 대본 원문 (업로드된 그대로, BOM 만 뗀 것). 파싱 기능 전에 올라온 행은 비어 있다 —
    # 그때는 S3 pitches/{pitch_id}/scripts/{version}.<확장자> 에 파일로만 올렸다
    content: Mapped[str | None] = mapped_column(Text)
    parse_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ScriptParseStatus.PENDING, server_default="PENDING"
    )
    # 파싱을 (다시) 요청한 시각. 두 가지로 쓴다 —
    #   1) 오래된 PENDING 을 만료로 보기 (BE 가 재시작돼 작업이 사라진 경우)
    #   2) 작업이 들고 가는 표식. 늦게 끝난 옛 작업이 새 결과를 덮지 못하게 저장 조건에 넣는다
    parse_requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # DONE 이 된 뒤에만 채운다. False 는 "구분자가 없어 대본 전체를 한 슬라이드로 뒀다"는 정상 결과
    segmented: Mapped[bool | None] = mapped_column(Boolean)
    # STT 가 잘못 알아듣기 쉬운 고유명사·전문 용어. Deepgram keyterm 으로 넘긴다
    terms: Mapped[Any | None] = mapped_column(JSONB)
    # AI 응답의 meta {schema_version, feature_version, model} 에 요청한 target_ms 를 더한 것.
    # 제한 시간이 바뀌어도 다시 파싱하지 않고, 쓰는 쪽이 expected_ms 를 이 target_ms 에 대한 비율로
    # 읽는다 (docs/ai/features/script-parser.md 12-2). meta 를 주지 않은 AI 응답이면 비어 있다
    parse_meta: Mapped[Any | None] = mapped_column(JSONB)
    # FAILED 원인 코드 (dto.ScriptParseErrorCode). 상세 원인은 서버 로그에만 남긴다.
    # DONE 인데 REPARSE_MISMATCH 면 다시 파싱한 결과가 저장본과 달라 적용하지 않았다는 뜻이다
    parse_error: Mapped[str | None] = mapped_column(String(32))
    # 평가 기준 만들기 작업 (ScriptRubricStatus). rubric_requested_at 은 작업을 시작·이어받은
    # 시각이자 작업의 표식이고, rubric_retry_used 는 버전마다 한 번인 재시도를 썼는가다
    # (보내기 전에 true). docs/ai/architecture.md 1-7
    rubric_status: Mapped[str | None] = mapped_column(String(16))
    rubric_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rubric_retry_used: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )


class ScriptSlide(UUIDPrimaryKeyMixin, Base):
    """슬라이드 한 장의 대본. 버전 안에서 slide_number 로 순서가 정해진다."""

    __tablename__ = "script_slides"
    __table_args__ = (
        # 한 버전에 같은 슬라이드 번호가 둘일 수 없다.
        UniqueConstraint(
            "script_version_id", "slide_number", name="uq_script_slides_version_slide"
        ),
    )

    script_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("script_versions.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    slide_number: Mapped[int] = mapped_column(Integer, nullable=False)
    full_content: Mapped[str] = mapped_column(Text, nullable=False)
    # 강조 구간·키워드는 개수와 모양이 자유로워 JSONB 로 둔다.
    # highlights: full_content 안의 문자 위치 [[start, end], ...] (end 미포함)
    # keywords  : ["키워드", ...] — 대본에 나온 표현 그대로
    highlights: Mapped[Any | None] = mapped_column(JSONB)
    keywords: Mapped[Any | None] = mapped_column(JSONB)
    # 공백·문장부호를 뺀 글자 수와 이 장의 예상 발표 시간(ms). 코치 계획의 장별 목표 시간에 쓴다.
    # 이 칸이 생기기 전에 파싱한 행과, 이 값을 주지 않은 AI 응답이면 비어 있다
    script_chars: Mapped[int | None] = mapped_column(Integer)
    expected_ms: Mapped[int | None] = mapped_column(Integer)
    # 이 장의 평가 기준 (`/scripts/rubric` 응답 rubrics[] 의 그 장 것 그대로)과 그 응답의 meta.
    # 비어 있으면 아직 없거나 만들기가 실패한 장. 첫 성공본을 덮지 않는다
    rubric: Mapped[Any | None] = mapped_column(JSONB)
    rubric_meta: Mapped[Any | None] = mapped_column(JSONB)


class CoachingPlan(UUIDPrimaryKeyMixin, Base):
    """코칭 계획 (`/coach/plan` 응답) 한 건. 응답마다 한 행을 더하고 덮어쓰지 않는다.

    다음 Take 를 시작할 때 바탕 값(source_take_id · script_version_id · target_ms · min_ms ·
    max_ms)이 그때 것과 모두 같은 행 중 `requested_at` 이 가장 늦은 것을 싣는다. 그래서 늦게 온
    옛 응답이 새 계획을 대신하지 못한다
    (`docs/ai/features/coach-agent.md` 12-1 · 12-2 '계획 고르기').
    """

    __tablename__ = "coaching_plans"
    __table_args__ = (Index("ix_coaching_plans_pitch_requested", "pitch_id", "requested_at"),)

    pitch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pitches.id", ondelete="CASCADE"), nullable=False
    )
    # 요청에 실은 바탕 값. 외래 키로 묶지 않는다 — Take · 대본을 지워도 계획 행은 발표와 함께 남고,
    # 이 값은 고를 때 비교하는 데만 쓴다
    source_take_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    script_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    target_ms: Mapped[int | None] = mapped_column(Integer)
    min_ms: Mapped[int | None] = mapped_column(Integer)
    max_ms: Mapped[int | None] = mapped_column(Integer)
    # BE 가 그 계획 요청을 보낸 시각. 응답을 받은 시각이 아니다
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # 응답의 {coaching_plan, fallback_reason, dropped, planner_hash}
    payload: Mapped[Any] = mapped_column(JSONB, nullable=False)
    meta: Mapped[Any] = mapped_column(JSONB, nullable=False)


class PendingFileDeletion(Base):
    """아직 지우지 못한 S3 경로 하나. 발표를 지운 같은 트랜잭션에 넣고, 지운 뒤에 뺀다.

    발표 행은 이미 사라져 외래 키로 묶을 수 없다. 지우는 일이 중간에 끊겨도(프로세스 중단 ·
    S3 장애) 이 행이 남아 주기 작업이 성공할 때까지 다시 한다 (`service/file_cleanup.py`).
    """

    __tablename__ = "pending_file_deletions"

    # 지울 S3 경로(`pitches/<id>/`). 같은 경로를 두 번 넣어도 한 행이다
    prefix: Mapped[str] = mapped_column(String(255), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    # 지금까지 실패한 횟수. 오래 남은 행을 찾는 데 쓴다
    attempts: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
