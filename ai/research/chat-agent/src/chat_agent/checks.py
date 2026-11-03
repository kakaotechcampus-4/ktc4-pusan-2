"""답 검사 — 문장마다 근거 · 숫자 · 대상 · 인용 · 문구를 코드로 검사한다 (기능 문서 6-9 · 7-3 L1-35 ~ L1-38 ·
7-6 L4-24 ~ L4-27 · 7-8 L6-04 · L6-05, 숫자 규칙은 `architecture.md` 0-2).

- 누가 부르나: loop.py가 최종 답을 돌려주기 전에 항상 부른다(모델이 고르는 도구가 아니다).
  `check_claims`(문장) → 걸리면 `CheckFailure`의 `reason`을 모델에 알려 한 번 고치게 함 →
  `apply_fix_round`로 고친 결과를 다시 검사해 통과한 문장만 남기고 → `check_display_strings`(카드 · 계획 ·
  trace · 요약) → `assemble_message`로 `text`를 만든다.
- 입력 → 출력
  - `check_claims(...)` → `list[CheckFailure]` (비어 있으면 모두 통과). 문장 순번 `claim_index`는 0부터.
  - `check_display_strings(...)` → `list[CheckFailure]` (`claim_index=None`, `field`에 어느 필드인지).
  - `assemble_message(claims)` → `(text, claims)`; `text`는 `claims[].text`를 LF로 이은 값과 정확히 같다(5-5).
  - `check_numbers(sentence, allowed_numbers, ...)` → 0-2 '숫자 검사 예시 표'의 통과/걸림(리뷰와 같은 규칙).
- 이 모듈이 하지 않는 것: 문장의 뜻이 근거와 맞는지(근거성), 프롬프트 주입에 넘어갔는지는 평가셋이 잰다(13절).
  행동 검사 `AUTHORIZATION` · `ACTION_ARGS`는 actions.py가 같은 `CheckId`로 기록한다(여기서는 ID만 정의).
- 규칙 요약(문장 종류별)
  1. RECORD: 근거 ≥1, 모두 이번 턴 근거(`registry.is_current`). 숫자 · Take · 장 · 지표 이름이 이은 근거(와 계산
     근거의 입력)의 값과 같아야 한다. 차이(`%p`)는 코드가 입력으로 다시 계산한 `COMPUTED` 차이 근거와만 맞는다.
  2. GUIDE: `source_ids` ≥1, 따옴표 안 인용은 자료 원문에 글자 그대로, 문장 속 숫자는 자료 원문에 있어야 한다.
  3. ACTION_RESULT: 이번 실행에서 성공한 행동이어야 하고 문장은 loop가 검증된 결과로 그린 문장과 글자까지 같아야 한다.
     비교 · 변화 · 목표 결론 낱말이 든 문장은 `code_sentences`(코드가 그린 문장)에 있어야 한다(P3).
  4. SYSTEM: `SystemText` 목록 안의 문장만.
  5. ASSESSMENT: 판단이 있어야 하고, `CONTRADICTED` · `PARTLY` · `UNVERIFIABLE`이면 코드가 그린 첫 문장으로 시작해야 한다.
     첫 문장(코드 템플릿)은 낱말 검사에서 뺀다. 첫 문장 뒤 모델 설명만 낱말 · 숫자 검사를 받는다.
  6. 모든 종류(SYSTEM · ACTION_RESULT는 코드 문장이라 낱말 검사 제외): 금지 주장 · 한글 수 · 인과 단정 · 동의/칭찬 낱말 ·
     개인정보.
- 고칠 때
  - 낱말 목록은 `config.py`에만 둔다(바꾸면 `version.FEATURE_VERSION`을 올린다). 검사 ID를 더하면 `CheckId`와
    기능 문서 11-5 표를 같이 고친다.
  - `reason`에는 사용자 말 원문 · 기록 값을 더 넣지 않는다. 문장에서 뽑은 숫자 표기와 이은 근거 값만 쓴다(P8).
  - 근거의 강점 · 나아짐 표시는 `Evidence.value`가 문자열 `STRENGTH` · `IMPROVED`인 근거(RECORD의 차원 상태, COMPUTED의
    비교 결과)로 읽는다. 값 이름이 바뀌면 `PRAISE_VALUES`를 같이 고친다. 차이 계산 근거는 `calc`에 `DIFF`가 든다.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .claims import METRIC_LABEL
from .config import DEFAULT_CONFIG, ChatAgentConfig
from .evidence import EvidenceRegistry
from .schemas import Assessment, Claim, Evidence
from .templates import SystemText


class CheckId(StrEnum):
    """답 검사 ID (11-5 표). 진단 기록의 `code`로 그대로 나간다."""

    EVIDENCE_MISSING = "EVIDENCE_MISSING"
    NUMBER_MISMATCH = "NUMBER_MISMATCH"
    TARGET_MISMATCH = "TARGET_MISMATCH"
    QUOTE_MISMATCH = "QUOTE_MISMATCH"
    STALE_EVIDENCE = "STALE_EVIDENCE"
    FORBIDDEN_CLAIM = "FORBIDDEN_CLAIM"
    NUMBER_FORMAT = "NUMBER_FORMAT"
    CAUSAL_CLAIM = "CAUSAL_CLAIM"
    ACTION_RESULT_UNVERIFIED = "ACTION_RESULT_UNVERIFIED"
    SYSTEM_TEXT = "SYSTEM_TEXT"
    VERDICT_CONFLICT = "VERDICT_CONFLICT"
    HEDGE_WORD = "HEDGE_WORD"
    AGREEMENT_WORD = "AGREEMENT_WORD"
    DISPLAY_TEXT = "DISPLAY_TEXT"
    PII = "PII"
    AUTHORIZATION = "AUTHORIZATION"
    ACTION_ARGS = "ACTION_ARGS"
    FIX_NEW_CONTENT = "FIX_NEW_CONTENT"


DISPLAY_FIELDS = ("cards", "plan", "trace", "history_summary")


@dataclass(frozen=True)
class CheckFailure:
    """검사에 걸린 것 하나. `reason`은 모델에 고치라고 알리는 한국어 문장이다(사용자 말 원문을 담지 않는다)."""

    claim_index: int | None
    check_id: CheckId
    reason: str
    field: str | None = None
    claim_kind: str | None = None


# ══ 숫자 추출 · 대조 (architecture 0-2) ═══════════════════════════════════════

#: 문장 속 단위 → 근거 값을 그 단위로 바꾸는 배율. 시간 근거는 ms 다(architecture 6-1).
_UNIT_SCALE: dict[str, float] = {
    "%": 100,
    "퍼센트": 100,
    "%p": 100,
    "ms": 1,
    "초": 1 / 1000,
    "분": 1 / 60000,
    "번": 1,
    "개": 1,
    "점": 1,
    "자": 1,
    "db": 1,
    "": 1,
}
#: 정수로 쓰면 반올림하지 않고 근거 값과 그대로 대조하는 단위(개수 · 점수 · 단위 없음, 0-2 ④).
#: 반올림하면 비율 0.62 가 1 이 되어 근거에 없는 "1번"이 통과한다
_EXACT_UNITS = {"번", "개", "점", ""}
_PERCENT_UNITS = {"%", "퍼센트"}
_TIME_UNITS = {"ms", "초", "분"}
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
#: 이어 쓴 "N분 M초" 는 한 값(초로 센다)
_MIN_SEC = re.compile(rf"({_NUM})분\s?({_NUM})초(?!과)")
#: Take 번호: "Take 5" · "Take5" · "5번째 Take" · "5번 Take". 값 추출보다 먼저 뽑아 "5번"으로 읽히지 않게 한다
_TAKE_AFTER = re.compile(r"(?:Take|테이크)\s*(\d+)", re.IGNORECASE)
_TAKE_BEFORE = re.compile(r"(\d+)\s*번째?\s*(?:Take|테이크)", re.IGNORECASE)
#: 장 번호: "3장", "슬라이드 3"
_SLIDE = re.compile(r"(\d+)\s?장|슬라이드\s*(\d+)")
#: 순서를 가리키는 "N번째"는 값이 아니다
_ORDINAL = re.compile(r"\d+\s?번째")
_NUMBER = re.compile(
    rf"({_NUM})(퍼센트\s?포인트|퍼센트p|%p|퍼센트|%|ms|dB|초(?!과)|분(?!당)|번|개|점|자)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NumberToken:
    """문장 속 값 하나. `unit`은 소문자 단위(`%` · `%p` · `퍼센트` · `ms` · `초` · `분` · `번` · `개` · `점` · `자` · `db` · 빈 문자열).

    `pos`는 문장 안 글자 위치다(값을 가까운 Take · 장 · 지표 언급에 묶을 때 쓴다, 6-9 대상 일치).
    """

    text: str
    unit: str
    value: float
    places: int
    pos: int = 0


@dataclass(frozen=True)
class Extracted:
    values: tuple[NumberToken, ...]
    takes: tuple[int, ...]
    slides: tuple[int, ...]
    take_pos: tuple[int, ...] = ()
    slide_pos: tuple[int, ...] = ()


def _parse(num: str) -> tuple[float, int]:
    num = num.replace(",", "")
    return float(num), len(num.partition(".")[2])


def extract_numbers(sentence: str) -> Extracted:
    """문장에서 값(정수 · 소수 + 단위) · Take 번호 · 장 번호를 뽑는다. 한글 수("세 번")는 뽑지 않는다(0-2).

    뽑은 자리는 같은 길이의 공백으로 덮어 다시 뽑지 않고, 글자 위치(`pos`)가 원문과 같게 유지된다.
    """
    takes: list[tuple[int, int]] = []
    slides: list[tuple[int, int]] = []

    def blank(m: re.Match[str]) -> str:
        return " " * len(m.group(0))

    def take(m: re.Match[str]) -> str:
        takes.append((m.start(), int(m.group(1))))
        return blank(m)

    def slide(m: re.Match[str]) -> str:
        slides.append((m.start(), int(m.group(1) or m.group(2))))
        return blank(m)

    rest = _TAKE_BEFORE.sub(take, sentence)
    rest = _TAKE_AFTER.sub(take, rest)
    rest = _SLIDE.sub(slide, rest)
    rest = _ORDINAL.sub(blank, rest)

    found: list[NumberToken] = []

    def min_sec(m: re.Match[str]) -> str:
        minutes, _ = _parse(m.group(1))
        seconds, places = _parse(m.group(2))
        found.append(NumberToken(m.group(0), "초", minutes * 60 + seconds, places, m.start()))
        return blank(m)

    rest = _MIN_SEC.sub(min_sec, rest)
    for m in _NUMBER.finditer(rest):
        value, places = _parse(m.group(1))
        raw_unit = (m.group(2) or "").replace(" ", "")
        unit = raw_unit.lower()
        if unit in ("퍼센트포인트", "퍼센트p"):
            unit = "%p"
        found.append(NumberToken(m.group(0).strip(), unit, value, places, m.start()))
    found.sort(key=lambda t: t.pos)
    takes.sort()
    slides.sort()
    return Extracted(
        tuple(found),
        tuple(n for _, n in takes),
        tuple(n for _, n in slides),
        tuple(p for p, _ in takes),
        tuple(p for p, _ in slides),
    )


def _round_half_up(value: float, places: int) -> float:
    """보여 준 자리까지 반올림(0.625 → 0.63). 부동소수 오차는 1e-9로 덮는다"""
    scale = 10**places
    return math.floor(value * scale + 0.5 + 1e-9) / scale


def _value_matches(token: NumberToken, allowed: float) -> bool:
    """근거 값 하나(`allowed`, 근거의 기본 단위)와 문장 속 값이 같은가. 음수는 크기로 본다(0-2 ⑤)."""
    scaled = abs(allowed) * _UNIT_SCALE[token.unit]
    if token.places == 0 and token.unit in _EXACT_UNITS:
        return abs(scaled - token.value) < 1e-9
    return abs(_round_half_up(scaled, token.places) - token.value) < 1e-9


def check_numbers(
    sentence: str,
    allowed_numbers: Iterable[float | int | None],
    *,
    take_numbers: Iterable[int] = (),
    slide_numbers: Iterable[int] = (),
) -> bool:
    """문장 속 숫자가 모두 입력에 있는 숫자면 True (0-2 '숫자 검사 예시 표').

    단위 · 증거 종류를 가리지 않는 가장 단순한 규칙이다: 비율 ↔ 퍼센트, ms ↔ 초 ↔ 분, 보여 준 자리까지 반올림,
    개수 · 점수는 그대로, 음수는 크기, 단위 없는 정수는 비율과 같지 않다. Take · 장 번호는 각각 목록에 있어야 한다.
    숫자가 없는 문장은 True, 한글 수는 검사하지 않는다. `None` · bool은 값으로 보지 않는다.
    """
    allowed = [abs(float(a)) for a in allowed_numbers if a is not None and not isinstance(a, bool)]
    ex = extract_numbers(sentence)
    if not set(ex.takes) <= set(take_numbers) or not set(ex.slides) <= set(slide_numbers):
        return False
    return all(any(_value_matches(t, a) for a in allowed) for t in ex.values)


def _fmt(value: float | int | str) -> str:
    if isinstance(value, str):
        return value
    return f"{value:g}"


# ══ 근거 모으기 ═══════════════════════════════════════════════════════════════


def _is_diff(ev: Evidence) -> bool:
    return ev.origin == "COMPUTED" and ev.calc is not None and "DIFF" in ev.calc.upper()


def _num(ev: Evidence) -> float | None:
    if isinstance(ev.value, bool) or not isinstance(ev.value, int | float):
        return None
    return float(ev.value)


def _expand(linked: list[Evidence], registry: EvidenceRegistry) -> list[Evidence]:
    """이은 근거 + 계산 근거의 입력(재귀). 같은 근거는 한 번만."""
    seen: dict[str, Evidence] = {}
    stack = list(linked)
    while stack:
        ev = stack.pop()
        if ev.evidence_id in seen:
            continue
        seen[ev.evidence_id] = ev
        for i in ev.inputs or ():
            sub = registry.get(i)
            if sub is not None:
                stack.append(sub)
    return list(seen.values())


def _bad_diff(ev: Evidence, registry: EvidenceRegistry) -> str | None:
    """차이 근거를 입력으로 다시 계산해 보고, 어긋나면 이유를 돌려준다(L4-25). 차이가 아니면 None.

    차이 = (마지막 입력 − 첫 입력)의 크기. 입력은 같은 지표 · 같은 장의 값이어야 하고(다른 지표 · 장을 섞은 계산 거절)
    숫자여야 한다.
    """
    if not _is_diff(ev):
        return None
    inputs = [registry.get(i) for i in ev.inputs or ()]
    if len(inputs) < 2 or any(i is None for i in inputs):
        return "차이 계산 근거의 입력이 모자라 다시 계산할 수 없음"
    ins: list[Evidence] = [i for i in inputs if i is not None]
    vals = [_num(i) for i in ins]
    if any(v is None for v in vals):
        return "차이 계산 근거의 입력이 숫자가 아님"
    if len({(i.metric, i.slide_number) for i in ins}) != 1:
        return "차이 계산 근거가 다른 지표 · 장의 값을 섞음"
    want = abs(vals[-1] - vals[0])  # type: ignore[operator]
    got = _num(ev)
    if got is None or abs(abs(got) - want) > 1e-6 * max(1.0, want):
        return f"차이 계산 근거 값 {_fmt(ev.value)}이 입력으로 다시 계산한 값 {_fmt(want)}과 다름"
    return None


def _kind_ok(token: NumberToken, ev: Evidence, cfg: ChatAgentConfig) -> bool:
    """단위가 근거 지표의 종류와 어울리는가(비율만 %, ms만 초 · 분, dB만 dB). 종류를 모르면 통과."""
    kind = cfg.metric_units.get(ev.metric) if ev.metric else None
    if kind is None:
        return True
    if token.unit in _PERCENT_UNITS or token.unit == "%p":
        return kind == "ratio"
    if token.unit in _TIME_UNITS:
        return kind == "ms"
    if token.unit == "db":
        return kind == "dB"
    return True


def _token_matches_any(token: NumberToken, evidences: list[Evidence], cfg: ChatAgentConfig) -> bool:
    for ev in evidences:
        v = _num(ev)
        if v is None or not _kind_ok(token, ev, cfg):
            continue
        if ev.unit == "s":
            # 화면의 초 단위 값(제한 시간)은 ms 로 바꿔 대조한다(시간 근거는 ms, architecture 6-1)
            v = v * 1000
        if token.unit == "%p":
            if not _is_diff(ev):
                continue  # %p 는 차이 계산 근거와만 맞는다(%/%p 혼동 거절, L4-25)
        elif token.unit in _PERCENT_UNITS and _is_diff(ev):
            continue  # 차이 값을 %로 쓰면 틀린 단위다
        if _value_matches(token, v):
            return True
    return False


def _name_map(cfg: ChatAgentConfig) -> dict[str, str]:
    """지표 이름 → 지표. 판단 근거 문구의 표시 이름(`claims.METRIC_LABEL`, 예 '청중 시선')에 설정의 문장용 이름을
    더한다. 판단 설명이 표시 이름만 써도 지표 언급으로 잡혀 근거 요구를 건너뛰지 못한다(같은 이름은 설정이 우선)."""
    names = {label: metric for metric, label in METRIC_LABEL.items()}
    names.update(cfg.metric_names)
    return names


def _metric_positions(text: str, cfg: ChatAgentConfig) -> list[tuple[int, str]]:
    """문장 속 지표 이름 → (글자 위치, 지표). 긴 이름부터 맞추고 맞춘 자리는 다시 맞추지 않는다."""
    masked = text
    out: list[tuple[int, str]] = []
    names = _name_map(cfg)
    for name in sorted(names, key=len, reverse=True):
        start = masked.find(name)
        while start != -1:
            out.append((start, names[name]))
            masked = masked[:start] + "\0" * len(name) + masked[start + len(name) :]
            start = masked.find(name)
    out.sort()
    return out


def _metric_mentions(text: str, cfg: ChatAgentConfig) -> list[str]:
    return [m for _, m in _metric_positions(text, cfg)]


def _targets_of(ev: Evidence, cfg: ChatAgentConfig) -> set[str]:
    """동의 · 완화 낱말의 대상 비교용: 지표와 그 차원(강점 근거는 차원 단위로 나온다)."""
    out: set[str] = set()
    if ev.metric:
        out.add(ev.metric)
        dim = cfg.dimension_of_metric(ev.metric)
        if dim:
            out.add(dim)
    return out


def _mention_in(metric: str, targets: set[str], cfg: ChatAgentConfig) -> bool:
    return metric in targets or (cfg.dimension_of_metric(metric) or "\0") in targets


def _metric_is(mention: str, ev: Evidence, cfg: ChatAgentConfig) -> bool:
    """문장 속 지표가 근거의 지표와 **같은 지표**인가. 근거가 차원 이름이면 그 차원 안의 지표만 인정한다.
    같은 차원의 다른 지표(대본 응시 비율 ↔ 대본 읽기 비율)는 다른 대상이다(6-9 대상 일치)."""
    if ev.metric is None:
        return False
    if ev.metric == mention:
        return True
    spec = cfg.dimensions.get(ev.metric)
    return spec is not None and mention in spec.metrics


def _takes_of(ev: Evidence, registry: EvidenceRegistry) -> set[int]:
    """근거의 Take 번호. 계산 근거는 입력의 Take 번호를 모두 가진다."""
    return {e.take_number for e in _expand([ev], registry) if e.take_number is not None}


def _slides_of(ev: Evidence, registry: EvidenceRegistry) -> set[int]:
    return {e.slide_number for e in _expand([ev], registry) if e.slide_number is not None}


def _stale_chain(ev: Evidence, registry: EvidenceRegistry) -> bool:
    """계산 근거의 입력을 재귀로 따라가 하나라도 이번 턴의 근거가 아니면 True (옛 값이 섞이는 것을 막는다)."""
    stack = list(ev.inputs or ())
    seen: set[str] = set()
    while stack:
        i = stack.pop()
        if i in seen:
            continue
        seen.add(i)
        sub = registry.get(i)
        if sub is None or not registry.is_current(i):
            return True
        stack.extend(sub.inputs or ())
    return False


#: 문장 안에서 값이 묶이는 범위를 끊는 자리(쉼표 · 화살표 · 문장 끝 · 접속 어미). Take · 장 · 지표 언급은 같은 절 안의
#: 앞쪽 가장 가까운 것이 뒤따르는 값의 대상이다("Take 4는 33%, Take 5는 28%"처럼 값을 바꿔 붙이는 것을 막는다)
_CLAUSE_SPLIT = re.compile(
    r"[,，;·→\n]|[.?!](?=\s)|(?:이며|으며|이고|면서|지만|는데|과|와|고|며)(?=\s)"
)


def _clause_start(text: str, pos: int) -> int:
    start = 0
    for m in _CLAUSE_SPLIT.finditer(text):
        if m.end() <= pos:
            start = m.end()
        else:
            break
    return start


def _clause_end(text: str, pos: int) -> int:
    for m in _CLAUSE_SPLIT.finditer(text):
        if m.start() >= pos:
            return m.start()
    return len(text)


def _nearest(
    positions: list[tuple[int, Any]], start: int, pos: int, end: int | None = None
) -> Any | None:
    """같은 절 안에서 `pos` 앞쪽 가장 가까운 언급. 없고 `end`가 주어지면 `pos` 뒤 `end` 앞의 가장 가까운 언급
    ("28%는 Take 5"처럼 값 뒤에 대상을 쓰는 어순). 앞 언급이 있으면 앞 언급이 우선이다."""
    best = None
    for p, v in positions:
        if start <= p < pos:
            best = v
    if best is None and end is not None:
        for p, v in positions:
            if pos < p < end:
                return v
    return best


def _check_record_text(
    index: int | None,
    kind: str | None,
    text: str,
    evidence_ids: list[str],
    registry: EvidenceRegistry,
    cfg: ChatAgentConfig,
    *,
    field: str | None = None,
    allow_no_evidence: bool = False,
) -> list[CheckFailure]:
    """RECORD 문장(또는 표시 문자열) 검사: 근거 존재 · 최신 · 숫자 · Take · 장 · 지표 이름 (6-9 표 1~5행).

    - 숫자는 같은 절에서 앞쪽 가장 가까운 Take · 장 · 지표 언급에 묶여, 그 대상의 근거 값과 같아야 한다.
    - `allow_no_evidence=True`(표시 문자열)이면 근거가 없어도 `EVIDENCE_MISSING`을 내지 않고, 숫자 · Take · 장이 있으면
      근거가 없는 것으로 걸린다(빈 목록 = 기록 숫자를 담을 수 없는 문자열).
    """
    f: list[CheckFailure] = []

    def fail(cid: CheckId, reason: str) -> None:
        f.append(CheckFailure(index, cid, reason, field=field, claim_kind=kind))

    if not evidence_ids and not allow_no_evidence:
        fail(
            CheckId.EVIDENCE_MISSING,
            "이 문장에 이은 근거가 없음. 기록 이야기는 근거 id를 이어야 함",
        )
        return f
    linked: list[Evidence] = []
    for n, eid in enumerate(dict.fromkeys(evidence_ids), 1):
        ev = registry.get(eid)
        if ev is None or not registry.is_current(eid):
            fail(
                CheckId.STALE_EVIDENCE,
                f"이은 근거 {n}번째 id는 이번 턴에 읽은 근거가 아니거나 없는 id임",
            )
        elif _stale_chain(ev, registry):
            fail(
                CheckId.STALE_EVIDENCE,
                f"이은 근거 {n}번째의 계산 입력에 이번 턴에 읽은 근거가 아닌 것이 있음",
            )
        else:
            linked.append(ev)
    if not linked and evidence_ids:
        return f
    effective = _expand(linked, registry)
    usable = []
    for ev in effective:
        bad = _bad_diff(ev, registry)
        if bad:
            fail(CheckId.NUMBER_MISMATCH, bad)
        else:
            usable.append(ev)

    ex = extract_numbers(text)
    # 1) 대상 집합 검사: Take · 장 · 지표 이름이 이은 근거의 것인가
    target_failed = False
    take_set = {e.take_number for e in effective if e.take_number is not None}
    for t in dict.fromkeys(ex.takes):
        if t not in take_set:
            target_failed = True
            fail(
                CheckId.TARGET_MISMATCH,
                f"'Take {t}'는 이은 근거의 Take 번호 {sorted(take_set) or '없음'}와 다름",
            )
    slide_set = {e.slide_number for e in effective if e.slide_number is not None}
    for s in dict.fromkeys(ex.slides):
        if s not in slide_set:
            target_failed = True
            fail(
                CheckId.TARGET_MISMATCH,
                f"'{s}장'은 이은 근거의 장 번호 {sorted(slide_set) or '없음'}와 다름",
            )
    mentions = _metric_positions(text, cfg)
    if (
        linked
    ):  # 근거가 없는 표시 문자열의 지표 이름("청중 시선 읽기")은 계획 문구일 수 있어 보지 않는다
        for metric in dict.fromkeys(m for _, m in mentions):
            if not any(_metric_is(metric, e, cfg) for e in effective):
                target_failed = True
                names = sorted({e.metric for e in effective if e.metric})
                fail(
                    CheckId.TARGET_MISMATCH,
                    f"지표 '{metric}'는 이은 근거의 지표 {names or '없음'}와 다름",
                )
    # 2) 값 검사 + 값을 가까운 Take · 장 · 지표 언급에 묶어 그 대상의 근거 값과 같은지 본다
    take_at = list(zip(ex.take_pos, ex.takes, strict=True))
    slide_at = list(zip(ex.slide_pos, ex.slides, strict=True))
    shown: set[tuple[str, int]] = set()
    for tok in ex.values:
        if (tok.text, tok.pos) in shown:
            continue
        shown.add((tok.text, tok.pos))
        if not _token_matches_any(tok, usable, cfg):
            vals = [_fmt(v) for e in usable if (v := _num(e)) is not None][:4]
            fail(
                CheckId.NUMBER_MISMATCH,
                f"'{tok.text}'는 근거 값 {', '.join(vals) if vals else '(숫자 근거 없음)'}과 다름",
            )
            continue
        if target_failed:
            continue  # 대상이 이미 틀렸다고 알렸으면 같은 원인을 되풀이하지 않는다
        start = _clause_start(text, tok.pos)
        # 뒤쪽 대상은 같은 절 안에서 다음 값 앞까지만 인정한다(다음 값의 대상을 가져오지 않는다)
        later = [v.pos for v in ex.values if v.pos > tok.pos]
        end = min([_clause_end(text, tok.pos), *later])
        take = _nearest(take_at, start, tok.pos, end)
        slide = _nearest(slide_at, start, tok.pos, end)
        metric = _nearest(mentions, start, tok.pos, end)
        if take is None and slide is None and metric is None:
            continue
        ok = False
        for e in usable:
            if not _token_matches_any(tok, [e], cfg):
                continue
            if take is not None and take not in _takes_of(e, registry):
                continue
            if slide is not None and slide not in _slides_of(e, registry):
                continue
            if metric is not None and e.metric is not None and not _metric_is(metric, e, cfg):
                continue
            ok = True
            break
        if not ok:
            where = " ".join(
                x
                for x in (
                    f"Take {take}" if take is not None else "",
                    f"{slide}장" if slide is not None else "",
                    f"지표 {metric}" if metric is not None else "",
                )
                if x
            )
            fail(
                CheckId.TARGET_MISMATCH,
                f"'{tok.text}'는 {where}의 근거 값이 아님(값과 대상이 서로 바뀜)",
            )
    return f


# ══ 낱말 · 형식 검사 ══════════════════════════════════════════════════════════

_QUOTES = (
    re.compile(r'"([^"]+)"'),
    re.compile(r"“([^”]+)”"),
    re.compile(r"‘([^’]+)’"),
    re.compile(r"'([^']+)'"),
    re.compile(r"「([^」]+)」"),
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
#: 전화번호(010-1234-5678 · 02-123-4567 · 01012345678)
_PHONE = re.compile(r"(?<!\d)0\d{1,2}[-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)")
#: 긴 숫자열(주민등록번호 · 카드 번호 · 계좌 등). 쉼표가 낀 값 · 소수는 제외되도록 9자리 이상 연속만 본다
_LONG_DIGITS = re.compile(r"(?<![\d.,])\d{9,}(?![\d])")
_SECRET = re.compile(r"sk-[A-Za-z0-9_-]{16,}|Bearer\s+[A-Za-z0-9._-]{16,}|AKIA[0-9A-Z]{16}")
_URL = re.compile(r"https?://|www\.", re.IGNORECASE)
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
#: 표시 문자열에 남은 주입 문장 표지(L4-26). 지시문은 실행 권한으로 인정하지 않는다
_INJECTION = (
    "이전 지시",
    "지시를 무시",
    "시스템 프롬프트",
    "ignore previous",
    "ignore all previous",
    "system prompt",
)

#: 강점 · 나아짐을 나타내는 근거 값(차원 상태 · 비교 결과)
PRAISE_VALUES = frozenset({"STRENGTH", "IMPROVED"})
#: 최상급 칭찬 낱말(`agreement_words`의 부분 문자열). 나아짐(IMPROVED)만으로는 부족하고 강점(STRENGTH) 근거가 있어야 한다
SUPERLATIVE_STEMS = ("완벽", "훌륭")
#: 판정과 반대되는 동의 표현. config.py의 동의 낱말에 더해 쓴다(config로 옮기기 전까지 여기에 둔다, 7-8 L6-04)
#: "맞는 말"은 동의 표현일 때만(“맞는 말 속도”처럼 수식어로 쓴 것은 빼려고 어미까지 적는다)
EXTRA_AGREEMENT_WORDS = (
    "정확합니다",
    "맞는 말이",
    "맞는 말입",
    "맞는 말.",
    "맞는 말,",
    "맞는 말씀",
    "말씀대로",
    "옳아요",
)
#: 비교 · 차이 · 목표 결론 낱말. 결론 문장은 모델이 쓰지 않고 코드가 그린다(P3, 6-9). 이 낱말이 든 문장은
#: `code_sentences`(코드가 그린 문장)에 글자까지 같게 들어 있지 않으면 걸린다
CONCLUSION_WORDS = (
    "좋아졌",
    "나빠졌",
    "늘었",
    "줄었",
    "증가",
    "감소",
    "향상",
    "개선됐",
    "개선되었",
    "악화",
    "이뤘",
    "달성",
    "높아졌",
    "낮아졌",
    "올랐",
    "내려갔",
    "빨라졌",
    "느려졌",
    "커졌",
    "작아졌",
    # 같음 · 변화 없음. 관형 "같은 장" · 부사 "그대로 따라 해"는 일반 문장이라 넣지 않는다
    "같아요",
    "같습니다",
    "같네요",
    "같았",
    "동일해",
    "동일합",
    "동일했",
    "차이 없",
    "차이가 없",
    "변화 없",
    "변화가 없",
    "그대로예요",
    "그대로입",
    "그대로였",
    # 목표 못 이룸
    "못 미",
    "미치지 못",
    "미달",
    # 변화 활용형. 명령형 "개선해 보세요"는 조언이라 넣지 않는다
    "떨어졌",
    "떨어진",
    "내려갔",
    "개선되",
    "개선했",
    "개선된",
    "악화되",
)
#: 배수 표현("2배" · "두 배")도 결론이다(숫자 비교의 방향 · 정도를 말한다)
_MULTIPLIER = re.compile(r"(?:\d+(?:\.\d+)?|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s?배")
#: "~해서/~라서/~때문에/~탓에/~덕분에 + 결과" 꼴의 원인 단정(예 "긴장해서 빨라졌어요"). 과거형 결과만 잡아서
#: "연습해서 좋아질 수 있어요" 같은 가능성 조언은 걸리지 않는다
_CAUSE_RESULT = re.compile(
    r"(?:해서|라서|때문에|탓에|덕분에)[^.,!?\n]{0,15}?"
    r"(?:빨라졌|느려졌|떨어졌|떨어진|늘었|줄었|많아졌|적어졌|높아졌|낮아졌|올랐|커졌|작아졌|흔들렸|못했)"
)


def _found(words: Iterable[str], text: str) -> list[str]:
    return [w for w in words if w in text]


def agreement_words(cfg: ChatAgentConfig) -> tuple[str, ...]:
    """동의 · 칭찬 낱말 전체(설정 목록 + 판정과 반대되는 동의 표현)."""
    return tuple(dict.fromkeys((*cfg.agreement_words, *EXTRA_AGREEMENT_WORDS)))


_QUOTED = re.compile(r'"[^"]*"|“[^”]*”|‘[^’]*’|「[^」]*」')


def _unquoted(text: str) -> str:
    """GUIDE 문장의 따옴표 안(자료 원문 인용, 글자 그대로 대조함)을 뺀 글. 동의 · 결론 · 완화 낱말은 모델이 쓴 부분에서만 본다."""
    return _QUOTED.sub(" ", text)


def _conclusion_word(text: str) -> str | None:
    for w in CONCLUSION_WORDS:
        if w in text:
            return w
    m = _MULTIPLIER.search(text)
    return m.group(0) if m else None


def _korean_numeral_re(cfg: ChatAgentConfig) -> re.Pattern[str]:
    nums = "|".join(sorted(map(re.escape, cfg.korean_numerals), key=len, reverse=True))
    # "두 배"처럼 배수도 한글 수로 쓰지 못한다(숫자 비교는 아라비아 숫자로, L4-24)
    units = "|".join(
        sorted(map(re.escape, (*cfg.korean_numeral_units, "배")), key=len, reverse=True)
    )
    return re.compile(rf"(?<![가-힣])(?:{nums})\s?(?:{units})")


def _pii_kinds(text: str) -> list[str]:
    kinds = []
    if _EMAIL.search(text):
        kinds.append("이메일")
    if _PHONE.search(text):
        kinds.append("전화번호")
    if _LONG_DIGITS.search(text):
        kinds.append("긴 숫자열")
    if _SECRET.search(text):
        kinds.append("비밀값")
    return kinds


def _word_checks(
    index: int | None,
    kind: str | None,
    text: str,
    cfg: ChatAgentConfig,
    *,
    field: str | None = None,
    approved_cause_sources: set[str] | None = None,
    source_ids: list[str] | None = None,
    causal: bool = True,
) -> list[CheckFailure]:
    """금지 주장 · 한글 수 · 인과 단정 · 개인정보 (모든 사용자 보이는 문장 공통)."""
    f: list[CheckFailure] = []

    def fail(cid: CheckId, reason: str) -> None:
        f.append(CheckFailure(index, cid, reason, field=field, claim_kind=kind))

    for w in _found(cfg.forbidden_claims, text):
        fail(
            CheckId.FORBIDDEN_CLAIM,
            f"'{w}'처럼 점수 · 미션을 바꿨다거나 발표를 시작했다고 말할 수 없음",
        )
    m = _korean_numeral_re(cfg).search(text)
    if m:
        fail(
            CheckId.NUMBER_FORMAT,
            f"측정 수치는 '{m.group(0)}'처럼 한글로 쓰지 말고 아라비아 숫자로 써야 함",
        )
    if causal:
        words = _found(cfg.causal_words, text)
        if not words and (cm := _CAUSE_RESULT.search(text)):
            words = [cm.group(0)[:12]]
        if words and not (set(source_ids or ()) & (approved_cause_sources or set())):
            fail(
                CheckId.CAUSAL_CLAIM,
                f"'{words[0]}'처럼 승인된 자료 근거 없이 원인을 단정할 수 없음",
            )
    for k in _pii_kinds(text):
        fail(CheckId.PII, f"{k} 같은 개인정보 · 비밀값은 보이면 안 됨")
    return f


# ══ 동의 · 완화 낱말의 근거 ═══════════════════════════════════════════════════


@dataclass(frozen=True)
class _Backing:
    """동의 · 완화 낱말을 받쳐 주는 판단 또는 근거 하나의 대상 (6-9 '동의 · 칭찬 낱말 검사')."""

    targets: frozenset[str]  # 지표 · 차원. 비면 전체(범위 제한 없음)
    slide: int | None
    is_assessment: bool


def _backings(
    kind: str,
    assessment_id: str | None,
    evidence_ids: Iterable[str],
    registry: EvidenceRegistry,
    assessments: Mapping[str, Assessment],
    cfg: ChatAgentConfig,
    *,
    allow_negative: bool,
    strength_only: bool = False,
) -> list[_Backing]:
    """동의 · 완화 낱말을 받쳐 줄 수 있는 것을 모은다.

    - 판단 근거: `ASSESSMENT` 문장이 이은 `SUPPORTED`(부정 주장은 칭찬 근거가 아님) 판단. 완화 낱말 검사
      (`allow_negative`)는 판단을 이은 어떤 문장이든 `SUPPORTED`면 받쳐 준다.
    - 기록 근거: `RECORD` 문장이 이은 강점 · 나아짐 근거, `ASSESSMENT` 문장은 그 판단이 쓴 강점 근거.
    - `strength_only`: 최상급(완벽 · 훌륭)은 강점(STRENGTH) 근거만 받쳐 준다. IMPROVED는 부족하다.
    """
    out: list[_Backing] = []
    a = assessments.get(assessment_id) if assessment_id else None
    if (
        a is not None
        and not strength_only
        and a.verdict == "SUPPORTED"
        and (allow_negative or (kind == "ASSESSMENT" and a.polarity != "NEGATIVE"))
    ):
        t = {x for x in (a.subject.metric, a.subject.dimension) if x}
        if a.subject.metric and (d := cfg.dimension_of_metric(a.subject.metric)):
            t.add(d)
        out.append(_Backing(frozenset(t), a.subject.slide_number, True))
    if kind == "RECORD":
        ids: Iterable[str] = evidence_ids
    elif kind == "ASSESSMENT" and a is not None:
        ids = a.evidence_ids
    else:
        ids = ()
    allowed = {"STRENGTH"} if strength_only else PRAISE_VALUES
    for eid in ids:
        ev = registry.get(eid)
        if ev is None or not registry.is_current(eid):
            continue
        if isinstance(ev.value, str) and ev.value.upper() in allowed:
            out.append(_Backing(frozenset(_targets_of(ev, cfg)), ev.slide_number, False))
    return out


def _backed(text: str, backings: list[_Backing], cfg: ChatAgentConfig) -> bool:
    """문장이 말하는 지표 · 장이 받쳐 주는 판단 · 근거의 대상과 같은가(범위를 밝히지 않은 부분 강점은 거절)."""
    mentions = list(dict.fromkeys(_metric_mentions(text, cfg)))
    slides = set(extract_numbers(text).slides)
    for b in backings:
        if (
            mentions
            and b.targets
            and not all(_mention_in(m, set(b.targets), cfg) for m in mentions)
        ):
            continue
        if slides:
            if b.slide is None:
                if not b.is_assessment:
                    continue  # 장 단위 강점은 근거에 그 장이 있어야 한다
            elif slides != {b.slide}:
                continue
        elif b.slide is not None:
            continue  # 일부 장의 강점이면 "2장 시선은 강점이에요"처럼 범위를 밝혀야 한다
        return True
    return False


def _agreement_failure(
    index: int | None,
    kind: str,
    text: str,
    assessment_id: str | None,
    evidence_ids: Iterable[str],
    registry: EvidenceRegistry,
    assessments: Mapping[str, Assessment],
    cfg: ChatAgentConfig,
    *,
    field: str | None = None,
) -> CheckFailure | None:
    """동의 · 칭찬 낱말이 맞는 판단 · 강점 근거에 이어졌는가 (6-9, L6-05). 최상급은 강점 근거가 따로 필요하다."""
    agree = _found(agreement_words(cfg), text)
    if not agree:
        return None
    ids = list(evidence_ids)
    ok = _backed(
        text,
        _backings(kind, assessment_id, ids, registry, assessments, cfg, allow_negative=False),
        cfg,
    )
    if ok and any(s in w for w in agree for s in SUPERLATIVE_STEMS):
        ok = _backed(
            text,
            _backings(
                kind,
                assessment_id,
                ids,
                registry,
                assessments,
                cfg,
                allow_negative=False,
                strength_only=True,
            ),
            cfg,
        )
    if ok:
        return None
    return CheckFailure(
        index,
        CheckId.AGREEMENT_WORD,
        f"'{agree[0]}'처럼 동의 · 칭찬하는 말은 같은 대상의 맞는 판단이나 강점 근거에 이어질 때만 쓸 수 있음",
        field=field,
        claim_kind=kind,
    )


# ══ 문장 검사 ═════════════════════════════════════════════════════════════════


def check_claims(
    claims: list[Claim],
    *,
    registry: EvidenceRegistry,
    assessments: Mapping[str, Assessment] | None = None,
    first_sentences: Mapping[str, str] | None = None,
    guide_texts: Mapping[str, str] | None = None,
    succeeded_actions: Mapping[str, str] | None = None,
    approved_cause_sources: set[str] | None = None,
    code_sentences: set[str] | None = None,
    cfg: ChatAgentConfig = DEFAULT_CONFIG,
) -> list[CheckFailure]:
    """문장마다 종류별 검사와 공통 낱말 검사를 하고 걸린 것을 순서대로 돌려준다(6-9). 모두 통과면 빈 목록.

    - `assessments`: 판단 id → 판단. `first_sentences`: 판단 id → 코드가 그린 첫 문장(7-9).
    - `guide_texts`: `source_id` → 자료 원문. **호출자(loop)는 이번 턴에 읽은 자료만 넘긴다**(앞 턴 자료를 넘기면
      옛 자료가 근거로 인정된다).
    - `succeeded_actions`: 이번 실행에서 SUCCEEDED인 `action_id` → 코드가 검증된 결과로 그린 성공 문장.
      `ACTION_RESULT` 문장은 이것과 글자까지 같아야 한다(제목 · Take 번호를 모델이 바꿀 수 없다).
    - `approved_cause_sources`: 원인 설명을 인정하는 승인된 자료 `source_id`.
    - `code_sentences`: 코드가 비교 · 차이 · 목표 결과로 그린 문장(값이 채워진 것). 결론 낱말(`CONCLUSION_WORDS`)이
      든 문장은 여기 글자 그대로 있어야 한다. 어떤 종류든 여기 있는 문장은 코드가 만든 것이라 결론 · 동의 · 완화 낱말
      규칙은 건너뛰고 근거 최신성 · 금지 주장 · 개인정보 검사는 그대로 한다. `SYSTEM` 문장은 `SystemText`
      값이거나 여기 있으면 통과한다.
    - 한 문장에 여러 검사가 걸리면 검사마다 하나씩 돌려준다. 입력은 바꾸지 않는다.
    """
    assessments = assessments or {}
    first_sentences = first_sentences or {}
    guide_texts = guide_texts or {}
    succeeded_actions = succeeded_actions or {}
    approved = approved_cause_sources or set()
    code = code_sentences or set()
    out: list[CheckFailure] = []
    for i, c in enumerate(claims):
        out.extend(
            _check_one(
                i,
                c,
                registry,
                assessments,
                first_sentences,
                guide_texts,
                succeeded_actions,
                approved,
                code,
                cfg,
            )
        )
    return out


def _check_one(
    i: int,
    c: Claim,
    registry: EvidenceRegistry,
    assessments: Mapping[str, Assessment],
    first_sentences: Mapping[str, str],
    guide_texts: Mapping[str, str],
    succeeded_actions: Mapping[str, str],
    approved: set[str],
    code: set[str],
    cfg: ChatAgentConfig,
) -> list[CheckFailure]:
    f: list[CheckFailure] = []

    def fail(cid: CheckId, reason: str) -> None:
        f.append(CheckFailure(i, cid, reason, claim_kind=c.kind))

    code_made = c.text in code
    scan = c.text  # 낱말 검사를 받는 글(코드 템플릿 첫 문장은 뺀다)
    # ── 종류별 검사 ──
    if c.kind == "RECORD":
        f.extend(_check_record_text(i, c.kind, c.text, c.evidence_ids, registry, cfg))
    elif c.kind == "GUIDE":
        f.extend(_check_guide(i, c, guide_texts))
    elif c.kind == "ACTION_RESULT":
        if not c.action_id or succeeded_actions.get(c.action_id) != c.text:
            fail(
                CheckId.ACTION_RESULT_UNVERIFIED,
                "이번 실행에서 성공한 행동이 아니거나 문장이 검증된 성공 결과로 그린 문장과 다름",
            )
        return f  # 코드가 그린 문장과 글자까지 같아야 하므로 낱말 검사는 하지 않는다
    elif c.kind == "SYSTEM":
        if c.text in _SYSTEM_TEXTS:
            return f
        if not code_made:
            fail(CheckId.SYSTEM_TEXT, "SYSTEM 문장은 정해진 고정 문구나 코드가 그린 문장이어야 함")
            return f
        f.extend(_word_checks(i, c.kind, c.text, cfg, causal=False))
        return f
    elif c.kind == "ASSESSMENT":
        scan, extra = _check_assessment(
            i, c, assessments, first_sentences, registry, cfg, code_made=code_made
        )
        f.extend(extra)
        if scan is None:
            return f

    # ── 공통 낱말 검사 ──
    f.extend(
        _word_checks(i, c.kind, scan, cfg, approved_cause_sources=approved, source_ids=c.source_ids)
    )
    if code_made:
        return f
    if c.kind == "GUIDE":
        scan = _unquoted(scan)  # 인용은 QUOTE_MISMATCH 로 원문과 대조했다
    if (w := _conclusion_word(scan)) is not None and c.kind != "ASSESSMENT":
        fail(
            CheckId.VERDICT_CONFLICT,
            f"'{w}'처럼 비교 · 변화 · 목표 결론은 코드가 쓰는 문장만 낼 수 있음. 결론 문장은 코드가 쓴다",
        )
    af = _agreement_failure(
        i, c.kind, scan, c.assessment_id, c.evidence_ids, registry, assessments, cfg
    )
    if af:
        f.append(af)
    if c.kind == "ASSESSMENT" or c.assessment_id:
        hedge = _found(cfg.hedge_words, scan)
        if hedge:
            backings = _backings(
                c.kind,
                c.assessment_id,
                c.evidence_ids,
                registry,
                assessments,
                cfg,
                allow_negative=True,
            )
            if not _backed(scan, backings, cfg):
                fail(
                    CheckId.HEDGE_WORD,
                    f"'{hedge[0]}'처럼 판정의 정도 · 범위를 바꾸는 말은 같은 대상의 맞는 판단이나 강점 근거 없이 쓸 수 없음",
                )
    return f


_SYSTEM_TEXTS = frozenset(s.value for s in SystemText)


def _check_guide(i: int, c: Claim, guide_texts: Mapping[str, str]) -> list[CheckFailure]:
    f: list[CheckFailure] = []
    if not c.source_ids:
        return [
            CheckFailure(
                i, CheckId.EVIDENCE_MISSING, "일반 조언은 자료 id를 이어야 함", claim_kind=c.kind
            )
        ]
    texts: list[str] = []
    for n, sid in enumerate(dict.fromkeys(c.source_ids), 1):
        t = guide_texts.get(sid)
        if t is None:
            f.append(
                CheckFailure(
                    i,
                    CheckId.STALE_EVIDENCE,
                    f"이은 자료 {n}번째 id는 이번 턴에 읽은 자료가 아니거나 없는 id임",
                    claim_kind=c.kind,
                )
            )
        else:
            texts.append(t)
    if not texts:
        return f
    # 인용은 따옴표 안 글. 따옴표가 없는 문장은 인용 검사 없이 숫자만 본다(자료 문장을 풀어 쓴 설명)
    for pat in _QUOTES:
        for m in pat.finditer(c.text):
            if not any(m.group(1) in t for t in texts):
                f.append(
                    CheckFailure(
                        i,
                        CheckId.QUOTE_MISMATCH,
                        "따옴표 안 인용이 이은 자료 원문에 글자 그대로 없음",
                        claim_kind=c.kind,
                    )
                )
    source_nums = {_parse(m.group(0))[0] for t in texts for m in re.finditer(_NUM, t)}
    seen: set[str] = set()
    for m in re.finditer(_NUM, c.text):
        if m.group(0) in seen:
            continue
        seen.add(m.group(0))
        if _parse(m.group(0))[0] not in source_nums:
            f.append(
                CheckFailure(
                    i,
                    CheckId.QUOTE_MISMATCH,
                    f"문장 속 숫자 '{m.group(0)}'가 이은 자료 원문에 없음",
                    claim_kind=c.kind,
                )
            )
    return f


def _check_assessment(
    i: int,
    c: Claim,
    assessments: Mapping[str, Assessment],
    first_sentences: Mapping[str, str],
    registry: EvidenceRegistry,
    cfg: ChatAgentConfig,
    *,
    code_made: bool = False,
) -> tuple[str | None, list[CheckFailure]]:
    """ASSESSMENT 문장. (낱말 검사를 받을 글, 걸린 것). 판단이 없으면 글은 None(더 볼 것이 없다)."""
    f: list[CheckFailure] = []
    a = assessments.get(c.assessment_id) if c.assessment_id else None
    if a is None:
        return None, [
            CheckFailure(i, CheckId.EVIDENCE_MISSING, "문장에 이은 판단이 없음", claim_kind=c.kind)
        ]
    first = first_sentences.get(a.assessment_id)
    scan = c.text
    mismatch_verdict = a.verdict in ("CONTRADICTED", "PARTLY", "UNVERIFIABLE")
    # 요청 · 정보 질문(REQUEST)은 근거가 있고 기록과 어긋날 때만 첫 문장이 있다(6-3). 없으면 요구하지 않는다
    template_required = mismatch_verdict and not (a.type == "REQUEST" and not first)
    if template_required:
        if not first or not c.text.startswith(first):
            f.append(
                CheckFailure(
                    i,
                    CheckId.VERDICT_CONFLICT,
                    "판정이 일치하지 않음으로 나온 판단의 첫 문장은 코드가 그린 문장이어야 함",
                    claim_kind=c.kind,
                )
            )
            # 템플릿으로 시작하지 않으면 본문 전체를 낱말 검사에 넣는다
        else:
            scan = c.text[len(first) :]
    elif first and c.text.startswith(first):
        scan = c.text[len(first) :]
    if mismatch_verdict:
        # 판정과 반대되는 동의 표현(설정 목록 + "정확합니다" 등)은 모델 설명에 올 수 없다(L6-04)
        agree = _found(agreement_words(cfg), scan)
        if agree:
            f.append(
                CheckFailure(
                    i,
                    CheckId.VERDICT_CONFLICT,
                    f"기록과 다르거나 확인되지 않았다는 판정인데 '{agree[0]}'처럼 판정과 반대되는 말을 함",
                    claim_kind=c.kind,
                )
            )
    if not code_made and (w := _conclusion_word(scan)) is not None:
        f.append(
            CheckFailure(
                i,
                CheckId.VERDICT_CONFLICT,
                f"'{w}'처럼 비교 · 변화 · 목표 결론은 코드가 쓰는 문장만 낼 수 있음. 결론 문장은 코드가 쓴다",
                claim_kind=c.kind,
            )
        )
    # 템플릿 뒤 설명 속 숫자 · Take · 장 · 지표 이름은 판단의 근거와 맞아야 한다
    ex = extract_numbers(scan)
    if ex.values or ex.takes or ex.slides or _metric_mentions(scan, cfg):
        f.extend(_check_record_text(i, c.kind, scan, a.evidence_ids, registry, cfg))
    return scan, f


# ══ 표시 문자열 검사 ══════════════════════════════════════════════════════════

#: 카드 안에서 글이 아닌 열쇠(id · 해시 · 코드 값). 이 열쇠의 값은 검사하지 않는다
_CARD_SKIP_KEYS = frozenset(
    {
        "type",
        "status",
        "label",
        "option",
        "operator",
        "metric",
        "kind",
        "content_hash",
        "fingerprint",
        "created_at",
        "undo",
        "auto",
        "change",
        "calc",
        "path",
        "unit",
        "rule_version",
        "goal_rule_version",
    }
)


def _strings(value: Any, key: str = "") -> Iterable[str]:
    """카드(모델 · dict · 목록)에서 사용자에게 보일 수 있는 문자열을 모은다(id · 해시 · 코드 값 제외)."""
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    if isinstance(value, str):
        if key in _CARD_SKIP_KEYS or key.endswith(("_id", "_ids", "_version", "_hash")):
            return
        yield value
    elif isinstance(value, Mapping):
        for k, v in value.items():
            yield from _strings(v, str(k))
    elif isinstance(value, list | tuple):
        for v in value:
            yield from _strings(v, key)


def check_display_strings(
    cards: Iterable[Any] = (),
    plan: Iterable[Any] = (),
    trace: Iterable[Any] = (),
    history_summary: str | None = None,
    *,
    registry: EvidenceRegistry,
    display_evidence: Mapping[str, list[str]] | None = None,
    cfg: ChatAgentConfig = DEFAULT_CONFIG,
    assessments: Mapping[str, Assessment] | None = None,
) -> list[CheckFailure]:
    """카드 · 계획 · trace · 요약 같은 모든 표시 문자열에 같은 출처 · 금지 주장 · 개인정보 검사를 한다(6-9, L1-37, L4-26).

    - 걸린 것은 `claim_index=None`, `field`가 어느 필드인지(`cards` · `plan` · `trace` · `history_summary`).
    - `display_evidence`: 문자열이 쓸 수 있는 근거 id. 열쇠는 `"cards:0"`(카드 0번) · `"plan:2"` · `"trace:1"` 같은 항목 열쇠
      또는 `"cards"` · `"plan"` · `"trace"` · `"history_summary"` 같은 필드 열쇠(항목 열쇠가 먼저). 열쇠가 없거나 빈 목록이면
      그 문자열은 기록 숫자 · Take · 장을 담을 수 없다. 숫자가 이은 근거와 다르면 `NUMBER_MISMATCH`, Take · 장 · 지표
      이름이 다르면 `TARGET_MISMATCH`, 동의 · 칭찬 낱말이 맞는 근거 없이 있으면 `AGREEMENT_WORD`다.
      `GOAL_SAVED` · `REASSESS_RESULT` 카드는 검증된 결과로 코드가 만들므로 호출자가 그 근거 id를 넘긴다.
    - 그 밖에 형식 규칙(`DISPLAY_TEXT`): 링크 · 제어 문자 · 주입 문장 표지. `history_summary`의 지시문은 실행 권한이 없다.
    - `assessments`에 일치하지 않는 판정(CONTRADICTED · PARTLY · UNVERIFIABLE)이 있으면 완화 낱말도 거른다(L6-04).
    """
    de = display_evidence or {}
    blur_guard = any(
        a.verdict in ("CONTRADICTED", "PARTLY", "UNVERIFIABLE")
        for a in (assessments or {}).values()
    )
    out: list[CheckFailure] = []

    def scan(field: str, key: str, text: str) -> None:
        ids = list(de.get(key, de.get(field, [])))
        out.extend(_word_checks(None, None, text, cfg, field=field, causal=False))
        out.extend(
            _check_record_text(
                None, None, text, ids, registry, cfg, field=field, allow_no_evidence=True
            )
        )
        af = _agreement_failure(
            None, "RECORD", text, None, ids, registry, assessments or {}, cfg, field=field
        )
        if af:
            out.append(af)
        if _URL.search(text):
            out.append(
                CheckFailure(None, CheckId.DISPLAY_TEXT, "링크는 표시할 수 없음", field=field)
            )
        if _CONTROL.search(text):
            out.append(
                CheckFailure(None, CheckId.DISPLAY_TEXT, "제어 문자가 들어 있음", field=field)
            )
        low = text.lower()
        if any(m in low for m in _INJECTION):
            out.append(
                CheckFailure(
                    None, CheckId.DISPLAY_TEXT, "지시문처럼 보이는 문장이 들어 있음", field=field
                )
            )
        if blur_guard and (w := _found(cfg.hedge_words, text)):
            out.append(
                CheckFailure(
                    None,
                    CheckId.HEDGE_WORD,
                    f"'{w[0]}'처럼 판정의 정도 · 범위를 바꾸는 말은 쓸 수 없음",
                    field=field,
                )
            )

    for n, card in enumerate(cards):
        for s in _strings(card):
            scan("cards", f"cards:{n}", s)
    for n, step in enumerate(plan):
        d = step.model_dump() if hasattr(step, "model_dump") else dict(step)
        for s in (d.get("step"), d.get("note")):
            if isinstance(s, str):
                scan("plan", f"plan:{n}", s)
    for n, item in enumerate(trace):
        d = item.model_dump() if hasattr(item, "model_dump") else dict(item)
        for s in (d.get("tool"), d.get("args_summary"), d.get("result_summary")):
            if isinstance(s, str):
                scan("trace", f"trace:{n}", s)
    if history_summary:
        scan("history_summary", "history_summary", history_summary)
    return out


# ══ 고치기 · 조립 ═════════════════════════════════════════════════════════════


def failing_indices(failures: Iterable[CheckFailure]) -> set[int]:
    """걸린 문장 순번(표시 문자열 · 행동처럼 순번이 없는 것은 제외)."""
    return {f.claim_index for f in failures if f.claim_index is not None}


@dataclass
class FixOutcome:
    """고치기 한 번의 결과. `claims`는 남은(검사를 통과한) 문장, 비면 호출자가 PARTIAL/SYSTEM 안내로 끝낸다(6-9)."""

    claims: list[Claim]
    used_fixed: bool
    rejected: list[CheckFailure]
    #: 지운 문장 수 (고친 뒤 다시 걸려 지운 것 또는 고치기 실패로 지운 원본 걸린 문장)
    dropped: int
    #: 고친 글을 다시 검사해 남은 실패(진단 기록 `round=1`용)
    remaining_failures: list[CheckFailure]

    @property
    def all_dropped(self) -> bool:
        """남은 문장이 하나도 없다 → 호출자(loop)가 검증된 근거 템플릿 PARTIAL 또는 고정 SYSTEM 안내로 끝낸다(6-9)."""
        return not self.claims


def _ids_of(claims: Iterable[Claim]) -> set[str]:
    out: set[str] = set()
    for c in claims:
        out.update(c.evidence_ids)
        out.update(c.source_ids)
        if c.action_id:
            out.add(c.action_id)
        if c.assessment_id:
            out.add(c.assessment_id)
    return out


def apply_fix_round(
    original: list[Claim],
    fixed: list[Claim] | None,
    failures_before: list[CheckFailure],
    *,
    recheck: Callable[[list[Claim]], list[CheckFailure]],
    fixed_action_count: int = 0,
) -> FixOutcome:
    """모델이 고친 글을 받아 검사를 통과한 문장만 남긴다 (6-9 '고친 뒤', L1-38 · L4-27).

    - `fixed=None`: 고치기 호출이 실패(시간 초과 · 거절 · 무효 출력)했다 → 원본에서 이미 통과한 문장만 남긴다.
    - 고친 결과가 새 행동(`fixed_action_count > 0`)이나 원본에 없던 근거 · 자료 · 행동 · 판단 id를 내면 고친 결과
      전체를 거부(`FIX_NEW_CONTENT`)하고 원본에서 통과한 문장만 남긴다.
    - 그 밖에는 `recheck(fixed)`(추가 모델 호출 없이 같은 검사)로 다시 보고 통과한 문장만 남긴다.
    """
    bad = failing_indices(failures_before)
    passing = [c for i, c in enumerate(original) if i not in bad]
    if fixed is None:
        return FixOutcome(passing, False, [], len(original) - len(passing), [])
    rejected: list[CheckFailure] = []
    if fixed_action_count > 0:
        rejected.append(CheckFailure(None, CheckId.FIX_NEW_CONTENT, "고치는 단계에서 새 행동을 냄"))
    new_ids = _ids_of(fixed) - _ids_of(original)
    if new_ids:
        rejected.append(
            CheckFailure(None, CheckId.FIX_NEW_CONTENT, "고치는 단계에서 새 근거 · 자료 id를 냄")
        )
    if rejected:
        return FixOutcome(passing, False, rejected, len(original) - len(passing), [])
    again = recheck(fixed)
    bad2 = failing_indices(again)
    kept = [c for i, c in enumerate(fixed) if i not in bad2]
    return FixOutcome(kept, True, [], len(fixed) - len(kept), again)


def assemble_message(claims: list[Claim]) -> tuple[str, list[Claim]]:
    """`message.text` = 검사를 통과한 `claims[].text`를 순서대로 LF로 이은 값과 정확히 같다(5-5)."""
    return "\n".join(c.text for c in claims), list(claims)


def summary_counts(
    failures: list[CheckFailure],
    round_: int,
    *,
    checked: int,
    dropped: int = 0,
    actions_dropped: int = 0,
) -> dict[str, int]:
    """진단 기록 `CHECK`/`SUMMARY` 단계의 `detail` (11-5): 그 회차의 문장 단위 `checked` · `failed` · `dropped`와
    버린 행동 수 `actions_dropped`. `failed`는 걸린 문장 수(한 문장에 여러 검사가 걸려도 1)다."""
    return {
        "round": round_,
        "checked": checked,
        "failed": len(failing_indices(failures)),
        "dropped": dropped,
        "actions_dropped": actions_dropped,
    }
