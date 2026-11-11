"""답 조립 — 판단 확정 · 템플릿 문장 · 답 검사 · 한 번 고치기 · 최종 메시지 (기능 문서 5-5 · 6-3 · 6-9 · 7-9 · 7-10).

- 누가 쓰나: turn.py. 답 초안(모델 구조화 출력)을 받아 화면에 나갈 `message`(문장 · 카드 · 자료)와 `assessments`를 만든다.
- 처리 순서
  1. 판단 확정: 초안을 `claims.finalize()`로 코드 판정 · 키 · 근거 지문 · 첫 문장(템플릿)까지 확정한다.
  2. 문장 만들기: 판단 문장 = 코드 첫 문장 + 모델 설명. 결론(CONCLUSION) · 행동 성공 · 확인 못 한 절 · 목표 판정 · 비교할 수 없는
     까닭(`not_comparable_sentence`) 문장은 코드 템플릿이다.
     모델이 쓴 RECORD · GUIDE 문장은 그대로 두고 검사한다.
  3. 답 검사(`checks.check_claims` · `check_display_strings`). 걸리면 걸린 모델 문장만 한 번 고치게 하고 다시 검사, 그래도 걸리면 지운다.
  4. 남은 문장이 없으면 질문 대상의 검증된 근거로 그린 템플릿 문장(값 `template_partial` · 읽은 범위의 '없음' `none_partial`)으로
     답하고, 근거도 없으면 고정 SYSTEM 안내(6-9 '고친 뒤'). 상태(DONE · PARTIAL)는 turn.py 가 정한다.
- `message.text` 는 남은 문장을 LF 로 이은 값과 정확히 같다(5-5). 모델 글을 그대로 내지 않는다.
- 고칠 때: 사용자에게 보이는 문구는 templates.py, 검사 규칙은 checks.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import Field

from . import claims as clm
from . import templates
from .corpus import sentence_spans
from .goals import GoalJudgement
from .schemas import Assessment, Claim, GuideCard

_CLAIM_MAX = 300


class DraftClaim(Claim):
    """모델 초안에서 온 문장. 응답의 `Claim` 모양은 그대로 두고 `GUIDE` 문장의 인용 문장(`quote`)만 안에서 곁에 싣는다
    (6-9 · 6-10). `quote`는 응답 · 서명 상태(`model_dump`)에 나가지 않는다."""

    quote: str | None = Field(default=None, exclude=True)
    #: 문장이 다루는 것(`DELIVERY` 발표 전달 조언 · `APP_USAGE` 피치코치 화면 · 기능 사용법 · None 기록 · 화면 사실). 내부 검사용
    about: str | None = Field(default=None, exclude=True)


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
    지우지 않는다(판단 첫 문장의 자료 표기는 카드 제목 "발표법 자료 '…'"이다, claims `knowledge_phrase`).

    숫자 검사가 id 속 숫자(`e-0e00ac50bb`의 0 · 00 · 50)를 문장의 값으로 읽어 맞는 문장을 빼지 않게 하고, 화면에 내부
    표기가 나가지 않게 한다(4회차: 고치기 응답이 "28%였습니다 (e-…)"로 써서 고친 문장까지 빠짐)."""
    t = text
    for p in _REF_PATTERNS:
        t = p.sub("", t)
    t = _SPACE_BEFORE_PUNCT.sub(r"\1", t)
    return re.sub(r"\s{2,}", " ", t).strip()


#: 모델이 쓴 'N번 Take' 표기(화면 · 근거 · 코드 문장의 Take 표기는 'Take N'). 'N번과 M번 Take'처럼 이어 쓴 꼴도 본다
_TAKE_LIST_RE = re.compile(
    r"((?:(?<![\d.])\d+번\s*(?:과|와|,|·|및)\s*)+)(\d+)번\s*Take(?![A-Za-z])"
)
_TAKE_ITEM_RE = re.compile(r"(\d+)번\s*(과|와|,|·|및)\s*")
_TAKE_ONE_RE = re.compile(r"(?<![\d.])(\d+)번\s*Take(?![A-Za-z])")


def _wa(num: str) -> str:
    """'Take N' 뒤 접속 조사: 숫자 읽기의 끝소리에 받침이 없으면(이 · 사 · 오 · 구) '와', 있으면 '과'."""
    return "와" if num[-1] in "2459" else "과"


