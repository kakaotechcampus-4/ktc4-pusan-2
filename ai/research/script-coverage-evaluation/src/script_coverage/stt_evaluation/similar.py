"""비슷한 말 찾기: 대본과 STT 의 같은 자리에 발음이 비슷한 다른 말(단어 · 수치)이 나온 곳. 판단 보류 항목이 된다."""

import re
from difflib import SequenceMatcher

from ..shared.facts import CriticalFact, extract_critical_facts
from ..shared.rubric import NAME_TYPES, EvaluationRubric
from ..shared.text import NormalizedScript
from .align import NUMERIC_TYPES, Token
from .config import SIMILAR_SOUNDS, WORD_DISTANCE
from .facts import jamo_diff, mismatch_signals
from .normalize import locate_raw
from .schemas import Alignment, FactCheck, SimilarItem

WORD_TAGS = {"NNG", "NNP", "SL", "SH", "XR"}  # 단어 후보는 대본 쪽 명사·어근·외국어만 본다


PARTICLE_TAIL = re.compile(
    r"(?:이랑|에서|으로|하고|까지|부터|은|는|이|가|을|를|의|에|로|도|만|과|와|랑)$"
)


def phonetic_distance(a: str, b: str) -> float:
    """음절 단위 편집 거리를 긴 쪽 음절 수로 나눈 값 (0 = 같음). 바꾸기 비용 = 초성·중성·종성 중 다른 수 / 3,
    셋 다 다른 음절(발음이 전혀 다름)은 1.5, 넣기·빼기 = 1.
    '재고'↔'제고' = 0.17 (중성 하나), '노쇼'↔'노조' = 0.33, '월요일'↔'금요일' = 0.5, '예측'↔'추천' = 0.83."""
    n, m = len(a), len(b)
    d = [[float(i + j) if i == 0 or j == 0 else 0.0 for j in range(m + 1)] for i in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            diff = jamo_diff(a[i - 1], b[j - 1])
            d[i][j] = min(
                d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (1.5 if diff == 3 else diff / 3)
            )
    return d[n][m] / max(n, m, 1)


def _plain(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z가-힣]", "", text).lower()


def _stt_word(text: str, start: int, end: int) -> tuple[str, int, int]:
    """STT 쪽 어절(띄어쓰기 단위)에서 조사를 뗀 표현과 그 위치."""
    while start > 0 and not text[start - 1].isspace():
        start -= 1
    while end < len(text) and not text[end].isspace():
        end += 1
    word = PARTICLE_TAIL.sub("", text[start:end]) or text[start:end]
    return word, start, start + len(word)


def _contiguous(tokens: list[Token], text: str) -> bool:
    """형태소들이 붙어 있는가 (사이에 공백만). 떨어진 두 단어를 한 표현으로 묶지 않는다."""
    return all(not text[a.end : b.start].strip() for a, b in zip(tokens, tokens[1:], strict=False))


def _number_signals(
    script_fact: CriticalFact, stt_fact: CriticalFact, stt_norm: NormalizedScript
) -> tuple[list[str], str, str]:
    probe = FactCheck(
        fact_id=script_fact.id,
        type=script_fact.type,
        value=script_fact.value,
        normalized=script_fact.normalized,
        importance=script_fact.importance,
        key_point_ids=script_fact.key_point_ids,
        status="mismatched",
        stt_value=stt_fact.value,
        stt_normalized=stt_fact.normalized,
        stt_numeric_value=stt_fact.numeric_value,
        stt_qualifier=stt_fact.qualifier,
        stt_span=tuple(stt_fact.spans[0]),
    )
    return mismatch_signals(probe, script_fact, stt_norm.text)


def find_similar_items(
    rubric: EvaluationRubric,
    script_norm: NormalizedScript,
    script_tokens: list[Token],
    stt_norm: NormalizedScript,
    stt_tokens: list[Token],
    alignments: list[Alignment],
    fact_checks: list[FactCheck],
    raw_stt: str,
) -> list[SimilarItem]:
    """비슷한 말 찾기 (규칙).

    ① `facts.py` 에서 대본과 다른 값이 나온 수치 중 발음이 비슷하거나 애매한 것 (발음 관계 similar·near, `mismatch_signals`)
    ② 대본 문장과 정렬된 STT 문장을 형태소 단위로 맞춰(SequenceMatcher) 같은 자리에서 바뀐 표현
       - 수치 → 수치 (발음이 비슷한 것만): 같은 수치를 다른 문장에서 맞게 말해 ①에서 빠진 경우도 잡힌다
       - 단어 → 단어: 대본 명사가 그 자리에서 빠지고, 발음 거리 ≤ WORD_DISTANCE 인 새 말이 나온 경우
    발음이 전혀 다르거나 음절 순서만 바뀐 수치, 어림 표현은 여기 들지 않는다 — `facts.py` 의 규칙 원인으로 비율에 반영한다.
    """
    facts = {f.id: f for f in rubric.critical_facts}
    fact_by_value = {(f.type, f.normalized): f for f in rubric.critical_facts}
    stt_facts = {
        f.spans[0][0]: f for f in extract_critical_facts(stt_norm) if f.type in NUMERIC_TYPES
    }
    rubric_values = set(fact_by_value)
    items, seen = [], set()

    def stt_sentence_of(pos: int) -> int:
        return next((s.index for s in stt_norm.sentences if s.start <= pos < s.end), 0)

    def add(script_span, stt_span, fact_id, **fields):
        key = (_plain(fields["script_text"]), _plain(fields["stt_text"]))
        if key in seen:
            return
        seen.add(key)
        sentence = fields.pop("script_sentence_index")
        kp_ids = [
            kp.id
            for kp in rubric.key_points
            if sentence in kp.sentence_indices or (fact_id and fact_id in kp.fact_ids)
        ]
        items.append(
            SimilarItem(
                item_id=f"R{len(items) + 1}",
                script_sentence_index=sentence,
                script_span=tuple(script_span),
                stt_sentence_index=stt_sentence_of(stt_span[0]),
                stt_span=tuple(stt_span),
                stt_raw_span=locate_raw(
                    raw_stt, fields["stt_text"], stt_span[0], len(stt_norm.text)
                ),
                fact_id=fact_id,
                key_point_ids=kp_ids,
                **fields,
            )
        )

    for c in fact_checks:  # ①
        if c.status == "mismatched" and c.sound in SIMILAR_SOUNDS:
            fact = facts[c.fact_id]
            sentence = min(fact.sentence_indices)
            span = next(
                (
                    sp
                    for sp, i in zip(fact.spans, fact.sentence_indices, strict=False)
                    if i == sentence
                ),
                fact.spans[0],
            )
            add(
                span,
                c.stt_span,
                c.fact_id,
                kind="number",
                script_text=c.value,
                stt_text=c.stt_value,
                script_sentence_index=sentence,
                signals=c.signals,
                rule_guess=c.rule_cause,
            )

    for a in alignments:  # ②
        if not a.stt_ids:
            continue
        src_all = [t for t in script_tokens if t.sentence == a.sentence_index]
        dst_all = [t for t in stt_tokens if t.sentence in a.stt_ids]
        sentence_plain = _plain(script_norm.sentences[a.sentence_index].text)
        matcher = SequenceMatcher(
            None, [t.key for t in src_all], [t.key for t in dst_all], autojunk=False
        )
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag != "replace" or i2 - i1 > 2 or j2 - j1 > 2:
                continue
            src, dst = src_all[i1:i2], dst_all[j1:j2]
            src_num = [t for t in src if t.tag == "NUM"]
            dst_num = [t for t in dst if t.tag == "NUM"]
            if len(src_num) == 1 and len(dst_num) == 1:
                s_type, s_value = src_num[0].key[1:-1].split(":", 1)
                d_type, d_value = dst_num[0].key[1:-1].split(":", 1)
                script_fact, stt_fact = (
                    fact_by_value.get((s_type, s_value)),
                    stt_facts.get(dst_num[0].start),
                )
                if (
                    s_type != d_type
                    or script_fact is None
                    or stt_fact is None
                    or (d_type, d_value) in rubric_values
                    or stt_fact.unit != script_fact.unit
                ):
                    continue
                signals, guess, sound = _number_signals(script_fact, stt_fact, stt_norm)
                if sound in SIMILAR_SOUNDS:
                    add(
                        (src_num[0].start, src_num[0].end),
                        (dst_num[0].start, dst_num[0].end),
                        None,
                        kind="number",
                        script_text=script_fact.value,
                        stt_text=stt_fact.value,
                        script_sentence_index=a.sentence_index,
                        signals=[
                            "같은 수치를 다른 곳에서 맞게 말했더라도 이 문장 자리에서는 다르게 나옴"
                        ]
                        + signals,
                        rule_guess=guess,
                    )
            elif (
                not src_num
                and not dst_num
                and all(t.tag in WORD_TAGS for t in src)
                and _contiguous(src, script_norm.text)
                and _contiguous(dst, stt_norm.text)
            ):
                script_word = script_norm.text[src[0].start : src[-1].end]
                sw = _plain(script_word)
                stt_id = stt_sentence_of(dst[0].start)
                if (
                    len(sw) < 2
                    or sw in _plain(stt_norm.sentences[stt_id].text)
                    or re.search(r"[A-Za-z]", script_word)
                ):
                    continue  # 짧은 말, 그 STT 문장에 이미 있는 말, 영문 이름(한글 표기는 `judge.py` 의 이름 확인이 본다)은 제외
                options = [
                    (stt_norm.text[dst[0].start : dst[-1].end], dst[0].start, dst[-1].end),
                    _stt_word(stt_norm.text, dst[0].start, dst[-1].end),
                ]
                best, b_start, b_end = min(
                    options, key=lambda o: phonetic_distance(sw, _plain(o[0]))
                )
                dist = phonetic_distance(sw, _plain(best))
                if dist > WORD_DISTANCE or not _plain(best) or _plain(best) in sentence_plain:
                    continue
                fact = next(
                    (
                        f
                        for f in rubric.critical_facts
                        if f.type in NAME_TYPES
                        and a.sentence_index in f.sentence_indices
                        and (_plain(f.value) in sw or sw in _plain(f.value))
                    ),
                    None,
                )
                add(
                    (src[0].start, src[-1].end),
                    (b_start, b_end),
                    fact.id if fact else None,
                    kind="word",
                    script_text=script_word,
                    stt_text=best,
                    script_sentence_index=a.sentence_index,
                    rule_guess="asr_error",
                    signals=[
                        f"발음 거리 {dist:.2f} ('{script_word}' ↔ '{best}')",
                        "대본 단어가 이 자리에서 빠지고 발음이 비슷한 다른 말이 나옴",
                    ],
                )
    return items


CAUSE_STATUS = {"speaker_error": "mismatched", "approximation": "approximate"}


def settle_facts(fact_checks: list[FactCheck], items: list[SimilarItem]) -> None:
    """사실 결과 정리 (규칙).
    - 발음이 비슷한 말에 걸린 수치·이름 → sound_alike (판단 보류, 비율에서 뺌)
    - 나머지 다른 수치 → 규칙 원인대로: 발음이 전혀 다름·순서만 바뀜 → mismatched, 어림 표현 → approximate
    """
    held = {it.fact_id for it in items if it.fact_id}
    for c in fact_checks:
        if c.fact_id in held and c.status in ("mismatched", "missing"):
            c.status, c.note = (
                "sound_alike",
                "발음이 비슷한 다른 말로 나옴 — 판단 보류 (비율에서 뺌)",
            )
        elif c.status == "mismatched":
            c.cause = c.rule_cause if c.rule_cause in CAUSE_STATUS else "speaker_error"
            c.cause_reason = {
                "speaker_error": "발음이 비슷하지 않거나 음절 순서만 바뀜 — 발표자가 다르게 말함",
                "approximation": "어림 표현이 붙어 있고 방향이 맞음 — 어림해 말함",
            }[c.cause]
            c.status = CAUSE_STATUS[c.cause]
