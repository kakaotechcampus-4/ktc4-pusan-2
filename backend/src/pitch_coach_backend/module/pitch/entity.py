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
    # takes 와 서로를 참조한다. use_alter 로 테이블 생성 후 FK 를 따로 걸어
    # 순환 때문에 생성 순서가 막히는 것을 피한다.
    # best_take_id는 추후 점수로 계산한 후, 가장 최고 점수의 take 를 best_take 로 지정할 때 사용한다.
    upper_deviation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lower_deviation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

class Standards(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """발표 표준. 발표를 평가할 때 기준이 되는 표준 발표를 저장한다."""

    __tablename__ = "standards"
    __table_args__ = (
        UniqueConstraint("pitch_id", "version", "position", name="uq_standards_pitch_version_position"),
    )

    # 자식 쪽에 외래키를 건다. 
    pitch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pitches.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(50), nullable=False)

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
    # 사용자가 남기는 변경 메모. 없어도 된다.
    description: Mapped[str | None] = mapped_column(Text)


class ScriptParseStatus(StrEnum):
    """대본을 AI 로 슬라이드 단위로 나누는 작업의 상태. FE 가 폴링으로 본다."""

    PENDING = "PENDING"
    DONE = "DONE"
    FAILED = "FAILED"


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
    # FAILED 원인 코드 (dto.ScriptParseErrorCode). 상세 원인은 서버 로그에만 남긴다
    parse_error: Mapped[str | None] = mapped_column(String(32))


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
