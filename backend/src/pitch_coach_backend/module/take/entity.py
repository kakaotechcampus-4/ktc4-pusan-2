"""연습 1회(take)와 그 분석 결과 테이블.

Take 는 "어떤 발표자료 + 어떤 대본으로 언제 연습했는가"를 고정하는 기록이다.
이후 버전이 올라가도 지난 take 가 무엇을 보고 연습한 것인지 남도록
presentation_version_id / script_version_id 를 같이 박아둔다.

측정값은 성격에 따라 나뉜다.
  - TakeSummary   : 연습이 끝난 뒤 한 번 계산되는 집계. take 당 1행.
  - LiveFeedback  : 연습 도중 실시간으로 쌓이는 이벤트. take 당 N행.
  - Calibration   : 시작 전 카메라·마이크 점검 결과.
  - Mission       : 결과에서 뽑아낸 다음 연습 과제.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
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
    UUIDPrimaryKeyMixin,
)


class Take(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """연습 1회. pitches.best_take_id 가 이 중 하나를 가리킨다."""

    __tablename__ = "takes"
    __table_args__ = (
        # 같은 pitch 안에서 take 번호가 겹치지 않게 한다.
        UniqueConstraint("pitch_id", "take_number", name="uq_takes_pitch_take_number"),
    )

    pitch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("pitches.id", ondelete="CASCADE"), index=True, nullable=False
    )
    take_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # 연습 시점의 버전을 고정한다. 나중에 새 버전이 올라와도 바뀌지 않는다.
    presentation_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("presentation_versions.id"), nullable=False
    )
    script_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("script_versions.id"), nullable=False
    )
    mode: Mapped[str] = mapped_column(String(20), nullable=False)
    script_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # 시작 전이거나 중단된 take 는 아래가 비어 있다.
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_sec: Mapped[int | None] = mapped_column(Integer)
    # 슬라이드 넘김·일시정지 같은 원본 이벤트열. 재생과 재분석에 쓴다.
    event_logs: Mapped[Any | None] = mapped_column(JSONB)


class TakeSummary(Base):
    """take 의 분석 집계. take 당 1행이라 take_id 를 그대로 PK 로 쓴다."""

    __tablename__ = "take_summaries"

    take_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("takes.id", ondelete="CASCADE"), primary_key=True
    )
    average_wpm: Mapped[float] = mapped_column(Float, nullable=False)
    filler_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # 시선이 청중·슬라이드·대본에 머문 정도. 셋을 합쳐 시선 배분을 본다.
    audience_gaze: Mapped[int] = mapped_column(Integer, nullable=False)
    slide_gaze: Mapped[int] = mapped_column(Integer, nullable=False)
    script_gaze: Mapped[int] = mapped_column(Integer, nullable=False)
    script_dependency_count: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    overall_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    speak_accuracy: Mapped[float] = mapped_column(Float, nullable=False)
    # dB 값이라 소수점이 의미가 있고 반올림 오차를 피해야 해서 Numeric 을 쓴다.
    volume: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    total_intermission: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)


class Calibration(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """연습 시작 전 카메라·마이크 점검 결과."""

    __tablename__ = "calibrations"

    take_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("takes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    face_detected: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    mic_detected: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    # 조용한 환경의 기준 음량. 이후 측정값을 여기에 상대적으로 해석한다.
    base_volume: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    gaze_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    result: Mapped[Any | None] = mapped_column(JSONB)


class LiveFeedback(UUIDPrimaryKeyMixin, Base):
    """연습 도중 실시간으로 띄운 피드백 1건."""

    __tablename__ = "live_feedbacks"

    take_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("takes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(String(255), nullable=False)
    # take 시작 시점부터의 경과 시간(ms). 녹화 재생 위치와 맞춘다.
    triggered_at_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)


class Mission(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """지난 take 에서 뽑아낸 다음 연습 과제."""

    __tablename__ = "missions"

    source_take_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("takes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    slide_number: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    complete: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)


class TakeTranscriptSegment(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """실시간 STT 가 확정(is_final)한 전사 구간 1건. 리포트의 유일한 원천이다.

    interim 은 저장하지 않는다 — 같은 구간의 단어·시각이 계속 바뀌어 분석 근거로 못 쓴다.
    어절을 행으로 펼치지 않고 words 에 통째로 둔다. 리포트는 Take 단위로 한 번에 읽는다.
    raw 는 words[].word, 정규화는 words[].punctuated_word + transcript 에 있다.
    """

    __tablename__ = "take_transcript_segments"
    __table_args__ = (
        # seq 는 Take 안에서 이어지는 번호다. WebSocket 이 다시 붙어도 리셋되지 않는다.
        UniqueConstraint("take_id", "seq", name="uq_take_transcript_segments_take_seq"),
    )

    take_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("takes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    # Deepgram 세션 교체 횟수. 커버리지·디버깅용
    stt_session_no: Mapped[int] = mapped_column(Integer, nullable=False)
    # Take 시작 = 0 기준 (ms). 시선·슬라이드 이벤트와 같은 축이다.
    start_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    end_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    transcript: Mapped[str] = mapped_column(Text, nullable=False)
    # [{word, punctuated_word, start_ms, end_ms, confidence}]
    words: Mapped[Any] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    # endpointing 이 침묵을 감지한 문장 경계인지
    speech_final: Mapped[bool] = mapped_column(Boolean, nullable=False)
