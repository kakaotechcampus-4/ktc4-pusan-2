"""규칙 기반 핵심 사실 검증: 대본의 수치 · 이름이 STT 에 나왔는지, 다르면 왜 다른지 신호를 만든다."""

import re
from difflib import SequenceMatcher

from ..shared.facts import CriticalFact, extract_critical_facts
from ..shared.rubric import NAME_TYPES, EvaluationRubric
from ..shared.text import NormalizedScript, compact
from .config import (
    APPROX_ANY,
    APPROX_LOWER,
    APPROX_UPPER,
    PARTICLE,
    QUALIFIER_ANY,
    QUALIFIER_LOWER,
    QUALIFIER_UPPER,
)
from .schemas import Alignment, FactCheck, MismatchCause


def _stt_sentence(norm: NormalizedScript, pos: int) -> int:
    return next((s.index for s in norm.sentences if s.start <= pos < s.end), 0)


def check_critical_facts(
    rubric: EvaluationRubric, stt_norm: NormalizedScript, alignments: list[Alignment]
) -> list[FactCheck]:
    """Critical Fact Check (규칙): 대본의 수치·이름이 STT 에 그대로 나왔는지 본다.

    - 수치: STT 에서도 같은 수 파서로 읽어 정규형으로 비교한다 ('42%' = '사십이 퍼센트').
      같은 값이 없고, 대본 문장과 맞춰진 STT 구간(없으면 앞뒤 한 문장)에 같은 종류·단위의 다른 값이 있으면 mismatched
    - 이름: 공백·문장부호를 무시하고 찾는다. 영문 이름은 STT 가 한글로 받아 적을 수 있어('SeatFlow' → '시트플로우')
      못 찾으면 missing 이 아니라 unverified 로 두고 LLM 에 확인시킨다
    """
    stt_facts = extract_critical_facts(stt_norm)
    rubric_values = {(f.type, f.normalized) for f in rubric.critical_facts}
    region_of = {a.sentence_index: a.stt_ids for a in alignments if a.coverage > 0}
    stt_compact = compact(stt_norm.text)[0]
    # '다른 값'으로 이미 쓴 STT 수치. 한 번 틀리게 말한 값이 여러 사실을 틀리게 만들지 않는다
    used: set[int] = set()
    checks = []
    for fact in rubric.critical_facts:
        base = dict(
            fact_id=fact.id,
            type=fact.type,
            value=fact.value,
            normalized=fact.normalized,
            importance=fact.importance,
            key_point_ids=fact.key_point_ids,
        )
        if fact.type in NAME_TYPES:
            if compact(fact.value)[0] in stt_compact:
                checks.append(FactCheck(status="matched", stt_value=fact.value, **base))
            elif re.search(r"[A-Za-z]", fact.value):
                checks.append(
                    FactCheck(
                        status="unverified", note="영문 이름: 한글 표기 여부를 LLM 이 확인", **base
                    )
                )
            else:
                checks.append(FactCheck(status="missing", **base))
            continue
        same = [f for f in stt_facts if f.type == fact.type and f.normalized == fact.normalized]
        if same:
            ids = sorted({_stt_sentence(stt_norm, s[0]) for f in same for s in f.spans})
            note = "한정어 차이" if fact.qualifier and fact.qualifier != same[0].qualifier else ""
            checks.append(
                FactCheck(status="matched", stt_value=same[0].value, stt_ids=ids, note=note, **base)
            )
            continue
        # 같은 값이 없으면, 대본 문장과 맞춰진 STT 문장 → 그 앞뒤 한 문장 순서로 같은 종류·단위의 다른 값을 찾는다
        region = {j for i in fact.sentence_indices for j in region_of.get(i, [])}
        candidates = [
            k
            for k, f in enumerate(stt_facts)
            if k not in used
            and f.type == fact.type
            and f.unit == fact.unit
            and (f.type, f.normalized) not in rubric_values
        ]
        pick = None
        for area in (region, {j + d for j in region for d in (-1, 1)}):
            pick = next(
                (
                    k
                    for k in candidates
                    if any(_stt_sentence(stt_norm, s[0]) in area for s in stt_facts[k].spans)
                ),
                None,
            )
            if pick is not None:
                break
        if pick is not None:
            used.add(pick)
            other = stt_facts[pick]
            ids = sorted({_stt_sentence(stt_norm, s[0]) for s in other.spans})
            checks.append(
                FactCheck(
                    status="mismatched",
                    stt_value=other.value,
                    stt_ids=ids,
                    note=f"대본 {fact.value} ≠ STT {other.value}",
                    stt_normalized=other.normalized,
                    stt_numeric_value=other.numeric_value,
                    stt_qualifier=other.qualifier,
                    stt_span=tuple(other.spans[0]),
                    **base,
                )
            )
        else:
            checks.append(FactCheck(status="missing", **base))
    return checks


