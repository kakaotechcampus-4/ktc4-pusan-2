"""1차 결과 정리·검증 (코드): 규칙 분석과 LLM 1차 분석을 합치고, 문제를 warnings 로 남긴다.

LLM 을 쓰지 않는다. 여기서 만든 초안과 경고가 `final.py` 최종 결론의 입력이 된다.
"""

import hashlib
import re
from datetime import UTC, datetime

from ..shared.facts import CriticalFact, extract_critical_facts, sentence_index
from ..shared.kiwi import get_kiwi
from ..shared.rubric import (
    IMPORTANCE_RANK,
    NAME_TYPES,
    ROLE_IMPORTANCE,
    EvaluationRubric,
    KeyPoint,
    Keyword,
    RubricMeta,
)
from ..shared.text import NormalizedScript, compact, normalize_script, overlaps
from ..version import RUBRIC_SCHEMA_VERSION
from .schemas import SlideScript, SlideSemanticAnalysis
from .semantic import semantic_config_hash


def importance_from_roles(roles: list[str]) -> str:
    """근거 문장 역할로 중요도를 정한다. LLM 이 중요도를 직접 고르지 않게 해서 호출마다 흔들리는 폭을 줄인다."""
    return max((ROLE_IMPORTANCE[r] for r in roles), key=IMPORTANCE_RANK.get, default="normal")


def _covers(outer: tuple[int, int], inner: tuple[int, int]) -> bool:
    return outer[0] <= inner[0] and inner[1] <= outer[1]


def term_spans(term: str, text: str) -> list[tuple[int, int]]:
    """용어 등장 위치. 영문 용어는 더 긴 영문 단어 안에서 잡지 않는다 (GPT ⊄ GPTs)."""
    pattern = re.escape(term)
    if re.match(r"[A-Za-z0-9]", term):
        pattern = r"(?<![A-Za-z0-9])" + pattern
    if re.search(r"[A-Za-z0-9]$", term):
        pattern += r"(?![A-Za-z0-9])"
    return [m.span() for m in re.finditer(pattern, text, flags=re.IGNORECASE)]


def _drop_nested(facts: list[CriticalFact]) -> list[CriticalFact]:
    """이름·용어 사실이 더 긴 이름·용어 안에 들어 있으면 그 위치를 지운다. 한 발화가 여러 번 채점되지 않게 가장 긴 것만 남긴다.
    (서울·대전 권역 안의 서울·대전 / 시계열 교차검증 안의 교차검증)
    수치 사실은 값으로 따로 검증하므로 용어 안에 있어도 지우지 않는다 ('2년 치 기록' 안의 2년)."""
    names = [f for f in facts if f.type in NAME_TYPES]
    kept = []
    for fact in facts:
        if fact.type not in NAME_TYPES:
            kept.append(fact)
            continue
        keep = [
            i
            for i, s in enumerate(fact.spans)
            if not any(
                o != s and _covers(o, s)
                for other in names
                if other is not fact
                for o in other.spans
            )
        ]
        if keep:
            fact.spans = [fact.spans[i] for i in keep]
            fact.sentence_indices = [fact.sentence_indices[i] for i in keep]
            kept.append(fact)
    return kept


def key_term_rejection(term: str, deck_texts: list[str]) -> str | None:
    """LLM key_term 을 Critical Fact 로 올리지 않을 이유. 올려도 되면 None.

    Critical Fact 는 STT 에 그 말이 그대로 나왔는지로 채점하므로, 다른 말로 바꿔 말해도 되는 말은 올리지 않는다.
    프롬프트로도 막지만 LLM 이 지키지 않을 때가 있어서 코드로 한 번 더 거른다.
    - 영문·숫자가 들어간 이름(SeatFlow, XGBoost)이나 Kiwi 가 고유명사(NNP)로 보는 말은 통과
    - 4어절 이상이면 용어가 아니라 구절(프로젝트 제목 등)이다: 글자 그대로 말하기를 기대하기 어렵다
    - 같은 발표의 2개 이상 슬라이드에 나오는 일반 명사는 이 발표의 공통 어휘다 (에이전트, 주간 리포트)
    """
    if len(term.split()) >= 4:
        return "phrase"
    if re.search(r"[A-Za-z0-9]", term) or any(t.tag == "NNP" for t in get_kiwi().tokenize(term)):
        return None
    if sum(bool(term_spans(term, text)) for text in deck_texts) >= 2:
        return "common_in_deck"
    return None


