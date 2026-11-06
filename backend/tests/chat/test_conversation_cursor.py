"""코치 대화 목록 이어 읽기(`cursor` · `next_cursor`)."""

import base64
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from pitch_coach_backend.module.chat.api_repository import ChatApiRepository
from pitch_coach_backend.module.chat.entity import ChatConversation
from pitch_coach_backend.module.pitch.entity import Pitch
from pitch_coach_backend.module.user.entity import User
from tests.chat.api_helpers import bearer

pytestmark = pytest.mark.usefixtures("chat_app")

PAGE = 50
BASE = datetime(2026, 11, 1, 9, 0, tzinfo=UTC)
LIST_PATH = "/api/chat/conversations"
SOME_ID = "0190a000-0000-7000-8000-000000000000"


def _seed(
    db: Session,
    user_id: uuid.UUID,
    count: int,
    *,
    step: timedelta = timedelta(minutes=1),
    pitch_id: uuid.UUID | None = None,
) -> list[str]:
    """대화 `count` 개를 만들고 ID 를 돌려준다. 뒤에 만든 것일수록 시각이 늦다."""
    rows = [
        ChatConversation(
            user_id=user_id,
            scope_pitch_id=pitch_id,
            title=f"대화 {index}",
            last_user_activity_at=BASE + step * index,
        )
        for index in range(count)
    ]
    db.add_all(rows)
    db.flush()
    return [str(row.id) for row in rows]


def _other_user(db: Session) -> uuid.UUID:
    user = User(email=f"{uuid.uuid4()}@example.com", name="다른 사용자")
    db.add(user)
    db.flush()
    return user.id


def _pitch(db: Session, user_id: uuid.UUID) -> uuid.UUID:
    pitch = Pitch(user_id=user_id, title="졸업 발표", time_limit_sec=300)
    db.add(pitch)
    db.flush()
    return pitch.id


def _page(client: TestClient, user_id: uuid.UUID, **params: Any) -> dict[str, Any]:
    response = client.get(LIST_PATH, params=params, headers=bearer(user_id))
    assert response.status_code == 200, response.text
    return response.json()


def _ids(page: dict[str, Any]) -> list[str]:
    return [item["conversation_id"] for item in page["conversations"]]


def _read_all(client: TestClient, user_id: uuid.UUID, **params: Any) -> list[list[str]]:
    """`next_cursor` 가 없어질 때까지 읽어 쪽마다의 ID 를 돌려준다."""
    pages = []
    cursor = None
    while True:
        page = _page(client, user_id, **params, **({"cursor": cursor} if cursor else {}))
        pages.append(_ids(page))
        cursor = page["next_cursor"]
        if cursor is None:
            return pages
        assert len(pages) < 10, "이어 읽기가 끝나지 않는다"


