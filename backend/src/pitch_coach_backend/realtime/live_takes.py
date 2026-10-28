"""HTTP 요청이 발표 중인 Take 에 알릴 일 (`module.hooks.LiveTakes` 구현). main.py 가 꽂는다.

요청 스레드(동기 엔드포인트)에서 불린다. 스트림 · 코치는 이벤트 루프에 있어서 루프로 넘긴다.
코치 · 전사 저장소는 프로세스에 하나라 여기서 바로 꺼내 쓴다 (`coach.dependencies` ·
`realtime.dependencies`).
"""

import uuid
from collections.abc import Iterable

import anyio

from pitch_coach_backend.coach.dependencies import get_coach_client, get_coach_store
from pitch_coach_backend.module.hooks import MarkEnded
from pitch_coach_backend.module.take.exception import TakeRecordsNotSaved
from pitch_coach_backend.realtime.dependencies import get_transcript_store
from pitch_coach_backend.realtime.stream import finish, record, registry


class RealtimeLiveTakes:
    def discard(self, take_ids: Iterable[uuid.UUID]) -> None:
        anyio.from_thread.run(registry.discard, list(take_ids))

    def is_live(self, take_id: uuid.UUID) -> bool:
        return anyio.from_thread.run_sync(registry.is_live, take_id)

    def end_closed_tab(self, take_id: uuid.UUID) -> None:
        anyio.from_thread.run(registry.end_closed_tab, take_id)

    def end(self, take_id: uuid.UUID, mark: MarkEnded) -> None:
        ending = anyio.from_thread.run(finish.end_without_stop, take_id)
        if ending is None:
            # 연결이 붙어 있다. 그 연결의 stop 이 마지막 전사를 저장한 뒤 끝낸다
            return
        coach = record.CoachDeps(client=get_coach_client(), store=get_coach_store())
        if not ending.finals_saved:
            # 확정 전사를 아직 저장하지 못했다. 지금 적으면 리포트에서 빠진다. 뒤에서 저장한 뒤
            # 적고, FE 에는 실패로 답해 다시 보내게 한다
            anyio.from_thread.run_sync(
                finish.mark_later, take_id, get_transcript_store(), ending, coach
            )
            raise TakeRecordsNotSaved()
        try:
            marked = mark(ending.end_reason, ending.utterance_ends)
        except Exception:
            # FE 는 실패를 받고 다시 보낼 수 있다. 다시 오지 않아도 뒤에서 마저 적는다
            anyio.from_thread.run_sync(
                finish.mark_later, take_id, get_transcript_store(), ending, coach
            )
            raise
        # WS stop 과 같은 뒤처리 — 적었으면 Take 결과를 띄우고, 아니면 코치의 기억을 놓는다
        anyio.from_thread.run_sync(finish.after_mark, take_id, marked, ending, coach)

    def sign_out(self, user_id: uuid.UUID) -> None:
        anyio.from_thread.run(registry.sign_out, user_id)