def normalize_take_refs(text: str) -> str:
    """모델 문장의 'N번 Take'를 'Take N'으로 바꾼다(6-9 숫자 검사 · 화면 표기).

    "4번과 5번 Take에서는"의 '4번'은 Take 번호인데 숫자 검사는 횟수(4번)로 읽어 근거 없는 숫자로 문장을 뺐다(r9 rep-repeat-issue:
    반복 문제 답이 빠짐 · glob-ambiguous-name 숫자 불일치). 순서('3번째')는 바꾸지 않는다.

    예: "4번과 5번 Take에서는" → "Take 4와 Take 5에서는", "‘졸업 발표’의 2번 Take 점수는" → "‘졸업 발표’의 Take 2의 점수는"
    """

    def _list(m: re.Match[str]) -> str:
        parts = []
        for n, sep in _TAKE_ITEM_RE.findall(m.group(1)):
            if sep in ("과", "와"):
                parts.append(f"Take {n}{_wa(n)} ")
            elif sep == ",":
                parts.append(f"Take {n}, ")
            else:
                parts.append(f"Take {n} {sep} ")
        return "".join(parts) + f"Take {m.group(2)}"

    out = _TAKE_LIST_RE.sub(_list, text)
    # 뒤에 '점수'가 오면 'Take N의 점수'로 잇는다('Take 2 점수'는 'Take 2'의 2 를 점수 값처럼 읽게 한다)
    return _TAKE_ONE_RE.sub(
        lambda m: (
            f"Take {m.group(1)}" + ("의" if m.string[m.end() :].lstrip().startswith("점수") else "")
        ),
        out,
    )


#: 모델 문장에 그대로 나온 차원 상태 원값(리뷰의 내부 enum). 영문 낱말 경계로만 찾는다(한글이 붙어도 찾는다: "PRIORITY로")
_STATE_ENUM_RE = re.compile(
    r"(?<![A-Za-z_])(" + "|".join(sorted(clm.STATE_WORD, key=len, reverse=True)) + r")(?![A-Za-z_])"
    r"(으로|로|은|는|을|를|과|와)?"
)
#: 받침 있는 말 뒤 · 없는 말 뒤 조사 짝(ㄹ 받침 뒤 '로'는 그대로)
_JOSA_PAIRS = {"으로": ("으로", "로"), "로": ("으로", "로"), "은": ("은", "는"), "는": ("은", "는"),
               "을": ("을", "를"), "를": ("을", "를"), "과": ("과", "와"), "와": ("과", "와")}  # fmt: skip


def _josa(word: str, particle: str) -> str:
    """`word` 끝소리에 맞춘 조사(받침 있음 → 앞, 없음 → 뒤. ㄹ 받침 뒤 '로')."""
    last = ord(word[-1]) - 0xAC00
    if not 0 <= last < 11172:
        return particle
    final = last % 28
    with_batchim, without = _JOSA_PAIRS[particle]
    if particle in ("으로", "로"):
        return "로" if final in (0, 8) else "으로"
    return with_batchim if final else without


def normalize_state_words(text: str) -> str:
    """모델 문장에 나온 차원 상태 원값(PRIORITY · STRENGTH · IMPROVED · STABLE · NOT_EVALUABLE)을 화면 이름(우선 개선 · 강점 ·
    나아짐 · 유지 · 평가 불가, claims `STATE_WORD`)으로 바꾼다. 바로 뒤 조사는 바꾼 말의 끝소리에 맞춘다.

    예: "시선 상태는 PRIORITY이고, 유창성은 STRENGTH입니다." → "시선 상태는 우선 개선이고, 유창성은 강점입니다."
        "PRIORITY로 기록" → "우선 개선으로 기록"(r9 safe-tamper-review: 내부 enum 이 답에 그대로 나감)
    """

    def _sub(m: re.Match[str]) -> str:
        word = clm.STATE_WORD[m.group(1)]
        return word + (_josa(word, m.group(2)) if m.group(2) else "")

    return _STATE_ENUM_RE.sub(_sub, text)


def tidy_model_text(text: str) -> str:
    """모델이 쓴 문장 글을 검사 전에 다듬는다: 근거 · 자료 id 표기를 지우고(`strip_ref_ids`) 'N번 Take'를 'Take N'으로,
    차원 상태 원값을 화면 이름으로 바꾼다."""
    return normalize_state_words(normalize_take_refs(strip_ref_ids(text)))