def _cursor_text(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def _bump(db: Session, conversation_id: str, at: datetime) -> None:
    """읽는 사이 사용자가 그 대화에 말을 걸어 맨 앞으로 올라간 상황."""
    db.execute(
        update(ChatConversation)
        .where(ChatConversation.id == uuid.UUID(conversation_id))
        .values(last_user_activity_at=at)
    )
    db.expire_all()


def test_empty_list_has_no_next_cursor(client: TestClient, user_id: uuid.UUID) -> None:
    assert _page(client, user_id) == {"conversations": [], "next_cursor": None}


def test_exactly_a_page_has_no_next_cursor(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    created = _seed(db_session, user_id, PAGE)

    page = _page(client, user_id)

    assert _ids(page) == created[::-1]
    assert page["next_cursor"] is None


def test_one_over_a_page_leaves_one_for_the_next_page(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    created = _seed(db_session, user_id, PAGE + 1)

    first = _page(client, user_id)
    second = _page(client, user_id, cursor=first["next_cursor"])

    assert _ids(first) == created[:0:-1]
    # FE 가 해석하지 않고 그대로 주소에 실어 보낼 수 있는 글자만 쓴다. 사용자 ID 는 담지 않는다
    assert re.fullmatch(r"[A-Za-z0-9_-]{92}", first["next_cursor"])
    padded = first["next_cursor"] + "=" * (-len(first["next_cursor"]) % 4)
    assert str(user_id) not in base64.urlsafe_b64decode(padded).decode()
    assert _ids(second) == [created[0]]
    assert second["next_cursor"] is None


def test_reading_every_page_misses_and_repeats_nothing(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    created = _seed(db_session, user_id, PAGE * 2 + 20)

    pages = _read_all(client, user_id)

    assert [len(page) for page in pages] == [PAGE, PAGE, 20]
    assert [value for page in pages for value in page] == created[::-1]


def test_same_activity_time_is_ordered_by_id_across_pages(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    created = _seed(db_session, user_id, PAGE + 7, step=timedelta(0))

    pages = _read_all(client, user_id)

    assert [len(page) for page in pages] == [PAGE, 7]
    assert [value for page in pages for value in page] == sorted(created, reverse=True)


def test_microsecond_apart_times_are_not_merged_by_the_cursor(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    # 마이크로초를 잃으면 경계의 대화가 빠지거나 다음 쪽에 다시 나온다
    created = _seed(db_session, user_id, PAGE + 3, step=timedelta(microseconds=1))

    pages = _read_all(client, user_id)

    assert [value for page in pages for value in page] == created[::-1]


def test_cursor_keeps_the_pitch_filter(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    pitch_id = _pitch(db_session, user_id)
    in_pitch = _seed(db_session, user_id, PAGE + 2, pitch_id=pitch_id)
    # 범위가 다른 대화가 사이사이 끼어도 쪽 나누기에 들어가지 않는다
    _seed(db_session, user_id, PAGE + 2, step=timedelta(seconds=30))

    pages = _read_all(client, user_id, pitch_id=str(pitch_id))

    assert [len(page) for page in pages] == [PAGE, 2]
    assert [value for page in pages for value in page] == in_pitch[::-1]


def test_cursor_from_the_whole_list_still_applies_the_filter(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    pitch_id = _pitch(db_session, user_id)
    in_pitch = _seed(db_session, user_id, 1, pitch_id=pitch_id)
    _seed(db_session, user_id, PAGE + 1, step=timedelta(minutes=1))

    # 첫 쪽의 마지막은 BASE + 1분이라 범위 안 대화(BASE)가 그 뒤에 온다
    cursor = _page(client, user_id)["next_cursor"]
    page = _page(client, user_id, pitch_id=str(pitch_id), cursor=cursor)

    assert _ids(page) == in_pitch
    assert page["next_cursor"] is None


def test_others_conversations_are_never_listed(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    other = _other_user(db_session)
    theirs = set(_seed(db_session, other, PAGE + 1, step=timedelta(seconds=30)))
    mine = _seed(db_session, user_id, PAGE + 1)

    pages = _read_all(client, user_id)

    assert [value for page in pages for value in page] == mine[::-1]
    # 남의 커서를 들고 와도 그 사람 대화가 아니라 내 대화만 나온다
    cursor = _page(client, other)["next_cursor"]
    assert not theirs & set(_ids(_page(client, user_id, cursor=cursor)))
    assert set(_ids(_page(client, _other_user(db_session), cursor=cursor))) == set()


def test_conversation_moved_to_front_while_reading_does_not_repeat(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    created = _seed(db_session, user_id, PAGE + 3)
    first = _page(client, user_id)
    newest = BASE + timedelta(days=1)

    # 이미 읽은 대화(첫 쪽의 맨 뒤)와 아직 안 읽은 대화(맨 끝)가 모두 맨 앞으로 올라간다
    _bump(db_session, _ids(first)[-1], newest)
    _bump(db_session, created[0], newest + timedelta(minutes=1))
    second = _page(client, user_id, cursor=first["next_cursor"])

    # 옛 커서는 그 위치 아래만 읽는다. 올라간 대화는 다시 나오지 않고, 처음부터 읽으면 맨 앞에 있다
    assert _ids(second) == created[1:3][::-1]
    assert second["next_cursor"] is None
    assert _ids(_page(client, user_id))[:2] == [created[0], _ids(first)[-1]]


def test_cursor_of_a_vanished_conversation_still_works(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    created = _seed(db_session, user_id, PAGE + 2)
    first = _page(client, user_id)

    db_session.delete(db_session.get(ChatConversation, uuid.UUID(_ids(first)[-1])))
    db_session.flush()

    # 커서는 행이 아니라 값으로 위치를 잡는다. 기준 대화가 지워져도 이어 읽는다
    assert _ids(_page(client, user_id, cursor=first["next_cursor"])) == created[1::-1]


def test_stale_is_worked_out_only_for_the_page(
    client: TestClient,
    db_session: Session,
    user_id: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _seed(db_session, user_id, PAGE + 1)
    original = ChatApiRepository.latest_answers
    asked: list[list[uuid.UUID]] = []

    def spy(self: ChatApiRepository, conversation_ids: list[uuid.UUID]) -> list[Any]:
        asked.append(conversation_ids)
        return original(self, conversation_ids)

    monkeypatch.setattr(ChatApiRepository, "latest_answers", spy)
    _page(client, user_id)

    assert [str(value) for value in asked[0]] == created[:0:-1]


def test_cursor_at_the_edges_of_the_time_range_is_read(
    client: TestClient, db_session: Session, user_id: uuid.UUID
) -> None:
    created = _seed(db_session, user_id, 2)

    latest = _cursor_text(f"9999-12-31T23:59:59.999999+00:00|{SOME_ID}")
    earliest = _cursor_text(f"0001-01-01T00:00:00.000000+00:00|{SOME_ID}")

    assert _ids(_page(client, user_id, cursor=latest)) == created[::-1]
    assert _ids(_page(client, user_id, cursor=earliest)) == []


@pytest.mark.parametrize(
    "cursor",
    [
        pytest.param("", id="비었다"),
        pytest.param("이어읽기", id="base64 가 아니다"),
        pytest.param("!!!!", id="base64 글자가 아니다"),
        pytest.param("a", id="base64 길이가 틀렸다"),
        pytest.param("\x00", id="NUL"),
        pytest.param("\ud800", id="서로게이트"),
        pytest.param("A" * 129, id="길이 상한을 넘었다"),
        pytest.param(base64.urlsafe_b64encode(b"\xff\xfe").decode(), id="UTF-8 이 아니다"),
        pytest.param(_cursor_text("no separator"), id="구분자가 없다"),
        pytest.param(_cursor_text(f"not-a-time|{SOME_ID}"), id="시각이 아니다"),
        pytest.param(
            _cursor_text("2026-11-07T00:00:00.000000+00:00|not-a-uuid"), id="UUID 가 아니다"
        ),
        pytest.param(
            _cursor_text(f"2026-11-07T00:00:00.000000+00:00|{SOME_ID}|x"), id="칸이 더 있다"
        ),
        pytest.param(_cursor_text(f"2026-11-07T00:00:00.000000|{SOME_ID}"), id="시간대가 없다"),
        pytest.param(_cursor_text(f"2026-13-40T00:00:00.000000+00:00|{SOME_ID}"), id="없는 날짜"),
        pytest.param(_cursor_text(f"0000-01-01T00:00:00.000000+00:00|{SOME_ID}"), id="0년"),
        pytest.param(_cursor_text(f"10000-01-01T00:00:00.000000+00:00|{SOME_ID}"), id="10000년"),
        pytest.param(
            _cursor_text(f"0001-01-01T00:00:00.000000+14:00|{SOME_ID}"), id="UTC 로 바꾸면 1년 앞"
        ),
        pytest.param(
            _cursor_text(f"9999-12-31T23:59:59.999999-14:00|{SOME_ID}"),
            id="UTC 로 바꾸면 9999년 뒤",
        ),
        # 같은 위치를 여러 글자로 쓸 수 없게 우리가 만든 모양만 받는다
        pytest.param(
            _cursor_text(f"2026-11-07T09:00:00.000000+09:00|{SOME_ID}"), id="UTC 가 아닌 시간대"
        ),
        pytest.param(_cursor_text(f"2026-11-07T00:00:00Z|{SOME_ID}"), id="만들지 않는 시각 표기"),
        pytest.param(
            _cursor_text(f"2026-11-07T00:00:00.000000+00:00|{SOME_ID.replace('-', '')}"),
            id="하이픈 없는 UUID",
        ),
        pytest.param(
            _cursor_text(f"2026-11-07T00:00:00.000000+00:00|{SOME_ID}") + "=",
            id="패딩이 붙었다",
        ),
    ],
)
def test_broken_cursor_is_422(client: TestClient, user_id: uuid.UUID, cursor: str) -> None:
    query = "cursor=" + "".join(
        f"%{byte:02X}" for byte in cursor.encode("utf-8", errors="surrogatepass")
    )

    response = client.get(f"{LIST_PATH}?{query}", headers=bearer(user_id))

    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    "query", [pytest.param("A" * 129, id="길이 상한"), pytest.param("A%00", id="NUL")]
)
def test_long_or_nul_cursor_is_rejected_before_decoding(
    client: TestClient, user_id: uuid.UUID, query: str
) -> None:
    response = client.get(f"{LIST_PATH}?cursor={query}", headers=bearer(user_id))

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    # 풀어 본 뒤의 거절(ChatCursorInvalid)은 detail 이 없다. 있으면 요청 검증에서 막힌 것이다
    assert response.json()["detail"][0]["loc"] == ["query", "cursor"]


def test_cursor_that_is_not_utf8_in_the_url_is_422(client: TestClient, user_id: uuid.UUID) -> None:
    response = client.get(f"{LIST_PATH}?cursor=%ff%fe", headers=bearer(user_id))

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


def test_broken_cursor_needs_login_first(client: TestClient) -> None:
    assert client.get(f"{LIST_PATH}?cursor=broken").status_code == 401
