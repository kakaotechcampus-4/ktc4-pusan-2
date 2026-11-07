import re

NEGATIONS = ("하지 마", "말고", "취소", "빼고", "안 해")
CONDITION = re.compile(r"(\S)?(으)?면(?=\s|$|,)")
NOUNS_ENDING_MYEON = ("화면", "표면", "내면", "수면", "체면", "지면", "대면", "서면")
RIEUL = 8
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
        or _conditional(clause)
        or any(mark in clause for mark in QUOTES)
    ):
        return "REJECTED"
    if not any(word in quote for word in ACTION_WORDS[kind]) or not REQUEST_ENDINGS.search(quote):
        return "MISSING"
    return "OK"


def _conditional(clause: str) -> bool:
    for match in CONDITION.finditer(clause):
        before, eu = match.group(1), match.group(2)
        if eu or before is None:
            return True
        if f"{before}면" in NOUNS_ENDING_MYEON:
            continue
        final = _final_consonant(before)
        if final and final != RIEUL:
            continue
        return True
    return False


def _final_consonant(char: str) -> int:
    code = ord(char) - 0xAC00
    return code % 28 if 0 <= code < 11172 else 0
