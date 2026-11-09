"""말의 구성: STT 를 대본 내용 · 다른 장 내용 · 진행 말 · 대본 밖 말로 나눈다 (규칙). 어떤 점수도 깎지 않는다.

- `build_offscript`: 이 장 대본으로 설명되지 않는 STT 구간(짝 없는 문장 · 문장 안 꼬리)을 찾고, 진행 말 사전 ·
  다른 장 대본 어휘로 범주를 붙인다. 위치는 정규화 STT 문장 안의 코드 포인트다.
- `build_speech_mix`: 대본 밖 구간 목록과 STT 형태소로 범주별 형태소 수 · 화면 목록을 만든다.
- `mix_display`: 범주별 형태소 수 → 화면 목록 (검증하지 않은 범주는 off_script 로 합친다). Take 합산에도 쓴다.
- `said_in_other_slides`: 다른 장 내용 구간이 가리키는 장의 핵심 내용을 찾아, 그 장의 놓친 중요 내용에
  '다른 장에서 말함'을 붙일 때 쓴다 (Take 단위).

분모는 정규화한 STT 의 내용 형태소다(간투사 · 바로 반복한 말은 정규화에서 이미 빠진다).
같은 입력이면 늘 같은 출력이다(번호 · 정렬 순서 고정, 반올림 3자리).
"""

import re
import unicodedata

from ..shared.rubric import EvaluationRubric
from ..shared.text import NormalizedScript, normalize_script
from .align import Token, positioned_tokens
from .config import (
    OFFSCRIPT_IN_SENTENCE,
    OFFSCRIPT_LLM_ENABLED,
    OFFSCRIPT_MIN_TOKENS,
    OTHER_SLIDE_MIN_TOKENS,
    OTHER_SLIDE_OVERLAP,
    PROCEDURAL_MAX_TOKENS,
    PROCEDURAL_PATTERNS,
    SPEECH_MIX_VALIDATED,
    UNALIGNED_SCRIPT_OVERLAP,
)
from .fillers import FILLER, REPEATED_WORD
from .schemas import (
    MIX_CATEGORIES,
    Alignment,
    MixItem,
    Offscript,
    OffscriptSpan,
    Repetitions,
    SpeechMix,
)

_PROCEDURAL = [re.compile(p) for p in PROCEDURAL_PATTERNS]


def _vocab(text: str) -> set[str]:
    return {t.key for t in positioned_tokens(normalize_script(text))}


def _overlap(tokens: list[Token], vocab: set[str]) -> float:
    return sum(1 for t in tokens if t.key in vocab) / len(tokens) if tokens else 0.0


def _is_procedural(text: str, tokens: int) -> bool:
    return tokens <= PROCEDURAL_MAX_TOKENS and any(p.search(text) for p in _PROCEDURAL)


def _reasons(spans: list[OffscriptSpan], tokens: int) -> tuple[str, list[str]]:
    """상태 · 이유 코드. 형태소가 없으면 `not_applicable`."""
    if tokens == 0:
        return "not_applicable", ["NO_SPEECH"]
    reasons = []
    if any(s.category != s.display_category for s in spans):
        reasons.append("NOT_VALIDATED")
    if not OFFSCRIPT_LLM_ENABLED and any(
        s.category == "off_script" and s.source == "rule" for s in spans
    ):
        reasons.append("OFFSCRIPT_DISABLED")
    return "ok", reasons


def _display_category(category: str) -> str:
    return category if category in SPEECH_MIX_VALIDATED else "off_script"