def _claim(
    kind: str, text: str, *, quote: str | None = None, about: str | None = None, **kw: Any
) -> Claim | None:
    t = (text or "").strip()
    if not t:
        return None
    if len(t) > _CLAIM_MAX:
        return None
    if quote is not None or about is not None:
        return DraftClaim(kind=kind, text=t, quote=quote, about=about, **kw)  # type: ignore[arg-type]
    return Claim(kind=kind, text=t, **kw)  # type: ignore[arg-type]


def claim_quote(c: Claim) -> str | None:
    """문장이 곁에 실은 인용 문장(없으면 None). 초안에서 온 `GUIDE` 문장만 갖는다."""
    return getattr(c, "quote", None)


def claim_about(c: Claim) -> str | None:
    """문장이 곁에 실은 다루는 것(`APP_USAGE` 등, 없으면 None). 초안에서 온 문장만 갖는다."""
    return getattr(c, "about", None)


def claim_quotes(claims: list[Claim]) -> list[str]:
    """`GUIDE` 문장들의 인용 문장(문장 순서대로, 6-10 '답하는 법'). 자료 카드의 보이는 인용을 고를 때 쓴다."""
    return [q for c in claims if c.kind == "GUIDE" and (q := claim_quote(c))]


def _num_display(ctx: Any, ev: Any) -> str | None:
    if isinstance(ev.value, bool) or not isinstance(ev.value, (int, float)) or not ev.metric:
        return None
    if ev.metric not in ctx.cfg.metric_units:
        return None
    # 소리 크기는 그 Take 의 기준 음량 출처로 견주는 때를 붙인다('점검 때보다 1.1dB 작게', #1182)
    base = clm.volume_base(ctx.snapshots.get(ev.take_id)) if ev.take_id else None
    return clm.format_metric_value(ev.metric, ev.value, ctx.cfg, base=base)


def be(shown: str) -> str:
    """값 표기 + 서술 '예요'. 소리 크기 표기('…작게 · 같게')면 '… 나왔어요'(규칙은 `templates.be` 한곳, #1182 · #1183 리뷰).

    숫자 · 단위 표기 뒤는 지금처럼 '예요'를 붙인다(기록 문장 템플릿과 같은 꼴)."""
    return templates.be(shown) if shown.endswith(templates.VOLUME_ENDINGS) else f"{shown}예요"


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
    "DIFFERENT_VOLUME_BASE": "소리 크기를 견준 기준이 달라",
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
            return (
                f"Take {a} · Take {b} 기록은 모두 끝까지 한 발표가 아니라 {what} 비교할 수 없어요."
            )
        n, other = (a, b) if pa else (b, a)
        return (
            f"Take {n} 기록은 끝까지 한 발표가 아니라 Take {other} 기록과 {what} 비교할 수 없어요."
        )
    if reason in _NC_PAIR:
        return f"Take {a} · Take {b} 기록은 {_NC_PAIR[reason]} {what} 비교할 수 없어요."
    fixed = _NC_FIXED.get(reason or "")
    return fixed.value if fixed is not None else templates.SystemText.NOT_COMPARABLE.value


def date_display(value: str) -> str:
    """날짜 근거 값 'YYYY-MM-DD' → 화면 표기 'M월 D일'(값이 그 꼴이 아니면 그대로)."""
    m = re.fullmatch(r"\d{4}-(\d{2})-(\d{2})", value)
    return f"{int(m.group(1))}월 {int(m.group(2))}일" if m else value


def value_display(ctx: Any, ev: Any) -> str | None:
    """근거 값을 화면 표기로(숫자 지표는 단위 · 자리수, 날짜는 'M월 D일', 글 값은 그대로)."""
    shown = _num_display(ctx, ev)
    if shown is not None:
        return shown
    if ev.unit == "date" and isinstance(ev.value, str):
        return date_display(ev.value)
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
    return f"Take {take_number}의 {where}{topic(metric_label(goal.metric))} {be(obs)}. {word}."


