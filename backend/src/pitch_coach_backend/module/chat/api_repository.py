"""대화 API(화면 ↔ BE) 가 읽고 쓰는 쿼리. AI 턴 실행 쪽 쿼리는 repository.py 에 있다."""

import uuid
from datetime import date

from sqlalchemy import Row, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.entity import (
    ChatConversation,
    ChatDailyUsage,
    ChatMessage,
    ChatRun,
)
from pitch_coach_backend.module.pitch.entity import Pitch
from pitch_coach_backend.module.take.entity import Take

ACTIVE_RUN_STATUSES = ("QUEUED", "RUNNING", "WAITING_APPROVAL")


class ChatApiRepository:
    def __init__(self, db: Session):
        self.db = db

    def find_request(self, user_id: uuid.UUID, client_request_id: uuid.UUID) -> ChatMessage | None:
        return self.db.scalar(
            select(ChatMessage).where(
                ChatMessage.user_id == user_id,
                ChatMessage.client_request_id == client_request_id,
            )
        )

    def get_owned_conversation(
        self, conversation_id: uuid.UUID, user_id: uuid.UUID, *, lock: bool = False
    ) -> ChatConversation | None:
        stmt = select(ChatConversation).where(
            ChatConversation.id == conversation_id, ChatConversation.user_id == user_id
        )
        return self.db.scalar(stmt.with_for_update() if lock else stmt)

    def list_conversations(
        self, user_id: uuid.UUID, pitch_id: uuid.UUID | None, *, limit: int
    ) -> list[Row]:
        stmt = (
            select(ChatConversation, Pitch.title.label("pitch_title"))
            .outerjoin(Pitch, Pitch.id == ChatConversation.scope_pitch_id)
            .where(ChatConversation.user_id == user_id)
            .order_by(ChatConversation.last_user_activity_at.desc(), ChatConversation.id.desc())
            .limit(limit)
        )
        if pitch_id is not None:
            stmt = stmt.where(ChatConversation.scope_pitch_id == pitch_id)
        return list(self.db.execute(stmt).all())

    def pitch_title(self, pitch_id: uuid.UUID | None) -> str | None:
        if pitch_id is None:
            return None
        return self.db.scalar(select(Pitch.title).where(Pitch.id == pitch_id))

    def has_active_run(self, conversation_id: uuid.UUID) -> bool:
        return (
            self.db.scalar(
                select(ChatRun.id)
                .where(
                    ChatRun.conversation_id == conversation_id,
                    ChatRun.status.in_(ACTIVE_RUN_STATUSES),
                )
                .limit(1)
            )
            is not None
        )

    def get_owned_run(
        self, run_id: uuid.UUID, user_id: uuid.UUID, *, lock: bool = False
    ) -> ChatRun | None:
        stmt = select(ChatRun).where(ChatRun.id == run_id, ChatRun.user_id == user_id)
        return self.db.scalar(stmt.with_for_update() if lock else stmt)

    def latest_run(self, conversation_id: uuid.UUID) -> ChatRun | None:
        return self.db.scalar(
            select(ChatRun)
            .where(ChatRun.conversation_id == conversation_id)
            .order_by(ChatRun.accepted_at.desc(), ChatRun.id.desc())
            .limit(1)
        )

    def get_message_in(
        self, message_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> ChatMessage | None:
        return self.db.scalar(
            select(ChatMessage).where(
                ChatMessage.id == message_id, ChatMessage.conversation_id == conversation_id
            )
        )

    def list_messages(
        self, conversation_id: uuid.UUID, *, before_seq: int | None, limit: int
    ) -> list[ChatMessage]:
        stmt = select(ChatMessage).where(ChatMessage.conversation_id == conversation_id)
        if before_seq is not None:
            stmt = stmt.where(ChatMessage.seq < before_seq)
        rows = self.db.scalars(stmt.order_by(ChatMessage.seq.desc()).limit(limit)).all()
        return list(reversed(rows))

    def list_assessments(self, conversation_id: uuid.UUID, *, from_seq: int) -> list[Row]:
        """`from_seq` 메시지부터 끝까지의 판단을 메시지 순서대로.

        판단을 대체한 새 판단은 늘 그 뒤 메시지에 있다. 그래서 한 페이지의 판단이 대체됐는지는
        그 페이지의 첫 메시지부터 읽으면 대화 전체로 정한 것과 같다.
        """
        return list(
            self.db.execute(
                select(ChatMessage.seq, ChatMessage.assessments)
                .where(
                    ChatMessage.conversation_id == conversation_id,
                    ChatMessage.seq >= from_seq,
                    ChatMessage.assessments.is_not(None),
                )
                .order_by(ChatMessage.seq)
            ).all()
        )

    def owned_pitch_ids(self, user_id: uuid.UUID, pitch_ids: set[uuid.UUID]) -> set[uuid.UUID]:
        """아직 있고 이 사용자의 것인 피치."""
        if not pitch_ids:
            return set()
        return set(
            self.db.scalars(
                select(Pitch.id).where(Pitch.id.in_(pitch_ids), Pitch.user_id == user_id)
            )
        )

    def owned_take_ids(self, user_id: uuid.UUID, take_ids: set[uuid.UUID]) -> set[uuid.UUID]:
        """아직 있고 이 사용자의 피치에 속한 Take."""
        if not take_ids:
            return set()
        return set(
            self.db.scalars(
                select(Take.id)
                .join(Pitch, Pitch.id == Take.pitch_id)
                .where(Take.id.in_(take_ids), Pitch.user_id == user_id)
            )
        )

    def charge_turn(self, user_id: uuid.UUID, day: date, limit: int) -> bool:
        """사용자 하루 턴을 1 선점한다. 한도에 닿았으면 늘리지 않고 False."""
        stmt = (
            insert(ChatDailyUsage)
            .values(id=uuid.uuid7(), user_id=user_id, day=day, turns=1)
            .on_conflict_do_update(
                index_elements=[ChatDailyUsage.user_id, ChatDailyUsage.day],
                set_={"turns": ChatDailyUsage.turns + 1},
                where=ChatDailyUsage.turns < limit,
            )
            .returning(ChatDailyUsage.id)
        )
        return self.db.scalar(stmt) is not None

    def release_service_turn(self, day: date) -> None:
        """보내지 않고 끝난 처음 호출의 서비스 전체 예약을 돌려준다."""
        self.db.execute(
            update(ChatDailyUsage)
            .where(
                ChatDailyUsage.user_id.is_(None),
                ChatDailyUsage.day == day,
                ChatDailyUsage.reserved > 0,
            )
            .values(reserved=ChatDailyUsage.reserved - 1)
        )
