"""대화 API(화면 ↔ BE) 가 읽고 쓰는 쿼리. AI 턴 실행 쪽 쿼리는 repository.py 에 있다."""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Row, Select, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.entity import (
    ChatAction,
    ChatConversation,
    ChatDailyUsage,
    ChatGoal,
    ChatMessage,
    ChatRun,
    ChatRunEvent,
)
from pitch_coach_backend.module.pitch.entity import Pitch
from pitch_coach_backend.module.take.entity import Take

# 한 주기에 끝낼 승인 대기 수. 남은 건은 다음 주기에 고른다
OVERDUE_LIMIT = 100


def _locked(stmt: Select) -> Select:
    # 잠금을 기다리는 사이 바뀐 값을 읽는다. 세션이 앞서 읽은 값을 그대로 두면 낡은 상태로 판단한다
    return stmt.with_for_update().execution_options(populate_existing=True)


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
        return self.db.scalar(_locked(stmt) if lock else stmt)

    def list_conversations(
        self,
        user_id: uuid.UUID,
        pitch_id: uuid.UUID | None,
        *,
        limit: int,
        after: tuple[datetime, uuid.UUID] | None,
    ) -> list[Row]:
        """`after`(마지막 활동 시각, 대화 ID) 다음부터 최근 활동 순으로 읽는다."""
        stmt = (
            select(ChatConversation, Pitch.title.label("pitch_title"))
            .outerjoin(Pitch, Pitch.id == ChatConversation.scope_pitch_id)
            .where(ChatConversation.user_id == user_id)
            .order_by(ChatConversation.last_user_activity_at.desc(), ChatConversation.id.desc())
            .limit(limit)
        )
        if pitch_id is not None:
            stmt = stmt.where(ChatConversation.scope_pitch_id == pitch_id)
        if after is not None:
            # 정렬과 같은 순서의 행 비교다. 같은 시각의 대화도 ID 로 갈려 빠짐 · 중복이 없다
            stmt = stmt.where(
                tuple_(ChatConversation.last_user_activity_at, ChatConversation.id) < after
            )
        return list(self.db.execute(stmt).all())

    def get_owned_run(
        self, run_id: uuid.UUID, user_id: uuid.UUID, *, lock: bool = False
    ) -> ChatRun | None:
        stmt = select(ChatRun).where(ChatRun.id == run_id, ChatRun.user_id == user_id)
        return self.db.scalar(_locked(stmt) if lock else stmt)

    def latest_run(self, conversation_id: uuid.UUID) -> ChatRun | None:
        return self.db.scalar(
            select(ChatRun)
            .where(ChatRun.conversation_id == conversation_id)
            .order_by(ChatRun.accepted_at.desc(), ChatRun.id.desc())
            .limit(1)
        )

    def list_run_events(
        self, run_id: uuid.UUID, *, after_seq: int, limit: int
    ) -> list[ChatRunEvent]:
        return list(
            self.db.scalars(
                select(ChatRunEvent)
                .where(ChatRunEvent.run_id == run_id, ChatRunEvent.event_seq > after_seq)
                .order_by(ChatRunEvent.event_seq)
                .limit(limit)
            )
        )

    def latest_answers(self, conversation_ids: list[uuid.UUID]) -> list[ChatMessage]:
        """대화마다 마지막 에이전트 답. 목록의 '최신 아님' 표시에 쓴다."""
        if not conversation_ids:
            return []
        return list(
            self.db.scalars(
                select(ChatMessage)
                .where(
                    ChatMessage.conversation_id.in_(conversation_ids),
                    ChatMessage.role == "assistant",
                )
                .distinct(ChatMessage.conversation_id)
                .order_by(ChatMessage.conversation_id, ChatMessage.seq.desc())
            )
        )

    def lock_conversation_of(self, run: ChatRun) -> ChatConversation | None:
        return self.get_owned_conversation(run.conversation_id, run.user_id, lock=True)

    def active_run(self, conversation_id: uuid.UUID) -> ChatRun | None:
        """끝나지 않은 실행(대화마다 하나). 앞서 읽은 값이 있어도 지금 값으로 다시 읽는다."""
        return self.db.scalar(
            select(ChatRun)
            .where(
                ChatRun.conversation_id == conversation_id,
                ChatRun.status.in_(("QUEUED", "RUNNING", "WAITING_APPROVAL")),
            )
            .limit(1)
            .execution_options(populate_existing=True)
        )

    def overdue_waiting_run_ids(self, started_before: datetime) -> list[uuid.UUID]:
        return list(
            self.db.scalars(
                select(ChatRun.id)
                .where(
                    ChatRun.status == "WAITING_APPROVAL",
                    ChatRun.approval_started_at <= started_before,
                )
                .order_by(ChatRun.approval_started_at)
                .limit(OVERDUE_LIMIT)
            )
        )

    def waiting_action(self, run_id: uuid.UUID) -> ChatAction | None:
        """승인 · 거절을 기다리는 행동. 승인 대기는 한 번에 하나다."""
        return self.db.scalar(
            select(ChatAction).where(
                ChatAction.run_id == run_id,
                ChatAction.autonomy == "ASK",
                ChatAction.status == "PENDING",
                ChatAction.approved.is_(None),
            )
        )

    def action_source_take_ids(self, run_id: uuid.UUID) -> list[Any]:
        """행동마다 근거로 삼은 Take ID 목록. 1초 조회 경로라 큰 JSONB 칸은 읽지 않는다."""
        return list(
            self.db.scalars(select(ChatAction.source_take_ids).where(ChatAction.run_id == run_id))
        )

    def get_message_in(
        self, message_id: uuid.UUID, conversation_id: uuid.UUID
    ) -> ChatMessage | None:
        return self.db.scalar(
            select(ChatMessage).where(
                ChatMessage.id == message_id, ChatMessage.conversation_id == conversation_id
            )
        )

    def list_assessments(self, conversation_id: uuid.UUID, *, from_seq: int) -> list[Row]:
        """`from_seq` 메시지부터 끝까지의 판단과 그 메시지의 근거를 메시지 순서대로.

        판단을 대체한 새 판단은 늘 그 뒤 메시지에 있다. 그래서 한 페이지의 판단이 대체됐는지는
        그 페이지의 첫 메시지부터 읽으면 대화 전체로 정한 것과 같다.
        """
        return list(
            self.db.execute(
                select(
                    ChatMessage.assessments,
                    ChatMessage.source_pitch_ids,
                    ChatMessage.source_take_ids,
                )
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

    def goal_saved_by(self, conversation_id: uuid.UUID, action_pk: uuid.UUID) -> ChatGoal | None:
        """그 목표 저장 행동이 만든 목표 판."""
        return self.db.scalar(
            select(ChatGoal).where(
                ChatGoal.conversation_id == conversation_id, ChatGoal.action_id == action_pk
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