def goal_value_sentence(ctx: Any, goal: Any, j: GoalJudgement, take_number: int) -> str | None:
    """닫힌 목표를 물을 때 보고 있는 Take 의 지금 값과 목표 기준의 관계(코드 템플릿). 값을 숫자로 못 쓰면 None.

    자동 목표 확인 문장(`goal_sentence`)과 달리 '목표를 이뤘어요'라고 하지 않는다: 이 Take 가 목표를 닫았는지는 알 수 없다.
    """
    word = templates.GOAL_VALUE_WORD.get(j.status)
    if (
        word is None
        or not isinstance(j.observed, (int, float))
        or goal.metric not in ctx.cfg.metric_units
    ):
        return None
    where = f"{goal.slide_number}장 " if goal.slide_number else ""
    obs = clm.format_metric_value(goal.metric, j.observed, ctx.cfg)
    return f"Take {take_number}의 {where}{topic(metric_label(goal.metric))} {be(obs)}. {word}."


def goal_open_sentence(ctx: Any, goal: Any) -> tuple[str, str, str] | None:
    """열린 대화 목표의 내용 문장(7-10 `GOAL_OPEN`)과 그 근거 id · 기준 값 표기(범위 목표는 ""). get_goals 가 이번 실행에 발급한
    목표 기준 값 근거가 없거나 값을 표시할 수 없으면 None."""
    eid = next(
        (
            e
            for e, ev in ctx.registry.items.items()
            if ev.kind == "GOAL"
            and ev.path == "conversation_goal.base.value"
            and ev.take_id == goal.base.take_id
            and ev.revision == goal.version
            and ctx.registry.is_current(e)
        ),
        None,
    )
    tpl = templates.GOAL_OPEN.get(goal.operator)
    if eid is None or tpl is None or goal.metric not in ctx.cfg.metric_units:
        return None
    if goal.operator != "in_range" and not isinstance(goal.base.value, (int, float)):
        return None
    value = (
        ""
        if goal.operator == "in_range"
        else clm.format_metric_value(goal.metric, goal.base.value, ctx.cfg)
    )
    text = tpl.format(
        take_number=goal.base.take_number,
        where=f"{goal.slide_number}장 " if goal.slide_number else "",
        label=metric_label(goal.metric),
        value=value,
    )
    return text, eid, value


def is_target_value(ev: Any) -> bool:
    """미션 기준값 근거인가(`missions[…].target.value` · `review.mission_results[…].target`). 측정값이 아니라 기준이라, 코드가
    측정값 문장("Take 4의 3장 청중 시선은 40%예요")을 그릴 때 고르지 않는다(Issue #1194 코드 검수)."""
    path = getattr(ev, "path", "") or ""
    return path.endswith(".target") or path.endswith(".target.value")


def template_partial(ctx: Any, used_ids: list[str]) -> list[Claim]:
    """남은 문장이 없을 때 검증된 근거 값으로 그린 문장(최대 3개). 미션 기준값은 측정값이 아니라 그리지 않는다."""
    out: list[Claim] = []
    for eid in used_ids:
        ev = ctx.registry.get(eid)
        if (
            ev is None
            or not ctx.registry.is_current(eid)
            or ev.origin != "RECORD"
            or ev.take_number is None
            or is_target_value(ev)
        ):
            continue
        disp = _num_display(ctx, ev)
        if disp is None:
            continue
        where = f"{ev.slide_number}장 " if ev.slide_number else ""
        c = _claim(
            "RECORD",
            f"Take {ev.take_number}의 {where}{topic(metric_label(ev.metric))} {be(disp)}.",
            evidence_ids=[eid],
        )
        if c is not None:
            out.append(c)
        if len(out) >= 3:
            break
    return out


# ── '없음' 근거 (6-8 · 6-9 '고친 뒤', 7-10) ───────────────────────────────────

#: '없음' 근거의 경로(tools/impl.py 가 낸다) → 종류. 값이 0 · NONE 일 때만 '없음'이다(피치의 Take 수 3 은 '없음'이 아니다)
_NONE_PATHS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^pitches\[pitch_id=[^\],]+\]\.take_count$"), "TAKES"),
    (re.compile(r"^takes\[pitch_id=[^\],]*\]\.count$"), "TAKES"),
    (re.compile(r"^takes\[pitch_id=[^\],]*,[^\]]*\]\.count$"), "TAKES_FILTERED"),
    (re.compile(r"^pitches\[query\]\.count$"), "PITCH_QUERY"),
    (re.compile(r"^review\.status$"), "REVIEW"),
    (re.compile(r"^goal\.status$"), "GOAL"),
    (re.compile(r"^problem_segments\[area=([A-Z_]+)(?:,slide_number=\d+)?\]\.count$"), "SEGMENTS"),
)
#: 근거 표(`evidence_table`)의 '무엇' 이름(지표가 없는 Take 수 · 상태 근거)
_NONE_WHAT = {
    "TAKES": "Take 수",
    "TAKES_FILTERED": "조건에 맞는 Take 수",
    "PITCH_QUERY": "이름으로 찾은 피치 수",
    "REVIEW": "리뷰 상태",
    "GOAL": "대화 목표 상태",
}