def merge_key_terms(
    key_points: list[KeyPoint],
    facts: list[CriticalFact],
    norm: NormalizedScript,
    deck_texts: list[str],
    warnings: list[str],
    filter_terms: bool = True,
) -> list[CriticalFact]:
    """LLM 의 key_terms 를 Critical Fact 로 합친다.

    이미 있는 사실이 덮지 않는 등장 위치가 남으면 source='llm' 인 term 으로 추가하고 (좌석 예약 시스템 ⊋ 좌석 예약),
    모든 위치가 덮여 있으면 그 사실을 그대로 쓴다 (SeatFlow = SeatFlow).
    대본에 없는 용어는 LLM 이 만들어 낸 것이므로 버리고 경고를 남긴다.
    바꿔 말해도 되는 용어(key_term_rejection)는 Key Point 의 key_terms 에는 남기되 사실로는 올리지 않는다.
    filter_terms=False 면 거르지 않는다 (최종 결론에 넘길 이름·용어 후보를 모을 때).
    """
    facts = list(facts)
    for kp in key_points:
        for term in kp.key_terms:
            spans = term_spans(term, norm.text)
            if not spans:
                warnings.append(f"key_term_not_found:{kp.id}:{term}")
                continue
            reason = key_term_rejection(term, deck_texts) if filter_terms else None
            if reason:
                warnings.append(f"key_term_not_fact:{kp.id}:{term}({reason})")
                continue
            fresh = [s for s in spans if not any(_covers(fs, s) for f in facts for fs in f.spans)]
            if fresh:
                facts.append(
                    CriticalFact(
                        type="term",
                        value=term,
                        normalized=term.casefold(),
                        spans=fresh,
                        sentence_indices=[sentence_index(norm, s[0]) for s in fresh],
                        source="llm",
                    )
                )
    facts = sorted(_drop_nested(facts), key=lambda f: f.spans[0][0])
    for i, fact in enumerate(facts, 1):
        fact.id = f"CF{i}"
    return facts


def _numeric_mentions(text: str) -> set[tuple[str, str]]:
    """문장에 나온 수치 사실의 (type, 정규형). Key Point content 가 어떤 수치를 말하는지 볼 때 쓴다."""
    return {
        (f.type, f.normalized)
        for f in extract_critical_facts(normalize_script(text))
        if f.type not in ("proper_noun", "term")
    }


def _says(kp: KeyPoint, fact: CriticalFact, kp_numbers: set[tuple[str, str]]) -> bool:
    """Key Point 가 이 사실을 직접 말하는가.

    - key_terms 로 직접 지목했으면 말한 것이다
    - content 에 같은 수치·이름이 있고, 그 사실이 Key Point 근거 구간 안에 나오면 말한 것이다
      (근거 구간 조건이 없으면 '서울시립도서관' 을 말하는 KP 에 '서울' 이 붙는다)
    """
    value = compact(fact.value)[0]
    if any(compact(t)[0] == value for t in kp.key_terms):
        return True
    if kp.source_span is not None and not any(overlaps(s, kp.source_span) for s in fact.spans):
        return False
    if fact.type not in ("proper_noun", "term"):
        return (fact.type, fact.normalized) in kp_numbers
    return value in compact(kp.content)[0] or any(value in compact(t)[0] for t in kp.key_terms)


def link_facts_to_key_points(
    key_points: list[KeyPoint], facts: list[CriticalFact], warnings: list[str]
) -> None:
    """Critical Fact ↔ Key Point 연결 (결정적).

    1) Key Point 가 그 사실을 직접 말하면 연결한다 (`_says`).
       한 문장을 두 KP 가 나눠 가져도, '약 1,240만 건으로 학습' 을 말한 KP 에만 숫자가 붙고 다른 KP 에는 안 붙는다.
    2) 어느 Key Point 도 직접 말하지 않는 사실은, 근거 구간이 그 사실을 품은 Key Point 가 하나뿐일 때만 연결한다.
    사실의 중요도는 연결된 Key Point 중 가장 높은 것을 물려받는다.
    content 에 대본에 없는 수치가 있으면 LLM 이 만들어 낸 것이므로 경고를 남긴다.
    """
    kp_numbers = {kp.id: _numeric_mentions(kp.content) for kp in key_points}
    script_numbers = {(f.type, f.normalized) for f in facts}
    for kp in key_points:
        kp.fact_ids = []
        for _, value in sorted(kp_numbers[kp.id] - script_numbers):
            warnings.append(f"unsupported_number_in_key_point:{kp.id}:{value}")

    for fact in facts:
        fact.key_point_ids, fact.importance = [], "normal"
        linked = [kp for kp in key_points if _says(kp, fact, kp_numbers[kp.id])]
        if not linked:
            holders = [
                kp
                for kp in key_points
                if kp.source_span and any(overlaps(s, kp.source_span) for s in fact.spans)
            ]
            linked = holders if len(holders) == 1 else []
        for kp in linked:
            kp.fact_ids.append(fact.id)
            fact.key_point_ids.append(kp.id)
            if IMPORTANCE_RANK[kp.importance] > IMPORTANCE_RANK[fact.importance]:
                fact.importance = kp.importance


