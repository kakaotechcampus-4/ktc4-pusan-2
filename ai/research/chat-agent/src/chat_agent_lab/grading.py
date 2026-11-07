"""평가 채점기 — 시나리오 검사(10-5 `expected.checks[]`)를 관찰값에 대 본다 (기능 문서 10-5 · 13-1 · 14-1, 연구 전용).

- 채점기 독립(10-5): 숫자 · 대상 · 인용 채점은 제품 답 검사(`chat_agent/checks.py`)를 부르지 않고 여기서 따로 구현한다.
  숫자 표기 규칙은 `architecture.md` 0-2 표(0.28 ↔ 28% ↔ 28퍼센트, 88000ms ↔ 88초 ↔ 1분 28초, 보여 준 자리까지 반올림,
  정수 개수 · 점수는 반올림 없이 그대로, 음수는 크기, 한글 수는 보지 않음)를 이 파일에서 다시 짠다. 고정 문구는
  `chat_agent.templates`의 상수를 이름으로 읽어 정규식으로 바꾸고, 자리 뒤 '으로' · '예요'는 채워진 값의 받침에 맞는 것만
  받는다(templates.fill 과 같은 규칙을 여기서 따로 구현: 받침 없거나 ㄹ이면 '로', 받침 있으면 '이에요', 숫자는 읽는 소리).
- 관찰값(evals.py 가 만든다, 모두 JSON 값)
  - answer: `{status, outcome, error_code, text, all_text, claims[], cards[], card_types[], sources[],
    source_ids[], assessments[], verdicts[], scope_pitch_id, goal_result, goal_results[], evidence[], has_record_evidence,
    profile_updates[], record_texts[], advice_text}`(record_texts: RECORD 문장 글만 순서대로, advice_text: GUIDE · GENERAL 문장 글만)
  - actions_proposed: `{types[], items[], cards[]}` — AI가 낸 행동(NEEDS_ACTION · DONE 의 START_PRACTICE 버튼)과 카드 종류.
    `items[].autonomy`는 `AUTO` · `ASK` · `BUTTON`
  - actions_executed: `{types[], attempted[], items[], counts{}, attempt_counts{}, review_calls}` — BE가 실제로 실행한 것
    (`ASK` 행동은 승인 · 선승인 뒤에만. START_PRACTICE 는 BE 가 실행하지 않는 버튼이라 세지 않고, 화면 이동은 사용자가
    누른 기록 `db_diff`의 `conversation.practice_moves`로만 본다)
  - db_diff: `{changed[], removed[], official_changed[], before{}, after{}}` — 실행 전후 저장소(공식 결과 · 대화 목표 · 기억(설정 ·
    번호 · 코칭 프로필) …)
  - trace: `{tools[], diag[], limits[], calls, statuses[], model_calls, tool_calls, claim_lines, stream_ok, profile_sent}` — 도구 기록과 진단 기록 단계
    (`종류:코드[:결과]`), 문장 줄 수 · 문장 줄이 끝 줄 앞부분과 같은가
- 비교(compare): equals(같음) · contains(문자열이면 부분 문자열, 목록이면 원소 · 딕셔너리면 일부 칸 일치. expect 가 목록이면
  모두 있어야) · set_equals(집합 같음) · absent(expect 의 원소가 하나라도 있으면 실패, 하나 값이면 다름) · one_of(허용 집합:
  값이 목록 안에 있음, 관찰값이 목록이면 하나라도. 목록 관찰값을 쓰는 검사는 모두 '이 중 하나라도 나오면 됨'이 뜻이고,
  목록 밖 원소를 막으려면 absent 를 함께 쓴다, 10-5). 관찰값이 없으면(경로가 없음, `items[].x`에서 한 원소라도 x 가 없음 포함)
  어떤 비교든 실패다(10-5). 딕셔너리 일부 칸 일치의 키 끝 `~`는 부분 문자열, `@`는 고정 문구가 있는가.
- 숫자 정확도(14-1)의 셈 규칙은 `number_accuracy` 설명(분모에 단위 없는 정수 포함, 대상 번호 · 순서 · 날짜 제외, 원본을 읽지
  못한 근거는 대조 불가로 따로).
- `GENERAL` 금지 위반(14-1)은 `general_violations`: 제품의 GENERAL_RULE(checks.py · config.py 낱말 목록)과 따로 짠 규칙 ·
  낱말이다. 제품 목록이 놓친 우회를 잡으려는 것이라 일부러 같게 맞추지 않는다.
- 고칠 때: 규칙을 바꾸면 tests/lab/test_grading.py(비교 · 숫자 · 대상 · 인용 규칙의 통과 · 실패 예시), GENERAL 금지 위반은
  tests/lab/test_evals_agent_service.py 의 예시를 같이 고친다(#892 리뷰). 제품 검사 코드를 import 하지 않는다.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, NamedTuple

from pitch_coach_ai.features.chat_agent import templates

MISSING: Any = object()


class TemplateMissing(LookupError):
    """시나리오가 가리킨 고정 문구가 templates.py 에 없다."""


@dataclass
class CheckResult:
    check_id: str
    passed: bool
    reason: str
    metric: str | None = None
    observed: Any = None


# ── 관찰값 경로 ───────────────────────────────────────────────────────────

_SEG = re.compile(r"^([^\[\]]*)((?:\[[^\]]*\])*)$")


def resolve(obj: Any, path: str | None) -> Any:
    """`a.b[0].c` · `items[type=FETCH_TAKE].status` · `assessments[]`(목록 그대로, 뒤 경로는 원소마다)를 읽는다. 없으면 MISSING."""
    if path is None or path == "":
        return obj
    parts = path.split(".")
    return _walk(obj, parts)


def _walk(obj: Any, parts: list[str]) -> Any:
    if not parts:
        return obj
    head, rest = parts[0], parts[1:]
    m = _SEG.match(head)
    if m is None:
        return MISSING
    name, brackets = m.group(1), m.group(2)
    cur = obj
    if name:
        if not isinstance(cur, Mapping) or name not in cur:
            return MISSING
        cur = cur[name]
    for b in re.findall(r"\[([^\]]*)\]", brackets):
        if not isinstance(cur, list):
            return MISSING
        if b == "":
            # 원소마다 뒤 경로를 읽는다. 한 원소라도 그 경로가 없으면 관찰값 없음(빠진 원소를 조용히 버리지 않는다)
            vals = [_walk(x, rest) for x in cur]
            return MISSING if any(v is MISSING for v in vals) else vals
        if "=" in b:
            k, v = b.split("=", 1)
            cur = next((x for x in cur if isinstance(x, Mapping) and str(x.get(k)) == v), MISSING)
            if cur is MISSING:
                return MISSING
        else:
            try:
                cur = cur[int(b)]
            except (ValueError, IndexError):
                return MISSING
    return _walk(cur, rest)


# ── 비교 ──────────────────────────────────────────────────────────────────


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b if isinstance(a, bool) and isinstance(b, bool) else False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(float(a), float(b), rel_tol=0, abs_tol=1e-9)
    if isinstance(a, Mapping) and isinstance(b, Mapping):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    return a == b


def partial_match(obj: Any, want: Mapping[str, Any]) -> bool:
    """딕셔너리 일부 칸 일치. 키 `a.b`는 경로. 끝이 `~`면 부분 문자열(예 `{"quote~": "시선"}`),
    끝이 `@`면 그 칸의 글에 고정 문구가 있는가(예 `{"text@": "CONTRADICTED"}`, 값은 template 채점기와 같은 모양)."""
    if not isinstance(obj, Mapping):
        return False
    for key, w in want.items():
        sub, tpl = key.endswith("~"), key.endswith("@")
        k = key[:-1] if sub or tpl else key
        got = resolve(obj, k)
        if got is MISSING:
            return False
        if tpl:
            if not isinstance(got, str) or not template_found(got, w):
                return False
        elif sub:
            if not isinstance(got, str) or not isinstance(w, str) or w not in got:
                return False
        elif isinstance(w, Mapping) and isinstance(got, Mapping):
            if not partial_match(got, w):
                return False
        elif not _same(got, w):
            return False
    return True


def _member(obs: list[Any], want: Any) -> bool:
    if isinstance(want, Mapping):
        return any(partial_match(x, want) for x in obs)
    return any(_same(x, want) for x in obs)


def _key(x: Any) -> str:
    return json.dumps(x, ensure_ascii=False, sort_keys=True, default=str)


def compare(op: str, obs: Any, expect: Any) -> tuple[bool, str]:
    """(통과, 사유). 관찰값이 없으면 실패."""
    if obs is MISSING:
        return False, "관찰값 없음"
    if op == "equals":
        ok = _same(obs, expect)
        return ok, "" if ok else f"{_short(obs)} ≠ {_short(expect)}"
    if op == "one_of":
        if not isinstance(expect, list):
            return False, "one_of 의 expect 는 목록이다"
        many = isinstance(obs, list)
        ok = any(_member(expect, x) for x in obs) if many else _member(expect, obs)
        return ok, "" if ok else f"{_short(obs)} ∉ {_short(expect)}"
    if op == "set_equals":
        if not isinstance(obs, list) or not isinstance(expect, list):
            return False, "set_equals 는 목록끼리"
        ok = {_key(x) for x in obs} == {_key(x) for x in expect}
        return ok, "" if ok else f"{_short(sorted(set(map(_key, obs))))} ≠ {_short(expect)}"
    if op in ("contains", "absent"):
        # expect 가 목록이면 원소마다 따로 본다: contains 는 모두 있어야, absent 는 하나라도 있으면 실패
        wants = expect if isinstance(expect, list) else [expect]
        present = [w for w in wants if _present(obs, w)]
        if op == "contains":
            missing = [w for w in wants if w not in present]
            if not missing:
                return True, ""
            return False, f"{_short(obs)} 에 없음: {_short(missing)}"
        if not present:
            return True, ""
        return False, f"{_short(obs)} 에 있음: {_short(present)}"
    return False, f"모르는 비교 {op}"


def _present(obs: Any, want: Any) -> bool:
    """관찰값 안에 기대 원소 하나가 있는가(글이면 부분 문자열, 목록이면 원소 · 딕셔너리 일부 칸, 하나 값이면 같음)."""
    if obs is None:
        return False
    if isinstance(obs, str):
        return isinstance(want, str) and want in obs
    if isinstance(obs, list):
        return _member(obs, want)
    if isinstance(obs, Mapping) and isinstance(want, Mapping):
        return partial_match(obs, want)
    return _same(obs, want)


def _short(x: Any, n: int = 160) -> str:
    s = x if isinstance(x, str) else _key(x)
    return s if len(s) <= n else s[: n - 1] + "…"


# ── 숫자 (architecture.md 0-2 표, 독립 구현) ──────────────────────────────

#: 단위 → (종류, 기본 단위로 바꾸는 배수)
_UNITS: dict[str, tuple[str, float]] = {
    "%p": ("pp", 0.01),
    "%포인트": ("pp", 0.01),
    "퍼센트포인트": ("pp", 0.01),
    "%": ("ratio", 0.01),
    "퍼센트": ("ratio", 0.01),
    "ms": ("time", 0.001),
    "밀리초": ("time", 0.001),
    "초": ("time", 1.0),
    "분": ("time", 60.0),
    "시간": ("time", 3600.0),
    "번": ("count", 1.0),
    "회": ("count", 1.0),
    "개": ("count", 1.0),
    "명": ("count", 1.0),
    "점": ("score", 1.0),
    "글자": ("chars", 1.0),
    "자": ("chars", 1.0),
    "dB": ("db", 1.0),
    "db": ("db", 1.0),
    "데시벨": ("db", 1.0),
    "장": ("slide", 1.0),
    "년": ("date", 1.0),
    "월": ("date", 1.0),
    "일": ("date", 1.0),
}
_UNIT_RE = "|".join(sorted((re.escape(u) for u in _UNITS), key=len, reverse=True))
# 앞에 영숫자 · 점이 붙은 숫자(예 'sv1' · '0.28'의 '28')는 따로 뽑지 않는다. 한글 뒤에 붙은 숫자('약28%')는 뽑는다
_NUM_RE = re.compile(
    r"(?<![0-9A-Za-z_.])(?P<int>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<frac>\d+))?(?P<sp>\s?)(?P<unit>"
    + _UNIT_RE
    + r")?"
)
_MIN_SEC_RE = re.compile(r"(?<![0-9A-Za-z_.])(\d+)\s?분\s?(\d+(?:\.\d+)?)\s?초")
_JOIN = r"(?:,|·|와|과|및|~|-|→|이나|이랑|랑|하고|부터)"
#: 'Take 4와 5' · 'Take 3, 4, 5'처럼 Take 번호를 이어 쓴 경우 뒤 번호도 Take 번호다
_TAKE_BEFORE = re.compile(r"(?:Take|take|TAKE|테이크)\s*(?:\d+\s*" + _JOIN + r"\s*)*$")
#: '2, 3장' · '2~3장'처럼 장 번호를 이어 쓴 경우 앞 번호도 장 번호다
_SLIDE_AFTER = re.compile(r"\s*" + _JOIN + r"\s*\d+\s*장")
#: 숫자 정확도(14-1) 분모에서 빼는 종류: 대상 번호(Take · 장) · 순서(몇 번째) · 날짜. 대상은 target 채점기가 본다
TARGET_KINDS = frozenset({"take", "slide", "ordinal", "date"})


@dataclass(frozen=True)
class Num:
    """문장 속 숫자 하나. kind: ratio · pp · time · count · score · chars · rate · db · slide · take · ordinal ·
    date · plain(단위 없음)."""

    kind: str
    #: 기본 단위 값(비율은 0~1, 시간은 초)
    value: float
    #: 보여 준 자리: 그 단위로 쓴 소수 자리 수(분 · 초는 단위 크기)
    shown_decimals: int
    unit_size: float
    raw: str
    start: int

    def as_shown(self) -> float:
        return self.value / self.unit_size


def extract_numbers(text: str) -> list[Num]:
    """문장 속 숫자를 단위와 함께 뽑는다. 'N분 M초'는 한 값, 'Take N'은 take, 'N장'은 slide."""
    out: list[Num] = []
    taken: list[tuple[int, int]] = []
    for m in _MIN_SEC_RE.finditer(text):
        secs = int(m.group(1)) * 60 + float(m.group(2))
        dec = len(m.group(2).split(".")[1]) if "." in m.group(2) else 0
        out.append(Num("time", secs, dec, 1.0, m.group(0), m.start()))
        taken.append((m.start(), m.end()))
    for m in _NUM_RE.finditer(text):
        if any(s <= m.start() < e for s, e in taken):
            continue
        whole = m.group("int").replace(",", "")
        frac = m.group("frac") or ""
        raw_val = float(f"{whole}.{frac}") if frac else float(whole)
        unit = m.group("unit")
        nxt = text[m.end() : m.end() + 1]
        if unit == "분" and nxt in ("당", "의"):
            unit = None  # '분당'은 시간이 아니다
        if unit in ("년", "월", "일") and (
            m.group("sp") or (nxt and nxt in "차반부단정상괄치관간")
        ):
            unit = None  # 날짜는 숫자에 붙여 쓴 '3일' 꼴만('5 일반' · '1일차' 는 날짜가 아니다)
        raw = m.group(0).strip()
        if unit in ("번", "회") and nxt in ("째", "차"):
            out.append(Num("ordinal", raw_val, len(frac), 1.0, raw, m.start()))
            continue  # '3번째' · '2회차'는 순서다
        if unit is None:
            raw = m.group("int") + (f".{frac}" if frac else "")
            kind, mul = ("plain", 1.0)
            if _TAKE_BEFORE.search(text[: m.start()]):
                kind = "take"
            elif not frac and _SLIDE_AFTER.match(text, m.end("int")):
                kind = "slide"
        else:
            kind, mul = _UNITS[unit]
        if kind == "time":
            out.append(Num("time", raw_val * mul, len(frac), mul, raw, m.start()))
        elif kind in ("ratio", "pp"):
            out.append(Num(kind, raw_val * mul, len(frac), mul, raw, m.start()))
        else:
            out.append(Num(kind, raw_val, len(frac), 1.0, raw, m.start()))
    return sorted(out, key=lambda n: n.start)


def _round(x: float, places: int) -> Decimal:
    q = Decimal(1).scaleb(-places)
    return Decimal(repr(x)).quantize(q, rounding=ROUND_HALF_UP)


def value_matches(expected: Num, got: Num) -> bool:
    """기대 숫자(정확한 값)와 답의 숫자가 같은가: 답이 보여 준 자리까지 기대값을 반올림해 비교한다(0-2 ③)."""
    ek, gk = expected.kind, got.kind
    d = got.shown_decimals

    def same(e: float, g: float) -> bool:
        return _round(e, d) == _round(g, d)

    def exact(e: float, g: float) -> bool:
        return math.isclose(e, g, rel_tol=0, abs_tol=1e-9)

    if gk in TARGET_KINDS:
        return ek == gk and exact(expected.value, got.value)
    if gk in ("ratio", "pp"):
        # 기대값이 단위 없는 수(예 0.28)면 비율로 읽는다
        return ek in (gk, "plain") and same(expected.value * 100, got.value * 100)
    if gk == "time":
        size = got.unit_size
        return ek in ("time", "plain") and same(expected.value / size, got.value / size)
    if gk == "db":
        return ek in ("db", "plain") and same(abs(expected.value), abs(got.value))
    if gk in ("count", "score"):
        if ek == "rate":
            return same(expected.value, got.value)  # 분당 횟수처럼 소수인 값은 보여 준 자리까지
        # 정수 개수 · 점수는 그대로(0-2 ④): 반올림하지 않는다(4.6 은 '5번'이 아니다)
        return ek in (gk, "plain") and exact(expected.value, got.value)
    if gk == "chars":
        return ek in ("chars", "plain", "rate") and same(expected.value, got.value)
    # 답이 단위 없는 수
    if ek == "ratio":
        # 0.28 처럼 소수로 쓴 비율만 같다. 단위 없는 62 는 62% 가 아니다(0-2 ①)
        return d > 0 and got.value <= 1 and same(expected.value, got.value)
    if ek in ("count", "score", "plain"):
        return exact(expected.value, got.value)
    if ek == "db":
        return same(abs(expected.value), abs(got.value))
    return ek in ("chars", "rate") and same(expected.value, got.value)


def parse_expected_number(spec: Any) -> Num:
    """기대 숫자 표기('28%' · '4분' · '1분 28초' · '68점' · '310' · 0.28)를 한 값으로."""
    if isinstance(spec, (int, float)) and not isinstance(spec, bool):
        return Num("plain", float(spec), 9, 1.0, str(spec), 0)
    if not isinstance(spec, str):
        raise ValueError(f"숫자 기대값이 아니다: {spec!r}")
    nums = extract_numbers(spec)
    if len(nums) != 1:
        raise ValueError(f"숫자 기대값은 숫자 하나여야 한다: {spec!r}")
    return nums[0]


def number_check(text: str, expect: Any, op: str) -> tuple[bool, str]:
    want = [parse_expected_number(e) for e in (expect if isinstance(expect, list) else [expect])]
    got = extract_numbers(text)
    found = [w for w in want if any(value_matches(w, g) for g in got)]
    if op == "contains":
        missing = [w.raw for w in want if w not in found]
        return (
            not missing
        ), "" if not missing else f"숫자 없음: {missing} (답의 숫자: {[g.raw for g in got]})"
    present = [w.raw for w in found]
    return (not present), "" if not present else f"있으면 안 되는 숫자: {present}"


# ── 대상 (Take · 장 · 지표) ──────────────────────────────────────────────

#: 지표 → 답에서 그 지표를 가리키는 말(제품 config 와 따로 둔다)
METRIC_WORDS: dict[str, tuple[str, ...]] = {
    "audience_ratio": (
        "청중 시선",
        "청중을 본",
        "청중을 바라",
        "청중 응시",
        "시선 비율",
        "청중 보기",
        "청중을 보",
    ),
    "script_ratio": ("대본 응시", "대본을 본", "대본을 보", "대본 보기", "대본 의존", "대본 시선"),
    "cpm": ("말 속도", "말하기 속도", "분당 글자", "속도"),
    "filler_per_min": ("군더더기", "채움말"),
    "filler_count": ("군더더기", "채움말"),
    "duration_ms": ("발표 시간", "전체 시간", "길이", "시간"),
    "voice_diff_db": ("목소리", "소리 크기", "음량"),
    "score": ("점수",),
}
_SENT_RE = re.compile(r"(?<=[.?!。])\s+|\n")


def sentences(text: str) -> list[str]:
    return [s for s in (x.strip() for x in _SENT_RE.split(text or "")) if s]


def _mentions(sentence: str, want: Mapping[str, Any]) -> bool:
    take = want.get("take")
    if take is not None and not re.search(
        rf"(?:Take|테이크)\s*{int(take)}(?!\d)|(?<!\d){int(take)}번째 (?:Take|연습)", sentence
    ):
        return False
    slide = want.get("slide")
    if slide is not None and not re.search(
        rf"(?<![\d.]){int(slide)}\s*(?:장|번째 장|번 슬라이드)|슬라이드\s*{int(slide)}(?!\d)",
        sentence,
    ):
        return False
    metric = want.get("metric")
    if metric is not None:
        words = METRIC_WORDS.get(str(metric), (str(metric),))
        if not any(w in sentence for w in words):
            return False
    word = want.get("word")
    return not (word is not None and str(word) not in sentence)


def target_check(text: str, expect: Any, op: str) -> tuple[bool, str]:
    """한 문장 안에 기대한 Take · 장 · 지표(· 낱말)가 함께 있는가."""
    if not isinstance(expect, Mapping) or not expect:
        return False, "target 기대값은 {take, slide, metric, word} 중 하나 이상"
    hit = next((s for s in sentences(text) if _mentions(s, expect)), None)
    if op == "contains":
        return hit is not None, "" if hit else f"대상을 함께 말한 문장 없음: {_short(dict(expect))}"
    return hit is None, "" if hit is None else f"있으면 안 되는 대상 문장: {_short(hit)}"


# ── 고정 문구 (templates.py 상수 → 정규식) ───────────────────────────────

_FIELD = re.compile(r"\{(\w*)\}")
#: 채운 값의 받침에 따라 바뀌는 조사(templates.py 문구는 받침 있는 꼴로 적혀 있다). 그 밖의 조사는 문구 그대로 맞춘다
_VAR_PARTICLES = {"으로": r"(?:으로|로)", "예요": r"(?:이에요|예요)"}
#: 숫자를 읽을 때의 끝소리 받침(영 · 일 · 이 · 삼 · 사 · 오 · 육 · 칠 · 팔 · 구). 0 = 받침 없음, 8 = ㄹ
_DIGIT_FINAL = {"0": 21, "1": 8, "2": 0, "3": 16, "4": 0, "5": 0, "6": 1, "7": 8, "8": 8, "9": 0}


def final_sound(word: str) -> int:
    """마지막 글자의 받침 번호(0 없음 · 8 ㄹ). 숫자는 읽는 소리로, 한글 · 숫자가 아니면 받침 없음('%' → 퍼센트)."""
    w = word.rstrip(" )]}\"'.")
    if not w:
        return 0
    ch = w[-1]
    if "가" <= ch <= "힣":
        return (ord(ch) - 0xAC00) % 28
    return _DIGIT_FINAL.get(ch, 0)


def particle_for(value: str, particle: str) -> str:
    """'{x}으로' → 받침 없거나 ㄹ이면 '로', '{x}예요' → 받침 있으면 '이에요'(templates.fill 과 같은 규칙, 따로 구현)."""
    f = final_sound(value)
    if particle == "으로":
        return "로" if f in (0, 8) else "으로"
    if particle == "예요":
        return "이에요" if f else "예요"
    return particle


def resolve_template(spec: Any) -> tuple[str, dict[str, str]]:
    """`"SystemText.NO_GUIDE"` · `"ACTION_SUCCESS.SAVE_GOAL"` · `"CONTRADICTED"` 또는 `{"name": …, "fields": {…}}` → (문구, 채울 값)."""
    if isinstance(spec, Mapping):
        name, fields = spec.get("name"), dict(spec.get("fields") or {})
    else:
        name, fields = spec, {}
    if not isinstance(name, str) or not name:
        raise TemplateMissing(f"이름이 없다: {spec!r}")
    head, _, tail = name.partition(".")
    obj = getattr(templates, head, MISSING)
    if obj is MISSING:
        raise TemplateMissing(name)
    if tail:
        if isinstance(obj, Mapping):
            obj = obj.get(tail, MISSING)
        else:
            obj = getattr(obj, tail, MISSING)
            obj = getattr(obj, "value", obj)
    if not isinstance(obj, str):
        raise TemplateMissing(name)
    return obj, {k: str(v) for k, v in fields.items()}


def _template_pattern(
    text: str, fields: Mapping[str, str]
) -> tuple[re.Pattern[str], list[tuple[str, str, str]]]:
    """고정 문구를 정규식으로: 준 자리는 그 값(조사는 그 값의 받침에 맞춘 것 하나), 모르는 자리는 아무 글.
    모르는 자리 뒤의 '으로' · '예요'는 둘 다 받고, 맞은 뒤 (자리 그룹, 조사 그룹, 원래 조사)로 받침을 대조한다."""
    out: list[str] = []
    pairs: list[tuple[str, str, str]] = []
    pos = 0
    #: 바로 앞 자리: (준 값 또는 None, 모르는 자리의 그룹 이름)
    prev: tuple[str | None, str] | None = None
    for i, m in enumerate(_FIELD.finditer(text)):
        out.append(_after_field(text[pos : m.start()], prev, i, pairs))
        name = m.group(1)
        if name in fields:
            out.append(re.escape(fields[name]))
            prev = (fields[name], "")
        else:
            out.append(f"(?P<f{i}>.+?)")
            prev = (None, f"f{i}")
        pos = m.end()
    out.append(_after_field(text[pos:], prev, len(out), pairs))
    return re.compile("".join(out), re.S), pairs


def _after_field(
    lit: str, prev: tuple[str | None, str] | None, i: int, pairs: list[tuple[str, str, str]]
) -> str:
    """자리 바로 뒤의 글. 받침에 따라 바뀌는 조사로 시작하면 준 값이면 그 받침에 맞는 조사 하나만, 모르는 자리면 둘 다
    받고 맞은 뒤 대조한다."""
    if prev is not None:
        for word, alt in _VAR_PARTICLES.items():
            if lit.startswith(word):
                value, group = prev
                rest = re.escape(lit[len(word) :])
                if value is not None:
                    return re.escape(particle_for(value, word)) + rest
                pairs.append((group, f"p{i}", word))
                return f"(?P<p{i}>{alt[3:-1]})" + rest
    return re.escape(lit)


def template_regex(text: str, fields: Mapping[str, str]) -> re.Pattern[str]:
    """고정 문구의 정규식(모르는 자리 뒤 조사의 받침 대조는 `template_found`가 한다)."""
    return _template_pattern(text, fields)[0]


def template_found(text: str, spec: Any) -> bool:
    """글에 고정 문구가 있는가. 자리 뒤 조사는 채워진 값의 받침에 맞아야 한다(templates.fill 규칙)."""
    tpl, fields = resolve_template(spec)
    pat, pairs = _template_pattern(tpl, fields)
    for m in pat.finditer(text or ""):
        if all(particle_for(m.group(f), word) == m.group(p) for f, p, word in pairs):
            return True
    return False


def template_check(text: str, expect: Any, op: str) -> tuple[bool, str]:
    try:
        tpl, _ = resolve_template(expect)
        found = template_found(text, expect)
    except TemplateMissing as e:
        return False, f"템플릿 없음: {e}"
    if op == "contains":
        return found, "" if found else f"문구 없음: {_short(tpl)}"
    return not found, "" if not found else f"있으면 안 되는 문구: {_short(tpl)}"


def regex_check(text: str, expect: Any, op: str) -> tuple[bool, str]:
    if not isinstance(expect, str):
        return False, "regex 기대값은 문자열"
    found = re.search(expect, text or "") is not None
    if op == "contains":
        return found, "" if found else f"맞는 글 없음: /{expect}/"
    return not found, "" if not found else f"있으면 안 되는 글: /{expect}/"


# ── 인용 (자료 원문 대조) ────────────────────────────────────────────────

_QUOTED = re.compile(r"[\"'“‘「『]([^\"'”’」』]{8,})[\"'”’」』]")


def quote_problems(answer: Mapping[str, Any], corpus: Mapping[str, str]) -> list[str]:
    """자료 인용 검사: 자료 칩 인용 · GUIDE 문장의 따옴표 속 글 · GUIDE 문장 숫자가 인용한 자료 원문에 그대로 있는가(6-9 GUIDE)."""
    probs: list[str] = []
    for s in answer.get("sources") or []:
        sid, q = s.get("source_id"), (s.get("quote") or "").strip()
        body = corpus.get(sid)
        if body is None:
            probs.append(f"없는 자료 {sid}")
        elif not q:
            probs.append(f"{sid} 자료 칩 인용이 비었다")  # 필수 인용이 비면 실패
        elif q not in body:
            probs.append(f"{sid} 인용이 원문에 없음: {_short(q, 60)}")
    for c in answer.get("claims") or []:
        if c.get("kind") != "GUIDE":
            continue
        sids = list(c.get("source_ids") or [])
        if not sids:
            probs.append(f"GUIDE 문장에 자료 없음: {_short(c.get('text', ''), 60)}")
            continue
        bodies = [corpus.get(s, "") for s in sids]
        if any(s not in corpus for s in sids):
            probs.append(f"없는 자료를 인용: {sids}")
        text = c.get("text") or ""
        for q in _QUOTED.findall(text):
            if not any(q in b for b in bodies):
                probs.append(f"인용이 원문에 없음: {_short(q, 60)}")
        for n in extract_numbers(text):
            if n.kind not in TARGET_KINDS and not guide_number_ok(n, bodies):
                probs.append(f"자료에 없는 숫자: {n.raw}")
    return probs


def guide_number_ok(tok: Num, bodies: Iterable[str]) -> bool:
    """GUIDE 문장의 숫자가 인용한 자료 원문에 같은 숫자 · 같은 단위로 있는가(숫자 경계까지 토큰으로 대조: '10%'는 '110%'가 아니다)."""
    for body in bodies:
        for b in extract_numbers(body):
            if b.kind == tok.kind and math.isclose(b.value, tok.value, rel_tol=0, abs_tol=1e-9):
                return True
    return False


def quote_check(
    answer: Mapping[str, Any], expect: Any, corpus: Mapping[str, str]
) -> tuple[bool, str]:
    """expect true: 자료 인용이 하나 이상 있고 모두 원문 그대로. false: 자료 인용이 없다."""
    guide = [c for c in answer.get("claims") or [] if c.get("kind") == "GUIDE"]
    has = bool(guide) or bool(answer.get("sources"))
    if expect is False:
        return (not has), "" if not has else "자료 인용이 있다"
    if not has:
        return False, "자료 인용이 없다"
    probs = quote_problems(answer, corpus)
    return (not probs), "; ".join(probs[:3])


# ── 검사 하나 ─────────────────────────────────────────────────────────────

_TEXT_GRADERS = {"number", "target", "regex", "template"}


def run_check(
    check: Any, observation: Any, *, corpus: Mapping[str, str] | None = None
) -> CheckResult:
    """시나리오 검사 하나(`scenario.Check`)를 관찰값에 대 본다."""
    path = check.path
    if path is None and check.grader in _TEXT_GRADERS:
        path = "all_text"
    obs = resolve(observation, path) if observation is not MISSING else MISSING
    if obs is MISSING:
        return CheckResult(check.check_id, False, "관찰값 없음", check.metric, None)
    g = check.grader
    try:
        if g == "value":
            ok, why = compare(check.compare, obs, check.expect)
        elif g == "quote":
            ok, why = quote_check(obs, check.expect, corpus or {})
        elif not isinstance(obs, str):
            ok, why = False, f"{g} 채점기는 글에만 쓴다"
        elif g == "number":
            ok, why = number_check(obs, check.expect, check.compare)
        elif g == "target":
            ok, why = target_check(obs, check.expect, check.compare)
        elif g == "regex":
            ok, why = regex_check(obs, check.expect, check.compare)
        else:
            ok, why = template_check(obs, check.expect, check.compare)
    except (ValueError, TypeError, re.error) as e:
        ok, why = False, f"채점 오류: {e}"
    return CheckResult(check.check_id, ok, why, check.metric, _observed(obs))


def _observed(obs: Any) -> Any:
    s = obs if isinstance(obs, str) else _key(obs)
    return s if len(s) <= 400 else s[:399] + "…"


# ── 숫자 정확도 (14-1, 6-9 와 같은 규칙 · 따로 구현) ───────────────────────

#: 근거 단위 · 지표 → 숫자 종류(제품 config 와 따로 둔다). per_min(분당 횟수)은 소수라 rate(보여 준 자리까지 반올림)
_UNIT_KIND = {
    "ratio": "ratio",
    "ms": "time",
    "score": "score",
    "count": "count",
    "cpm": "chars",
    "per_min": "rate",
    "dB": "db",
}
_METRIC_KIND = {
    **dict.fromkeys(
        (
            "audience_ratio",
            "script_ratio",
            "reading_ratio",
            "screen_ratio",
            "away_ratio",
            "low_ratio",
            "content_coverage",
            "critical_fact_accuracy",
        ),
        "ratio",
    ),
    "duration_ms": "time",
    "slide_duration_ms": "time",
    "cpm": "chars",
    "filler_per_min": "rate",
    "filler_count": "count",
    "pause_count": "count",
    "long_silence_count": "count",
    "reading_runs": "count",
    "voice_diff_db": "db",
}
#: 판단 문장의 따옴표 속 사용자 원문('말씀하신 '…'는')은 에이전트의 숫자가 아니라 세지 않는다
_USER_QUOTE = re.compile(r"'[^'\n]*'|‘[^’\n]*’|“[^”\n]*”")


class NumberScore(NamedTuple):
    """숫자 정확도(14-1) 한 답의 셈. ok / total 이 지표다. unverified 는 total 에 들고 ok 에는 들지 않는다."""

    ok: int
    total: int
    bad: list[str]
    #: 원본 경로를 읽지 못한 RECORD 근거로만 맞은 숫자(대조 불가, 통과로 치지 않는다)
    unverified: int


def _evidence_num(ev: Mapping[str, Any], value: float) -> Num:
    kind = _UNIT_KIND.get(str(ev.get("unit"))) or _METRIC_KIND.get(str(ev.get("metric")), "plain")
    if kind == "time":
        return Num("time", value / 1000.0, 9, 1.0, str(value), 0)
    if "DIFF" in str(ev.get("calc") or "").upper() and kind == "ratio":
        return Num("pp", value, 9, 1.0, str(value), 0)
    return Num(kind, value, 9, 1.0, str(value), 0)


def _token_ok(tok: Num, e: Num) -> bool:
    """답의 숫자 하나가 근거 값 하나와 표기 규칙으로 같은가(%p 는 차이 근거와만, %는 차이 근거와는 아님)."""
    if tok.kind == "pp":
        d = tok.shown_decimals
        return e.kind == "pp" and _round(abs(e.value) * 100, d) == _round(tok.value * 100, d)
    if e.kind == "pp":
        return False
    return value_matches(e, tok)


def counted_numbers(text: str, *, assessment: bool = False) -> list[Num]:
    """숫자 정확도의 분모에 드는 숫자: 단위 없는 정수를 포함한 모든 숫자에서 대상 번호(Take · 장) · 순서('몇 번째') ·
    날짜를 뺀다. 판단 문장(ASSESSMENT)은 따옴표 속 사용자 원문을 뺀다."""
    if assessment:
        text = _USER_QUOTE.sub(lambda m: " " * len(m.group(0)), text)
    return [t for t in extract_numbers(text) if t.kind not in TARGET_KINDS]


def number_accuracy(
    answer: Mapping[str, Any],
    *,
    fixture_value: Any = None,
    corpus: Mapping[str, str] | None = None,
) -> NumberScore:
    """답 문장 속 숫자가 그 문장에 이은 근거 값과 같은가(14-1 숫자 정확도). `(ok, total, bad, unverified)`로 풀린다.

    규칙
    - 분모: SYSTEM(고정 문구)이 아닌 문장의 숫자 전부(단위 없는 정수 포함). Take · 장 번호('Take 4와 5' · '2, 3장'처럼
      이어 쓴 번호 포함) · 순서('3번째' · '2회차') · 날짜('11월 3일')와 판단 문장 따옴표 속 사용자 원문은 뺀다.
    - 근거 값: final.evidence 에서 claim.evidence_ids(계산 근거면 inputs 까지)로 찾는다. 단위 없는 정수는 개수 · 점수 ·
      글자 수 근거와만 같다(62 는 62% 가 아니다).
    - 원본 대조: `fixture_value(evidence) -> 값 | None`을 주면 RECORD 근거를 가상 기록에서 다시 읽는다. 원본과 다르면 그
      근거는 틀린 근거, 경로를 읽지 못하면(None) 대조 불가다. 계산 근거(COMPUTED)는 입력의 상태를 따르고, 차이(DIFF)는
      입력으로 다시 계산한다. 대조 불가 근거로만 맞은 숫자는 `unverified`로 따로 세고 맞음(ok)에 넣지 않는다.
    - GUIDE 문장의 숫자는 인용한 자료 원문에 같은 숫자 · 같은 단위로 있어야 한다(`guide_number_ok`).
    """
    evidence = {e.get("evidence_id"): e for e in answer.get("evidence") or []}
    status: dict[str, str] = {}

    def state(eid: str, depth: int = 0) -> str:
        """VERIFIED · TRUSTED(원본 대조 대상 아님) · UNVERIFIED(대조 불가) · WRONG."""
        if eid in status:
            return status[eid]
        ev = evidence.get(eid)
        st = "WRONG"
        if ev is not None and depth < 20:
            v = ev.get("value")
            origin = ev.get("origin")
            if origin == "RECORD" and fixture_value is not None and _is_num(v):
                truth = fixture_value(ev)
                if truth is None:
                    st = "UNVERIFIED"
                else:
                    st = (
                        "VERIFIED"
                        if math.isclose(float(truth), float(v), abs_tol=1e-9)
                        else "WRONG"
                    )
            elif origin == "COMPUTED":
                ins = [state(i, depth + 1) for i in ev.get("inputs") or []]
                st = (
                    "WRONG"
                    if "WRONG" in ins
                    else "UNVERIFIED"
                    if "UNVERIFIED" in ins
                    else "VERIFIED"
                )
                if st != "WRONG" and "DIFF" in str(ev.get("calc") or "").upper():
                    vals = [(evidence.get(i) or {}).get("value") for i in ev.get("inputs") or []]
                    nums_ok = len(vals) >= 2 and all(_is_num(x) for x in [*vals, v])
                    if not nums_ok or (
                        abs(abs(float(v)) - abs(float(vals[-1]) - float(vals[0]))) > 1e-6
                    ):
                        st = "WRONG"  # 차이 근거를 입력으로 다시 계산한 값과 다르다
            else:
                st = "TRUSTED"
        status[eid] = st
        return st

    ok = total = unverified = 0
    bad: list[str] = []
    for c in answer.get("claims") or []:
        kind = c.get("kind")
        if kind == "SYSTEM":
            continue
        toks = counted_numbers(c.get("text") or "", assessment=kind == "ASSESSMENT")
        if not toks:
            continue
        if kind == "GUIDE":
            bodies = [(corpus or {}).get(s, "") for s in c.get("source_ids") or []]
            for t in toks:
                total += 1
                if guide_number_ok(t, bodies):
                    ok += 1
                else:
                    bad.append(t.raw)
            continue
        ids = list(c.get("evidence_ids") or [])
        seen: list[str] = []
        while ids:
            i = ids.pop()
            if i in seen or i not in evidence:
                continue
            seen.append(i)
            ids.extend(evidence[i].get("inputs") or [])
        nums = [
            (_evidence_num(evidence[i], float(evidence[i]["value"])), state(i))
            for i in seen
            if _is_num(evidence[i].get("value"))
        ]
        for t in toks:
            total += 1
            hits = {st for n, st in nums if st != "WRONG" and _token_ok(t, n)}
            if hits & {"VERIFIED", "TRUSTED"}:
                ok += 1
            elif "UNVERIFIED" in hits:
                unverified += 1
            else:
                bad.append(t.raw)
    return NumberScore(ok, total, bad, unverified)


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def flatten_text(claims: Iterable[Mapping[str, Any]]) -> str:
    return "\n".join(str(c.get("text") or "") for c in claims)


# ── 일반 조언 금지 위반 (14-1 · 6-9 GENERAL 행, 따로 구현) ─────────────────

GENERAL_MAX_CLAIMS = 3
GENERAL_MAX_CHARS = 120
_GEN_DIGIT = re.compile(r"\d")
#: 한글 수 + 단위(횟수 · 개수 · 시간 · 비율). 단위 뒤에는 글이 끝나거나 조사 · 접미(씩 · 쯤 · 정도 · 은 · 을 …)만 와야 한다:
#: '한 분석' · '한 개념' · '한 장소'처럼 단위로 시작하는 낱말은 수가 아니다. '두세 번' · '한두 번'처럼 붙여 쓴 수도 본다.
#: 서수(번째)와 관용구 '한 번에'(한꺼번에), 개수 '가지'는 보지 않는다
_GEN_KO_NUM = re.compile(
    r"(?<![가-힣])(?:한두|두세|서너|네다섯|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|스무|서른|마흔|쉰)\s?"
    r"(?:번|개|장|분|초|퍼센트|배|시간|회|명|점)"
    r"(?:씩|쯤|정도|이상|이하|은|는|이|가|을|를|에|만|도|의|으로|로|까지|부터|마다)?(?![가-힣])"
)
_GEN_KO_NUM_IDIOMS = ("한 번에", "한번에")
#: 까닭 → 낱말(소문자로 맞춘다). 권하는 말끝 '~하면 좋아요'는 동의로 보지 않아 '좋아요'는 넣지 않는다
GENERAL_FORBIDDEN: dict[str, tuple[str, ...]] = {
    "기록 지시어": (
        "이번 발표",
        "이번 연습",
        "이번에는",
        "지난번",
        "지난 발표",
        "지난 연습",
        "내 발표",
        "제 발표",
        "님의 발표",
        "당신의 발표",
        "기록에서",
        "기록에는",
        "기록상",
        "기록을 보",
        "기록을 확인",
        "기록으로 보면",
        "기록이 있",
        "내 기록",
        "지난 기록",
        "이전 기록",
        "연습 기록",
        "발표 기록",
        "take",
        "테이크",
        "점수",
        "리뷰",
    ),
    "측정값 이름": ("비율", "횟수", "빈도", "분당", "cpm", "전달률", "정확도", "데시벨"),
    "동의 · 칭찬": (
        "맞아요",
        "맞습니다",
        "맞네요",
        "잘하셨",
        "잘했",
        "훌륭",
        "완벽",
        "정확합니다",
        "말씀대로",
    ),
    "판정": (
        "강점",
        "약점",
        "합격",
        "통과했",
        "통과해",
        "통과한",
        "통과예요",
        "통과입",
        "미달",
        "달성",
        "문제없",
        "문제 없",
    ),
    "완화": ("충분히", "사실상", "거의", "나쁘지 않"),
    "출처": (
        "연구",
        "논문",
        "학회",
        "연구 기관",
        "공공 기관",
        "정부 기관",
        "교육 기관",
        "통계",
        "따르면",
        "전문가",
        "교수",
        "책에서",
        "입증",
    ),
    "인과 단정": ("때문에", "때문이", "탓에", "탓이", "탓입", "원인은"),
    "행동 · 성공 주장": ("저장했", "바꿨어요", "다시 평가했", "시작했", "옮겼"),
    "범위 밖": ("예상 질문", "연습 계획", "연습 일정", "고쳐 써", "수정안", "추천 기준"),
}


def general_violations(claims: Iterable[Mapping[str, Any]], *, searched: bool) -> list[str]:
    """한 답의 `GENERAL` 문장 중 허용 조건(자료 찾기 뒤 · 3문장 · 120자 · 근거 · 자료 id 없음) 또는 금지 목록을 어긴 문장마다
    `"<문장 앞부분>: 까닭, …"` 하나(14-1 'GENERAL 금지 위반'은 이 수). `searched`: 그 차례에 자료 찾기 도구를 불렀는가."""
    out: list[str] = []
    gen = [c for c in claims if c.get("kind") == "GENERAL"]
    for i, c in enumerate(gen):
        text = str(c.get("text") or "")
        low = text.lower()
        why: list[str] = []
        if not searched:
            why.append("자료 찾기 전")
        if i >= GENERAL_MAX_CLAIMS:
            why.append(f"{GENERAL_MAX_CLAIMS}문장 넘음")
        if (
            c.get("evidence_ids")
            or c.get("source_ids")
            or c.get("assessment_id")
            or c.get("action_id")
        ):
            why.append("근거 · 자료 · 판단 · 행동에 이음")
        if len(text) > GENERAL_MAX_CHARS:
            why.append(f"{GENERAL_MAX_CHARS}자 넘음")
        plain = text
        for idiom in _GEN_KO_NUM_IDIOMS:
            plain = plain.replace(idiom, " ")
        if _GEN_DIGIT.search(text) or _GEN_KO_NUM.search(plain):
            why.append("숫자")
        why += [label for label, words in GENERAL_FORBIDDEN.items() if any(w in low for w in words)]
        if why:
            out.append(f"{text[:40]}: {', '.join(why)}")
    return out


__all__ = [
    "MISSING",
    "TARGET_KINDS",
    "CheckResult",
    "NumberScore",
    "TemplateMissing",
    "compare",
    "counted_numbers",
    "extract_numbers",
    "final_sound",
    "general_violations",
    "guide_number_ok",
    "number_accuracy",
    "number_check",
    "partial_match",
    "particle_for",
    "quote_check",
    "resolve",
    "resolve_template",
    "run_check",
    "target_check",
    "template_check",
    "template_found",
    "template_regex",
    "value_matches",
]