def _none_match(ev: Any) -> tuple[str, re.Match[str]] | None:
    if ev is None or ev.origin != "RECORD":
        return None
    for pat, kind in _NONE_PATHS:
        m = pat.match(ev.path)
        if m is not None:
            return kind, m
    return None


def none_kind(ev: Any) -> str | None:
    """근거가 읽은 범위의 '없음 · 0'이면 그 종류(`TAKES` · `TAKES_FILTERED` · `PITCH_QUERY` · `REVIEW` · `GOAL` ·
    `SEGMENTS`), 아니면 None."""
    hit = _none_match(ev)
    if hit is None:
        return None
    kind = hit[0]
    if kind in ("REVIEW", "GOAL"):
        return kind if ev.value == "NONE" else None
    return kind if ev.value == 0 and not isinstance(ev.value, bool) else None


def segment_area(ev: Any) -> str | None:
    """문제 구간 0곳 근거의 영역(`problem_segments[area=GAZE].count` · `[area=GAZE,slide_number=2]` → GAZE)."""
    hit = _none_match(ev)
    return hit[1].group(1) if hit is not None and hit[0] == "SEGMENTS" else None


def evidence_what(ev: Any) -> str | None:
    """지표가 없는 Take 수 · 상태 · 날짜 근거의 이름(근거 표 '무엇'). 그 밖은 None."""
    if ev is not None and ev.unit == "date" and ev.path.endswith(".last_practiced_at"):
        return "마지막 연습 시작"
    hit = _none_match(ev)
    if hit is None:
        return None
    if hit[0] == "SEGMENTS":
        area = hit[1].group(1)
        return f"문제 구간 수({templates.AREA_LABEL.get(area, area)})"
    return _NONE_WHAT.get(hit[0])


def pitch_title(ctx: Any, pitch_id: str | None) -> str | None:
    """피치 제목(범위 피치는 기본 자료의 피치 정보, 아니면 피치 목록에서)."""
    if pitch_id is None:
        return None
    if ctx.pitch is not None and pitch_id == ctx.scope_pitch_id:
        return ctx.pitch.info.title
    return next((p.title for p in ctx.pitches if p.pitch_id == pitch_id), None)


def segments_sentence(take_number: int, slide: int | None, label: str | None) -> str:
    """'Take 5 2장의 시선에서 찾은 문제 구간이 없어요.' 영역 이름이 없으면 읽은 Take · 장 전체(모든 영역이 측정됐을 때만 쓴다).
    장 번호와 영역 이름 사이에 '의'를 둔다('2장 시간 배분'이 지표 이름 '장 시간'으로 읽히지 않게)."""
    where = " ".join(
        x for x in (f"{slide}장" + ("의" if label else "") if slide else "", label or "") if x
    )
    if not where:
        return templates.NONE_SEGMENTS.replace(" {area}", "").format(n=take_number)
    return templates.NONE_SEGMENTS.format(n=take_number, area=where)


def none_sentence(ctx: Any, ev: Any) -> str | None:
    """'없음' 근거 하나의 문장(코드 템플릿, 7-10). 읽은 범위를 함께 말한다. 거른 조건의 Take 0개 · 이름 검색 0개는 그리지 않는다
    (조건 · 이름 범위를 문장으로 옮기지 않고 넓히지도 않는다. 이름 검색 0개는 고정 문장 `PITCH_NOT_FOUND` 길)."""
    kind = none_kind(ev)
    if kind == "TAKES":
        title = pitch_title(ctx, ev.pitch_id)
        return templates.NONE_TAKES.format(title=title) if title else None
    if kind == "REVIEW" and ev.take_number is not None:
        return templates.NONE_REVIEW.format(n=ev.take_number)
    if kind == "GOAL":
        return templates.NONE_GOAL
    if kind == "SEGMENTS" and ev.take_number is not None:
        area = segment_area(ev) or ""
        return segments_sentence(ev.take_number, ev.slide_number, templates.AREA_LABEL.get(area))
    return None


