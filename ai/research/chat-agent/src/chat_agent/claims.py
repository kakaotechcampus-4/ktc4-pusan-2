"""사용자 주장 판정 — 코드가 정한다 (기능 문서 6-3 '발표 전문가 원칙과 주장 판정', 7-8 L6, 5-2 `Assessment`).

- 누가 쓰나: loop.py(판단을 답보다 먼저 확정하는 단계) · checks.py(첫 문장 · 동의 문장 검사의 입력) ·
  tools/(목표 요청 충돌 판정).
- 입력: 모델이 제안한 판단 초안(`AssessmentDraft`) · 사용자 말 원문 · 읽어 둔 Take 스냅샷들 · 앞 판단(`prior`) · 근거 등록부.
- 출력: 코드가 확정한 `schemas.Assessment`(+ 판정 근거 · 첫 문장 · 진단 값, `FinalizeResult`).
  `MEASURED` 판정은 모델 판정을 버리고 코드가 정한다(`verdict_source:"CODE"`). 맞장구는 프롬프트가 아니라 여기서 막는다.
- 처리 순서
  1. `split_check`: 형식 위반(원문에 없는 `quote` · 빈 `quote` · 모양 틀림) · 덮이지 않은 측정 절 · 유형 · 대상 불일치를
     찾아 문제 코드로 돌려준다. 호출한 쪽(loop)이 1회 고치게 한다(6-1).
  2. `code_verdict`: 판정 대상 Take를 정하고(가리킨 Take → 지금 보던 Take → 가장 최근 완료 Take) 6-3 판정 표로
     Take별 판정 → 여러 Take는 `relation`(`ALL` · `SOME` · `COMPARE`)으로 모은다. 읽지 못했거나 미측정 · 생략 ·
     자료 부족이면 `UNVERIFIABLE`(빈 문제 목록을 '문제 없음'으로 읽지 않는다).
  3. `finalize`: 판단마다 `claim_key` · `claim_link_key` · `evidence_basis`를 코드가 채우고, 압력(같은 주장 · 같은 근거)에는
     앞 판정을 유지하고, 근거가 바뀌면 `supersedes`로 앞 판단을 잇고, 첫 문장을 코드 템플릿으로 그린다.
- 고칠 때
  - 판정 표(`_ONE_*` 함수)는 6-3 표와 글자까지 맞춘다. 표를 바꾸면 tests/unit/test_claims.py의 표 시험을 같이 고친다.
  - 낱말 사전(측정 항목 · 문제 방향 · 상대 대상)은 config.py에만 둔다. 여기서 낱말을 새로 만들지 않는다
    (`DIM_LABEL` · `METRIC_LABEL` · `ISSUE_LABEL`은 근거 문구용 표시 이름이라 여기 둔다).
  - 키(`claim_key` · `claim_link_key`)의 재료를 바꾸면 이미 저장된 앞 판단과 맞지 않으니 `version.FEATURE_VERSION`을 올린다.
  - 값은 표시 자리수로 반올림해 비교하되 반올림은 `Decimal` 반올림-올림(ROUND_HALF_UP)이다(부동소수 0.285 → 29%).
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from . import templates
from .config import ChatAgentConfig
from .evidence import EvidenceRegistry, RecordVersion, canonical_json
from .goals import Comparison, compare_takes, record_read_value
from .schemas import (
    Assessment,
    AssessmentType,
    Number,
    Polarity,
    Relation,
    Speaker,
    Subject,
    TakeSnapshot,
    Verdict,
    VerdictSource,
)
from .values import (
    ISSUE_AREA,
    read_dimension_state,
    read_metric,
    read_problems,
)

VersionOf = Callable[[TakeSnapshot], RecordVersion]

#: 대본 사용 주장의 지표 이름(모델이 명시한다). 지표 표에는 없는 가상 이름이다
SCRIPT_USE_METRIC = "script_mode"
_SCRIPT_USE_RE = re.compile(r"(대본|원고)\s*(을|를)?\s*(보고|보면서|보며|켜고|켜 놓고|띄우고)")
CLAIM_OPS = ("OVERALL", "ISSUE", "VALUE", "CHANGE")

#: 근거 문구용 표시 이름
DIM_LABEL: dict[str, str] = {
    "audience_gaze": "청중 시선",
    "script_independence": "대본 의존도",
    "pacing_rhythm": "말 속도·시간 배분",
    "vocal_delivery": "목소리 전달",
    "fluency": "군더더기",
    "content_delivery": "내용 전달",
}
METRIC_LABEL: dict[str, str] = {
    "audience_ratio": "청중 시선",
    "script_ratio": "대본 응시",
    "reading_ratio": "대본 읽기 비율",
    "reading_runs": "대본 읽기 횟수",
    "screen_ratio": "화면 응시",
    "away_ratio": "시선 이탈",
    "cpm": "말 속도",
    "pause_count": "멈춤 횟수",
    "long_silence_count": "긴 침묵",
    "duration_ms": "발표 시간",
    "slide_duration_ms": "장 시간",
    "voice_diff_db": "소리 크기",
    "low_ratio": "작은 소리 비율",
    "filler_per_min": "분당 군더더기",
    "filler_count": "군더더기 수",
    "content_coverage": "내용 전달률",
    "critical_fact_accuracy": "핵심 사실 정확도",
}
STATE_WORD: dict[str, str] = {
    "STRENGTH": "강점",
    "PRIORITY": "우선 개선",
    "IMPROVED": "나아짐",
    "STABLE": "유지",
    "NOT_EVALUABLE": "평가 불가",
}
ISSUE_LABEL: dict[str, str] = {
    "GAZE_LOW_EYE_CONTACT": "청중 시선 부족",
    "GAZE_AWAY": "시선 이탈",
    "GAZE_ON_SCRIPT": "대본 응시",
    "GAZE_ON_SCREEN": "화면 응시",
    "PACE_FAST": "말이 빠름",
    "PACE_SLOW": "말이 느림",
    "LONG_SILENCE": "긴 침묵",
    "BEHIND_SCHEDULE": "시간 배분 지연",
    "AHEAD_OF_SCHEDULE": "시간 배분 앞섬",
    "SLIDE_OVER": "장 시간 초과",
    "TIME_OVER": "발표 시간 초과",
    "FINAL_MINUTE": "마지막 1분",
    "VOLUME_LOW": "목소리가 작음",
    "FILLER_FREQUENT": "군더더기 잦음",
    "CONTENT": "빠뜨리거나 틀린 핵심 내용",
}
#: 같은 지표의 반대 방향 문제 (6-3 ISSUE '반대 방향 문제만 있음')
_OPPOSITE_ISSUE = {"PACE_FAST": "PACE_SLOW", "PACE_SLOW": "PACE_FAST"}
#: 문제 낱말 뒤에서 그 말을 부정하는 표현("빠르지 않았어" · "빠르진 않아" · "빨랐던 건 아니죠").
#: 문제 주장(ISSUE)의 부정은 극성이 아니라 이 말로 가린다: 모델은 "느렸지?"처럼 문제를 말한 부정적 평가에도 NEGATIVE 를 붙인다
_NEG_AFTER = re.compile(r"않|지\s*못|진\s*못|아니|아닌")
#: 문제 낱말 바로 앞의 부정("안 빨랐어" · "안빨랐어")
_NEG_BEFORE = re.compile(r"(?:^|\s)안\s?$")
#: 절을 끊는 말: 부정이 이 말 너머의 다른 절에 걸리지 않게 한다("안정적이지 않고 빨랐어"의 '않'은 '빨랐'을 부정하지 않음)
_NEG_CLAUSE_BREAK = re.compile(r"[,.!?]|(?:지만|는데|은데|고)\s")
#: 차원만 말하고 지표를 말하지 않은 값 · 비교 주장에서 쓰는 대표 지표
PRIMARY_METRIC: dict[str, str] = {
    "audience_gaze": "audience_ratio",
    "script_independence": "script_ratio",
    "pacing_rhythm": "cpm",
    "vocal_delivery": "voice_diff_db",
    "fluency": "filler_per_min",
    "content_delivery": "content_coverage",
}

#: 값 주장의 원문 단위 → 지표 기본 단위로 바꾸는 곱 (6-3 VALUE). 키는 지표 단위(`cfg.metric_units`)
_UNIT_FACTORS: dict[str, dict[str, Decimal]] = {
    "ratio": {
        "%": Decimal("0.01"),
        "퍼센트": Decimal("0.01"),
        "percent": Decimal("0.01"),
        "프로": Decimal("0.01"),
        "ratio": Decimal(1),
        "비율": Decimal(1),
    },
    "ms": {
        "ms": Decimal(1),
        "밀리초": Decimal(1),
        "초": Decimal(1000),
        "s": Decimal(1000),
        "sec": Decimal(1000),
        "분": Decimal(60000),
        "min": Decimal(60000),
    },
    "cpm": {
        "cpm": Decimal(1),
        "자/분": Decimal(1),
        "글자/분": Decimal(1),
        "자": Decimal(1),
        "글자": Decimal(1),
    },
    "per_min": {
        "회/분": Decimal(1),
        "개/분": Decimal(1),
        "번/분": Decimal(1),
        "per_min": Decimal(1),
        "/분": Decimal(1),
    },
    "count": {
        "회": Decimal(1),
        "번": Decimal(1),
        "개": Decimal(1),
        "건": Decimal(1),
        "count": Decimal(1),
    },
    "dB": {"db": Decimal(1), "데시벨": Decimal(1)},
}


# ── 작은 도구 ─────────────────────────────────────────────────────────────


def _dec(x: float | int | Decimal) -> Decimal:
    return x if isinstance(x, Decimal) else Decimal(str(x))


def round_display(x: float | int | Decimal, decimals: int) -> Decimal:
    """화면 표시 자리수로 반올림(ROUND_HALF_UP). `decimals`가 음수면 10의 거듭제곱 자리(-3 → 천 단위).

    예: round_display(0.285, 2) → Decimal('0.29')  (부동소수 0.285가 0.28499…여도 문자열 기준으로 올린다)
    """
    return _dec(x).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)


def _fmt(d: Decimal) -> str:
    if d == d.to_integral_value():
        return str(int(d))
    return format(d.normalize(), "f")


def to_base_unit(
    value: float | int, value_unit: str | None, metric: str, cfg: ChatAgentConfig
) -> Decimal | None:
    """값 주장의 원문 단위를 지표의 기본 단위로 바꾼다(31 `%` → 0.31, 2 `분` → 120000 ms).

    Returns:
        바꾼 값. 단위를 정하지 못하면(없음 · 이 지표에 맞지 않는 단위) None → 호출한 쪽이 `UNVERIFIABLE`로 둔다.
    """
    kind = cfg.metric_units.get(metric)
    if kind is None or not value_unit:
        return None
    factor = _UNIT_FACTORS.get(kind, {}).get(value_unit.strip().lower())
    if factor is None:
        factor = _UNIT_FACTORS.get(kind, {}).get(value_unit.strip())
    if factor is None:
        return None
    return _dec(value) * factor


def format_metric_value(metric: str, value: float | int, cfg: ChatAgentConfig) -> str:
    """근거 문구용 값 표시(화면 표시 자리수). 비율 0.31 → '31%', ms 92000 → '92초', cpm 300 → '300자/분'."""
    kind = cfg.metric_units.get(metric)
    dec = cfg.display_decimals.get(kind or "", 2)
    if kind == "ratio":
        return _fmt(round_display(value, dec) * 100) + "%"
    if kind == "ms":
        return _fmt(round_display(value, dec) / 1000) + "초"
    shown = _fmt(round_display(value, dec))
    return shown + {"cpm": "자/분", "per_min": "회/분", "count": "회", "dB": "dB"}.get(
        kind or "", ""
    )


def normalize_quote(quote: str) -> str:
    """키용 원문 정규화: 앞뒤 공백 제거 · 연속 공백 하나로 · 끝 문장부호 제거."""
    q = " ".join(quote.split())
    return q.rstrip(".,!?~…。！？ ").strip()


def _hash(prefix: str, payload: Any) -> str:
    return prefix + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:16]


# ── 입력 모양: 모델이 제안하는 판단 초안 ─────────────────────────────────


class DraftSubject(BaseModel):
    """판단 초안의 대상. `take_ids`는 비어도 된다(코드가 가리킨 Take → 지금 보던 Take → 최근 완료 Take로 정한다)."""

    model_config = ConfigDict(extra="ignore")

    dimension: str | None = None
    metric: str | None = None
    slide_number: int | None = None
    take_ids: list[str] = Field(default_factory=list)
    #: 사용자가 Take 번호로 가리켰을 때(예: "Take 5"). 코드가 가진 Take 중 번호가 같은 것으로 풀어 쓴다
    take_numbers: list[int] = Field(default_factory=list)
    relation: Relation | None = None


class AssessmentDraft(BaseModel):
    """모델이 제안하는 판단 하나(6-3). 코드가 검사 · 판정 · 키 · 근거 지문을 채워 `Assessment`로 확정한다.

    - `claim_op`는 `str`이다: 넷(`OVERALL` · `ISSUE` · `VALUE` · `CHANGE`) 밖이면 코드가 `UNVERIFIABLE`로 둔다(6-3 먼저 검사 2).
    - `evidence_ids`는 비측정 유형(`PREFERENCE` · `REQUEST`)이 기댄 근거다. `MEASURED`에서는 무시한다(코드가 정한다).
    - `verdict`는 `MEASURED`에서는 버려지고, 비측정 유형에서는 모델 판정을 유지한다.
    """

    model_config = ConfigDict(extra="ignore")

    assessment_id: str | None = None
    quote: str
    type: AssessmentType
    speaker: Speaker = "USER"
    speaker_label: str | None = None
    claim_op: str | None = None
    subject: DraftSubject = Field(default_factory=DraftSubject)
    value: Number | None = None
    value_unit: str | None = None
    polarity: Polarity = "NEUTRAL"
    verdict: Verdict = "UNVERIFIABLE"
    reason: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class Problem:
    """`split_check`가 찾은 문제 하나.

    - `code`: `EMPTY_QUOTE` · `QUOTE_NOT_IN_MESSAGE` · `CLAIM_OP_ON_NON_MEASURED`(형식 위반),
      `UNCOVERED_CLAUSE`(측정 절을 덮는 판단이 없음), `TYPE_MISMATCH`(내 Take의 사실을 말하는 절인데 `MEASURED`가 아님),
      `TARGET_MISMATCH`(초안의 차원이 원문의 측정 낱말이 가리키는 차원과 다름), `MIXED_QUOTE`(한 `quote`가 서로 다른
      차원의 측정 절을 함께 덮음: 주장마다 판단이 하나씩 있어야 한다).
    - `quote`: 문제의 글(판단의 `quote` 또는 덮이지 않은 절).
    - `draft_index`: 입력 `drafts`에서의 위치(절 문제는 None).
    """

    code: str
    quote: str
    draft_index: int | None = None


FORMAT_PROBLEMS = frozenset({"EMPTY_QUOTE", "QUOTE_NOT_IN_MESSAGE", "CLAIM_OP_ON_NON_MEASURED"})
#: 고치기 뒤에도 남으면 `finalize`가 모델 판정을 버리고 `UNVERIFIABLE`로 두는 문제(초안은 형식은 맞다)
FORCED_UNVERIFIABLE = frozenset({"TYPE_MISMATCH", "TARGET_MISMATCH", "MIXED_QUOTE"})

# ── 먼저 검사: 절 나누기 · 덮기 · 유형 · 대상 ─────────────────────────────

_TAKE_REF = re.compile(r"(?:take|테이크)\s*\d+|\d+\s*번째", re.IGNORECASE)
_SLIDE_REF = re.compile(r"\d+\s*(?:번\s*)?장|슬라이드\s*\d+")
_PAST_CHARS = "았었였했랐렸겼웠봤됐"


def _measure_hits(text: str, cfg: ChatAgentConfig, offset: int = 0) -> list[tuple[int, int, str]]:
    """글 속 측정 항목 낱말의 (시작, 끝, 차원). 제외 낱말(예: '실시간') 안에서 맞은 것은 세지 않는다.

    제외 낱말은 같은 길이의 공백으로 덮어 위치를 보존한다.
    """
    masked = text
    for ex in cfg.measure_word_exclusions:
        masked = masked.replace(ex, " " * len(ex))
    hits: list[tuple[int, int, str]] = []
    for word, dim in cfg.measure_words.items():
        i = masked.find(word)
        while i != -1:
            hits.append((offset + i, offset + i + len(word), dim))
            i = masked.find(word, i + 1)
    return hits


def _has_measure_word(text: str, cfg: ChatAgentConfig) -> bool:
    """측정 항목 낱말이 있는가. 제외 낱말(예: '실시간') 안에서만 맞은 것은 세지 않는다."""
    return bool(_measure_hits(text, cfg))


#: 절을 가르는 연결 표현. '시선이 좋았지만 속도가 빨랐어'처럼 한 문장 안의 다른 주장을 나눈다(6-3 먼저 검사 1)
_CLAUSE_CUT = re.compile(r"(?:그리고|지만|는데|은데|으며|고|며)(?=\s)")


def _clauses(message: str) -> list[tuple[int, int, str]]:
    """사용자 말을 절로 나눈다: 문장부호 · 쉼표 · '~고 · ~지만 · ~는데 · ~며 · 그리고' 연결로 자르고 (시작, 끝, 글)을 돌려준다."""
    out: list[tuple[int, int, str]] = []
    for m in re.finditer(r"[^.!?。！？,，;\n]+", message):
        seg_start, seg = m.start(), m.group()
        cuts = [0] + [c.end() for c in _CLAUSE_CUT.finditer(seg)] + [len(seg)]
        for a, b in zip(cuts, cuts[1:], strict=False):
            part = seg[a:b]
            text = part.strip()
            if not text:
                continue
            lead = len(part) - len(part.lstrip())
            start = seg_start + a + lead
            out.append((start, start + len(text), text))
    return out


def _quote_spans(message: str, quote: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    if not quote:
        return spans
    i = message.find(quote)
    while i != -1:
        spans.append((i, i + len(quote)))
        i = message.find(quote, i + 1)
    return spans


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _states_my_fact(clause: str, draft: AssessmentDraft) -> bool:
    """이 절이 '내 Take의 사실'(Take 번호 · 장 번호 · 과거형 서술)을 말하는데 유형이 MEASURED가 아닌가."""
    if draft.type == "REQUEST":
        return False  # 행동 요청은 그 유형을 유지한다(6-3 먼저 검사 2)
    ref = bool(_TAKE_REF.search(clause) or _SLIDE_REF.search(clause))
    past = any(ch in clause for ch in _PAST_CHARS)
    if draft.speaker == "OTHER":
        return ref or past  # 다른 사람의 평가 인용도 내 Take에 대한 측정 주장이다
    if draft.type == "KNOWLEDGE":
        return ref  # 발표법 주장은 과거형이어도('~라고 했어') 유형을 유지한다
    return ref or past  # PREFERENCE


def split_check(
    user_message: str, drafts: Sequence[AssessmentDraft], cfg: ChatAgentConfig
) -> tuple[list[AssessmentDraft], list[Problem]]:
    """판단 초안의 형식 · 덮기 · 유형을 검사한다(6-3 '먼저 검사' 1 · 2).

    Args:
        user_message: 사용자 말 원문. `quote`는 이 글의 부분 문자열이어야 한다.
        drafts: 모델이 낸 초안들.
        cfg: 측정 항목 낱말(`measure_words`)을 쓴다.

    Returns:
        (valid_drafts, problems).
        - valid_drafts: 형식 위반이 없는 초안(`TYPE_MISMATCH`인 초안은 형식은 맞아서 남는다. 고치기 뒤에도 남아 있으면
          `finalize`가 `UNVERIFIABLE`로 둔다).
        - problems: 문제 코드 목록(`Problem`). loop는 이를 모델에게 1회 고치게 한다.
          측정 항목 낱말이 든 절마다 그 절과 겹치는 `quote`가 있는 유효 초안이 있어야 한다(`UNCOVERED_CLAUSE`).
    """
    valid, problems, _ = _split_check_indexed(user_message, drafts, cfg)
    return valid, problems


def _split_check_indexed(
    user_message: str, drafts: Sequence[AssessmentDraft], cfg: ChatAgentConfig
) -> tuple[list[AssessmentDraft], list[Problem], list[int]]:
    """`split_check`의 본체. 유효 초안의 입력 위치(`drafts`에서의 index)도 함께 돌려준다."""
    valid: list[AssessmentDraft] = []
    valid_idx: list[int] = []
    problems: list[Problem] = []
    for i, d in enumerate(drafts):
        if not d.quote.strip():
            problems.append(Problem("EMPTY_QUOTE", d.quote, i))
        elif d.quote not in user_message:
            problems.append(Problem("QUOTE_NOT_IN_MESSAGE", d.quote, i))
        elif d.type != "MEASURED" and d.claim_op is not None:
            problems.append(Problem("CLAIM_OP_ON_NON_MEASURED", d.quote, i))
        else:
            valid.append(d)
            valid_idx.append(i)

    hits_all = _measure_hits(user_message, cfg)
    clauses = [
        (start, end, text, [h for h in hits_all if h[0] >= start and h[1] <= end])
        for start, end, text in _clauses(user_message)
    ]
    covers: dict[int, set[int]] = {}  # 초안 위치 → 덮은 측정 절(위치)
    flagged: set[int] = set()
    for i, d in zip(valid_idx, valid, strict=True):
        # 원문과 초안의 대상 대조: quote 속 측정 낱말이 가리키는 차원이 초안의 차원과 다르면 대상 불일치
        q_hits = _measure_hits(d.quote, cfg)
        q_dims = {h[2] for h in q_hits}
        if d.type == "MEASURED" and q_dims:
            dim = d.subject.dimension or (
                cfg.dimension_of_metric(d.subject.metric) if d.subject.metric else None
            )
            if dim in cfg.dimensions and dim not in q_dims:
                problems.append(Problem("TARGET_MISMATCH", d.quote, i))
                flagged.add(i)
        # 덮기: quote 가 절 안에 있거나 절이 quote 안에 있고, 절의 측정 낱말을 quote 가 덮어야 한다
        for q in _quote_spans(user_message, d.quote):
            hit_clauses = [
                ci
                for ci, (cs, ce, _, hits) in enumerate(clauses)
                if hits
                and ((cs <= q[0] and q[1] <= ce) or (q[0] <= cs and ce <= q[1]))
                and any(_overlaps(q, (h[0], h[1])) for h in hits)
            ]
            dims = {
                h[2] for ci in hit_clauses for h in clauses[ci][3] if _overlaps(q, (h[0], h[1]))
            }
            if len(hit_clauses) > 1 and len(dims) > 1:
                # 한 quote 가 서로 다른 차원의 주장을 함께 덮으면 어느 쪽도 덮은 것으로 보지 않는다(주장마다 판단 하나)
                if i not in flagged:
                    problems.append(Problem("MIXED_QUOTE", d.quote, i))
                    flagged.add(i)
                continue
            covers.setdefault(i, set()).update(hit_clauses)
    for ci, (_, _, text, _) in enumerate(clauses):
        if not clauses[ci][3]:
            continue
        covering = [
            (i, d) for i, d in zip(valid_idx, valid, strict=True) if ci in covers.get(i, ())
        ]
        if not covering:
            problems.append(Problem("UNCOVERED_CLAUSE", text, None))
            continue
        for i, d in covering:
            if d.type != "MEASURED" and _states_my_fact(text, d):
                problems.append(Problem("TYPE_MISMATCH", d.quote, i))
    return valid, problems, valid_idx


# ── 코드 판정 ─────────────────────────────────────────────────────────────


@dataclass
class CodeVerdict:
    """`code_verdict`의 결과.

    - `verdict` · `verdict_source`: 확정 판정과 출처(`MEASURED`는 `CODE`, 비측정은 모델 판정 유지라 `MODEL`).
    - `evidence_ids`: 판정에 쓴 근거(RECORD · COMPUTED). `evidence_phrase`: 첫 문장에 들어갈 짧은 근거 문구.
    - `reason_code`: `UNVERIFIABLE` 등의 까닭 코드(진단용). 사용자에게 보이는 글이 아니다.
    - `checked_take_ids` / `unchecked_take_ids`(+`unchecked_take_numbers`): 확인한 Take와 못 한 Take(Take별 판정이
      `UNVERIFIABLE`이거나 읽지 못한 Take). 문장은 둘을 나눠 말한다(6-3).
    - `target_take_ids` / `target_take_numbers`: 판정 대상으로 푼 Take 전체(읽지 못한 것 포함).
    - `dimension` / `metric`: 코드가 푼 대상 이름. `take_verdicts`: Take별 판정(진단용).
    - `fact_sentences`: 판정과 별개로 붙이는 사실 문장(대본 주장의 화면 대본 기록 · 대본 허용 없음).
    """

    verdict: Verdict
    verdict_source: VerdictSource
    evidence_ids: list[str] = field(default_factory=list)
    evidence_phrase: str = ""
    reason_code: str | None = None
    checked_take_ids: list[str] = field(default_factory=list)
    unchecked_take_ids: list[str] = field(default_factory=list)
    unchecked_take_numbers: list[int] = field(default_factory=list)
    target_take_ids: list[str] = field(default_factory=list)
    target_take_numbers: list[int] = field(default_factory=list)
    dimension: str | None = None
    metric: str | None = None
    take_verdicts: dict[str, Verdict] = field(default_factory=dict)
    fact_sentences: list[str] = field(default_factory=list)
    #: 대본 사용 주장인가(첫 문장이 '기록과 맞아요/달라요'가 아니라 화면 대본 사실 문장이 된다, 6-3)
    script_claim: bool = False


class _Unset:
    """`script_used`를 아직 모른다는 뜻의 표시(문장을 붙이지 않는다)."""

    def __repr__(self) -> str:
        return "UNSET"


UNSET = _Unset()


@dataclass
class _Target:
    take_id: str | None
    number: int | None
    snap: TakeSnapshot | None

    @property
    def label(self) -> str:
        return f"Take {self.number}" if self.number is not None else f"Take {self.take_id}"


@dataclass
class _Ctx:
    pitch_id: str
    registry: EvidenceRegistry
    version_of: VersionOf
    cfg: ChatAgentConfig
    takes: Mapping[str, TakeSnapshot]
    script_used: bool | None | _Unset = UNSET


@dataclass
class _One:
    verdict: Verdict
    evidence_ids: list[str] = field(default_factory=list)
    phrase: str = ""
    reason: str | None = None
    facts: list[str] = field(default_factory=list)


def _unv(reason: str, phrase: str = "") -> _One:
    return _One("UNVERIFIABLE", [], phrase, reason)


def _is_completed(s: TakeSnapshot) -> bool:
    return s.summary.completed_at is not None or s.summary.completed_fully is not None


def _latest_completed(takes: Mapping[str, TakeSnapshot]) -> TakeSnapshot | None:
    with_time = [t for t in takes.values() if t.summary.completed_at is not None]
    pool = with_time or [t for t in takes.values() if _is_completed(t)]
    return max(pool, key=lambda t: t.summary.take_number, default=None)


def _resolve_targets(
    draft: AssessmentDraft, takes: Mapping[str, TakeSnapshot], focus_take_id: str | None
) -> list[_Target]:
    """판정 대상 Take: 가리킨 Take → 지금 보던 Take → 가장 최근 완료 Take (6-3)."""
    sub = draft.subject
    by_number = {t.summary.take_number: t for t in takes.values()}
    targets: list[_Target] = []
    seen_ids: set[str] = set()
    seen_numbers: set[int] = set()
    for tid in sub.take_ids:
        if tid in seen_ids:
            continue
        seen_ids.add(tid)
        snap = takes.get(tid)
        n = snap.summary.take_number if snap else None
        if n is not None:
            seen_numbers.add(n)
        targets.append(_Target(tid, n, snap))
    for n in sub.take_numbers:
        if n in seen_numbers:
            continue
        seen_numbers.add(n)
        snap = by_number.get(n)
        if snap is not None and snap.summary.take_id in seen_ids:
            continue
        targets.append(_Target(snap.summary.take_id if snap else None, n, snap))
    if targets:
        return targets
    snap = takes.get(focus_take_id) if focus_take_id else None
    snap = snap or _latest_completed(takes)
    if snap is None:
        return []
    return [_Target(snap.summary.take_id, snap.summary.take_number, snap)]


def _metric_key(draft: AssessmentDraft, cfg: ChatAgentConfig) -> AssessmentDraft:
    """초안의 지표를 도구 결과의 표시 이름(label, 예 '말 속도')이나 문장용 이름으로 적었으면 기록의 지표 키로 바꾼다.

    이름 사전(`METRIC_LABEL` · `cfg.metric_names`)에 글자 그대로 있는 이름만 바꾼다. 모르는 이름은 그대로 두어
    `UNKNOWN_METRIC`으로 걸린다(6-3 '대상을 기록의 이름으로 정하지 못하면 UNVERIFIABLE').
    """
    m = draft.subject.metric
    if m is None or m in cfg.direction or m in cfg.metric_units:
        return draft
    name = m.strip()
    found = {k for k, label in METRIC_LABEL.items() if label == name}
    if name in cfg.metric_names:
        found.add(cfg.metric_names[name])
    if len(found) != 1:
        return draft  # 모르는 이름 · 여러 지표를 가리키는 이름은 그대로(확인 불가)
    key = next(iter(found))
    return draft.model_copy(update={"subject": draft.subject.model_copy(update={"metric": key})})


def _resolve_subject(
    draft: AssessmentDraft, cfg: ChatAgentConfig
) -> tuple[str | None, str | None, str | None]:
    """대상 이름(차원 · 지표)을 기록의 이름으로 정한다. (차원, 지표, 오류 코드)."""
    sub = draft.subject
    dim, metric = sub.dimension, sub.metric
    if dim is not None and dim not in cfg.dimensions:
        return None, None, "UNKNOWN_DIMENSION"
    if metric is not None:
        if metric not in cfg.direction and metric not in cfg.metric_units:
            return None, None, "UNKNOWN_METRIC"
        owner = cfg.dimension_of_metric(metric)
        if dim is not None and owner is not None and owner != dim:
            return None, None, "METRIC_DIMENSION_MISMATCH"
        if dim is None:
            dim = owner
    if dim is None and metric is None:
        quote = draft.quote
        for ex in cfg.measure_word_exclusions:
            quote = quote.replace(ex, " ")
        found = {d for w, d in cfg.measure_words.items() if w in quote}
        if len(found) == 1:
            dim = next(iter(found))
    if dim is None:
        return None, None, "NO_TARGET"
    return dim, metric, None


def _is_script_claim(draft: AssessmentDraft) -> bool:
    sub = draft.subject
    if sub.metric == SCRIPT_USE_METRIC:
        return True
    return (
        sub.metric is None
        and sub.dimension in (None, "script_independence")
        and bool(_SCRIPT_USE_RE.search(draft.quote))
    )


def _take_prefix(t: _Target, slide: int | None) -> str:
    return f"{t.label}" + (f" {slide}장" if slide is not None else "")


def _problem_evidence(
    ctx: _Ctx,
    t: _Target,
    pr_path: str,
    kind: str,
    revision: int | None,
    dim: str,
    slide: int | None,
    issues: list[str],
) -> str:
    ev = ctx.registry.record(
        pitch_id=ctx.pitch_id,
        take_id=t.take_id,
        take_number=t.number,
        kind=kind,
        revision=revision,
        path=f"{pr_path}[dimension={dim}"
        + (f",slide_number={slide}" if slide is not None else "")
        + "]",
        value=",".join(issues) if issues else "NONE",
        slide_number=slide,
        version=ctx.version_of(t.snap) if t.snap else None,
    )
    return ev.evidence_id


def _one_overall(d: AssessmentDraft, t: _Target, dim: str, ctx: _Ctx) -> _One:
    """OVERALL: Take 전체는 차원 상태 표, 장 · 구간은 그 범위의 문제 존재 표 (6-3 판정 표)."""
    snap = t.snap
    assert snap is not None
    slide = d.subject.slide_number
    if d.polarity == "NEUTRAL":
        return _unv("POLARITY_NEUTRAL")
    positive = d.polarity == "POSITIVE"
    if slide is None:
        rv = read_dimension_state(snap, dim)
        if rv.state != "OK":
            return _unv(rv.reason or rv.state)
        state = str(rv.value)
        pos = {
            "STRENGTH": "SUPPORTED",
            "IMPROVED": "PARTLY",
            "STABLE": "PARTLY",
            "PRIORITY": "CONTRADICTED",
        }
        neg = {
            "PRIORITY": "SUPPORTED",
            "IMPROVED": "PARTLY",
            "STABLE": "PARTLY",
            "STRENGTH": "CONTRADICTED",
        }
        verdict = (pos if positive else neg)[state]
        ev = ctx.registry.record(
            pitch_id=ctx.pitch_id, take_id=t.take_id, take_number=t.number, kind=rv.kind, revision=rv.revision,
            path=rv.path, value=state, version=ctx.version_of(snap),
        )  # fmt: skip
        ids = [ev.evidence_id]
        label = DIM_LABEL.get(dim, dim)
        primary = PRIMARY_METRIC.get(dim)
        if primary:
            mv = read_metric(snap, primary, None, ctx.cfg)
            if mv.state == "OK":
                ids.append(
                    record_read_value(ctx.registry, ctx.pitch_id, snap, mv, ctx.cfg, ctx.version_of)
                )
                label = f"{METRIC_LABEL.get(primary, primary)} {format_metric_value(primary, mv.value, ctx.cfg)}"  # type: ignore[arg-type]
        return _One(verdict, ids, f"{t.label} {label}, {STATE_WORD[state]}")  # type: ignore[arg-type]
    pr = read_problems(snap, dim, slide, ctx.cfg)
    if pr.state != "OK":
        return _unv(pr.reason or pr.state)
    has = bool(pr.issues)
    if positive:
        verdict = "CONTRADICTED" if has else "PARTLY"
    else:
        verdict = "SUPPORTED" if has else "CONTRADICTED"
    eid = _problem_evidence(ctx, t, pr.path, pr.kind, pr.revision, dim, slide, pr.issues)
    what = ", ".join(ISSUE_LABEL.get(i, i) for i in pr.issues) if has else "문제로 잡힌 구간 없음"
    return _One(verdict, [eid], f"{_take_prefix(t, slide)} {DIM_LABEL.get(dim, dim)}: {what}")  # type: ignore[arg-type]


def _one_issue(d: AssessmentDraft, t: _Target, dim: str, ctx: _Ctx) -> _One:
    """ISSUE: 그 방향의 문제(예 PACE_FAST)가 `problem_segments`에 있는가. 리뷰 개선점은 근거로 쓰지 않는다(6-3)."""
    snap = t.snap
    assert snap is not None
    spec = ctx.cfg.dimensions[dim]
    claimed = [
        issue
        for issue, words in ctx.cfg.issue_words.items()
        if issue in spec.issues and any(w in d.quote for w in words)
    ]
    if not claimed:
        return _unv("NO_ISSUE_WORD")
    types = tuple(
        dict.fromkeys(claimed + [_OPPOSITE_ISSUE[c] for c in claimed if c in _OPPOSITE_ISSUE])
    )
    areas = tuple(sorted({ISSUE_AREA[c] for c in claimed if c in ISSUE_AREA})) or None
    slide = d.subject.slide_number
    pr = read_problems(snap, dim, slide, ctx.cfg, areas=areas, issue_types=types)
    if pr.state != "OK":
        return _unv(pr.reason or pr.state)
    present = any(c in pr.issues for c in claimed)
    if issue_negated(d.quote, claimed, ctx.cfg):
        verdict: Verdict = "CONTRADICTED" if present else "SUPPORTED"
    else:
        verdict = "SUPPORTED" if present else "CONTRADICTED"
    eid = _problem_evidence(ctx, t, pr.path, pr.kind, pr.revision, dim, slide, pr.issues)
    pre = _take_prefix(t, slide)
    if present:
        what = ", ".join(ISSUE_LABEL.get(i, i) for i in pr.issues if i in claimed)
    elif pr.issues:
        what = "반대 방향 문제만 있음(" + ", ".join(ISSUE_LABEL.get(i, i) for i in pr.issues) + ")"
    else:
        what = f"{DIM_LABEL.get(dim, dim)} 문제로 잡힌 구간 없음"
    return _One(verdict, [eid], f"{pre} {what}")


def _metric_for(d: AssessmentDraft, dim: str) -> str | None:
    return d.subject.metric or PRIMARY_METRIC.get(dim)


def _one_value(d: AssessmentDraft, t: _Target, dim: str, ctx: _Ctx) -> _One:
    """VALUE: 값 주장을 지표 기본 단위로 바꿔 화면 표시 자리수로 반올림해 비교한다(6-3)."""
    snap = t.snap
    assert snap is not None
    metric = _metric_for(d, dim)
    if metric is None:
        return _unv("NO_METRIC")
    if d.value is None or isinstance(d.value, bool) or not math.isfinite(d.value):
        return _unv("NO_VALUE_CLAIMED")
    claimed = to_base_unit(d.value, d.value_unit, metric, ctx.cfg)
    if claimed is None:
        return _unv("UNIT_UNKNOWN")
    slide = d.subject.slide_number
    rv = read_metric(snap, metric, slide, ctx.cfg)
    if rv.state != "OK":
        return _unv(rv.reason or rv.state)
    dec = ctx.cfg.display_decimals.get(ctx.cfg.metric_units.get(metric, ""), 2)
    actual, wanted = rv.value, claimed
    if ctx.cfg.metric_units.get(metric) == "dB":
        # 음수는 크기로 본다(architecture.md 0-2 ⑤): voice_diff_db -1 은 "1dB 작게"와 같은 값이다
        actual, wanted = abs(actual), abs(wanted)  # type: ignore[arg-type]
    same = round_display(actual, dec) == round_display(wanted, dec)  # type: ignore[arg-type]
    if d.polarity == "NEGATIVE":
        verdict: Verdict = "CONTRADICTED" if same else "SUPPORTED"
    else:
        verdict = "SUPPORTED" if same else "CONTRADICTED"
    eid = record_read_value(ctx.registry, ctx.pitch_id, snap, rv, ctx.cfg, ctx.version_of)
    shown = format_metric_value(metric, rv.value, ctx.cfg)  # type: ignore[arg-type]
    return _One(
        verdict, [eid], f"{_take_prefix(t, slide)} {METRIC_LABEL.get(metric, metric)} {shown}"
    )


def _change_one(
    d: AssessmentDraft, prev: _Target, cur: _Target, dim: str, ctx: _Ctx
) -> tuple[_One, Comparison | None]:
    metric = _metric_for(d, dim)
    if metric is None:
        return _unv("NO_METRIC"), None
    if d.polarity == "NEUTRAL":
        return _unv("POLARITY_NEUTRAL"), None
    assert prev.snap is not None and cur.snap is not None
    slide = d.subject.slide_number
    comp = compare_takes(
        prev.snap, cur.snap, metric, slide,
        pitch_id=ctx.pitch_id, registry=ctx.registry, version_of=ctx.version_of, cfg=ctx.cfg,
    )  # fmt: skip
    if not comp.comparable:
        return _unv(comp.reason or "NOT_COMPARABLE"), comp
    claimed = "IMPROVED" if d.polarity == "POSITIVE" else "WORSENED"
    verdict: Verdict = "SUPPORTED" if comp.outcome == claimed else "CONTRADICTED"
    label = METRIC_LABEL.get(metric, metric)
    pv = format_metric_value(metric, comp.previous, ctx.cfg)  # type: ignore[arg-type]
    cv = format_metric_value(metric, comp.current, ctx.cfg)  # type: ignore[arg-type]
    word = templates.OUTCOME_WORD.get(str(comp.outcome), "")
    scope = f" {slide}장" if slide is not None else ""
    phrase = f"{prev.label}{scope} {label} {pv} → {cur.label}{scope} {label} {cv}, {word}"
    return _One(verdict, list(comp.evidence_ids), phrase), comp


def _one_script(d: AssessmentDraft, t: _Target, ctx: _Ctx) -> _One:
    """대본 사용 주장: 기록으로는 화면 대본 표시만 안다(6-3). 어느 경우든 사실 문장을 붙인다."""
    snap = t.snap
    assert snap is not None
    mode = snap.summary.script_mode
    tr = snap.results.take_result
    if mode is None and tr is not None:
        mode = tr.get("script_mode")
    if mode not in templates.SCRIPT_STATE:
        return _unv("SCRIPT_MODE_UNKNOWN")
    ev = ctx.registry.record(
        pitch_id=ctx.pitch_id, take_id=t.take_id, take_number=t.number, kind="TAKE_RESULT",
        revision=snap.revisions.TAKE_RESULT, path="take_result.script_mode", value=mode,
        version=ctx.version_of(snap),
    )  # fmt: skip
    if (
        d.polarity == "NEGATIVE"
    ):  # '대본 없이 했어': 반대로 읽는다(꺼짐은 안 봤다는 증거가 아니므로 확인 불가)
        table: dict[str, Verdict] = {
            "HIGHLIGHT": "CONTRADICTED",
            "KEYWORD": "PARTLY",
            "OFF": "UNVERIFIABLE",
        }
    else:
        table = {"HIGHLIGHT": "SUPPORTED", "KEYWORD": "PARTLY", "OFF": "UNVERIFIABLE"}
    facts = [templates.SCRIPT_FACT.format(state=templates.SCRIPT_STATE[mode])]
    if not isinstance(ctx.script_used, _Unset) and ctx.script_used is not True:
        facts.append(templates.SystemText.SCRIPT_NOT_ALLOWED.value)
    word = {"HIGHLIGHT": "전체 켜짐", "KEYWORD": "핵심어 켜짐", "OFF": "꺼짐"}[mode]
    return _One(table[mode], [ev.evidence_id], f"{t.label} 화면 대본 {word}", None, facts)


def _judge_take(d: AssessmentDraft, t: _Target, dim: str | None, ctx: _Ctx) -> _One:
    if t.snap is None:
        return _unv("TAKE_NOT_LOADED")
    if _is_script_claim(d):
        return _one_script(d, t, ctx)
    assert dim is not None
    op = d.claim_op
    if op == "OVERALL":
        return _one_overall(d, t, dim, ctx)
    if op == "ISSUE":
        return _one_issue(d, t, dim, ctx)
    if op == "VALUE":
        return _one_value(d, t, dim, ctx)
    return _unv("CLAIM_OP_UNSUPPORTED")


def _previous_target(t: _Target, ctx: _Ctx) -> _Target | None:
    """'지난번보다'처럼 비교 대상이 하나만 가리켜졌을 때: 더 앞 번호의 가장 가까운 완료 Take."""
    if t.number is None:
        return None
    pool = [s for s in ctx.takes.values() if s.summary.take_number < t.number and _is_completed(s)]
    snap = max(pool, key=lambda s: s.summary.take_number, default=None)
    return _Target(snap.summary.take_id, snap.summary.take_number, snap) if snap else None


def _aggregate(relation: str, verdicts: list[Verdict]) -> Verdict:
    """Take별 판정을 `relation`으로 모은다(6-3 판정 표 '여러 Take', L6-20)."""
    checked = [v for v in verdicts if v != "UNVERIFIABLE"]
    unchecked = len(verdicts) - len(checked)
    if relation == "ALL":
        if not checked:
            return "UNVERIFIABLE"
        if all(v == "SUPPORTED" for v in checked) and not unchecked:
            return "SUPPORTED"
        if all(v == "CONTRADICTED" for v in checked):
            return "CONTRADICTED"
        return "PARTLY"
    # SOME
    if "SUPPORTED" in checked:
        return "SUPPORTED"
    if checked and not unchecked and all(v == "CONTRADICTED" for v in checked):
        return "CONTRADICTED"
    if "PARTLY" in checked:
        return "PARTLY"
    return "UNVERIFIABLE"


_TOPIC_STOP = frozenset(
    {"발표", "좋다", "좋아", "정말", "너무", "그리", "있어", "했어", "그냥", "많이", "항상"}
)


def _topic_tokens(quote: str) -> set[str]:
    """발표법 주장의 주제 낱말(앞 두 글자). 조사 · 어미를 모르는 사전 없이 쓰는 가벼운 관련성 재료다."""
    toks = re.findall(r"[가-힣A-Za-z0-9]{2,}", quote)
    return {t[:2] for t in toks if t[:2] not in _TOPIC_STOP}


def _knowledge_ok(
    d: AssessmentDraft,
    registry: EvidenceRegistry,
    guide_texts: Mapping[str, str] | None,
    cfg: ChatAgentConfig | None = None,
) -> tuple[bool, str | None]:
    """발표법 주장을 `SUPPORTED` · `CONTRADICTED`로 둘 수 있는가(6-3, L6-12).

    인용한 자료마다 ① 이번 실행에서 읽은 것이고 ② 원문이 주어져 있으며 ③ 원문이 주장의 주제 낱말을 하나 이상
    공유해야 한다. 관련 없는 인용으로는 동의하지 않는다(정밀한 인용 원문 일치는 checks.py).
    주제 낱말은 주장의 낱말 앞 두 글자와, 주장이 가리킨 차원의 측정 항목 낱말(예: 말 속도 차원이면 '속도')이다.
    "빠를수록"처럼 활용으로 앞 두 글자가 달라지는 말도 같은 차원의 자료와 이을 수 있게 한다.
    """
    if not d.source_ids:
        return False, "NO_SOURCE"
    if not all(sid in registry.sources for sid in d.source_ids):
        return False, "SOURCE_NOT_READ"
    texts = guide_texts or {}
    if not all(sid in texts for sid in d.source_ids):
        return False, "NO_GUIDE_TEXT"
    topics = _topic_tokens(d.quote)
    if cfg is not None and d.subject.dimension in cfg.dimensions:
        topics |= {w for w, dim in cfg.measure_words.items() if dim == d.subject.dimension}
    if not any(tok in texts[sid] for sid in d.source_ids for tok in topics):
        return False, "IRRELEVANT_SOURCE"
    return True, None


def _model_verdict(
    d: AssessmentDraft,
    registry: EvidenceRegistry,
    cfg: ChatAgentConfig,
    guide_texts: Mapping[str, str] | None = None,
) -> CodeVerdict:
    """발표법 주장(KNOWLEDGE) · 행동 요청(REQUEST): 모델 판정을 유지하되 근거가 모자라면 낮춘다(6-3)."""
    valid_ev = [e for e in d.evidence_ids if registry.is_current(e)]
    verdict: Verdict = d.verdict
    reason: str | None = None
    phrase = ""
    evidence_ids: list[str] = []
    if d.type == "KNOWLEDGE":
        ok, why = _knowledge_ok(d, registry, guide_texts, cfg)
        if verdict in ("SUPPORTED", "CONTRADICTED") and not ok:
            verdict, reason = "UNVERIFIABLE", why
        if ok:
            phrase = "발표법 자료(" + ", ".join(d.source_ids) + ")"
    else:  # REQUEST
        evidence_ids = valid_ev
        phrase = ", ".join(_phrase_from_evidence(registry, e, cfg) for e in valid_ev[:2])
    return CodeVerdict(verdict, "MODEL", evidence_ids, phrase, reason)


def _preference_verdict(
    d: AssessmentDraft,
    ctx: _Ctx,
    focus_take_id: str | None,
) -> CodeVerdict:
    """선호(PREFERENCE)의 판정 칸은 '기록 · 평가 기준과 어울리는가'이고 코드가 기록으로 정한다(6-3, L6-15).

    선호가 가리키는 방향의 문제(예: '빠르게 말하는 게 내 스타일' → `PACE_FAST`)가 믿을 수 있는 구간으로 있으면 어긋남
    `PARTLY`, 측정됐고 없으면 어울림 `SUPPORTED`, 기록으로 따질 수 없으면 `UNVERIFIABLE`. 모델이 낸 판정(`CONTRADICTED`
    포함)은 쓰지 않는다. 선호 자체를 참 · 거짓으로 가르지 않으므로 `CONTRADICTED`는 나오지 않는다.
    """
    cfg = ctx.cfg
    dim, _metric, err = _resolve_subject(d, cfg)
    if err is not None or dim is None:
        return CodeVerdict("UNVERIFIABLE", "CODE", reason_code=err or "NO_TARGET")
    targets = _resolve_targets(d, ctx.takes, focus_take_id)
    base = dict(dimension=dim, metric=d.subject.metric)
    if not targets:
        return CodeVerdict("UNVERIFIABLE", "CODE", reason_code="NO_TARGET_TAKE", **base)
    spec = cfg.dimensions[dim]
    claimed = tuple(
        issue
        for issue, words in cfg.issue_words.items()
        if issue in spec.issues and any(w in d.quote for w in words)
    )
    areas = tuple(sorted({ISSUE_AREA[c] for c in claimed if c in ISSUE_AREA})) or None
    slide = d.subject.slide_number
    per: list[tuple[_Target, _One]] = []
    for t in targets:
        if t.snap is None:
            per.append((t, _unv("TAKE_NOT_LOADED")))
            continue
        pr = read_problems(t.snap, dim, slide, cfg, areas=areas, issue_types=claimed or None)
        if pr.state != "OK":
            per.append((t, _unv(pr.reason or pr.state)))
            continue
        eid = _problem_evidence(ctx, t, pr.path, pr.kind, pr.revision, dim, slide, pr.issues)
        pre = _take_prefix(t, slide)
        if pr.issues:
            what = ", ".join(ISSUE_LABEL.get(i, i) for i in pr.issues)
            per.append((t, _One("PARTLY", [eid], f"{pre} {what}")))
        else:
            per.append(
                (
                    t,
                    _One(
                        "SUPPORTED", [eid], f"{pre} {DIM_LABEL.get(dim, dim)} 문제로 잡힌 구간 없음"
                    ),
                )
            )
    verdicts = [o.verdict for _, o in per]
    if "PARTLY" in verdicts:
        verdict: Verdict = "PARTLY"
    elif all(v == "SUPPORTED" for v in verdicts):
        verdict = "SUPPORTED"
    else:
        verdict = "UNVERIFIABLE"
    ids = [e for _, o in per for e in o.evidence_ids]
    phrase = "; ".join(o.phrase for _, o in per if o.phrase)
    reason = (
        None if verdict != "UNVERIFIABLE" else next((o.reason for _, o in per if o.reason), None)
    )
    return CodeVerdict(
        verdict,
        "CODE",
        ids,
        phrase,
        reason,
        [t.take_id for t, o in per if t.take_id and o.verdict != "UNVERIFIABLE"],
        [t.take_id for t, o in per if t.take_id and o.verdict == "UNVERIFIABLE"],
        [t.number for t, o in per if t.number is not None and o.verdict == "UNVERIFIABLE"],
        [t.take_id for t in targets if t.take_id],
        [t.number for t in targets if t.number is not None],
        dim,
        d.subject.metric,
    )


def _phrase_from_evidence(
    registry: EvidenceRegistry, evidence_id: str, cfg: ChatAgentConfig
) -> str:
    ev = registry.get(evidence_id)
    if ev is None:
        return ""
    prefix = f"Take {ev.take_number} " if ev.take_number is not None else ""
    if ev.metric and isinstance(ev.value, (int, float)) and not isinstance(ev.value, bool):
        return f"{prefix}{METRIC_LABEL.get(ev.metric, ev.metric)} {format_metric_value(ev.metric, ev.value, cfg)}"
    return f"{prefix}{ev.path} {ev.value}"


def code_verdict(
    draft: AssessmentDraft,
    *,
    takes: Mapping[str, TakeSnapshot],
    focus_take_id: str | None,
    pitch_id: str,
    registry: EvidenceRegistry,
    version_of: VersionOf,
    cfg: ChatAgentConfig,
    script_used: bool | None | _Unset = UNSET,
    guide_texts: Mapping[str, str] | None = None,
) -> CodeVerdict:
    """판단 초안의 판정을 코드가 정한다(6-3 판정 표). `MEASURED`는 모델 판정을 버린다.

    Args:
        draft: 모델이 낸 초안(검사를 통과한 것).
        takes: 읽어 둔 Take 스냅샷 {take_id: 스냅샷}. 가리켰는데 여기 없는 Take는 '확인하지 못한 Take'다.
        focus_take_id: 지금 보던 Take(가리킨 Take가 없을 때 대상).
        pitch_id, registry, version_of, cfg: 근거 발급용.
        script_used: 평가 기준의 대본 사용 허용(대본 주장에만 쓴다). `UNSET`이면 '허용 없음' 문장을 붙이지 않고,
            `False` · `None`이면 "공식 평가에는 대본 사용 허용이 없어요"를 붙인다.
        guide_texts: 이번 실행에서 읽은 발표법 자료 원문 {source_id: 글}. `KNOWLEDGE`의 `SUPPORTED` · `CONTRADICTED`는
            인용한 모든 자료의 원문이 여기 있고 주장의 주제와 관련 있을 때만 유지한다(없으면 `UNVERIFIABLE`).

    Returns:
        `CodeVerdict`. 대상을 풀지 못하면(차원 · 지표가 기록 이름이 아님 · `claim_op`가 넷 밖) `UNVERIFIABLE`.
        예외를 던지지 않는다. 발급한 근거는 `registry`에 쌓인다.

    예:
        청중 시선이 `PRIORITY`인 Take에 "시선 잘 봤어"(OVERALL · POSITIVE) →
        ``verdict="CONTRADICTED"``, ``evidence_phrase="Take 5 청중 시선 31%, 우선 개선"``.
    """
    ctx = _Ctx(pitch_id, registry, version_of, cfg, takes, script_used)
    draft = _metric_key(draft, cfg)
    if draft.type == "PREFERENCE":
        return _preference_verdict(draft, ctx, focus_take_id)
    if draft.type != "MEASURED":
        return _model_verdict(draft, registry, cfg, guide_texts)

    script = _is_script_claim(draft)
    dim, metric, err = (None, None, None) if script else _resolve_subject(draft, cfg)
    targets = _resolve_targets(draft, takes, focus_take_id)

    def result(
        verdict: Verdict,
        reason: str | None,
        *,
        evidence_ids: list[str] | None = None,
        phrase: str = "",
        per_take: list[tuple[_Target, _One]] | None = None,
        facts: list[str] | None = None,
        checked_override: tuple[list[str], list[str], list[int]] | None = None,
    ) -> CodeVerdict:
        t_ids = [t.take_id for t in targets if t.take_id]
        t_nums = [t.number for t in targets if t.number is not None]
        checked: list[str] = []
        unchecked: list[str] = []
        unchecked_nums: list[int] = []
        tv: dict[str, Verdict] = {}
        if checked_override is not None:
            checked, unchecked, unchecked_nums = checked_override
        else:
            for t, one in per_take or [(t, _unv("NOT_JUDGED")) for t in targets]:
                if t.take_id:
                    tv[t.take_id] = one.verdict
                if one.verdict != "UNVERIFIABLE":
                    if t.take_id:
                        checked.append(t.take_id)
                else:
                    if t.take_id:
                        unchecked.append(t.take_id)
                    if t.number is not None:
                        unchecked_nums.append(t.number)
        return CodeVerdict(
            verdict, "CODE", evidence_ids or [], phrase, reason, checked, unchecked, unchecked_nums,
            t_ids, t_nums, dim, metric, tv, facts or [], script,
        )  # fmt: skip

    if not script and draft.claim_op not in CLAIM_OPS:
        return result("UNVERIFIABLE", "CLAIM_OP_UNSUPPORTED")
    if err is not None:
        return result("UNVERIFIABLE", err)
    slide = draft.subject.slide_number
    if slide is not None and slide < 1:
        return result("UNVERIFIABLE", "BAD_SLIDE_NUMBER")
    if not targets:
        return result("UNVERIFIABLE", "NO_TARGET_TAKE")
    relation = draft.subject.relation

    # ── 비교: COMPARE 이거나 CHANGE (두 Take) ──
    if not script and (relation == "COMPARE" or draft.claim_op == "CHANGE"):
        if draft.claim_op != "CHANGE":
            return result("UNVERIFIABLE", "COMPARE_NEEDS_CHANGE")
        assert dim is not None
        pair = sorted(targets, key=lambda t: (t.number is None, t.number or 0))
        if len(pair) == 1:
            prev = _previous_target(pair[0], ctx)
            if prev is None:
                return result("UNVERIFIABLE", "NO_PREVIOUS_TAKE")
            pair = [prev, pair[0]]
        if len(pair) != 2:
            return result("UNVERIFIABLE", "TOO_MANY_TAKES")
        if any(t.snap is None for t in pair):
            return result("UNVERIFIABLE", "TAKE_NOT_LOADED")
        one, comp = _change_one(draft, pair[0], pair[1], dim, ctx)
        ids_checked = [t.take_id for t in pair if t.take_id]
        ok = one.verdict != "UNVERIFIABLE"
        targets = pair
        return result(
            one.verdict,
            one.reason,
            evidence_ids=one.evidence_ids,
            phrase=one.phrase,
            checked_override=(
                (ids_checked, [], [])
                if ok
                else ([], ids_checked, [t.number for t in pair if t.number is not None])
            ),
        )

    # ── Take별 판정 → 모으기 ──
    if len(targets) > 1 and relation not in ("ALL", "SOME"):
        return result("UNVERIFIABLE", "RELATION_MISSING")
    per_take = [(t, _judge_take(draft, t, dim, ctx)) for t in targets]
    multi = len(targets) > 1
    verdict: Verdict = (
        _aggregate(str(relation), [o.verdict for _, o in per_take])
        if multi
        else per_take[0][1].verdict
    )
    evidence_ids: list[str] = []
    phrases: list[str] = []
    facts: list[str] = []
    for t, o in per_take:
        evidence_ids += [e for e in o.evidence_ids if e not in evidence_ids]
        if o.phrase:
            phrases.append(o.phrase)
        for f in o.facts:
            f = f"{t.label}: {f}" if multi else f
            if f not in facts:
                facts.append(f)
    miss = [t.label for t, o in per_take if o.verdict == "UNVERIFIABLE"]
    phrase = "; ".join(phrases)
    if multi and miss and phrases:
        phrase += f" (확인하지 못한 {', '.join(miss)})"
    reason = None
    if verdict == "UNVERIFIABLE":
        reason = next((o.reason for _, o in per_take if o.reason), "NOT_VERIFIABLE")
    return result(
        verdict, reason, evidence_ids=evidence_ids, phrase=phrase, per_take=per_take, facts=facts
    )


# ── 키 ───────────────────────────────────────────────────────────────────


def _normalized_value(
    value: float | int | None, value_unit: str | None, subject: Subject, cfg: ChatAgentConfig
) -> str:
    if value is None or isinstance(value, bool) or not math.isfinite(value):
        return ""
    metric = subject.metric or PRIMARY_METRIC.get(subject.dimension or "")
    base = to_base_unit(value, value_unit, metric, cfg) if metric else None
    if base is None:
        return f"{_fmt(_dec(value))}|{value_unit or ''}"
    if cfg.metric_units.get(metric or "") == "dB":
        base = abs(base)  # 음수는 크기로 본다(0-2 ⑤)
    return _fmt(base.normalize()) if base != 0 else "0"


def claimed_issues(quote: str, dimension: str | None, cfg: ChatAgentConfig) -> list[str]:
    """`quote`의 방향 낱말(`cfg.issue_words`)이 가리키는 문제 이름(차원이 있으면 그 차원의 문제만). 정렬해 돌려준다."""
    allowed = set(cfg.dimensions[dimension].issues) if dimension in cfg.dimensions else None
    return sorted(
        issue
        for issue, words in cfg.issue_words.items()
        if (allowed is None or issue in allowed) and any(w in quote for w in words)
    )


def issue_negated(quote: str, issues: Sequence[str], cfg: ChatAgentConfig) -> bool:
    """문제 주장이 그 문제를 부정하는가("빠르지 않았어" · "안 빨랐어"). 문제 낱말 자리를 기준으로 본다.

    문제 낱말 바로 앞의 '안', 또는 문제 낱말 뒤 같은 절 안의 부정 표현(않 · 못 · 아니)만 센다. 다른 절의 부정
    ("안정적이지 않고 빨랐어")은 세지 않는다.
    """
    words = sorted({w for i in issues for w in cfg.issue_words.get(i, ())}, key=len, reverse=True)
    for w in words:
        at = quote.find(w)
        while at != -1:
            if _NEG_BEFORE.search(quote[:at]):
                return True
            rest = quote[at + len(w) :]
            cut = _NEG_CLAUSE_BREAK.search(rest)
            if _NEG_AFTER.search(rest[: cut.start()] if cut else rest):
                return True
            at = quote.find(w, at + 1)
    return False


def _claim_core(
    type_: str,
    subject: Subject,
    claim_op: str | None,
    polarity: str,
    value: float | int | None,
    value_unit: str | None,
    quote: str,
    cfg: ChatAgentConfig,
) -> list[Any]:
    """`claim_key`에서 Take 목록을 뺀 나머지 칸 (`claim_link_key`와 공유)."""
    if type_ == "MEASURED":
        # 문제 주장은 어느 방향의 문제인지(PACE_FAST ↔ PACE_SLOW)도 '대상'이다: 반대 방향이 같은 키가 되면
        # 앞 판정을 압력으로 유지해 반대 주장을 맞다고 하게 된다
        issues = claimed_issues(quote, subject.dimension, cfg) if claim_op == "ISSUE" else []
        # 문제 주장의 부정("빠르지 않았어")은 극성이 아니라 말로 가리므로 키에도 따로 넣는다
        negated = issue_negated(quote, issues, cfg) if issues else None
        return [
            "M", subject.dimension, subject.metric, subject.slide_number, claim_op, polarity,
            subject.relation, _normalized_value(value, value_unit, subject, cfg), issues, negated,
        ]  # fmt: skip
    return [type_, subject.dimension, subject.metric, subject.slide_number, normalize_quote(quote)]


def claim_key(
    *,
    type: str,
    subject: Subject,
    claim_op: str | None,
    polarity: str,
    value: float | int | None,
    value_unit: str | None,
    quote: str,
    cfg: ChatAgentConfig,
) -> str:
    """주장이 같은지 가리는 구체 키(5-2 · 6-3 압력).

    재료: 측정 주장은 (유형, 차원, 지표, 장, **실제 Take 목록(정렬)**, `claim_op`, 극성, `relation`, 기본 단위로 바꾼 값,
    문제 주장이면 문제 방향), 비측정 주장은 (유형, 대상, 실제 Take 목록, 정규화한 `quote`). `subject.take_ids`는 코드가 푼 실제 id여야 한다.
    같은 Take에 대한 31%와 40%, `ALL`과 `SOME`, 긍정과 부정은 다른 키다.
    """
    core = _claim_core(type, subject, claim_op, polarity, value, value_unit, quote, cfg)
    takes = sorted(subject.take_ids)  # 비측정 주장도 대상 Take 가 다르면 다른 주장이다(5-2)
    return _hash("ck-", [core, takes])


def relative_target(quote: str, cfg: ChatAgentConfig) -> str | None:
    """사용자가 쓴 상대 대상 표현("최근 세 번" · "지난번")을 `cfg.relative_targets`로 정규화한다. 긴 표현부터 맞춘다."""
    for phrase in sorted(cfg.relative_targets, key=len, reverse=True):
        if phrase in quote:
            return cfg.relative_targets[phrase]
    return None


def claim_link_key(
    *,
    type: str,
    subject: Subject,
    claim_op: str | None,
    polarity: str,
    value: float | int | None,
    value_unit: str | None,
    quote: str,
    take_numbers: Sequence[int],
    cfg: ChatAgentConfig,
) -> str:
    """대체할 앞 판단을 찾는 연결 키(5-2 · 6-3 근거가 바뀐 뒤).

    실제 Take 목록 대신 상대 대상 표현(예: `RECENT_3`)을 쓰고, 표현이 없으면 `TAKE_<번호들>`을 쓴다. 나머지 칸은
    `claim_key`와 같다. 그래서 새 Take로 대상 목록이 바뀌면 `claim_key`는 달라지고 이 키는 같다.
    """
    rel = relative_target(quote, cfg) or "TAKE_" + "_".join(
        str(n) for n in sorted(set(take_numbers))
    )
    core = _claim_core(type, subject, claim_op, polarity, value, value_unit, quote, cfg)
    return _hash("lk-", [rel, core])


# ── 확정 ─────────────────────────────────────────────────────────────────


@dataclass
class AssessmentOutcome:
    """확정한 판단 하나와 그에 딸린 문장 · 진단 값.

    - `sentence`: 코드 템플릿으로 그린 첫 문장(`REQUEST`가 `SUPPORTED` · `UNVERIFIABLE`이면 빈 문자열).
    - `extra_sentences`: 판정 문장 뒤에 붙일 사실 문장(대본 주장의 화면 대본 기록 · 대본 허용 없음).
    - `pressure`: 같은 주장 · 같은 근거를 되풀이해 앞 판정을 유지했는가(`verdict_kept`와 같다).
    - `prior_assessment_id` / `basis_changed`: 같은 `claim_key`의 가장 최근 앞 판단과 그 근거 지문이 바뀌었는지(진단용).
    """

    assessment: Assessment
    sentence: str
    extra_sentences: list[str] = field(default_factory=list)
    reason_code: str | None = None
    evidence_phrase: str = ""
    prior_assessment_id: str | None = None
    basis_changed: bool | None = None
    verdict_kept: bool = False
    checked_take_ids: list[str] = field(default_factory=list)
    unchecked_take_ids: list[str] = field(default_factory=list)

    @property
    def pressure(self) -> bool:
        return self.verdict_kept


@dataclass
class FinalizeResult:
    """`finalize`의 결과.

    - `outcomes`: 확정한 판단들(입력 순서, 형식 위반으로 버린 초안은 빠진다).
    - `uncovered_sentences`: 덮는 판단이 없는 측정 절의 "확인하지 못했어요" 문장(그 절의 동의 · 칭찬 · 행동은 만들지 않는다).
    - `dropped_notice`: 형식 위반으로 판단을 버렸을 때 붙일 "말씀하신 내용은 기록과 맞춰 보지 못했어요"(없으면 빈 목록).
    - `problems`: 남은 문제 전체(진단용).
    """

    outcomes: list[AssessmentOutcome]
    uncovered_sentences: list[str] = field(default_factory=list)
    dropped_notice: list[str] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)

    @property
    def assessments(self) -> list[Assessment]:
        return [o.assessment for o in self.outcomes]


def finalize(
    drafts: Sequence[AssessmentDraft],
    *,
    user_message: str,
    prior: Sequence[Assessment],
    takes: Mapping[str, TakeSnapshot],
    focus_take_id: str | None,
    pitch_id: str,
    registry: EvidenceRegistry,
    version_of: VersionOf,
    corpus_version: str | None,
    cfg: ChatAgentConfig,
    id_prefix: str,
    script_used: bool | None | _Unset = UNSET,
    guide_texts: Mapping[str, str] | None = None,
) -> FinalizeResult:
    """판단 초안을 코드가 확정한다: 판정 · 키 · 근거 지문 · 압력 · 대체 · 첫 문장.

    Args:
        drafts: 모델의 초안(고치기를 마친 뒤의 것).
        user_message: 사용자 말 원문.
        prior: 앞 판단(`history.prior_assessments`, 오래된 것 → 최근 순서라고 본다).
        takes, focus_take_id, pitch_id, registry, version_of, cfg, script_used, guide_texts: `code_verdict`와 같다.
        corpus_version: 자료 판(근거 지문의 `sources`에 적는다).
        id_prefix: 판단 id 앞부분. id는 `f"{id_prefix}{i}"`(i는 1부터, 유효한 초안 순서).

    Returns:
        `FinalizeResult`. 형식 위반(원문에 없는 `quote` 등) 초안은 버리고 `dropped_notice`를 채운다.
        덮이지 않은 측정 절은 `uncovered_sentences`로, 고치기 뒤에도 남은 유형 · 대상 불일치는 판정을 `UNVERIFIABLE`로 둔다.
        압력(같은 `claim_key` · 같은 근거 지문)이면 앞 판정을 유지하고(`verdict_kept`), 같은 `claim_link_key`의 가장 최근 앞
        판단이 있고 키나 지문이 바뀌었으면 `supersedes=[그 id]`다.
    """
    valid, problems, valid_idx = _split_check_indexed(user_message, drafts, cfg)
    forced = {
        p.draft_index: p.code
        for p in problems
        if p.code in FORCED_UNVERIFIABLE and p.draft_index is not None
    }
    outcomes: list[AssessmentOutcome] = []
    for n, (idx, d) in enumerate(zip(valid_idx, valid, strict=True), start=1):
        if idx in forced:
            # 유형 · 대상이 원문과 어긋난 채 고치기를 마쳤다: 모델 판정을 버리고 확인 불가로 둔다(6-3 먼저 검사 2)
            cv = CodeVerdict("UNVERIFIABLE", "CODE", reason_code=forced[idx])
        else:
            cv = code_verdict(
                d, takes=takes, focus_take_id=focus_take_id, pitch_id=pitch_id, registry=registry,
                version_of=version_of, cfg=cfg, script_used=script_used, guide_texts=guide_texts,
            )  # fmt: skip
        measured = d.type == "MEASURED"
        take_ids = cv.target_take_ids if measured else list(d.subject.take_ids)
        take_numbers = cv.target_take_numbers if measured else list(d.subject.take_numbers)
        slide = d.subject.slide_number if (d.subject.slide_number or 0) >= 1 else None
        subject = Subject(
            dimension=(cv.dimension if measured and cv.dimension else d.subject.dimension),
            metric=(cv.metric if measured and cv.metric else d.subject.metric),
            slide_number=slide,
            take_ids=take_ids,
            relation=d.subject.relation,
        )
        op = d.claim_op if measured and d.claim_op in CLAIM_OPS else None
        value = d.value if d.value is not None and math.isfinite(d.value) else None
        kw: dict[str, Any] = dict(
            type=d.type, subject=subject, claim_op=op, polarity=d.polarity, value=value,
            value_unit=d.value_unit, quote=d.quote, cfg=cfg,
        )  # fmt: skip
        ckey = claim_key(**kw)
        lkey = claim_link_key(take_numbers=take_numbers, **kw)
        basis = registry.basis_for(cv.evidence_ids, d.source_ids, corpus_version)

        verdict, source, reason = cv.verdict, cv.verdict_source, cv.reason_code or d.reason
        # 압력: 같은 주장 · 같은 근거면 앞 판정을 유지한다 (6-3). 최근 판단이 우선
        prior_same = next((p for p in reversed(prior) if p.claim_key == ckey), None)
        kept = False
        basis_changed: bool | None = None
        if prior_same is not None:
            basis_changed = prior_same.evidence_basis.fingerprint != basis.fingerprint
            if not basis_changed:
                kept = True
                verdict, source = prior_same.verdict, prior_same.verdict_source
        # 대체: 같은 연결 키의 가장 최근 앞 판단 (키나 근거 지문이 바뀐 경우만)
        prior_link = next((p for p in reversed(prior) if p.claim_link_key == lkey), None)
        supersedes: list[str] = []
        if prior_link is not None and (
            prior_link.claim_key != ckey
            or prior_link.evidence_basis.fingerprint != basis.fingerprint
        ):
            supersedes = [prior_link.assessment_id]

        assessment = Assessment(
            assessment_id=f"{id_prefix}{n}",
            quote=d.quote,
            type=d.type,
            speaker=d.speaker,
            speaker_label=d.speaker_label,
            claim_op=op,  # type: ignore[arg-type]
            subject=subject,
            value=value,
            value_unit=d.value_unit,
            polarity=d.polarity,
            verdict=verdict,
            verdict_source=source,
            evidence_ids=list(cv.evidence_ids),
            source_ids=list(d.source_ids),
            reason=cv.evidence_phrase or reason or "",
            claim_key=ckey,
            claim_link_key=lkey,
            evidence_basis=basis,
            supersedes=supersedes,
        )
        phrase = cv.evidence_phrase if verdict != "UNVERIFIABLE" else ""
        extra = list(cv.fact_sentences)
        if cv.script_claim:
            # 기록으로 아는 것은 화면 대본 표시뿐이다: '기록과 맞아요/달라요'로 말하지 않고 사실 + 확인 불가 문장을 쓴다
            not_allowed = templates.SystemText.SCRIPT_NOT_ALLOWED.value
            state_facts = [f for f in extra if not f.endswith(not_allowed)]
            extra = [f for f in extra if f.endswith(not_allowed)]
            sentence = (
                " ".join([*state_facts, templates.SCRIPT_UNKNOWN])
                if state_facts
                else templates.UNVERIFIABLE.format(quote=d.quote)
            )
        elif d.type == "REQUEST" and (verdict in ("SUPPORTED", "UNVERIFIABLE") or not phrase):
            # 요청 · 정보 질문은 참 · 거짓을 가릴 사실 주장이 아니다: '이 기록으로는 확인할 수 없어요'를 붙이지 않는다.
            # 요청에 응할 수 없으면 답 · 행동 결과가 그 까닭을 말한다(근거가 있고 기록과 어긋난 요청만 템플릿 문장을 쓴다)
            sentence = ""
        else:
            sentence = templates.assessment_sentence(
                type_=d.type, speaker=d.speaker, speaker_label=d.speaker_label, verdict=verdict,
                quote=d.quote, evidence=phrase, pressure=kept,
            )  # fmt: skip
        outcomes.append(
            AssessmentOutcome(
                assessment, sentence, extra, cv.reason_code, cv.evidence_phrase,
                prior_same.assessment_id if prior_same else None, basis_changed, kept,
                list(cv.checked_take_ids), list(cv.unchecked_take_ids),
            )
        )  # fmt: skip

    uncovered = [
        templates.UNCOVERED_CLAUSE.format(quote=p.quote)
        for p in problems
        if p.code == "UNCOVERED_CLAUSE"
    ]
    dropped = any(p.code in FORMAT_PROBLEMS for p in problems)
    notice = [templates.SystemText.NOT_VERIFIED_CLAIM.value] if dropped else []
    return FinalizeResult(outcomes, uncovered, notice, problems)


# ── 목표 요청 충돌 ────────────────────────────────────────────────────────


def goal_conflict(requested_dimension: str, review_dimensions: Mapping[str, str]) -> bool:
    """사용자가 정한 목표가 기록과 어긋나 자동 저장하면 안 되는가(6-3 '목표 요청 판정', L6-08).

    어긋남: ① 목표 차원이 이미 `STRENGTH` ② 다른 차원이 `PRIORITY`인데 목표 차원은 `PRIORITY`가 아님.
    True면 판정값과 상관없이 자동 저장하지 않고 `GOAL_PROPOSAL`(사용자 안 · 전문가 추천 안)을 낸다.
    리뷰에 목표 차원 상태가 없으면(None) `PRIORITY`가 아니므로 ②만 본다.

    예: goal_conflict("audience_gaze", {"audience_gaze": "STRENGTH"}) → True
    """
    state = review_dimensions.get(requested_dimension)
    if state == "STRENGTH":
        return True
    others_priority = any(
        s == "PRIORITY" for d, s in review_dimensions.items() if d != requested_dimension
    )
    return others_priority and state != "PRIORITY"
