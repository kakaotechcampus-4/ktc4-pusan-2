import uuid
from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy import Row, Select, and_, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased

from pitch_coach_backend.module.chat.entity import (
    ChatAction,
    ChatConversation,
    ChatDailyUsage,
    ChatGoal,
    ChatMessage,
    ChatRun,
)
from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion
from pitch_coach_backend.module.take.entity import Mission, Take, TakeAIResults, TakeSlideEvent


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

    def lock_pitch_id(self, pitch_id: uuid.UUID) -> uuid.UUID | None:
        return self.db.scalar(
            select(Pitch.id).where(Pitch.id == pitch_id).with_for_update(read=True)
        )

    def lock_take_id(self, take_id: uuid.UUID) -> uuid.UUID | None:
        pitch_id = self.db.scalar(select(Take.pitch_id).where(Take.id == take_id))
        if pitch_id is None or self.lock_pitch_id(pitch_id) is None:
            return None
        return self.db.scalar(
            select(Take.id)
            .where(Take.id == take_id, Take.pitch_id == pitch_id)
            .with_for_update(read=True)
        )

    def charge_reassess(self, user_id: uuid.UUID, day: date, limit: int) -> bool:
        stmt = (
            insert(ChatDailyUsage)
            .values(id=uuid.uuid7(), user_id=user_id, day=day, reassessments=1)
            .on_conflict_do_update(
                index_elements=[ChatDailyUsage.user_id, ChatDailyUsage.day],
                set_={"reassessments": ChatDailyUsage.reassessments + 1},
                where=ChatDailyUsage.reassessments < limit,
            )
            .returning(ChatDailyUsage.id)
        )
        return self.db.scalar(stmt) is not None

    def count_succeeded_actions(self, run_id: uuid.UUID, kind: str) -> int:
        return self.db.scalar(
            select(func.count()).where(
                ChatAction.run_id == run_id,
                ChatAction.type == kind,
                ChatAction.status == "SUCCEEDED",
            )
        )

    # 행 잠금. UPDATING, DELETING 시 다른 트랜잭션이 접근하지 못하도록 잠금
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

    # Conversation에 대한 행 잠금. UPDATING, DELETING 시 다른 트랜잭션이 접근하지 못하도록 잠금
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

    def list_pitch_summaries(
        self, user_id: uuid.UUID, *, limit: int, pitch_ids: Sequence[uuid.UUID] | None = None
    ) -> list[Row]:
        # completed 된 take 수와 마지막으로 연습한 시각을 가져오는 서브쿼리
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
        if pitch_ids is not None:
            stmt = stmt.where(Pitch.id.in_(pitch_ids))
        return list(self.db.execute(stmt).all())

    def find_pitch_ids_by_name(self, user_id: uuid.UUID, name: str) -> list[uuid.UUID]:
        return list(
            self.db.scalars(
                select(Pitch.id).where(Pitch.user_id == user_id, Pitch.title.ilike(f"%{name}%"))
            )
        )

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

    def list_recent_takes(self, pitch_id: uuid.UUID, *, limit: int) -> list[Row]:
        stmt = (
            _take_with_review()
            .where(Take.pitch_id == pitch_id)
            .order_by(Take.take_number.desc())
            .limit(limit)
        )
        return list(self.db.execute(stmt).all())

    def get_take_with_review(self, take_id: uuid.UUID) -> Row | None:
        return self.db.execute(_take_with_review().where(Take.id == take_id)).first()

    def list_ai_results(self, take_id: uuid.UUID, kinds: Sequence[str]) -> list[TakeAIResults]:
        return list(
            self.db.scalars(
                select(TakeAIResults).where(
                    TakeAIResults.take_id == take_id, TakeAIResults.kind.in_(kinds)
                )
            )
        )

    def get_latest_completed_take(self, pitch_id: uuid.UUID) -> Row | None:
        return self.db.execute(
            _take_with_review()
            .where(Take.pitch_id == pitch_id, Take.status == "COMPLETED")
            .order_by(Take.completed_at.desc().nulls_last(), Take.take_number.desc())
            .limit(1)
        ).first()

    def list_judged_missions(self, take_id: uuid.UUID) -> list[Mission]:
        return list(
            self.db.scalars(
                select(Mission)
                .where(Mission.take_id == take_id, Mission.status != "ACTIVE")
                .order_by(Mission.priority, Mission.slide_number, Mission.id)
            )
        )

    def slide_numbers(self, take_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, set[int]]:
        if not take_ids:
            return {}
        rows = self.db.execute(
            select(TakeSlideEvent.take_id, TakeSlideEvent.slide_number)
            .where(TakeSlideEvent.take_id.in_(take_ids))
            .distinct()
        )
        slides: dict[uuid.UUID, set[int]] = {}
        for take_id, slide_number in rows:
            slides.setdefault(take_id, set()).add(slide_number)
        return slides


def _take_with_review() -> Select:
    review = aliased(TakeAIResults)
    return (
        select(
            Take,
            PresentationVersion.page_count,
            review.revision.label("review_revision"),
            review.payload.label("review_payload"),
        )
        .join(PresentationVersion, PresentationVersion.id == Take.presentation_version_id)
        .outerjoin(review, and_(review.take_id == Take.id, review.kind == "REVIEW"))
    )