def none_partial(ctx: Any, ids: list[str], *, areas: frozenset[str] | None = None) -> list[Claim]:
    """'없음' 근거로 그린 RECORD 문장(같은 문장은 근거를 합쳐 한 번, 최대 3개, 근거 순서대로).

    문제 구간 0곳은 물은 영역(`areas`, 판단 초안의 지표 · 차원)이 있으면 그 영역만 영역 이름과 함께 그린다. 물은 영역을 모르면 읽은
    Take · 장의 모든 영역이 측정됐고 모두 0곳일 때만 영역 이름 없이 한 문장으로 그린다(측정 안 된 영역을 '없음'으로 넓히지 않는다)."""
    reg = ctx.registry
    #: 처음 본 근거 순서대로: 문장 하나(글 → 근거들) 또는 문제 구간 묶음(Take · 장)
    order: list[tuple[str, Any]] = []
    texts: dict[str, list[str]] = {}
    seg_groups: dict[tuple[str | None, int | None], list[Any]] = {}
    for eid in dict.fromkeys(ids):
        ev = reg.get(eid)
        if ev is None or not reg.is_current(eid):
            continue
        kind = none_kind(ev)
        if kind == "SEGMENTS":
            key = (ev.take_id, ev.slide_number)
            if key not in seg_groups:
                order.append(("seg", key))
            seg_groups.setdefault(key, []).append(ev)
            continue
        text = none_sentence(ctx, ev) if kind is not None else None
        if text is None:
            continue
        if text not in texts:
            order.append(("text", text))
        texts.setdefault(text, []).append(eid)
    pairs: list[tuple[str, list[str]]] = []
    for what, key in order:
        if what == "text":
            pairs.append((key, texts[key]))
        else:
            pairs.extend(_segment_pairs(ctx, key, seg_groups[key], areas))
    out: list[Claim] = []
    for text, eids in pairs:
        c = _claim("RECORD", text, evidence_ids=list(dict.fromkeys(eids)))
        if c is not None and all(c.text != o.text for o in out):
            out.append(c)
    return out[:3]


def _segment_pairs(
    ctx: Any, key: tuple[str | None, int | None], evs: list[Any], areas: frozenset[str] | None
) -> list[tuple[str, list[str]]]:
    take_id, slide = key
    n = evs[0].take_number
    if n is None:
        return []
    if areas is not None:
        return [
            (
                segments_sentence(n, slide, templates.AREA_LABEL.get(segment_area(ev) or "")),
                [ev.evidence_id],
            )
            for ev in evs
            if segment_area(ev) in areas
        ]
    snap = ctx.snapshots.get(take_id) if take_id else None
    tr = (snap.results.take_result or {}) if snap is not None else {}
    all_areas = {a for a in (tr.get("areas") or {}) if isinstance(a, str)}
    if all_areas and {segment_area(ev) for ev in evs} == all_areas:
        return [(segments_sentence(n, slide, None), [ev.evidence_id for ev in evs])]
    return []


def first_sentence(text: str) -> str:
    """카드 본문의 첫 문장(300자 안: 카드 문장은 한 문장씩 300자 안으로 쓴다)."""
    spans = sentence_spans(text)
    if not spans:
        return text[:300]
    start, end = spans[0]
    return text[start:end][:300]


def guide_cards(
    ctx: Any, source_ids: list[str], quotes: list[str] | None = None
) -> list[GuideCard]:
    """자료 카드. 보이는 인용(`quote`)은 `quotes`(`GUIDE` 문장의 인용 문장, 순서대로) 중 그 카드 본문에 있는 첫 문장이고,
    없으면(코드 템플릿이 자료를 이은 때) 카드의 첫 문장이다. 카드 앞 300자를 그냥 자르지 않는다(인용한 문장과 화면이
    달라지지 않게)."""
    corpus = ctx.corpus
    out: list[GuideCard] = []
    if corpus is None:
        return out
    quotes = quotes or []
    for sid in dict.fromkeys(source_ids):
        card = corpus.get(sid)
        if card is None:
            continue
        q = next((x for x in quotes if x in card.text), None) or first_sentence(card.text)
        out.append(
            GuideCard(
                source_id=sid,
                title=card.title,
                quote=q[:300],
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
