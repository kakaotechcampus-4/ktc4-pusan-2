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
    CheckConstraint,
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
        CheckConstraint(
            "review_status IN ('DONE', 'FAILED', 'SKIPPED')",
            name="check_take_review_status",
        ),
        CheckConstraint(
            "analysis_stage IN (" \
            "'FINALIZING', 'SCORING', 'AWAITING_CONFIRMATION', 'REVIEWING', 'DONE')",
            name="check_take_analysis_stage",
        ),
        CheckConstraint(
            "status IN ('READY', 'RUNNING', 'ANALYZING', 'COMPLETED', 'FAILED')",
            name="check_take_status",
        ),
        CheckConstraint("mode IN ('COACHING', 'EXAM')", name="check_take_mode"),
        CheckConstraint(
            "script_mode IN ('HIGHLIGHT', 'KEYWORD', 'OFF')", name="check_take_script_mode"
        ),
        # SILENCE · TIME_LIMIT 은 realtime AutoEndReason 과 같은 값이다.
        # ABANDONED · CLOSED 는 떠난 Take
        CheckConstraint(
            "end_reason IN ('SILENCE', 'TIME_LIMIT', 'ABANDONED', 'CLOSED')",
            name="check_take_end_reason",
        ),
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
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="READY")
    analysis_stage: Mapped[str | None] = mapped_column(String(32), 
                                                       nullable=True)
    analysis_stage_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), 
                                                               nullable=True)
    review_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # 시작 전이거나 중단된 take 는 아래가 비어 있다.
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_sec: Mapped[int | None] = mapped_column(Integer)
    goal_time_sec: Mapped[int] = mapped_column(Integer)
    # BE 가 끝낸 이유. 사용자가 끝냈거나 아직 진행 중이면 비어 있다.
    # 실시간 STT 재연결은 이 값이 아니라 status 가 막는다.
    #   SILENCE·TIME_LIMIT : 자동 종료. status 는 ANALYZING 으로 바뀐다
    #   ABANDONED          : 탭을 닫고 돌아오지 않았다 (끊긴 뒤 30분, 발표가 더 남았으면 최대
    #                        시간까지). 시선 기록 등을 못 받아 분석하지 않고 status 를 FAILED 로
    #                        둔다. 그 뒤에 끝내기 PUT 이 오면(STT 없이 발표를 이어 간 FE)
    #                        ANALYZING 으로 되살리고 이유를 지운다
    #   CLOSED             : 창을 닫았다 (닫힘 코드 1001 뒤 2분 동안 다시 안 붙음, 또는 다시 연 창이
    #                        abandon API 를 부름). 이어 가지 않는다 — FAILED 로 두고 되살리지 않는다
    end_reason: Mapped[str | None] = mapped_column(String(20))
    # 슬라이드 넘김·일시정지 같은 원본 이벤트열. 재생과 재분석에 쓴다.
    event_logs: Mapped[Any | None] = mapped_column(JSONB)


class TakeAIResults(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):

    __tablename__ = "take_ai_results"
    __table_args__ = (
        UniqueConstraint("take_id", "kind", name="uq_take_ai_results_take_version"),
    )

    take_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("takes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    payload: Mapped[Any | None] = mapped_column(JSONB, nullable=False)
    meta: Mapped[Any | None] = mapped_column(JSONB, nullable=True)

class TakeSummary(Base):
    """take 의 분석 집계. take 당 1행이라 take_id 를 그대로 PK 로 쓴다."""

    __tablename__ = "take_summaries"

    take_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("takes.id", ondelete="CASCADE"), primary_key=True
    )
    average_wpm: Mapped[float | None] = mapped_column(Float, nullable=True)
    filler_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 시선이 청중·슬라이드·대본에 머문 정도. 셋을 합쳐 시선 배분을 본다.
    audience_gaze: Mapped[int | None] = mapped_column(Integer, nullable=True)
    slide_gaze: Mapped[int | None] = mapped_column(Integer, nullable=True)
    script_gaze: Mapped[int | None] = mapped_column(Integer, nullable=True)
    script_dependency_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    overall_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    speak_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    # dB 값이라 소수점이 의미가 있고 반올림 오차를 피해야 해서 Numeric 을 쓴다.
    volume: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    total_intermission: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Calibration(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """연습 시작 전 카메라·마이크 점검 결과."""

    __tablename__ = "calibrations"

    take_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("takes.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    face_detected: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    mic_detected: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    # 조용한 환경의 기준 음량. 이후 측정값을 여기에 상대적으로 해석한다.
    base_volume: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    base_level_db: Mapped[float | None] = mapped_column(Float)
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
    __table_args__ = (
        UniqueConstraint("source_take_id", "priority", name="uq_missions_source_take_priority"),
        CheckConstraint(
            "status IN ('ACTIVE', 'ACHIEVED', 'PARTIAL', 'FAILED', 'NOT_EVALUABLE')",
            name="check_mission_status",
        ),
    )

    # Mission을 등록한 take의 id
    source_take_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("takes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Mission을 수행하는 take의 id
    take_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("takes.id", ondelete="SET NULL"), index=True, nullable=True
    )
    area: Mapped[str] = mapped_column(String(16), nullable=False)
    slide_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target: Mapped[Any] = mapped_column(JSONB, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), server_default="ACTIVE", nullable=False)
    observed: Mapped[float | None] = mapped_column(Float, nullable=True)
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
