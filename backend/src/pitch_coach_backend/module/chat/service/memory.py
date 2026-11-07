import re
from typing import Annotated, Any

from pydantic import Field, TypeAdapter, ValidationError

from pitch_coach_backend.module.chat.dto import HISTORY_MESSAGES, AgentSummaryItemDTO, AgentTrigger
from pitch_coach_backend.module.chat.entity import ChatConversation, ChatRun, ChatSummary
from pitch_coach_backend.module.chat.repository import ChatRepository
from pitch_coach_backend.module.chat.service.request import overflow_messages
from pitch_coach_backend.module.chat.service.stale import MASKED_TEXT

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
WITHDRAW_WORDS = ("잊어", "기억하지 마", "기억하지마")
WITHDRAW_VERBS = ("지워", "지우고", "지워 줘")
WITHDRAW_TARGETS = ("기억", "그거", "그건", "그 말", "방금 말", "아까 말")
WISH_WORDS = ("좋겠", "고맙", "좋아", "좋을", "감사")
INJECTION_MARKERS = (
    "이전 지시",
    "지시를 무시",
    "시스템 프롬프트",
    "ignore previous",
    "ignore all previous",
    "system prompt",
)
SECRET = re.compile(r"sk-[A-Za-z0-9_-]{16,}|Bearer\s+[A-Za-z0-9._-]{16,}|AKIA[0-9A-Z]{16}")
NEGATED_AFTER = re.compile(r"[가-힣]{0,4}?(?:지마|지말|말고)")
REQUEST_FILLERS = re.compile(
    r"(?:줘요|줘|둬요|둬|주세요|두세요|줄래|줄래요|해요|해|요|꼭|제발|좀|~)"
)
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
    repo: ChatRepository,
    row: ChatRun,
    conversation: ChatConversation,
    profile_updates: Any,
    cards: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """기억 요청을 저장하고, 이 실행에서 저장한 기억 카드를 붙인 답 카드를 돌려준다."""
    saved = memory_cards(row)
    kept = [card for card in cards if card.get("type") != "MEMORY_SAVED"]
    new = new_memory_cards(repo, row, conversation, profile_updates, saved)
    return [*kept, *saved, *new]


def memory_cards(row: ChatRun) -> list[dict[str, Any]]:
    return list((row.interim_message or {}).get("memory_saved") or [])


def new_memory_cards(
    repo: ChatRepository,
    row: ChatRun,
    conversation: ChatConversation,
    raw: Any,
    saved: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    done = {(card["key"], card["value"]) for card in saved}
    cards: list[dict[str, Any]] = []
    for update in _accepted(repo, row, conversation, raw):
        if (update["key"], update["value"]) in done:
            continue
        item_id = repo.upsert_profile_item(row.user_id, update["key"], update["value"])
        cards.append(
            {
                "type": "MEMORY_SAVED",
                "item_id": str(item_id),
                "key": update["key"],
                "value": update["value"],
            }
        )
    return cards


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
    # 이미 가린 답을 덮으면 처음부터 비운다. 삭제 기록이 없어진 뒤라면 정리 작업이 이 요약을 고르지
    # 못해, 근거를 옮겨 적으면 비워지지 않은 채 읽을 때마다 가려진다
    masked = any(message.text == MASKED_TEXT for message in covered)
    summary.items = [] if masked else items
    summary.summary_version += 1
    summary.covered_message_id = covered[-1].id
    summary.source_pitch_ids = [] if masked else sorted(pitch_ids)
    summary.source_take_ids = [] if masked else sorted(take_ids)


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
    if row.first_trigger != AgentTrigger.USER_MESSAGE or not isinstance(raw, list):
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
    if _withdrawn(message):
        return False
    if any(mark in clause for mark in QUOTES) or _conditional(clause):
        return False
    if key not in PROFILE_KEYS or not value or len(value) > VALUE_LIMIT:
        return False
    if _has_number(value) or any(word in value for word in DIRECTIVE_WORDS):
        return False
    if _unsafe(value):
        return False
    squashed = _squash(value)
    request_clauses = _request_clauses(quote)
    if not any(squashed in _squash(part) for part in request_clauses):
        return False
    return not any(_negated(squashed, part) for part in request_clauses)


def _unsafe(value: str) -> bool:
    lowered = value.lower()
    return (
        any(marker in lowered for marker in INJECTION_MARKERS) or SECRET.search(value) is not None
    )


def _withdrawn(message: str) -> bool:
    if any(word in message for word in WITHDRAW_WORDS):
        return True
    return any(
        any(verb in part for verb in WITHDRAW_VERBS)
        and any(target in part for target in WITHDRAW_TARGETS)
        for part in CLAUSE_SPLIT.split(message)
    )


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
        len(word) > 1 and word.endswith(CONDITION_SUFFIXES) and not _wish(words, i)
        for i, word in enumerate(words)
        if i != first
    )


def _wish(words: list[str], i: int) -> bool:
    return i + 1 < len(words) and words[i + 1].startswith(WISH_WORDS)


def _request_clauses(quote: str) -> list[str]:
    parts = [part for part in CLAUSE_SPLIT.split(quote) if part.strip()]
    clauses: list[str] = []
    for i, part in enumerate(parts):
        if any(word in part for word in REQUEST_WORDS):
            clauses.append(part)
            if i > 0 and _only_request(part):
                clauses.append(parts[i - 1])
    return clauses


def _only_request(clause: str) -> bool:
    rest = clause
    for word in sorted(REQUEST_WORDS, key=len, reverse=True):
        if word.startswith("기억"):
            rest = rest.replace(word, " ")
    return not _squash(REQUEST_FILLERS.sub("", _squash(rest)))


def _negated(value: str, clause: str) -> bool:
    squashed = _squash(clause)
    at = squashed.find(value)
    return at >= 0 and NEGATED_AFTER.match(squashed[at + len(value) :]) is not None


def _has_number(text: str) -> bool:
    if re.search(r"\d", text) or "%" in text:
        return True
    squashed = _squash(text)
    return any(f"{n}{u}" in squashed for n in KOREAN_NUMERALS for u in KOREAN_NUMERAL_UNITS)


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text)
