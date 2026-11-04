import re

NEGATIONS = ("하지 마", "말고", "취소", "빼고", "안 해")
CONDITION = re.compile(r"(으)?면(\s|$|,)")
QUOTES = ('"', "'", "“", "”", "‘", "’", "라고")
REQUEST_ENDINGS = re.compile(r"(자|줘|주세요|할래|해 줘|하고 싶어|갈래|해요)(\s|$|[.!?,])")
ACTION_WORDS = {
    "START_PRACTICE": ("연습", "발표", "시작", "가자"),
    "SAVE_GOAL": ("목표", "잡", "정해", "저장"),
}
CLAUSE_SPLIT = re.compile(r"[.!?\n]")


def authorization(original: str | None, quote: str | None, kind: str) -> str:
    if not original or not quote or quote not in original:
        return "MISSING"
    clause = next((part for part in CLAUSE_SPLIT.split(original) if quote in part), original)
    if (
        any(word in clause for word in NEGATIONS)
        or CONDITION.search(clause)
        or any(mark in clause for mark in QUOTES)
    ):
        return "REJECTED"
    if not any(word in quote for word in ACTION_WORDS[kind]) or not REQUEST_ENDINGS.search(quote):
        return "MISSING"
    return "OK"