DIGIT_READING = "영일이삼사오육칠팔구"


def _read_below_10000(n: int) -> str:
    out = ""
    for value, unit in ((1000, "천"), (100, "백"), (10, "십"), (1, "")):
        digit = n // value % 10
        if digit:
            out += ("" if digit == 1 and unit else DIGIT_READING[digit]) + unit
    return out


def read_number(text: str) -> str:
    """'42' → '사십이', '12400000' → '천이백사십만', '2.4' → '이점사'. 발표자가 소리 내어 읽는 방식."""
    integer, _, decimal = text.partition(".")
    n, parts = int(integer), []
    for value, unit in ((10**12, "조"), (10**8, "억"), (10**4, "만"), (1, "")):
        chunk = n // value % 10000
        if chunk:
            parts.append(("" if chunk == 1 and unit == "만" else _read_below_10000(chunk)) + unit)
    out = "".join(parts) or "영"
    return out + ("점" + "".join(DIGIT_READING[int(d)] for d in decimal) if decimal else "")


NATIVE_HOUR = {
    1: "한",
    2: "두",
    3: "세",
    4: "네",
    5: "다섯",
    6: "여섯",
    7: "일곱",
    8: "여덟",
    9: "아홉",
    10: "열",
    11: "열한",
    12: "열두",
}


def reading_of(normalized: str, kind: str = "") -> str:
    """정규형 안의 수를 말하는 대로 읽는다: '12,400,000건' → '천이백사십만', 시각 '18:30' → '여섯시 삼십분'."""
    if kind == "time" and re.fullmatch(r"\d{2}:\d{2}", normalized):
        hour, minute = map(int, normalized.split(":"))
        return (
            NATIVE_HOUR[hour % 12 or 12]
            + "시"
            + (f" {read_number(str(minute))}분" if minute else "")
        )
    return " ".join(
        read_number(g) for g in re.findall(r"\d+(?:\.\d+)?", normalized.replace(",", ""))
    )


