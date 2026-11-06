"""코칭 프로필 기억 설정 · 항목 API(chat-agent.md 6-14, 12-3 '기억 설정 · 코칭 프로필').

설정을 바꾸거나 항목을 지우는 쪽과 프로필을 저장하는 쪽(service/memory.py `_accepted`)이 설정 행으로
줄을 선다. 여기서는 번호를 먼저 올려 설정 행을 쥔 채 지우고, 저장 쪽은 그 행을 FOR SHARE 로 잠근 채
번호를 비교한다. 그래서 먼저 커밋한 쪽의 결과를 뒤쪽이 본다.
 - 저장이 먼저면: 번호 올리기가 저장의 커밋을 기다리고, 뒤이은 삭제가 방금 저장된 항목까지 지운다.
 - 여기가 먼저면: 저장이 커밋을 기다렸다가 바뀐 번호를 읽고 버린다.
번호를 올리기 전에 지우면 저장 중인 항목을 보지 못해 지운 뒤에 남으므로 순서를 바꾸지 않는다.
"""

import uuid

from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.api_dto import MemoryItemResponse, MemorySettingResponse
from pitch_coach_backend.module.chat.api_repository import ChatApiRepository
from pitch_coach_backend.module.chat.exception import ChatTargetNotFound
from pitch_coach_backend.module.chat.repository import ChatRepository


def read_memory(db: Session, user_id: uuid.UUID) -> MemorySettingResponse:
    # 설정 행이 없어도 항목은 있을 수 있다. 기본 켜짐으로 저장할 때는 행을 만들지 않는다
    enabled = ChatApiRepository(db).memory_enabled(user_id)
    return MemorySettingResponse(
        enabled=True if enabled is None else enabled,
        items=[
            MemoryItemResponse(item_id=item.id, key=item.key, value=item.value)
            for item in ChatRepository(db).list_profile_items(user_id)
        ],
    )


def set_enabled(db: Session, user_id: uuid.UUID, enabled: bool) -> MemorySettingResponse:
    repo = ChatApiRepository(db)
    _wait_for_saving_runs(repo, user_id)
    repo.set_memory_enabled(user_id, enabled)
    view = read_memory(db, user_id)
    db.commit()
    return view


def delete_all_items(db: Session, user_id: uuid.UUID) -> MemorySettingResponse:
    repo = ChatApiRepository(db)
    _wait_for_saving_runs(repo, user_id)
    repo.bump_memory_version(user_id)
    if not repo.delete_profile_items(user_id):
        # 줄어든 항목이 없으면 번호는 그대로다. 실행 중 프로필 후보를 괜히 버리지 않는다
        db.rollback()
        return read_memory(db, user_id)
    view = read_memory(db, user_id)
    db.commit()
    return view


def delete_item(db: Session, user_id: uuid.UUID, item_id: uuid.UUID) -> MemorySettingResponse:
    repo = ChatApiRepository(db)
    _wait_for_saving_runs(repo, user_id)
    repo.bump_memory_version(user_id)
    if not repo.delete_profile_item(user_id, item_id):
        db.rollback()
        raise ChatTargetNotFound()
    view = read_memory(db, user_id)
    db.commit()
    return view


def _wait_for_saving_runs(repo: ChatApiRepository, user_id: uuid.UUID) -> None:
    """설정 행이 없으면 끝나지 않은 실행 행으로 줄을 선다.

    저장 쪽은 잠글 설정 행이 없으면 기다리지 않고 번호 0 으로 통과한다. 이때 여기서 행을 만들고
    지워도 아직 커밋 전인 항목은 보이지 않아 지운 뒤에 남는다. 저장 쪽은 실행 행을 잠근 채로
    설정 행을 읽으므로, 실행 행을 먼저 잠그면 진행 중인 저장의 커밋을 기다리고 그 뒤의 저장은
    여기서 만든 행을 본다.
    """
    if repo.memory_enabled(user_id) is None:
        repo.lock_active_runs(user_id)
