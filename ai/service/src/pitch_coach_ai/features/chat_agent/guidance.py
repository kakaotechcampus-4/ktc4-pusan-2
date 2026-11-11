"""'안 된다' 답의 대안 · 다음 걸음과 [이어서 확인] 반복 막기 (기능 문서 6-1 7번 '끝 상태' · 7-9 · 7-10, Issue #1194).

- 누가 쓰나: turn.py `_finish`가 끝 줄을 만들기 직전, 끝 상태 · 최종 문장 목록으로 부른다. 모든 끝(모델 없이 끝내기 · 거절 ·
  부분 답)이 한 곳을 지나므로 여기서 한 번만 본다. 모델 호출은 없다.
- 하는 일
  1. **'안 된다' 답의 대안 · 다음 걸음**(`follow_up_claims`): 최종 문장에 거절 · 할 수 없음 안내의 핵심 고정 문장(지금 문장 그대로,
     `CORE_KINDS`)이 있으면 그 뒤에 코드가 고른 대안(지금 상태로 할 수 있는 것, 확인된 후보가 있을 때만)과 다음 걸음 문장을 붙인다.
     후보는 이번 요청의 기본 자료(피치 목록 · 범위 피치의 Take 목록)로만 고른다 — 없는 Take · 피치 · 기능을 말하지 않는다.
     직전 어시스턴트 메시지에 같은 핵심 문장이 있으면(대화 기록으로 알아볼 수 있을 때만) 대안을 되풀이하지 않고 선택지(예시 질문)를,
     후보가 없으면 지금 상태와 다음 걸음만 낸다. 핵심 문장은 모델이 쓰지 않는다는 규칙(7-10)은 그대로다.
  2. **[이어서 확인] 반복 막기**(`repeat_stop`): 사용자 말이 `이어서 확인해 줘`(공백 무시)이고 이번 실행이 `PARTIAL`로 끝나는데,
     직전 어시스턴트 메시지에 이번과 같은 `PARTIAL` 안내 고정 문장(`PARTIAL_NOTICES`)이 있으면 같은 까닭으로 본다. 대화 기록에는
     실패 까닭이 없으므로(차수 저장은 BE 계약이 필요해 이번 범위 밖) 안내 유형이 같은지로 근사한다. 그때 끝 상태를 `DONE`으로
     바꾸고 "같은 이유로 더 확인할 수 없어요." + 대안을 낸다 — [이어서 확인] 버튼은 `PARTIAL`에만 붙으므로 고리가 끊긴다(FE 12-4).
     FE 문구가 바뀌면 이 규칙만 꺼지고 나머지는 그대로다.
- 입력 → 출력: 실행 맥락 · 사용자 말 · 최종 문장 → 덧붙일 `SYSTEM` 문장(검사를 통과한 것만) · 바꿀 끝 상태.
- 고칠 때: 문장은 templates.py `SystemText`(고정 문장)와 이 파일의 채움 문장(제목 · Take 번호를 넣는 꼴)이다. 둘 다 7-9 · 7-10 과
  글자까지 같게 둔다. 대안 · 다음 걸음은 행동 · 버튼 · 카드를 만들지 않는다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from . import checks as chk
from . import compose
from .schemas import Claim
from .templates import SystemText

#: [이어서 확인] 버튼이 보내는 말(FE 12-4 F8, 공백 무시)
CONTINUE_MESSAGE = "이어서확인해줘"

#: `PARTIAL` 답의 안내 고정 문장(같은 까닭인지 가리는 안내 유형)
PARTIAL_NOTICES: frozenset[str] = frozenset(
    t.value
    for t in (
        SystemText.PARTIAL_CHECKED,
        SystemText.LIMIT_PARTIAL,
        SystemText.NO_MORE_SOURCES,
        SystemText.STATE_TOO_LARGE,
        SystemText.VERIFIED_ONLY,
        SystemText.TURN_LIMIT,
    )
)

#: 거절 · 할 수 없음 안내의 핵심 고정 문장 → 상황 종류
CORE_KINDS: dict[str, str] = {
    SystemText.PITCH_NOT_FOUND.value: "PITCH",
    SystemText.RECORD_NOT_FOUND.value: "TAKE",
    SystemText.TAKE_FETCH_FAILED.value: "TAKE",
    SystemText.NO_REVIEW.value: "REVIEW",
    SystemText.CANNOT_CHANGE_RECORD.value: "CHANGE",
    SystemText.OUT_OF_SCOPE.value: "SCOPE_BARE",
    SystemText.OUT_OF_SCOPE_UNKNOWN.value: "SCOPE_BARE",
    SystemText.CONTENT_COACHING.value: "SCOPE",
    SystemText.QA_PRACTICE.value: "SCOPE",
    SystemText.PRACTICE_PLAN.value: "SCOPE",
    SystemText.CANNOT_ANSWER.value: "ANSWER",
    SystemText.ANSWER_INCOMPLETE.value: "ANSWER",
    SystemText.USAGE_NO_GUIDE.value: "USAGE",
    SystemText.MEASUREMENT_FIXED.value: "MEASURE",
}
#: 답이 있어도 다음 걸음을 붙이는 상황(리뷰 없음 · 측정값 이의 — 코치 기록 · 판단 답과 함께 나간다)
_WITH_ANSWER = ("REVIEW", "MEASURE")
#: 답에 이미 다음 Take · 연습에서 확인할 방법이 있다(측정값 이의의 다음 걸음을 되풀이하지 않는다)
_NEXT_TAKE = re.compile(r"다음\s*(Take|연습)")

#: 대안 후보 수(피치 제목 · Take 번호)와 선택지 예시 수
MAX_CANDIDATES = 3


@dataclass(frozen=True)
class Outcome:
    """끝 줄 직전에 덧붙일 것. `status`가 None 이면 끝 상태는 그대로다."""

    claims: list[Claim]
    status: str | None = None


def is_continue(message: str | None) -> bool:
    """사용자 말이 [이어서 확인] 버튼의 말인가(공백 · 끝 문장 부호 무시)."""
    if not message:
        return False
    return "".join(message.split()).rstrip(".!?~") == CONTINUE_MESSAGE


def previous_assistant_text(history_messages: Sequence[Any]) -> str | None:
    """대화 기록에서 가장 최근 어시스턴트 메시지의 글. 가려진(지워진 근거) 메시지도 글 그대로다."""
    for m in reversed(list(history_messages)):
        if getattr(m, "role", None) == "assistant":
            return str(getattr(m, "text", "") or "")
    return None


def _copula(word: str) -> str:
    """서술 '이에요/예요': 끝 글자(한글 음절 · 숫자 읽는 소리)에 받침이 있으면 '이에요'."""
    w = word.rstrip(" ')\"")
    last = w[-1] if w else ""
    if "가" <= last <= "힣":
        return "이에요" if (ord(last) - 0xAC00) % 28 else "예요"
    if last.isdigit():
        # 영 일 이 삼 사 오 육 칠 팔 구: 받침 있음 = 영 · 일 · 삼 · 육 · 칠 · 팔
        return "이에요" if last in "013678" else "예요"
    return "이에요"


def _practiced(p: Any) -> datetime | None:
    """마지막 연습 시작 시각(시간대가 섞여도 같은 기준으로 견주게 읽는다). 없거나 읽을 수 없으면 None."""
    try:
        dt = datetime.fromisoformat(str(p.last_practiced_at).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _recent_pitches(ctx: Any) -> list[Any]:
    """기본 자료에서 분석이 끝난 Take 가 있는 피치를 마지막 연습 시작이 늦은 순으로(시각이 없으면 뒤로). 볼 기록이 없는 피치는
    대안 · 예시 질문으로 권하지 않는다(없는 Take 를 말하지 않게)."""
    floor = datetime.min.replace(tzinfo=UTC)
    seen = [p for p in ctx.pitches if (p.take_count or 0) > 0]
    return sorted(seen, key=lambda p: (_practiced(p) is not None, _practiced(p) or floor), reverse=True)


def _recent_takes(ctx: Any) -> list[int]:
    """범위 피치에서 볼 수 있는 Take 번호(큰 번호부터)."""
    return sorted({t.take_number for t in ctx.summaries.values()}, reverse=True)


def _quote_list(items: Iterable[str]) -> str:
    return " · ".join(f"'{s}'" for s in items)


def _take_list(numbers: Sequence[int]) -> str:
    return " · ".join(f"Take {n}" for n in numbers)


def _next_step(ctx: Any) -> SystemText:
    """기본 자료로 고른 다음 걸음(인사 뒤 다음 걸음과 같은 규칙, 7-10)."""
    if ctx.scope_pitch_id is None:
        return SystemText.NEXT_PICK_PITCH if ctx.pitches else SystemText.NEXT_FIRST_PITCH
    has_take = bool(ctx.summaries or ctx.snapshots) or any(
        p.pitch_id == ctx.scope_pitch_id and p.take_count > 0 for p in ctx.pitches
    )
    return SystemText.NEXT_REVIEW if has_take else SystemText.NEXT_FIRST_TAKE


def _capability(ctx: Any) -> SystemText:
    corpus = ctx.corpus
    usage = corpus is not None and any(getattr(c, "kind", None) == "usage" for c in corpus.cards)
    return SystemText.TALK_CAPABILITY_USAGE if usage else SystemText.TALK_CAPABILITY


def _first_time(kind: str, ctx: Any) -> list[str]:
    """처음 안내: 대안(후보가 있을 때만) + 다음 걸음."""
    if kind == "PITCH":
        titles = [p.title for p in _recent_pitches(ctx)][:MAX_CANDIDATES]
        if not titles:
            return [SystemText.NEXT_FIRST_PITCH.value]
        listed = _quote_list(titles)
        return [f"지금 볼 수 있는 발표는 {listed}{_copula(titles[-1])}.", SystemText.NEXT_PICK_PITCH.value]
    if kind == "TAKE":
        takes = _recent_takes(ctx)[:MAX_CANDIDATES]
        if not takes:
            return [SystemText.NEXT_FIRST_TAKE.value]
        return [
            f"이 발표에서 볼 수 있는 기록은 {_take_list(takes)}{_copula(str(takes[-1]))}.",
            SystemText.NEXT_PICK_TAKE.value,
        ]
    if kind == "REVIEW":
        return [SystemText.NEXT_AFTER_REVIEW.value]
    if kind == "CHANGE":
        return [SystemText.NEXT_REVIEW.value] if _recent_takes(ctx) else []
    if kind == "SCOPE_BARE":
        return [_capability(ctx).value, _next_step(ctx).value]
    if kind == "SCOPE":
        return [_next_step(ctx).value]
    if kind == "ANSWER":
        return [SystemText.NEXT_ASK_FORM.value]
    if kind == "USAGE":
        return [_next_step(ctx).value]
    if kind == "MEASURE":
        return [SystemText.NEXT_MEASURE_CHECK.value]
    return []


def _choices(kind: str, ctx: Any) -> list[str]:
    """두 번째 같은 안내: 확인할 후보가 있으면 그 후보를 넣은 예시 질문 2~3개(없으면 빈 목록)."""
    examples: list[str] = []
    takes = _recent_takes(ctx)
    if kind == "PITCH" or (ctx.scope_pitch_id is None and kind in ("SCOPE_BARE", "SCOPE", "USAGE")):
        examples = [f"{p.title} 최근 Take 어땠어?" for p in _recent_pitches(ctx)[:MAX_CANDIDATES]]
    elif takes:
        n = takes[0]
        examples = [f"Take {n}에서 고칠 점이 뭐야?"]
        if len(takes) >= 2:
            examples.append(f"Take {takes[1]} · Take {n} 비교해 줘")
        examples.append(f"Take {n} 말 속도 어땠어?")
    if len(examples) < 2:
        return []
    return [f"{SystemText.CHOICES_HEADER.value} {_quote_list(examples[:MAX_CANDIDATES])}."]


def _clean(texts: Iterable[str], claims: Sequence[Claim], ctx: Any) -> list[Claim]:
    """이미 있는 문장은 빼고, 코드 문장 검사(금지 주장 · 개인정보 등, 6-9)를 통과한 것만 `SYSTEM` 문장으로."""
    have = {c.text for c in claims}
    out: list[Claim] = []
    for t in texts:
        if t in have:
            continue
        c = Claim(kind="SYSTEM", text=t)
        if chk.check_claims([c], registry=ctx.registry, code_sentences={t}, cfg=ctx.cfg):
            continue
        have.add(t)
        out.append(c)
    return out


def follow_up(
    ctx: Any,
    *,
    message: str | None,
    history_messages: Sequence[Any],
    claims: Sequence[Claim],
    status: str | None,
) -> Outcome:
    """끝 줄 직전에 붙일 대안 · 다음 걸음 · 반복 막기(위 1 · 2). 끝 상태가 DONE · PARTIAL · REFUSED 일 때만 본다."""
    if status not in ("DONE", "PARTIAL", "REFUSED"):
        return Outcome([])
    prev = previous_assistant_text(history_messages)
    texts: list[str] = []
    new_status: str | None = None
    if status == "PARTIAL" and is_continue(message):
        notices = {c.text for c in claims if c.kind == "SYSTEM" and c.text in PARTIAL_NOTICES}
        if notices and prev is not None and any(n in prev for n in notices):
            new_status = "DONE"
            texts += [SystemText.REPEAT_SAME_REASON.value, SystemText.REPEAT_SPLIT.value]
    seen: set[str] = set()
    # 답이 이미 있으면(기록 · 판단 · 자료 · 행동 결과 문장) 대안은 붙이지 않는다: 섞인 요청의 범위 밖 안내 · 끝에 붙는 안내는
    # 그 부분의 핵심 문장으로 충분하다. 리뷰 없음 · 측정값 이의는 답과 함께 다음 걸음을 붙인다
    # '없음' 근거만 이은 기록 문장("'…'라는 이름으로 찾은 피치는 0개예요")은 답이 아니라 '안 된다'의 사실이다: 대안 · 다음 걸음을
    # 막지 않는다(Issue #1194 독립 평가 — 모델이 이 문장을 쓰면 '어느 발표를 볼지 말씀해 주시면 …'이 빠졌다)
    answered = any(c.kind != "SYSTEM" and not _none_only(c, ctx) for c in claims)
    for c in claims:
        kind = CORE_KINDS.get(c.text) if c.kind == "SYSTEM" else None
        if kind is None or kind in seen or (answered and kind not in _WITH_ANSWER):
            continue
        seen.add(kind)
        if kind == "MEASURE":
            # 다음 걸음만(대안 · 선택지 없음). 모델이 이미 다음 Take 확인을 말했으면 붙이지 않는다
            if not any(_NEXT_TAKE.search(x.text) for x in claims):
                texts += _first_time(kind, ctx)
            continue
        if prev is not None and c.text in prev:
            texts += _choices(kind, ctx) or _first_time(kind, ctx)[-1:]
        else:
            texts += _first_time(kind, ctx)
    return Outcome(_clean(texts, claims, ctx), new_status)


__all__ = [
    "CONTINUE_MESSAGE",
    "CORE_KINDS",
    "PARTIAL_NOTICES",
    "Outcome",
    "follow_up",
    "is_continue",
    "previous_assistant_text",
]


def _none_only(c: Claim, ctx: Any) -> bool:
    """기록 문장이 '없음 · 0' 근거(`compose.none_kind`: 이름 검색 0개 · Take 0개 · 리뷰 없음 등)만 이었는가."""
    if c.kind != "RECORD" or not c.evidence_ids:
        return False
    registry = getattr(ctx, "registry", None)
    if registry is None:
        return False
    return all(compose.none_kind(registry.get(e)) is not None for e in c.evidence_ids)
