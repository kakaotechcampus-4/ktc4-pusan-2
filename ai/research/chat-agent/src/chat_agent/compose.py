"""답 조립 — 판단 확정 · 템플릿 문장 · 답 검사 · 한 번 고치기 · 최종 메시지 (기능 문서 5-5 · 6-3 · 6-9 · 7-9 · 7-10).

- 누가 쓰나: turn.py. 답 초안(모델 구조화 출력)을 받아 화면에 나갈 `message`(문장 · 카드 · 자료)와 `assessments`를 만든다.
- 처리 순서
  1. 판단 확정: 초안을 `claims.finalize()`로 코드 판정 · 키 · 근거 지문 · 첫 문장(템플릿)까지 확정한다.
  2. 문장 만들기: 판단 문장 = 코드 첫 문장 + 모델 설명. 결론(CONCLUSION) · 행동 성공 · 확인 못 한 절 · 목표 판정 · 비교할 수 없는
     까닭(`not_comparable_sentence`) 문장은 코드 템플릿이다.
     모델이 쓴 RECORD · GUIDE 문장은 그대로 두고 검사한다.
  3. 답 검사(`checks.check_claims` · `check_display_strings`). 걸리면 걸린 모델 문장만 한 번 고치게 하고 다시 검사, 그래도 걸리면 지운다.
  4. 남은 문장이 없으면 검증된 근거 값을 템플릿으로 그린 PARTIAL, 근거도 없으면 고정 SYSTEM 안내(6-9 '고친 뒤').
- `message.text` 는 남은 문장을 LF 로 이은 값과 정확히 같다(5-5). 모델 글을 그대로 내지 않는다.
- 고칠 때: 사용자에게 보이는 문구는 templates.py, 검사 규칙은 checks.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from . import claims as clm
from . import templates
from .goals import GoalJudgement
from .schemas import Assessment, Claim, GuideCard

_CLAIM_MAX = 300


@dataclass
class Composed:
    """조립 결과."""

    status: str
    claims: list[Claim]
    assessments: list[Assessment]
    cards: list[dict[str, Any]] = field(default_factory=list)
    sources: list[GuideCard] = field(default_factory=list)
    history_summary: str | None = None
    #: 진단용: 판단마다 (outcome) — ASSESS 단계
    outcomes: list[Any] = field(default_factory=list)


#: 근거 · 자료 id(기록 근거 e- · 계산 근거 c- · 자료 카드 g- · u-). id 는 evidence_ids · source_ids 칸에만 쓴다(6-9)
_REF_ID = r"(?<![0-9A-Za-z])(?:[ec]-[0-9a-f]{10}|[gu]-\d{2,3})(?![0-9A-Za-z])"
_REF_LIST = rf"{_REF_ID}(?:\s*[,·、/]\s*{_REF_ID})*"
_REF_PATTERNS = (
    # "(e-0e00ac50bb)" · "(근거: e-…, e-…)" · "[g-13]"
    re.compile(
        rf"\s*[(\[]\s*(?:(?:근거|출처|자료)\s*(?:id)?\s*[:：]?\s*)?{_REF_LIST}\s*[)\]]",
        re.IGNORECASE,
    ),
    # "근거: e-…"
    re.compile(rf"\s*(?:근거|출처)\s*(?:id)?\s*[:：]\s*{_REF_LIST}", re.IGNORECASE),
    # 맨 id
    re.compile(rf"\s*{_REF_LIST}"),
)
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([.,!?])")


def strip_ref_ids(text: str) -> str:
    """모델이 문장 글에 적은 근거 · 자료 id 표기를 지운다(6-9). 모델이 쓴 글에만 쓴다(answer.py): 코드 템플릿 문장은
    자료 id 를 일부러 싣는다(판단 첫 문장의 "발표법 자료(g-18)").

    숫자 검사가 id 속 숫자(`e-0e00ac50bb`의 0 · 00 · 50)를 문장의 값으로 읽어 맞는 문장을 빼지 않게 하고, 화면에 내부
    표기가 나가지 않게 한다(4회차: 고치기 응답이 "28%였습니다 (e-…)"로 써서 고친 문장까지 빠짐)."""
    t = text
    for p in _REF_PATTERNS:
        t = p.sub("", t)
    t = _SPACE_BEFORE_PUNCT.sub(r"\1", t)
    return re.sub(r"\s{2,}", " ", t).strip()


def _claim(kind: str, text: str, **kw: Any) -> Claim | None:
    t = (text or "").strip()
    if not t:
        return None
    if len(t) > _CLAIM_MAX:
        return None
    return Claim(kind=kind, text=t, **kw)  # type: ignore[arg-type]


def _num_display(ctx: Any, ev: Any) -> str | None:
    if isinstance(ev.value, bool) or not isinstance(ev.value, (int, float)) or not ev.metric:
        return None
    if ev.metric not in ctx.cfg.metric_units:
        return None
    return clm.format_metric_value(ev.metric, ev.value, ctx.cfg)


def obj(word: str) -> str:
    """목적격 조사 '을/를': 마지막 한글 음절에 받침이 있으면 '을'."""
    w = word.rstrip(" )")
    last = w[-1] if w else ""
    if "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28:
        return word + "을"
    return word + "를"


#: 비교할 수 없는 까닭 → 문장 틀(7-10). 두 Take 의 조건이 다른 까닭만 틀로, 값이 없는 등 그 밖의 까닭은 고정 문장(templates)
_NC_PAIR = {
    "DIFFERENT_SCRIPT_VERSION": "대본 버전이 달라",
    "DIFFERENT_CRITERIA_VERSION": "평가 기준 버전이 달라",
    "RULE_VERSION_CHANGED": "판정 기준 버전이 달라",
    "MISSING_VERSION": "버전 정보가 없어",
}
_NC_FIXED = {
    "NO_VALUE": templates.SystemText.METRIC_UNMEASURED,
    "OMITTED": templates.SystemText.OMITTED,
    "NO_DIRECTION": templates.SystemText.NO_DIRECTION,
    "INVALID_DATA": templates.SystemText.INVALID_DATA,
}


def not_comparable_sentence(rec: dict[str, Any]) -> str:
    """`compare_takes`가 비교할 수 없다고 한 까닭 문장(코드 템플릿, 7-10).

    예: 'Take 4 기록은 끝까지 한 발표가 아니라 Take 5 기록과 3장 청중 시선을 비교할 수 없어요.'
    숫자 바로 뒤에 조사를 붙이지 않는다(읽는 소리에 따라 은/는 · 과/와가 달라 틀리기 쉽다)."""
    a, b = rec["previous"], rec["current"]
    where = f"{rec['slide_number']}장 " if rec.get("slide_number") else ""
    what = obj(where + metric_label(rec.get("metric")))
    reason = rec.get("reason")
    if reason == "NOT_COMPLETED_FULLY":
        if rec.get("previous_complete") is None or rec.get("current_complete") is None:
            # 끝까지 했는지 모르는 Take(null)는 '끝까지 하지 않았다'고 단정하지 않는다(12-3)
            return templates.SystemText.COMPLETION_UNKNOWN.value
        pa, pb = rec.get("previous_complete") is not True, rec.get("current_complete") is not True
        if pa and pb:
            return f"Take {a} · Take {b} 기록은 모두 끝까지 한 발표가 아니라 {what} 비교할 수 없어요."
        n, other = (a, b) if pa else (b, a)
        return f"Take {n} 기록은 끝까지 한 발표가 아니라 Take {other} 기록과 {what} 비교할 수 없어요."
    if reason in _NC_PAIR:
        return f"Take {a} · Take {b} 기록은 {_NC_PAIR[reason]} {what} 비교할 수 없어요."
    fixed = _NC_FIXED.get(reason or "")
    return fixed.value if fixed is not None else templates.SystemText.NOT_COMPARABLE.value
def value_display(ctx: Any, ev: Any) -> str | None:
    """근거 값을 화면 표기로(숫자 지표는 단위 · 자리수, 글 값은 그대로)."""
    shown = _num_display(ctx, ev)
    if shown is not None:
        return shown
    return ev.value if isinstance(ev.value, str) else None


def topic(word: str) -> str:
    """주제 조사 '은/는': 마지막 한글 음절에 받침이 있으면 '은'."""
    w = word.rstrip(" )")
    last = w[-1] if w else ""
    if "가" <= last <= "힣" and (ord(last) - 0xAC00) % 28:
        return word + "은"
    return word + "는"


def metric_label(metric: str | None) -> str:
    labels = getattr(clm, "METRIC_LABEL", None) or {}
    return labels.get(metric or "", metric or "")


def conclusion_sentence(ctx: Any, outcome_id: str) -> tuple[str, list[str]] | None:
    """비교 결론 문장(코드 템플릿, 6-9 · L4-25): 'Take 4의 28% → Take 5의 33%, 5%p 증가예요(좋아졌어요)'.

    `outcome_id` 는 compare_takes 가 발급한 결론 계산 근거다. 입력 근거(두 Take 값)를 따라가 문장을 그린다.
    """
    reg = ctx.registry
    ev = reg.get(outcome_id)
    if ev is None or ev.origin != "COMPUTED" or not reg.is_current(outcome_id):
        return None
    if not isinstance(ev.value, str) or ev.value not in templates.OUTCOME_WORD:
        return None
    records: list[Any] = []
    stack = list(ev.inputs or [])
    seen: set[str] = set()
    while stack:
        i = stack.pop(0)
        if i in seen:
            continue
        seen.add(i)
        e = reg.get(i)
        if e is None:
            continue
        if e.origin == "RECORD" and e.take_number is not None and isinstance(e.value, (int, float)):
            records.append(e)
        elif e.origin == "COMPUTED":
            stack.extend(e.inputs or [])
    by_take: dict[int, Any] = {}
    for r in records:
        if r.metric == ev.metric:
            by_take.setdefault(r.take_number, r)
    if len(by_take) != 2:
        return None
    (n1, a), (n2, b) = sorted(by_take.items())
    da, db = _num_display(ctx, a), _num_display(ctx, b)
    if da is None or db is None:
        return None
    where = f"{ev.slide_number}장 " if ev.slide_number else ""
    word = templates.OUTCOME_WORD[ev.value]
    text = f"{where}{metric_label(ev.metric)}: Take {n1}의 {da} → Take {n2}의 {db}, {word}."
    return text, [outcome_id, a.evidence_id, b.evidence_id]


def goal_sentence(ctx: Any, goal: Any, j: GoalJudgement, take_number: int) -> str:
    """자동 목표 확인 문장(코드 템플릿)."""
    where = f"{goal.slide_number}장 " if goal.slide_number else ""
    obs = (
        clm.format_metric_value(goal.metric, j.observed, ctx.cfg)
        if isinstance(j.observed, (int, float)) and goal.metric in ctx.cfg.metric_units
        else None
    )
    word = templates.GOAL_WORD[j.status]
    if obs is None:
        return f"Take {take_number}: {word}."
    return f"Take {take_number}의 {where}{topic(metric_label(goal.metric))} {obs}예요. {word}."


def template_partial(ctx: Any, used_ids: list[str]) -> list[Claim]:
    """남은 문장이 없을 때 검증된 근거 값으로 그린 문장(최대 3개)."""
    out: list[Claim] = []
    for eid in used_ids:
        ev = ctx.registry.get(eid)
        if (
            ev is None
            or not ctx.registry.is_current(eid)
            or ev.origin != "RECORD"
            or ev.take_number is None
        ):
            continue
        disp = _num_display(ctx, ev)
        if disp is None:
            continue
        where = f"{ev.slide_number}장 " if ev.slide_number else ""
        c = _claim(
            "RECORD",
            f"Take {ev.take_number}의 {where}{topic(metric_label(ev.metric))} {disp}예요.",
            evidence_ids=[eid],
        )
        if c is not None:
            out.append(c)
        if len(out) >= 3:
            break
    return out


def guide_cards(ctx: Any, source_ids: list[str]) -> list[GuideCard]:
    corpus = ctx.corpus
    out: list[GuideCard] = []
    if corpus is None:
        return out
    for sid in dict.fromkeys(source_ids):
        card = corpus.get(sid)
        if card is None:
            continue
        out.append(
            GuideCard(
                source_id=sid,
                title=card.title,
                quote=card.text[:300],
                source=card.source,
                license=card.license,
            )
        )
    return out


def referenced(ctx: Any, ids: list[str]) -> list[dict[str, Any]]:
    """답 · 판단 · 카드가 가리킨 근거와 그 입력(계산 근거를 따라감)의 사본(5-5: 입력 근거도 같은 목록에)."""
    out: dict[str, Any] = {}
    stack = list(ids)
    while stack:
        i = stack.pop()
        if i in out:
            continue
        ev = ctx.registry.get(i)
        if ev is None:
            continue
        out[i] = ev.model_dump(mode="json")
        stack.extend(ev.inputs or [])
    return list(out.values())
