import re
from typing import Annotated, Any

from pydantic import Field, TypeAdapter, ValidationError

from pitch_coach_backend.module.chat.dto import AgentSummaryItemDTO
from pitch_coach_backend.module.chat.entity import ChatConversation, ChatRun, ChatSummary
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.request import HISTORY_MESSAGES, overflow_messages

PROFILE_KEYS = ("TONE", "EXPLAIN_STYLE", "FOCUS_AREA", "CONTEXT")
VALUE_LIMIT = 80
UPDATE_LIMIT = 2
REQUEST_WORDS = (
    "기억해",
    "기억해 줘",
    "기억해줘",
    "기억해 두",
    "기억해두",
    "앞으로는",
    "앞으로",
    "다음부터",
    "다음부터는",
    "매번",
    "항상",
)
WITHDRAW_WORDS = ("잊어", "기억하지 마", "기억하지마", "지워", "지우고")
QUOTES = (
    '"',
    "“",
    "”",
    "'",
    "‘",
    "’",
    "라던데",
    "라고 했",
    "라고 하",
    "다던데",
    "하라던",
    "라더라",
    "랬어",
    "래요",
    "라는",
    "라 했",
    "라 하",
    "라고",
)
CONDITION_SUFFIXES = ("면", "다면", "으면", "라면", "거든")
DIRECTIVE_WORDS = (
    "무시",
    "승인",
    "허용",
    "맞다고",
    "동의해",
    "틀렸다고",
    "판정",
    "점수",
    "통과",
    "지시",
    "규칙",
    "시스템",
    "프롬프트",
    "저장해",
    "실행",
)
KOREAN_NUMERALS = (
    "한",
    "두",
    "세",
    "네",
    "다섯",
    "여섯",
    "일곱",
    "여덟",
    "아홉",
    "열",
    "스무",
    "서른",
    "마흔",
    "쉰",
)
KOREAN_NUMERAL_UNITS = ("번", "개", "장", "분", "초", "퍼센트", "점")
CLAUSE_MARKS = ",.?!\n"
CLAUSE_SPLIT = re.compile(r"[.,!?\n]")


def save_profile_updates(
    repo: ChatRepository, row: ChatRun, conversation: ChatConversation, data: dict[str, Any]
) -> None:
    cards = [card for card in data["message"]["cards"] if card.get("type") != "MEMORY_SAVED"]
    for update in _accepted(repo, row, conversation, data.get("profile_updates")):
        item_id = repo.upsert_profile_item(row.user_id, update["key"], update["value"])
        cards.append(
            {
                "type": "MEMORY_SAVED",
                "item_id": str(item_id),
                "key": update["key"],
                "value": update["value"],
            }
        )
    data["message"]["cards"] = cards


def save_summary(repo: ChatRepository, row: ChatRun, turn_call: int, data: dict[str, Any]) -> None:
    items = _summary_items(data.get("history_summary"))
    sent = next(call for call in row.turn_calls if call["turn_call"] == turn_call)
    if items is None or sent.get("summary_version") is None:
        return
    summary = repo.lock_summary(row.conversation_id)
    if sent["summary_version"] != (summary.summary_version if summary else 0):
        return
    trigger = repo.get_message(row.message_id) if row.message_id else None
    recent = repo.list_recent_messages(
        row.conversation_id, before_seq=trigger.seq if trigger else None, limit=HISTORY_MESSAGES
    )
    covered = overflow_messages(repo, row.conversation_id, summary, recent)
    if not covered:
        return
    pitch_ids = set(summary.source_pitch_ids or []) if summary else set()
    take_ids = set(summary.source_take_ids or []) if summary else set()
    for message in covered:
        pitch_ids.update(message.source_pitch_ids or [])
        take_ids.update(message.source_take_ids or [])
    if summary is None:
        summary = ChatSummary(conversation_id=row.conversation_id, summary_version=0)
        repo.add(summary)
    summary.items = items
    summary.summary_version += 1
    summary.covered_message_id = covered[-1].id
    summary.source_pitch_ids = sorted(pitch_ids)
    summary.source_take_ids = sorted(take_ids)


def _summary_items(raw: Any) -> list[dict[str, str]] | None:
    if not isinstance(raw, list) or not raw:
        return None
    try:
        items = TypeAdapter(
            Annotated[list[AgentSummaryItemDTO], Field(max_length=12)]
        ).validate_python(raw)
    except ValidationError:
        return None
    return [item.model_dump() for item in items]


def _accepted(
    repo: ChatRepository, row: ChatRun, conversation: ChatConversation, raw: Any
) -> list[dict[str, str]]:
    if row.first_trigger != "USER_MESSAGE" or not isinstance(raw, list):
        return []
    if conversation.without_memory:
        return []
    setting = repo.lock_memory_setting(row.user_id)
    if setting is not None and not setting.enabled:
        return []
    current = setting.memory_version if setting else 0
    if row.turn_calls[0].get("memory_version") != current:
        return []
    message = row.input.get("user_message") or ""
    kept: list[dict[str, str]] = []
    for item in raw:
        if len(kept) >= UPDATE_LIMIT:
            break
        if _valid(item, message) and all(saved["key"] != item["key"] for saved in kept):
            kept.append({"key": item["key"], "value": item["value"].strip()})
    return kept


def _valid(item: Any, message: str) -> bool:
    if not isinstance(item, dict):
        return False
    key, value, quote = item.get("key"), item.get("value"), item.get("quote")
    if not (isinstance(key, str) and isinstance(value, str) and isinstance(quote, str)):
        return False
    value = value.strip()
    if not quote.strip() or quote not in message:
        return False
    if not any(word in quote for word in REQUEST_WORDS):
        return False
    clause = _clause_of(message, quote)
    if any(word in message for word in WITHDRAW_WORDS):
        return False
    if any(mark in clause for mark in QUOTES) or _conditional(clause):
        return False
    if key not in PROFILE_KEYS or not value or len(value) > VALUE_LIMIT:
        return False
    if _has_number(value) or any(word in value for word in DIRECTIVE_WORDS):
        return False
    request_clauses = [
        part for part in CLAUSE_SPLIT.split(quote) if any(word in part for word in REQUEST_WORDS)
    ]
    return any(_squash(value) in _squash(part) for part in request_clauses)


def _clause_of(message: str, quote: str) -> str:
    at = message.find(quote)
    start = max(message.rfind(mark, 0, at) for mark in CLAUSE_MARKS) + 1
    ends = [message.find(mark, at + len(quote)) for mark in CLAUSE_MARKS]
    end = min([index for index in ends if index >= 0], default=len(message))
    return message[start:end]


def _conditional(clause: str) -> bool:
    words = [word.strip(".,!?~") for word in clause.split()]
    first = next(
        (i for i, word in enumerate(words) if any(req in word for req in REQUEST_WORDS)),
        len(words),
    )
    return any(
        len(word) > 1 and word.endswith(CONDITION_SUFFIXES)
        for word in words[:first] + words[first + 1 :]
    )


def _has_number(text: str) -> bool:
    if re.search(r"\d", text) or "%" in text:
        return True
    squashed = _squash(text)
    return any(f"{n}{u}" in squashed for n in KOREAN_NUMERALS for u in KOREAN_NUMERAL_UNITS)


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)
