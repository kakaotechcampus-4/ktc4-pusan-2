"""코치 대화 목록을 이어 읽는 커서. FE 는 풀어 보지 않고 받은 값을 그대로 돌려보낸다.

담는 값은 목록의 정렬 기준인 마지막 활동 시각과 대화 ID 뿐이다. 사용자는 담지 않고 쿼리가
매번 로그인한 사용자로 거른다.
"""

import base64
import uuid
from datetime import UTC, datetime

from pitch_coach_backend.module.chat.exception import ChatCursorInvalid


def encode_cursor(last_user_activity_at: datetime, conversation_id: uuid.UUID) -> str:
    # UTC 로 고정하고 마이크로초까지 적는다. 저장한 값과 같은 위치를 가리켜야 빠짐 · 중복이 없다
    at = last_user_activity_at.astimezone(UTC).isoformat(timespec="microseconds")
    return base64.urlsafe_b64encode(f"{at}|{conversation_id}".encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        at_text, id_text = raw.decode().split("|")
        at = datetime.fromisoformat(at_text)
        conversation_id = uuid.UUID(id_text)
        # 위에서 넘어간 다른 표기(시간대 · 패딩 · 하이픈)는 여기서 거절해 만든 모양만 받는다.
        # UTC 로 바꾸면 범위를 벗어나는 시각은 OverflowError 다
        if at.tzinfo is None or encode_cursor(at, conversation_id) != cursor:
            raise ValueError("커서 모양이 다릅니다.")
    except (ValueError, OverflowError) as exc:
        raise ChatCursorInvalid() from exc
    return at, conversation_id
