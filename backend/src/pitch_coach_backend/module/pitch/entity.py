"""발표(pitch)와 그 재료 테이블.

발표자료(PresentationVersion)와 대본(ScriptVersion)은 pitch 에 종속된
버전 이력이다. 둘 다 version 이 pitch 안에서만 1부터 세는 번호라서
DB 시퀀스를 쓰지 않고 애플리케이션이 채운다.
"""

import uuid
from datetime import date
from typing import Any

from sqlalchemy import (
    Date,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
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
    best_take_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid
    )

class Standards(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """발표 표준. 발표를 평가할 때 기준이 되는 표준 발표를 저장한다."""

    __tablename__ = "standards"
    __table_args__ = (
        UniqueConstraint("pitch_id", "version", name="uq_standards_pitch_version"),
    )

    pitch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pitches.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(50), nullable=False)
    create_at: Mapped[date] = mapped_column(Date, nullable=False)

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


class ScriptVersion(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """대본의 한 버전. 실제 내용은 슬라이드 단위로 ScriptSlide 에 나뉘어 있다."""

    __tablename__ = "script_versions"
    __table_args__ = (
        UniqueConstraint("pitch_id", "version", name="uq_script_versions_pitch_version"),
    )

    pitch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pitches.id", ondelete="CASCADE"), index=True, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    file_key: Mapped[str] = mapped_column(String(255), nullable=False)


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
    highlights: Mapped[Any | None] = mapped_column(JSONB)
    keywords: Mapped[Any | None] = mapped_column(JSONB)