def validate_rubric(
    key_points: list[KeyPoint], facts: list[CriticalFact], roles: list[str], warnings: list[str]
) -> None:
    """LLM 출력이 채점 기준으로 쓸 만한지 규칙으로 점검한다. 문제는 고치지 않고 경고로 남긴다."""
    n_claims = roles.count("claim")
    if n_claims > 1:
        warnings.append(f"too_many_claims:{n_claims}")
    covered = {i for kp in key_points for i in kp.sentence_indices}
    uncovered = [i for i, role in enumerate(roles) if role != "skip" and i not in covered]
    if uncovered:
        warnings.append(f"uncovered_sentences:{','.join(map(str, uncovered))}")
    n_critical = sum(kp.importance == "critical" for kp in key_points)
    if not key_points:
        warnings.append("no_key_points")
    elif n_critical == 0:
        warnings.append("no_critical_key_point")  # 팀 소개처럼 나열만 하는 슬라이드라면 정상
    elif n_critical > 1:
        warnings.append(f"too_many_critical:{n_critical}")
    if len(key_points) > 6:
        warnings.append(f"too_many_key_points:{len(key_points)}")
    for kp in key_points:
        if kp.source_span is None:
            warnings.append(f"key_point_without_sentence:{kp.id}")
    unlinked = [f.id for f in facts if not f.key_point_ids]
    if unlinked:
        warnings.append(f"unlinked_facts:{','.join(unlinked)}")


def resolve_roles(labels: list, n: int, warnings: list[str]) -> list[str]:
    """LLM 이 표시한 문장 역할을 문장 순서대로 편다. 번호가 맞지 않거나 빠진 문장은 detail 로 두고 경고한다."""
    labeled = {label.id: label.role for label in labels if 0 <= label.id < n}
    missing = [i for i in range(n) if i not in labeled]
    if missing:
        warnings.append(f"missing_sentence_roles:{','.join(map(str, missing))}")
    return [labeled.get(i, "detail") for i in range(n)]


def make_key_points(
    drafts: list, roles: list[str], norm: NormalizedScript, warnings: list[str]
) -> list[KeyPoint]:
    """Key Point 초안 → KeyPoint. 문장 번호로 근거 원문·위치를, 근거 문장 역할로 중요도를 정한다."""
    n = len(norm.sentences)
    key_points = []
    for i, draft in enumerate(drafts, 1):
        ids = sorted({j for j in draft.sentence_ids if 0 <= j < n})
        bad = sorted(set(draft.sentence_ids) - set(ids))
        if bad:
            warnings.append(f"invalid_sentence_id:KP{i}:{','.join(map(str, bad))}")
        sents = [norm.sentences[j] for j in ids]
        key_points.append(
            KeyPoint(
                id=f"KP{i}",
                content=draft.content,
                importance=importance_from_roles([roles[j] for j in ids]),
                sentence_indices=ids,
                source_quote=" ".join(x.text for x in sents),
                source_span=(sents[0].start, sents[-1].end) if sents else None,
                key_terms=list(getattr(draft, "key_terms", [])),
                fact_ids=[],
            )
        )
    return key_points


def new_rubric(
    script_name: str,
    slide: SlideScript,
    norm: NormalizedScript,
    keywords: list[Keyword],
    roles: list[str],
    core_claim: str,
    key_points: list[KeyPoint],
    facts: list[CriticalFact],
    warnings: list[str],
    model: str,
    final_config: str = "",
    **review,
) -> EvaluationRubric:
    config_hash = semantic_config_hash(model)
    rubric_key = f"{norm.content_hash}|{config_hash}|{final_config}|{RUBRIC_SCHEMA_VERSION}"
    return EvaluationRubric(
        meta=RubricMeta(
            rubric_id=hashlib.sha256(rubric_key.encode()).hexdigest()[:16],
            script_name=script_name,
            slide_number=slide.slide_number,
            content_hash=norm.content_hash,
            rubric_schema_version=RUBRIC_SCHEMA_VERSION,
            llm_model=model,
            llm_config_hash=config_hash,
            final_config_hash=final_config,
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        ),
        normalized_script=norm.text,
        sentences=norm.sentences,
        sentence_roles=roles,
        core_claim=core_claim,
        key_points=key_points,
        critical_facts=facts,
        keywords=keywords,
        warnings=warnings,
        **review,
    )


def draft_rubric(
    script_name: str,
    slide: SlideScript,
    norm: NormalizedScript,
    facts: list[CriticalFact],
    keywords: list[Keyword],
    semantics: SlideSemanticAnalysis,
    deck_texts: list[str],
    model: str,
) -> EvaluationRubric:
    """1차 결과 정리·검증 (코드): 규칙 분석과 LLM 1차 분석을 합치고, 문제를 warnings 로 남긴다.

    이 초안과 경고가 6장 최종 결론(LLM)의 입력이 된다.
    deck_texts 는 같은 발표의 정규화된 슬라이드 대본들이다 (key_term 이 발표 공통 어휘인지 볼 때 쓴다).
    """
    warnings: list[str] = []
    roles = resolve_roles(semantics.sentence_roles, len(norm.sentences), warnings)
    key_points = make_key_points(semantics.key_points, roles, norm, warnings)
    facts = merge_key_terms(
        key_points, [f.model_copy(deep=True) for f in facts], norm, deck_texts, warnings
    )
    link_facts_to_key_points(key_points, facts, warnings)
    validate_rubric(key_points, facts, roles, warnings)
    return new_rubric(
        script_name,
        slide,
        norm,
        keywords,
        roles,
        semantics.core_claim,
        key_points,
        facts,
        warnings,
        model,
    )
