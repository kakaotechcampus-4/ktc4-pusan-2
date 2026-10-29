"""전달 단위: 문장마다 청중이 기억해야 할 정보 하나(단위)를 LLM 이 나누고, 코드가 검증한다."""

import hashlib

from ..shared.kiwi import get_kiwi
from ..shared.llm_step import config_hash
from ..shared.rubric import ContentUnit, EvaluationRubric
from ..shared.text import Sentence, compact, overlaps
from .config import QUOTE_MATCH, QUOTE_TAGS
from .prompts.units import UNIT_SYSTEM_PROMPT
from .schemas import SlideScript, SlideUnits


def unit_config_hash(model: str) -> str:
    return config_hash(
        {"model": model, "prompt": UNIT_SYSTEM_PROMPT, "schema": SlideUnits.model_json_schema()}
    )


def unit_user_message(slide: SlideScript, rubric: EvaluationRubric) -> str:
    """입력: 문장 번호 + 최종 문장 역할(skip 은 나누지 않음) + 문장 속 핵심 수치·이름."""
    lines = [f"[슬라이드 {slide.slide_number}]"]
    for s, role in zip(rubric.sentences, rubric.sentence_roles, strict=False):
        if role == "skip":
            lines.append(f"[S{s.index}] (나누지 않음) {s.text}")
            continue
        facts = [f.value for f in rubric.critical_facts if s.index in f.sentence_indices]
        lines.append(
            f"[S{s.index}] {s.text}" + (f" (핵심 수치·이름: {', '.join(facts)})" if facts else "")
        )
    return "\n".join(lines)


def extract_units(message: str, unit_llm) -> SlideUnits:
    """LLM API (전달 단위): 문장마다 전달 단위를 나눈다. 슬라이드당 한 번."""
    return unit_llm.invoke([("system", UNIT_SYSTEM_PROMPT), ("user", message)])


def _find_in_sentence(
    quote: str, sentence: Sentence, text: str
) -> tuple[tuple[int, int] | None, bool]:
    """quote 를 그 문장 안에서 찾는다. (위치, 그대로 찾았는가). 위치는 정규화 대본 기준.

    1) 공백·문장부호를 무시하고 그대로 찾는다
    2) 없으면 quote 의 내용 형태소가 문장에 QUOTE_MATCH 이상 있을 때 그 범위로 받는다.
       LLM 이 나열 항목을 나누면서 표현을 조금 고쳐 옮기는 경우가 있다 ('서울과 대전의 대학' → '서울의 대학')
    """
    flat, index = compact(text[sentence.start : sentence.end])

    def to_span(a, b):
        return (sentence.start + index[a], sentence.start + index[b - 1] + 1)

    target = compact(quote)[0]
    k = flat.find(target) if target else -1
    if k >= 0:
        return to_span(k, k + len(target)), True
    words = [
        w
        for w in (compact(t.form)[0] for t in get_kiwi().tokenize(quote) if t.tag in QUOTE_TAGS)
        if w
    ]
    found = {w: [m for m in range(len(flat)) if flat.startswith(w, m)] for w in set(words)}
    hits = [w for w in words if found[w]]
    if not words or len(hits) / len(words) < QUOTE_MATCH:
        return None, False
    # 가장 긴 형태소의 위치를 기준으로, 나머지는 그 근처의 등장 위치를 고른다
    anchor = found[max(hits, key=len)][0]
    picks = [min(found[w], key=lambda m: abs(m - anchor)) for w in hits]
    return to_span(min(picks), max(p + len(w) for p, w in zip(picks, hits, strict=False))), False


def build_content_units(
    rubric: EvaluationRubric, slide_units: SlideUnits | None, warnings: list[str]
) -> list[ContentUnit]:
    """LLM 이 나눈 전달 단위를 검증해 평가 기준에 넣는다 (코드).

    - quote 가 그 문장에 없으면(내용 형태소도 거의 없으면) 버린다 (LLM 이 지어낸 단위)
    - 남은 단위가 없으면 문장 전체를 단위 하나로 둔다 (단위를 나누기 전과 같은 판정)
    - 문장의 수치·이름이 어느 단위(quote 위치나 단위 내용)에도 안 들어갔으면 그 사실을 단위로 보탠다 (수치는 반드시 따로 판정되게)
    """
    drafts = {d.sentence_id: d.units for d in (slide_units.sentences if slide_units else [])}
    units: list[ContentUnit] = []
    for s, role in zip(rubric.sentences, rubric.sentence_roles, strict=False):
        if role == "skip":
            continue
        own: list[tuple[str, str, tuple[int, int], str]] = []
        for d in drafts.get(s.index, []):
            span, exact = _find_in_sentence(d.quote, s, rubric.normalized_script)
            if span is None:
                warnings.append(f"unit_quote_not_found:S{s.index}:{d.quote}")
                continue
            if not exact:
                warnings.append(f"unit_quote_loose:S{s.index}:{d.quote}")
            if all(d.text != u[0] for u in own):
                own.append((d.text, d.quote, span, "llm"))
        if not own:
            warnings.append(f"units_not_split:S{s.index}")
            own = [(s.text, s.text, (s.start, s.end), "sentence")]
        facts_here = [
            (f, [sp for sp, i in zip(f.spans, f.sentence_indices, strict=False) if i == s.index])
            for f in rubric.critical_facts
            if s.index in f.sentence_indices
        ]

        def holds(fact, spans, unit):
            return (
                any(overlaps(sp, unit[2]) for sp in spans)
                or compact(fact.value)[0] in compact(unit[0])[0]
            )

        for fact, spans in facts_here:
            if not any(holds(fact, spans, u) for u in own):
                warnings.append(f"unit_missing_fact:S{s.index}:{fact.value}")
                own.append((fact.value, fact.value, spans[0], "fact"))
        if len(own) > 6:
            warnings.append(f"too_many_units:S{s.index}:{len(own)}")
        for k, unit in enumerate(sorted(own, key=lambda u: u[2]), 1):
            text, quote, span, source = unit
            fact_ids = [f.id for f, spans in facts_here if holds(f, spans, unit)]
            units.append(
                ContentUnit(
                    id=f"S{s.index}-U{k}",
                    sentence_index=s.index,
                    text=text,
                    quote=quote,
                    span=span,
                    fact_ids=fact_ids,
                    source=source,
                )
            )
    return units


def attach_units(
    rubric: EvaluationRubric, slide_units: SlideUnits | None, model: str
) -> EvaluationRubric:
    """평가 기준에 전달 단위를 붙이고, rubric_id 에 전달 단위 설정을 반영한다."""
    warnings: list[str] = []
    rubric.content_units = build_content_units(rubric, slide_units, warnings)
    rubric.warnings += warnings
    rubric.meta.unit_config_hash = unit_config_hash(model) if slide_units is not None else ""
    key = f"{rubric.meta.rubric_id}|{rubric.meta.unit_config_hash}"
    rubric.meta.rubric_id = hashlib.sha256(key.encode()).hexdigest()[:16]
    return rubric
