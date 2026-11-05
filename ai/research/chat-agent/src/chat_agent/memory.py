"""기억 — 코칭 프로필 · 대화 요약을 모델에 주는 법과 그 결과의 코드 검사 (기능 문서 6-14 · 5-3 `memory` · 5-5).

- 누가 쓰나: loop.py(데이터 칸 · 요약 호출 · 기억 번호), turn.py(끝 줄의 `profile_updates` · `history_summary`).
- 하는 일
  - 기억 번호: 처음 요청의 `memory_version`을 서명 상태에 담고, 이어 부를 때 번호가 다르면 그 실행 끝까지 '바뀜'으로 둔다
    (`sync_version`). 바뀐 실행은 프로필 저장 후보를 내지 않는다.
  - 코칭 프로필은 켜져 있을 때 답 쓰기 · 고치기 호출의 데이터 칸에만 준다(`profile_for_model`). 계획 · 조사 호출에는 주지
    않는다. 프로필은 서명 상태의 모델 메시지(`st.messages`, 조사 단계만 쌓임)에 들어가지 않으므로, 번호가 바뀌어도 떼어 낼
    메시지가 없다(이어 부를 때는 그 요청의 프로필을 다시 받는다).
  - 저장 후보 검사(`check_profile_updates`, 6-14 ①~⑥)와 요약 항목 거르기(`check_summary_items`).
- 하지 않는 것: 판단 · 비교 · 목표 계산(그 함수들은 기억을 입력으로 받지 않는다, P14), 저장(BE 몫), 기억에서 근거 id 만들기.
- 고칠 때: 낱말 목록 · 수는 config.py(`memory_*` · `max_profile_*` · `max_summary_*`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from . import checks as chk
from .config import ChatAgentConfig

PROFILE_KEYS = ("TONE", "EXPLAIN_STYLE", "FOCUS_AREA", "CONTEXT")
SUMMARY_KINDS = ("TOPIC", "INTENT", "DECISION", "OPEN")

#: 측정 단위(이 앞의 숫자는 Take · 장 번호가 아니다 — "Take 28초")
_UNITS = r"(?:초|분|시간|%|퍼센트|번|회|개|점|배)"
#: 요약 항목에 허용하는 번호(Take 번호 · 장 번호). 이것을 뺀 뒤 숫자가 남으면 그 항목을 버린다
_ALLOWED_NUMBERS = re.compile(rf"(?i)take\s*\d+(?![\d.]|\s*{_UNITS})|\d+\s*장")
#: 인용을 절로 나누는 곳
_CLAUSE_SPLIT = re.compile(r"[.,!?\n]")
_DIGIT = re.compile(r"\d")
#: 요약 항목의 판정 · 방향 낱말(동의 · 칭찬 낱말은 checks 가 따로 본다). 판정은 도구로 다시 확인하기 때문이다
_SUMMARY_VERDICT_WORDS = (
    "틀렸",
    "틀려",
    "좋아졌",
    "나빠졌",
    "개선됐",
    "개선되었",
    "향상됐",
    "떨어졌",
    "기록과 달",
    "확인할 수 없",
)


def sync_version(st: Any, memory: Any) -> None:
    """처음 요청의 기억 번호를 상태에 담고, 이어 부를 때 번호가 다르면 '바뀜'을 남긴다(한 번 바뀌면 그 실행 끝까지)."""
    if st.memory_version is None:
        st.memory_version = memory.memory_version
    elif st.memory_version != memory.memory_version:
        st.memory_changed = True


def profile_for_model(memory: Any) -> list[dict[str, str]] | None:
    """답 쓰기 · 고치기 데이터 칸에 줄 코칭 프로필(켜져 있을 때만). 항목 id 는 주지 않는다."""
    if not memory.enabled or not memory.profile:
        return None
    return [{"key": p.key, "value": p.value} for p in memory.profile]


@dataclass(frozen=True)
class Dropped:
    """버린 후보 · 항목 하나(진단 기록 `CHECK` · `MEMORY_RULE`)."""

    field: str
    reason: str


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", s)


def _has_number(text: str, cfg: ChatAgentConfig, *, allow_take_slide: bool) -> bool:
    t = _ALLOWED_NUMBERS.sub(" ", text) if allow_take_slide else text
    if _DIGIT.search(t) or "%" in t:
        return True
    return any(
        f"{n}{u}" in _squash(t) for n in cfg.korean_numerals for u in cfg.korean_numeral_units
    )


def _clause_of(message: str, quote: str) -> str:
    """사용자 말에서 인용이 든 절(쉼표 · 마침표 · 물음표 · 줄바꿈으로 나눈 조각들 중 인용이 걸친 것)."""
    at = message.find(quote)
    if at < 0:
        return quote
    start = max((message.rfind(c, 0, at) for c in ",.?!\n"), default=-1) + 1
    ends = [message.find(c, at + len(quote)) for c in ",.?!\n"]
    end = min([e for e in ends if e >= 0], default=len(message))
    return message[start:end]


def check_profile_updates(
    raw: Any, *, user_message: str, cfg: ChatAgentConfig
) -> tuple[list[dict[str, str]], list[Dropped]]:
    """코칭 프로필 저장 후보 검사(6-14 ①~⑥). 통과한 후보(최대 2개, key 마다 하나)와 버린 까닭을 돌려준다.

    ① `quote`가 이번 사용자 말의 부분 문자열 ② 인용에 직접적인 기억 요청 표현이 있음 ③ 인용이 든 절에 철회 · 조건 · 남의 말
    인용 표현이 없음 ④ key 가 목록 안 ⑤ 값이 1~80자이고 숫자 · 측정값 · 점수 · 지시형 낱말 · 금지 주장 · 개인정보가 없음
    ⑥ 값은 요청 구간에서 온 것(공백을 무시하고 인용 안에 그대로 있음). 하나라도 걸리면 그 후보를 버린다.
    """
    kept: list[dict[str, str]] = []
    dropped: list[Dropped] = []
    items = raw if isinstance(raw, list) else []
    for item in items:
        why = _profile_problem(item, user_message, cfg)
        if why is None and any(k["key"] == item["key"] for k in kept):
            why = "같은 key 의 후보가 이미 있음"
        if why is None and len(kept) >= cfg.max_profile_updates:
            why = "한 응답의 후보 수를 넘음"
        if why is not None:
            dropped.append(Dropped("profile_updates", why))
            continue
        kept.append({"key": item["key"], "value": item["value"].strip(), "quote": item["quote"]})
    return kept, dropped


def _profile_problem(item: Any, message: str, cfg: ChatAgentConfig) -> str | None:
    if not isinstance(item, dict):
        return "모양이 틀림"
    key, value, quote = item.get("key"), item.get("value"), item.get("quote")
    if not (isinstance(key, str) and isinstance(value, str) and isinstance(quote, str)):
        return "모양이 틀림"
    value = value.strip()
    if not quote.strip() or quote not in message:
        return "① 인용이 이번 사용자 말에 없음"
    if not any(w in quote for w in cfg.memory_request_words):
        return "② 직접적인 기억 요청 표현이 없음"
    clause = _clause_of(message, quote)
    if any(w in message for w in cfg.memory_withdraw_words) or any(
        any(v in c for v in cfg.memory_withdraw_verbs)
        and any(t in c for t in cfg.memory_withdraw_targets)
        for c in _CLAUSE_SPLIT.split(message)
    ):
        # 철회는 다른 절에 있어도("~해 줘, 아니 기억하지 마") 그 말 전체의 기억 요청을 거둔다. 지우기 동사는 기억을
        # 가리키는 말과 같은 절일 때만("대본 파일은 지우고 다시 올릴게"는 철회가 아니다, #916 리뷰)
        return "③ 기억 철회 표현"
    if any(m in clause for m in cfg.quotation_markers):
        return "③ 남의 말 인용"
    words = [w.strip(".,!?~") for w in clause.split()]
    first_req = next(
        (i for i, w in enumerate(words) if any(r in w for r in cfg.memory_request_words)),
        len(words),
    )
    if any(
        len(w) > 1 and w.endswith(cfg.condition_suffixes) and not _is_wish(words, i, cfg)
        for i, w in enumerate(words)
        if i != first_req
    ):
        return "③ 조건부 요청"
    if key not in PROFILE_KEYS:
        return "④ 모르는 key"
    if not value or len(value) > cfg.max_profile_value_chars:
        return "⑤ 값 길이"
    if _has_number(value, cfg, allow_take_slide=False):
        return "⑤ 숫자 · 측정값"
    if any(w in value for w in cfg.memory_directive_words):
        return "⑤ 지시형 · 판정 낱말"
    if chk.check_memory_text(value, "profile_updates", cfg):
        return "⑤ 금지 주장 · 개인정보 · 판정 낱말"
    # ②⑥ 값은 기억 요청 표현이 있는 절 안에서 온 것이어야 한다("발표는 길게 해. 앞으로 짧게 해 줘"의 '길게'는 안 됨)
    # 기억 요청 낱말만 있는 절("짧게 설명해 줘, 기억해 줘"의 '기억해 줘')은 바로 앞 절을 가리키므로 앞 절도 요청 구간이다
    # (#917 리뷰). 앞 절이 아닌 다른 내용을 가리키는 절("앞으로 짧게 해 줘")은 넓히지 않는다
    parts = [c for c in _CLAUSE_SPLIT.split(quote) if c.strip()]
    request_clauses = []
    for i, c in enumerate(parts):
        if any(w in c for w in cfg.memory_request_words):
            request_clauses.append(c)
            if i > 0 and _only_request(c, cfg):
                request_clauses.append(parts[i - 1])
    if not any(_squash(value) in _squash(c) for c in request_clauses):
        return "⑥ 값이 기억 요청 구간에 없음"
    return None


#: 기억 요청만 있는 절에서 지워도 되는 말(요청 어미 · 꾸밈)
_REQUEST_FILLERS = re.compile(
    r"(?:줘요|줘|둬요|둬|주세요|두세요|줄래|줄래요|해요|해|요|꼭|제발|좀|~)"
)


def _only_request(clause: str, cfg: ChatAgentConfig) -> bool:
    """절에 기억 요청 낱말과 요청 어미만 있는가. 예: '기억해 줘' · ' 기억해 둬' → True, '앞으로 짧게 해 줘' → False"""
    rest = clause
    for w in sorted(cfg.memory_request_words, key=len, reverse=True):
        if w.startswith("기억"):
            rest = rest.replace(w, " ")
    return not _squash(_REQUEST_FILLERS.sub("", _squash(rest)))


def _is_wish(words: list[str], i: int, cfg: ChatAgentConfig) -> bool:
    """`~면` 낱말 바로 뒤가 바람 표현이면("설명해 주면 좋겠어") 조건이 아니다(#916 리뷰)."""
    return i + 1 < len(words) and words[i + 1].startswith(cfg.wish_after_condition)


def check_summary_items(
    raw: Any, *, cfg: ChatAgentConfig
) -> tuple[list[dict[str, str]], list[Dropped]]:
    """대화 요약 항목 거르기(6-14): 모양 · 1~120자, 숫자(Take · 장 번호만 허용) · 판정 · 동의 낱말 · 금지 주장 · 개인정보 ·
    지시문 표지가 든 항목은 버린다. 남은 항목을 앞에서부터 12개까지."""
    kept: list[dict[str, str]] = []
    dropped: list[Dropped] = []
    items = raw if isinstance(raw, list) else []
    for item in items:
        why = _summary_problem(item, cfg)
        if why is None and len(kept) >= cfg.max_summary_items:
            why = "항목 수를 넘음"
        if why is not None:
            dropped.append(Dropped("history_summary", why))
            continue
        kept.append({"kind": item["kind"], "text": item["text"].strip()})
    return kept, dropped


def _summary_problem(item: Any, cfg: ChatAgentConfig) -> str | None:
    if not isinstance(item, dict):
        return "모양이 틀림"
    kind, text = item.get("kind"), item.get("text")
    if kind not in SUMMARY_KINDS or not isinstance(text, str):
        return "모양이 틀림"
    text = text.strip()
    if not text or len(text) > cfg.max_summary_item_chars:
        return "길이"
    if _has_number(text, cfg, allow_take_slide=True):
        return "숫자 · 측정값(Take · 장 번호만 허용)"
    if any(w in text for w in _SUMMARY_VERDICT_WORDS):
        return "판정 낱말"
    if chk.check_memory_text(text, "history_summary", cfg):
        return "금지 주장 · 개인정보 · 동의 낱말 · 지시문"
    return None