def jamo_diff(a: str, b: str) -> int:
    """한글 음절 두 개의 초성·중성·종성 중 다른 것의 수 (0~3). '삼'↔'사' = 1 (종성), '육'↔'팔' = 3."""
    if not ("가" <= a <= "힣" and "가" <= b <= "힣"):
        return 0 if a == b else 3
    x, y = ord(a) - 0xAC00, ord(b) - 0xAC00
    return sum(
        p != q for p, q in ((x // 588, y // 588), (x // 28 % 21, y // 28 % 21), (x % 28, y % 28))
    )


def mismatch_signals(
    check: FactCheck, script_fact: CriticalFact, stt_text: str
) -> tuple[list[str], MismatchCause, str]:
    """대본 값과 STT 값이 다를 때, 원인을 가늠할 규칙 신호를 만든다. 최종 원인은 `verify.py` 의 LLM 교차 검증이 정한다.

    - 어림 표현: STT 값에 '넘게', '가까이', '약', '정도' 같은 말이 붙어 있고 방향과 차이가 맞으면 → 어림 (틀린 말 아님).
      대본에 원래 있던 한정어('약 18%' → '약 28%' 의 '약')는 발표자의 어림으로 보지 않는다
    - 음절 순서만 바뀜 ('칠십육' ↔ '육십칠'): 사람이 숫자를 바꿔 말하는 실수. 음성 인식은 음절 순서를 바꾸지 않는다
    - 발음이 비슷한 음절 하나 차이('삼백' ↔ '사백')나 음절 하나가 빠짐('사십이' → '사십'): 음성 인식 오류일 수 있음
    - 음절 하나가 조금 비슷함(초성·중성·종성 중 두 개가 다름, '여섯' ↔ '다섯'): 애매함 → 발표자 실수 쪽으로 두되 LLM 이 확인
    - 그 밖(다른 수): 발표자 실수 쪽
    세 번째 값(발음 관계)으로 LLM 인식 확인 대상을 고른다: similar·near 만 확인하고 swap·different·approx 는 규칙으로 정한다.
    """
    a = reading_of(check.normalized, check.type).replace(" ", "")
    b = reading_of(check.stt_normalized or "", check.type).replace(" ", "")
    signals = [f"읽기: 대본 '{a}' / STT '{b}'"]
    start, end = check.stt_span or (0, 0)
    before, after = stt_text[max(0, start - 6) : start], stt_text[end : end + 8]
    t, script_value = check.stt_numeric_value, script_fact.numeric_value
    if script_value and t is not None:
        diff = abs(script_value - t) / abs(script_value)
        q = check.stt_qualifier if check.stt_qualifier != script_fact.qualifier else None
        lower = (
            re.match(rf"{PARTICLE}(?:{APPROX_LOWER})", after) is not None or q in QUALIFIER_LOWER
        )
        upper = (
            re.match(rf"{PARTICLE}(?:{APPROX_UPPER})", after) is not None
            or re.search(r"거의\s?$", before) is not None
            or q in QUALIFIER_UPPER
        )
        loose = (
            re.search(rf"(?:{APPROX_ANY})\s?$", before) is not None
            or re.match(rf"{PARTICLE}(?:{APPROX_ANY})", after) is not None
            or q in QUALIFIER_ANY
        )
        if lower or upper or loose:
            direction_ok = (
                (lower and t <= script_value)
                or (upper and t >= script_value)
                or (loose and not lower and not upper)
            )
            if direction_ok and diff <= 0.3:
                signals.append(
                    f"어림 표현이 붙어 있고 방향이 맞음 (차이 {diff:.0%}): '{check.stt_value}{after.rstrip()}'"
                )
                return signals, "approximation", "approx"
            signals.append(
                f"어림 표현이 붙어 있지만 방향이나 차이가 맞지 않음 (차이 {diff:.0%}) → 사실과 다른 말"
            )
    if a and b and sorted(a) == sorted(b) and a != b:
        signals.append(f"음절 순서만 바뀜 ({a} ↔ {b}) → 사람이 숫자를 바꿔 말하는 실수에 가까움")
        return signals, "speaker_error", "swap"
    ops = [
        op for op in SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op[0] != "equal"
    ]
    if len(ops) == 1:
        tag, i1, i2, j1, j2 = ops[0]
        if tag == "replace" and i2 - i1 == 1 and j2 - j1 == 1 and jamo_diff(a[i1], b[j1]) <= 1:
            signals.append(
                f"발음이 비슷한 음절 하나 차이 ({a[i1]} ↔ {b[j1]}) → 음성 인식 오류일 수 있음"
            )
            return signals, "asr_error", "similar"
        if tag == "replace" and i2 - i1 == 1 and j2 - j1 == 1 and jamo_diff(a[i1], b[j1]) == 2:
            signals.append(
                f"발음이 조금 비슷한 음절 하나 차이 ({a[i1]} ↔ {b[j1]}) → 인식 오류인지 발표자 실수인지 애매함"
            )
            return signals, "speaker_error", "near"
        if tag in ("delete", "insert") and max(i2 - i1, j2 - j1) == 1:
            changed = a[i1:i2] or b[j1:j2]
            signals.append(
                f"음절 하나('{changed}')가 {'빠짐' if tag == 'delete' else '더해짐'} → 음성 인식 오류일 수 있음"
            )
            return signals, "asr_error", "similar"
    signals.append("발음이 비슷하지 않은 다른 수 → 발표자가 다른 값을 말했을 가능성이 큼")
    return signals, "speaker_error", "different"


def annotate_mismatches(
    fact_checks: list[FactCheck], rubric: EvaluationRubric, stt_norm: NormalizedScript
) -> None:
    """mismatched 인 수치마다 원인 신호(signals)와 규칙 추정(rule_cause)을 붙인다."""
    facts = {f.id: f for f in rubric.critical_facts}
    for check in fact_checks:
        if check.status == "mismatched":
            check.signals, check.rule_cause, check.sound = mismatch_signals(
                check, facts[check.fact_id], stt_norm.text
            )
