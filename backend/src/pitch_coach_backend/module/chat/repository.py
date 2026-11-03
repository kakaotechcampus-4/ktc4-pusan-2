import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import Row, and_, func, select
from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.entity import (
    ChatAction,
    ChatConversation,
    ChatGoal,
    ChatMessage,
    ChatRun,
)
from pitch_coach_backend.module.pitch.entity import Pitch
from pitch_coach_backend.module.take.entity import Take, TakeAIResults


class ChatRepository:
    def __init__(self, db: Session):
        self.db = db

    def add(self, entity: Any) -> None:
        self.db.add(entity)

    def get_run(self, run_id: uuid.UUID) -> ChatRun | None:
        return self.db.get(ChatRun, run_id)

    def get_action(self, run_id: uuid.UUID, action_id: str) -> ChatAction | None:
        return self.db.scalar(
            select(ChatAction).where(ChatAction.run_id == run_id, ChatAction.action_id == action_id)
        )

    def lock_run(
        self,
        run_id: uuid.UUID,
        *,
        statuses: Sequence[str],
        row_revision: int | None = None,
    ) -> ChatRun | None:
        stmt = select(ChatRun).where(ChatRun.id == run_id, ChatRun.status.in_(statuses))
        if row_revision is not None:
            stmt = stmt.where(ChatRun.row_revision == row_revision)
        return self.db.scalar(stmt.with_for_update())

    def get_conversation(self, conversation_id: uuid.UUID) -> ChatConversation | None:
        return self.db.get(ChatConversation, conversation_id)

    def lock_conversation(self, conversation_id: uuid.UUID) -> ChatConversation | None:
        return self.db.scalar(
            select(ChatConversation).where(ChatConversation.id == conversation_id).with_for_update()
        )

    def get_message(self, message_id: uuid.UUID) -> ChatMessage | None:
        return self.db.get(ChatMessage, message_id)

    def next_message_seq(self, conversation_id: uuid.UUID) -> int:
        last = self.db.scalar(
            select(func.max(ChatMessage.seq)).where(ChatMessage.conversation_id == conversation_id)
        )
        return (last or 0) + 1

    def list_recent_messages(
        self, conversation_id: uuid.UUID, *, before_seq: int | None, limit: int
    ) -> list[ChatMessage]:
        stmt = select(ChatMessage).where(ChatMessage.conversation_id == conversation_id)
        if before_seq is not None:
            stmt = stmt.where(ChatMessage.seq < before_seq)
        rows = self.db.scalars(stmt.order_by(ChatMessage.seq.desc()).limit(limit)).all()
        return list(reversed(rows))

    def list_recent_assessments(
        self, conversation_id: uuid.UUID, *, before_seq: int | None, limit: int
    ) -> list[dict[str, Any]]:
        stmt = select(ChatMessage.assessments).where(
            ChatMessage.conversation_id == conversation_id,
            ChatMessage.assessments.is_not(None),
        )
        if before_seq is not None:
            stmt = stmt.where(ChatMessage.seq < before_seq)
        recent: list[dict[str, Any]] = []
        for assessments in self.db.scalars(stmt.order_by(ChatMessage.seq.desc())):
            recent[:0] = assessments
            if len(recent) >= limit:
                return recent[-limit:]
        return recent

    def find_proposal_card(
        self, conversation_id: uuid.UUID, proposal_id: str
    ) -> dict[str, Any] | None:
        cards = self.db.scalars(
            select(ChatMessage.cards).where(
                ChatMessage.conversation_id == conversation_id,
                ChatMessage.role == "assistant",
                ChatMessage.cards.contains([{"type": "GOAL_PROPOSAL", "proposal_id": proposal_id}]),
            )
        ).first()
        if cards is None:
            return None
        return next(
            (
                card
                for card in cards
                if card.get("type") == "GOAL_PROPOSAL" and card.get("proposal_id") == proposal_id
            ),
            None,
        )

    def get_goal(self, conversation_id: uuid.UUID, version: int) -> ChatGoal | None:
        return self.db.get(ChatGoal, (conversation_id, version))

    def list_pitch_summaries(self, user_id: uuid.UUID, *, limit: int) -> list[Row]:
        stats = (
            select(
                Take.pitch_id,
                func.count(Take.id).filter(Take.status == "COMPLETED").label("take_count"),
                func.max(Take.started_at).label("last_practiced_at"),
            )
            .group_by(Take.pitch_id)
            .subquery()
        )
        stmt = (
            select(
                Pitch.id.label("pitch_id"),
                Pitch.title,
                func.coalesce(stats.c.take_count, 0).label("take_count"),
                stats.c.last_practiced_at,
            )
            .outerjoin(stats, stats.c.pitch_id == Pitch.id)
            .where(Pitch.user_id == user_id)
            .order_by(
                stats.c.last_practiced_at.desc().nulls_last(),
                Pitch.created_at.desc(),
                Pitch.id.desc(),
            )
            .limit(limit)
        )
        return list(self.db.execute(stmt).all())

    def count_pitches(self, user_id: uuid.UUID) -> int:
        return self.db.scalar(
            select(func.count()).select_from(Pitch).where(Pitch.user_id == user_id)
        )

    def list_last_reviews(self, pitch_ids: Sequence[uuid.UUID]) -> list[Row]:
        if not pitch_ids:
            return []
        stmt = (
            select(
                Take.pitch_id,
                Take.id.label("take_id"),
                Take.take_number,
                TakeAIResults.payload,
            )
            .join(
                TakeAIResults,
                and_(TakeAIResults.take_id == Take.id, TakeAIResults.kind == "REVIEW"),
            )
            .where(Take.pitch_id.in_(pitch_ids))
            .distinct(Take.pitch_id)
            .order_by(Take.pitch_id, Take.take_number.desc())
        )
        return list(self.db.execute(stmt).all())
