import uuid
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import Row, Select, and_, delete, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, aliased

from pitch_coach_backend.module.chat.entity import (
    ChatAction,
    ChatConversation,
    ChatDailyUsage,
    ChatGoal,
    ChatLease,
    ChatMemorySetting,
    ChatMessage,
    ChatProfileItem,
    ChatRun,
    ChatRunEvent,
    ChatRunLog,
    ChatSummary,
)
from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion
from pitch_coach_backend.module.take.entity import Mission, Take, TakeAIResults, TakeSlideEvent

FINISHED_RUN_STATUSES = ("DONE", "PARTIAL", "REFUSED", "FAILED", "CANCELLED", "EXPIRED")


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

    def get_approved_action(self, run_id: uuid.UUID) -> ChatAction | None:
        return self.db.scalar(
            select(ChatAction).where(
                ChatAction.run_id == run_id,
                ChatAction.approved.is_(True),
                ChatAction.status == "PENDING",
            )
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

    def foreign_sources(
        self, user_id: uuid.UUID, pitch_ids: set[uuid.UUID], take_ids: set[uuid.UUID]
    ) -> bool:
        take_pitches = select(Take.pitch_id).where(Take.id.in_(take_ids))
        return bool(
            self.db.scalar(
                select(
                    exists().where(
                        or_(Pitch.id.in_(pitch_ids), Pitch.id.in_(take_pitches)),
                        Pitch.user_id != user_id,
                    )
                )
            )
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

    def charge_goal_check(self, user_id: uuid.UUID, day: date, limit: int) -> bool:
        stmt = (
            insert(ChatDailyUsage)
            .values(id=uuid.uuid7(), user_id=user_id, day=day, goal_checks=1)
            .on_conflict_do_update(
                index_elements=[ChatDailyUsage.user_id, ChatDailyUsage.day],
                set_={"goal_checks": ChatDailyUsage.goal_checks + 1},
                where=ChatDailyUsage.goal_checks < limit,
            )
            .returning(ChatDailyUsage.id)
        )
        return self.db.scalar(stmt) is not None

    def reserve_service_turn(self, day: date, limit: int) -> bool:
        stmt = (
            insert(ChatDailyUsage)
            .values(id=uuid.uuid7(), user_id=None, day=day, reserved=1)
            .on_conflict_do_update(
                index_elements=[ChatDailyUsage.user_id, ChatDailyUsage.day],
                set_={"reserved": ChatDailyUsage.reserved + 1},
                where=ChatDailyUsage.reserved + ChatDailyUsage.sent < limit,
            )
            .returning(ChatDailyUsage.id)
        )
        return self.db.scalar(stmt) is not None

    def release_service_turn(self, day: date) -> None:
        self.db.execute(
            update(ChatDailyUsage)
            .where(
                ChatDailyUsage.user_id.is_(None),
                ChatDailyUsage.day == day,
                ChatDailyUsage.reserved > 0,
            )
            .values(reserved=ChatDailyUsage.reserved - 1)
        )

    def add_service_turn(self, day: date) -> None:
        self.db.execute(
            insert(ChatDailyUsage)
            .values(id=uuid.uuid7(), user_id=None, day=day, sent=1)
            .on_conflict_do_update(
                index_elements=[ChatDailyUsage.user_id, ChatDailyUsage.day],
                set_={"sent": ChatDailyUsage.sent + 1},
            )
        )

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

    def get_memory_setting(self, user_id: uuid.UUID) -> ChatMemorySetting | None:
        return self.db.get(ChatMemorySetting, user_id)

    def lock_memory_setting(self, user_id: uuid.UUID) -> ChatMemorySetting | None:
        return self.db.scalar(
            select(ChatMemorySetting)
            .where(ChatMemorySetting.user_id == user_id)
            .with_for_update(read=True)
        )

    def upsert_profile_item(self, user_id: uuid.UUID, key: str, value: str) -> uuid.UUID:
        return self.db.scalar(
            insert(ChatProfileItem)
            .values(id=uuid.uuid7(), user_id=user_id, key=key, value=value)
            .on_conflict_do_update(
                constraint="uq_chat_profile_items_user_key",
                set_={"value": value, "updated_at": func.now()},
            )
            .returning(ChatProfileItem.id)
        )

    def list_profile_items(self, user_id: uuid.UUID) -> Sequence[ChatProfileItem]:
        return self.db.scalars(
            select(ChatProfileItem)
            .where(ChatProfileItem.user_id == user_id)
            .order_by(ChatProfileItem.key)
        ).all()

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

    def next_event_seq(self, run_id: uuid.UUID) -> int:
        last = self.db.scalar(
            select(func.max(ChatRunEvent.event_seq)).where(ChatRunEvent.run_id == run_id)
        )
        return (last or 0) + 1

    def list_claim_events(self, run_id: uuid.UUID) -> list[ChatRunEvent]:
        return list(
            self.db.scalars(
                select(ChatRunEvent)
                .where(ChatRunEvent.run_id == run_id, ChatRunEvent.event == "claim")
                .order_by(ChatRunEvent.event_seq)
            )
        )

    def list_recent_messages(
        self, conversation_id: uuid.UUID, *, before_seq: int | None, limit: int
    ) -> list[ChatMessage]:
        stmt = select(ChatMessage).where(ChatMessage.conversation_id == conversation_id)
        if before_seq is not None:
            stmt = stmt.where(ChatMessage.seq < before_seq)
        rows = self.db.scalars(stmt.order_by(ChatMessage.seq.desc()).limit(limit)).all()
        return list(reversed(rows))

    def get_summary(self, conversation_id: uuid.UUID) -> ChatSummary | None:
        return self.db.get(ChatSummary, conversation_id)

    def lock_summary(self, conversation_id: uuid.UUID) -> ChatSummary | None:
        return self.db.scalar(
            select(ChatSummary)
            .where(ChatSummary.conversation_id == conversation_id)
            .with_for_update()
        )

    def list_overflow_messages(
        self, conversation_id: uuid.UUID, *, after_seq: int, before_seq: int, limit: int
    ) -> list[ChatMessage]:
        return list(
            self.db.scalars(
                select(ChatMessage)
                .where(
                    ChatMessage.conversation_id == conversation_id,
                    ChatMessage.seq > after_seq,
                    ChatMessage.seq < before_seq,
                )
                .order_by(ChatMessage.seq)
                .limit(limit)
            )
        )

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

    def last_completed_at(self, pitch_id: uuid.UUID) -> datetime | None:
        return self.db.scalar(
            select(func.max(Take.completed_at)).where(
                Take.pitch_id == pitch_id, Take.status == "COMPLETED"
            )
        )

    def latest_corpus_version(self, since: datetime) -> str | None:
        """같은 배포에서 가장 최근에 성공한 AI 응답의 자료 판(chat-agent.md 12-3).

        AI 응답으로 만들지 않은 메시지(실패 · 시간 초과 · 승인 대기 종료)는 판이 없어 고르지 않는다.
        """
        version = ChatMessage.meta["corpus_version"].astext
        return self.db.scalar(
            select(version)
            .where(
                ChatMessage.role == "assistant",
                ChatMessage.created_at >= since,
                version.is_not(None),
            )
            .order_by(ChatMessage.created_at.desc())
            .limit(1)
        )

    def list_open_goals(self) -> list[Row]:
        return list(
            self.db.execute(
                select(
                    ChatGoal.conversation_id,
                    ChatConversation.user_id,
                    ChatGoal.version,
                    ChatGoal.goal["pitch_id"].astext.label("pitch_id"),
                    ChatGoal.first_take_number,
                )
                .join(
                    ChatConversation,
                    and_(
                        ChatConversation.id == ChatGoal.conversation_id,
                        ChatConversation.goal_version == ChatGoal.version,
                    ),
                )
                .where(ChatGoal.status == "OPEN", ChatGoal.first_take_number.is_not(None))
                .order_by(ChatGoal.created_at)
            )
        )

    def get_cleanup_lease(self) -> ChatLease | None:
        return self.db.scalar(select(ChatLease).where(ChatLease.kind == "CLEANUP"))

    def take_cleanup(self, owner: str, until: datetime, now: datetime) -> ChatLease | None:
        stmt = (
            insert(ChatLease)
            .values(
                id=uuid.uuid7(), kind="CLEANUP", owner=owner, lease_until=until, fencing_token=1
            )
            .on_conflict_do_update(
                index_elements=[ChatLease.kind],
                index_where=ChatLease.kind == "CLEANUP",
                set_={
                    "owner": owner,
                    "lease_until": until,
                    "fencing_token": ChatLease.fencing_token + 1,
                },
                where=ChatLease.lease_until <= now,
            )
            .returning(ChatLease)
        )
        return self.db.scalars(stmt).first()

    def existing_pitch_ids(self, pitch_ids: set[uuid.UUID]) -> set[uuid.UUID]:
        return set(self.db.scalars(select(Pitch.id).where(Pitch.id.in_(pitch_ids))))

    def existing_take_ids(self, take_ids: set[uuid.UUID]) -> set[uuid.UUID]:
        return set(self.db.scalars(select(Take.id).where(Take.id.in_(take_ids))))

    def list_unmasked_answers(self, masked_text: str) -> list[ChatMessage]:
        return list(
            self.db.scalars(
                select(ChatMessage).where(
                    ChatMessage.role == "assistant",
                    ChatMessage.text != masked_text,
                )
            )
        )

    def list_finished_runs_with_content(self) -> list[ChatRun]:
        return list(
            self.db.scalars(
                select(ChatRun).where(
                    ChatRun.status.in_(FINISHED_RUN_STATUSES),
                    or_(
                        ChatRun.state.is_not(None),
                        ChatRun.interim_message.is_not(None),
                        ChatRun.plan.is_not(None),
                        ChatRun.trace.is_not(None),
                        exists().where(ChatRunEvent.run_id == ChatRun.id),
                    ),
                )
            )
        )

    def list_actions(self, run_id: uuid.UUID) -> list[ChatAction]:
        return list(self.db.scalars(select(ChatAction).where(ChatAction.run_id == run_id)))

    def list_unfinished_run_ids(self) -> list[uuid.UUID]:
        return list(
            self.db.scalars(select(ChatRun.id).where(ChatRun.status.in_(("QUEUED", "RUNNING"))))
        )

    def get_goal_from_action(self, action_pk: uuid.UUID) -> ChatGoal | None:
        return self.db.scalar(select(ChatGoal).where(ChatGoal.action_id == action_pk))

    def get_last_action(self, run_id: uuid.UUID) -> ChatAction | None:
        return self.db.scalar(
            select(ChatAction)
            .where(ChatAction.run_id == run_id)
            .order_by(ChatAction.created_at.desc(), ChatAction.id.desc())
            .limit(1)
        )

    def renew_run_lease(self, run_id: uuid.UUID, owner: str, until: datetime) -> bool:
        result = self.db.execute(
            update(ChatRun)
            .where(
                ChatRun.id == run_id,
                ChatRun.lease_owner == owner,
                ChatRun.status.in_(("QUEUED", "RUNNING")),
            )
            .values(lease_until=until)
        )
        return result.rowcount > 0

    def delete_run_events(self, run_id: uuid.UUID) -> None:
        self.db.execute(delete(ChatRunEvent).where(ChatRunEvent.run_id == run_id))

    def delete_inactive_conversations(self, cutoff: datetime, limit: int) -> int:
        inactive = ChatConversation.last_user_activity_at < cutoff
        result = self.db.execute(
            delete(ChatConversation).where(
                ChatConversation.id.in_(select(ChatConversation.id).where(inactive).limit(limit)),
                inactive,
            )
        )
        return result.rowcount

    def delete_finished_runs(self, cutoff: datetime, limit: int) -> int:
        finished = and_(ChatRun.status.in_(FINISHED_RUN_STATUSES), ChatRun.finished_at < cutoff)
        run_ids = list(self.db.scalars(select(ChatRun.id).where(finished).limit(limit)))
        if not run_ids:
            return 0
        self.db.execute(
            update(ChatAction).where(ChatAction.run_id.in_(run_ids)).values(result=None)
        )
        result = self.db.execute(delete(ChatRun).where(ChatRun.id.in_(run_ids), finished))
        return result.rowcount

    def delete_run_logs_before(self, day: date) -> int:
        result = self.db.execute(delete(ChatRunLog).where(ChatRunLog.day < day))
        return result.rowcount

    def delete_unused_profile_items(self, cutoff: datetime) -> int:
        last_used = func.coalesce(ChatProfileItem.last_used_at, ChatProfileItem.created_at)
        result = self.db.execute(delete(ChatProfileItem).where(last_used < cutoff))
        return result.rowcount

    def touch_profile_items(self, item_ids: list[uuid.UUID], now: datetime) -> None:
        if item_ids:
            self.db.execute(
                update(ChatProfileItem)
                .where(ChatProfileItem.id.in_(item_ids))
                .values(last_used_at=now)
            )

    def list_summaries(self) -> list[ChatSummary]:
        return list(self.db.scalars(select(ChatSummary)))

    def delete_summary(self, summary: ChatSummary) -> None:
        self.db.delete(summary)

    def list_goals_with_base(self) -> list[ChatGoal]:
        return list(
            self.db.scalars(
                select(ChatGoal).where(
                    ChatGoal.base_take_id.is_not(None), ChatGoal.goal.is_not(None)
                )
            )
        )

    def list_goals_without_base(self) -> list[Row]:
        return list(
            self.db.execute(
                select(ChatGoal.conversation_id, ChatGoal.version)
                .join(
                    ChatConversation,
                    and_(
                        ChatConversation.id == ChatGoal.conversation_id,
                        ChatConversation.goal_version == ChatGoal.version,
                    ),
                )
                .where(
                    ChatGoal.base_take_id.is_not(None),
                    ~exists().where(Take.id == ChatGoal.base_take_id),
                )
            )
        )

    def list_takes_from(self, pitch_id: uuid.UUID, first_take_number: int) -> list[Row]:
        return list(
            self.db.execute(
                select(
                    Take.id,
                    Take.status,
                    Take.review_status,
                    TakeAIResults.revision.label("review_revision"),
                )
                .outerjoin(
                    TakeAIResults,
                    and_(TakeAIResults.take_id == Take.id, TakeAIResults.kind == "REVIEW"),
                )
                .where(Take.pitch_id == pitch_id, Take.take_number >= first_take_number)
                .order_by(Take.take_number)
            )
        )

    def has_running_run(self, conversation_id: uuid.UUID) -> bool:
        return (
            self.db.scalar(
                select(ChatRun.id)
                .where(
                    ChatRun.conversation_id == conversation_id,
                    ChatRun.status.in_(("QUEUED", "RUNNING", "WAITING_APPROVAL")),
                )
                .limit(1)
            )
            is not None
        )

    def take_goal_check(
        self,
        *,
        conversation_id: uuid.UUID,
        goal_version: int,
        take_id: uuid.UUID,
        review_revision: int,
        owner: str,
        until: datetime,
        now: datetime,
    ) -> ChatLease | None:
        stmt = (
            insert(ChatLease)
            .values(
                id=uuid.uuid7(),
                kind="GOAL_CHECK",
                conversation_id=conversation_id,
                goal_version=goal_version,
                take_id=take_id,
                review_revision=review_revision,
                status="PENDING",
                owner=owner,
                lease_until=until,
                fencing_token=1,
            )
            .on_conflict_do_update(
                constraint="uq_chat_leases_goal_check",
                set_={
                    "owner": owner,
                    "lease_until": until,
                    "fencing_token": ChatLease.fencing_token + 1,
                },
                where=and_(ChatLease.status == "PENDING", ChatLease.lease_until <= now),
            )
            .returning(ChatLease)
        )
        return self.db.scalars(stmt).first()

    def lock_goal_check(self, run_id: uuid.UUID) -> ChatLease | None:
        return self.db.scalar(select(ChatLease).where(ChatLease.run_id == run_id).with_for_update())

    def get_review_revision(self, take_id: uuid.UUID) -> int | None:
        return self.db.scalar(
            select(TakeAIResults.revision).where(
                TakeAIResults.take_id == take_id, TakeAIResults.kind == "REVIEW"
            )
        )

    def list_goal_leases(self, conversation_id: uuid.UUID, goal_version: int) -> list[ChatLease]:
        return list(
            self.db.scalars(
                select(ChatLease).where(
                    ChatLease.kind == "GOAL_CHECK",
                    ChatLease.conversation_id == conversation_id,
                    ChatLease.goal_version == goal_version,
                )
            )
        )

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
                select(Pitch.id).where(
                    Pitch.user_id == user_id, Pitch.title.icontains(name, autoescape=True)
                )
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