def build_offscript(
    rubric: EvaluationRubric,
    stt_norm: NormalizedScript,
    stt_ptokens: list[Token],
    alignments: list[Alignment],
    other_rubrics: dict[int, EvaluationRubric],
) -> Offscript:
    """이 장의 대본 밖 구간 목록 (규칙). STT 가 비면 `state: not_applicable` (`NO_SPEECH`).

    - 대본 어휘 = 이 장 대본 전체(skip 문장 포함)의 내용 형태소.
    - 짝(정렬 구간)이 없는 STT 문장은 대본 어휘 비율이 `UNALIGNED_SCRIPT_OVERLAP` 이상이면 대본 내용,
      아니면 문장 전체가 후보다. 짝 있는 문장 안의 꼬리(대본 어휘에 없는 형태소가 `OFFSCRIPT_MIN_TOKENS` 개
      이상 이어진 구간)는 `OFFSCRIPT_IN_SENTENCE` 를 켰을 때만 후보다(기본 꺼짐 — 대부분 바꿔 말한 대본 내용).
    - 후보의 범주: 진행 말 → 다른 장 내용(가장 비슷한 장, 같으면 작은 번호) → 대본 밖.
    """
    if not stt_ptokens:
        return Offscript(state="not_applicable", reason_codes=["NO_SPEECH"], spans=[])
    vocab = _vocab(rubric.normalized_script)
    others = {n: _vocab(r.normalized_script) for n, r in sorted(other_rubrics.items())}
    aligned = {j for a in alignments for j in a.stt_ids}
    by_sentence: dict[int, list[Token]] = {}
    for t in stt_ptokens:
        by_sentence.setdefault(t.sentence, []).append(t)

    candidates: list[tuple[int, list[Token]]] = []
    for j in sorted(by_sentence):
        tokens = by_sentence[j]
        if j not in aligned:
            if _overlap(tokens, vocab) < UNALIGNED_SCRIPT_OVERLAP:
                candidates.append((j, tokens))
            continue
        if (
            not OFFSCRIPT_IN_SENTENCE
        ):  # 짝 있는 문장 안의 꼬리는 대부분 바꿔 말한 대본 내용이다 (config)
            continue
        run: list[Token] = []
        for t in [*tokens, None]:
            if t is not None and t.key not in vocab:
                run.append(t)
                continue
            if len(run) >= OFFSCRIPT_MIN_TOKENS:
                candidates.append((j, run))
            run = []

    spans = []
    for j, tokens in candidates:
        sentence = stt_norm.sentences[j]
        start, end = tokens[0].start - sentence.start, tokens[-1].end - sentence.start
        # 진행 말 정규식은 어미까지 봐야 하므로(감사 + 합니다) 구간 시작부터 문장 끝까지의 글에서 찾는다
        category, number = "off_script", None
        if _is_procedural(sentence.text[start:], len(tokens)):
            category = "procedural"
        elif len(tokens) >= OTHER_SLIDE_MIN_TOKENS:
            best = max(((_overlap(tokens, v), -n) for n, v in others.items()), default=(0.0, 0))
            if best[0] >= OTHER_SLIDE_OVERLAP:
                category, number = "other_slide", -best[1]
        spans.append(
            OffscriptSpan(
                id=f"O{len(spans) + 1}",
                stt_sentence_index=j,
                start=start,
                end=end,
                category=category,
                display_category=_display_category(category),
                source="rule",
                other_slide_number=number,
                tokens=len(tokens),
            )
        )
    state, reasons = _reasons(spans, len(stt_ptokens))
    return Offscript(state=state, reason_codes=reasons, spans=spans)


def mix_display(categories: dict[str, int]) -> list[MixItem]:
    """범주별 형태소 수 → 화면 목록.

    검증하지 않은 범주는 `off_script` 로 합치고 0 인 범주는 뺀다. 순서는 `MIX_CATEGORIES` 순이고
    `share` 는 반올림 3자리이며 합이 1.0 이 되도록 남는 차이를 가장 큰 칸에 얹는다.
    """
    merged = dict.fromkeys(MIX_CATEGORIES, 0)
    for category, count in categories.items():
        merged[category if category in SPEECH_MIX_VALIDATED else "off_script"] += count
    items = [(c, n) for c, n in merged.items() if n > 0]
    total = sum(n for _, n in items)
    if total == 0:
        return []
    shares = [round(n / total, 3) for _, n in items]
    biggest = max(range(len(items)), key=lambda i: (items[i][1], -i))
    shares[biggest] = round(shares[biggest] + (1.0 - sum(shares)), 3)
    return [MixItem(category=c, tokens=n, share=s) for (c, n), s in zip(items, shares, strict=True)]


def _repetitions(raw_stt: str | None) -> int:
    """원본 STT 에서 간투사만 지운 뒤 바로 반복한 말의 추가 횟수 합 ('그 그 그 결과' → 2)."""
    if not raw_stt:
        return 0
    text = FILLER.sub(" ", unicodedata.normalize("NFKC", raw_stt))
    return sum(len(m.group(0).split()) - 1 for m in REPEATED_WORD.finditer(text))


def build_speech_mix(
    offscript: Offscript, stt_ptokens: list[Token], raw_stt: str | None
) -> SpeechMix:
    """범주별 형태소 수 · 화면 목록. `raw_stt` 가 있으면 바로 반복한 말의 횟수를 센다(없으면 0).

    `script` 는 전체에서 대본 밖 구간을 뺀 나머지이고, 상태 · 이유는 `offscript` 와 같다.
    """
    total = len(stt_ptokens)
    categories = dict.fromkeys(MIX_CATEGORIES, 0)
    for span in offscript.spans:
        categories[span.category] += span.tokens
    categories["script"] = max(total - sum(categories.values()), 0)
    if total == 0:
        state, reasons = "not_applicable", ["NO_SPEECH"]
    else:
        state, reasons = offscript.state, list(offscript.reason_codes)
    return SpeechMix(
        state=state,
        reason_codes=reasons,
        tokens=total,
        categories=categories,
        display=mix_display(categories),
        repetitions=Repetitions(count=_repetitions(raw_stt)),
    )


def said_in_other_slides(
    evaluations_offscript: dict[int, tuple[Offscript, list[str]]],
    rubrics: dict[int, EvaluationRubric],
) -> dict[tuple[int, str], list[int]]:
    """{(장 번호, 핵심 내용 id): [그 내용을 말한 다른 장 번호]}.

    `evaluations_offscript` = {장 번호: (그 장의 대본 밖 구간 목록, 정규화 STT 문장 목록)}.
    `other_slide` 구간 글의 형태소가 가리킨 장 핵심 내용의 근거 문장 형태소를 `OTHER_SLIDE_OVERLAP`
    이상 덮으면 그 장의 그 내용을 이 장에서 말한 것으로 센다. 장 번호는 정렬하고 중복을 없앤다.
    """
    kp_tokens: dict[int, dict[str, set[str]]] = {}

    def keypoints(number: int) -> dict[str, set[str]]:
        if number not in kp_tokens:
            rubric = rubrics[number]
            by_sentence: dict[int, set[str]] = {}
            for t in positioned_tokens(normalize_script(rubric.normalized_script)):
                by_sentence.setdefault(t.sentence, set()).add(t.key)
            kp_tokens[number] = {
                kp.id: set().union(*(by_sentence.get(i, set()) for i in kp.sentence_indices))
                for kp in rubric.key_points
            }
        return kp_tokens[number]

    found: dict[tuple[int, str], set[int]] = {}
    for number in sorted(evaluations_offscript):
        offscript, sentences = evaluations_offscript[number]
        for span in offscript.spans:
            target = span.other_slide_number
            if span.category != "other_slide" or target is None or target not in rubrics:
                continue
            if target == number or not 0 <= span.stt_sentence_index < len(sentences):
                continue
            text = sentences[span.stt_sentence_index][span.start : span.end]
            said = {t.key for t in positioned_tokens(normalize_script(text))}
            for kp_id, keys in keypoints(target).items():
                if keys and len(keys & said) / len(keys) >= OTHER_SLIDE_OVERLAP:
                    found.setdefault((target, kp_id), set()).add(number)
    return {k: sorted(v) for k, v in sorted(found.items())}
