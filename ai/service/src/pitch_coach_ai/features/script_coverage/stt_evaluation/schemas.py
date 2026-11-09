"""STT 평가의 데이터 모델: 입력(STT), 규칙 결과, LLM 출력 스키마, 평가 결과.

LLM 출력 스키마(UnitJudgment · SentenceJudgment · NameCheck · SemanticEvaluation · VerifiedSentence · VerifierResult)는
클래스 이름 · docstring · 필드 순서 · description 이 모두 설정 해시(캐시 키)에 들어간다. 글자 하나도 바꾸지 않는다.
"""

from typing import Literal

from pydantic import BaseModel, Field

from ..shared.facts import Importance


class SlideSTT(BaseModel):
    slide_number: int
    stt: str = Field(description="이 슬라이드에서 발표자가 말한 내용의 음성 인식 결과")


class Take(BaseModel):
    """발표 연습 한 번의 STT. 슬라이드별로 나뉘어 있다."""

    script_name: str = Field(description="어느 대본의 연습인지 (대본 JSON 파일 이름)")
    take_id: str
    scenario: str = Field(
        default="",
        description="가상 데이터의 시나리오 (충실 / 의역 / 누락 / 실수 …). 실제 데이터에는 없어도 된다",
    )
    slides: list[SlideSTT]


FactStatus = Literal["matched", "mismatched", "missing", "unverified", "sound_alike", "approximate"]
MismatchCause = Literal["speaker_error", "asr_error", "approximation"]


class FactCheck(BaseModel):
    fact_id: str
    type: str
    value: str = Field(description="대본 표기")
    normalized: str
    importance: Importance
    key_point_ids: list[str]
    status: FactStatus = Field(
        description="matched: 같은 값을 말함 / mismatched: 다른 값을 말함 / missing: 없음 / unverified: 규칙으로 판단 불가(LLM 확인)"
        " / sound_alike: 발음이 비슷한 다른 말로 나옴 — 판단 보류, 비율에서 뺌 / approximate: 값을 어림해 말함"
    )
    stt_value: str | None = Field(
        default=None, description="STT 에서 찾은 표기 (다른 값이면 그 값)"
    )
    stt_ids: list[int] = Field(default_factory=list)
    note: str = ""
    # 다른 값이 나왔을 때(mismatched)만 채운다 — 원인 신호 (`facts.mismatch_signals`)
    stt_normalized: str | None = None
    stt_numeric_value: float | None = None
    stt_qualifier: str | None = None
    stt_span: tuple[int, int] | None = None
    signals: list[str] = Field(default_factory=list, description="원인 판단에 쓰는 규칙 신호")
    rule_cause: MismatchCause | None = Field(
        default=None, description="규칙 신호만으로 본 원인 (비교용)"
    )
    sound: str | None = Field(
        default=None, description="두 수의 발음 관계: similar / near / swap / different / approx"
    )
    cause: MismatchCause | None = Field(
        default=None, description="규칙으로 정한 원인 (발음이 비슷하면 정하지 않음)"
    )
    cause_reason: str = ""


class Alignment(BaseModel):
    sentence_index: int = Field(description="대본 문장 번호")
    stt_ids: list[int] = Field(description="가장 비슷한 STT 문장 번호 (연속 1~3문장)")
    coverage: float = Field(description="대본 문장의 내용 형태소 중 STT 에 나온 비율 (0~1)")


HintCause = Literal["asr_error", "speaker_error", "paraphrase"]


class SimilarItem(BaseModel):
    """비슷한 말: 대본과 STT 의 같은 자리에 발음이 비슷한 다른 말(단어·수치)이 나온 곳.

    발표자가 잘못 말했는지 음성 인식이 잘못 적었는지는 텍스트만으로 가릴 수 없다. 그래서 점수 비율에서 빼고(판단 보류)
    개수만 점수에 넣으며, 위치와 함께 저장해 두고 리뷰 agent 가 사용자에게 확인한 뒤 점수를 다시 계산한다 (`confirm.py`).
    """

    item_id: str
    kind: Literal["number", "word"]
    script_text: str = Field(description="대본 표현")
    stt_text: str = Field(description="STT 표현")
    script_sentence_index: int = Field(description="대본 문장 번호")
    script_span: tuple[int, int] = Field(description="정규화 대본 텍스트 기준 위치")
    stt_sentence_index: int = Field(description="정규화 STT 문장 번호 (T번호)")
    stt_span: tuple[int, int] = Field(description="정규화 STT 텍스트 기준 위치")
    stt_raw_span: tuple[int, int] | None = Field(
        description="원본 STT 텍스트 기준 위치 — Deepgram 단어 타임스탬프와 맞출 때 쓴다"
    )
    fact_id: str | None = Field(default=None, description="관련 Critical Fact")
    key_point_ids: list[str] = Field(description="이 자리가 걸린 Key Point")
    signals: list[str] = Field(description="규칙 신호 (발음 거리, 읽기 비교 등)")
    rule_guess: HintCause = Field(
        description="규칙 신호만으로 본 원인 — 참고 (asr_error: 인식 오류 쪽 / speaker_error: 발표자 실수 쪽)"
    )


CoverageStatus = Literal[
    "covered", "partial", "missing", "contradicted"
]  # Key Point 판정 (코드가 문장 판정을 모아 정한다)
SentenceStatus = Literal[
    "said", "partial", "missing", "contradicted"
]  # 대본 문장 판정 (코드가 전달 단위 판정을 모아 정한다)
UnitStatus = Literal["said", "vague", "missing", "contradicted"]  # 전달 단위 판정 (LLM)


class UnitJudgment(BaseModel):
    unit_id: str = Field(description="전달 단위 번호. 입력에 적힌 그대로 (예: S1-U2)")
    status: UnitStatus


class SentenceJudgment(BaseModel):
    # 근거 → 이유 → 단위 판정 순서로 생성하게 해서, 근거를 먼저 찾고 판정하도록 한다
    sentence_id: int = Field(description="대본 문장 번호 [S번호]")
    evidence: list[int] = Field(
        description="이 문장의 내용이 나온 STT 문장 번호 [T번호]. 하나도 없으면 빈 목록"
    )
    reason: str = Field(
        description="판단 이유 한 문장. 흐리게 말했거나 빠졌거나 다르게 말한 단위가 있으면 무엇인지"
    )
    units: list[UnitJudgment] = Field(description="이 문장의 전달 단위마다 하나씩")
    confidence: Literal["high", "medium", "low"]


class NameCheck(BaseModel):
    name_id: str = Field(description="이름 확인 목록의 번호 (N1, N2 …)")
    said: bool = Field(description="발표자가 이 이름을 말했는가 (한글로 받아 적혔어도 말한 것)")
    evidence: list[int] = Field(description="말한 STT 문장 번호")


class SemanticEvaluation(BaseModel):
    sentences: list[SentenceJudgment] = Field(description="판정 대상 대본 문장마다 하나씩")
    name_checks: list[NameCheck] = Field(description="이름 확인 목록마다 하나씩")


class VerifiedSentence(BaseModel):
    sentence_id: int
    reason: str = Field(description="최종 판단 이유 한 문장")
    units: list[UnitJudgment] = Field(description="이 문장의 전달 단위마다 하나씩, 최종 판정")


class VerifierResult(BaseModel):
    judgments: list[VerifiedSentence] = Field(description="검증 대상 문장마다 하나씩")


class UnitResult(BaseModel):
    """전달 단위 한 개의 판정 (LLM 1차 → 교차 검증)."""

    unit_id: str
    text: str
    fact_ids: list[str] = Field(default_factory=list)
    status: UnitStatus = Field(description="최종 판정")
    first_status: UnitStatus = Field(description="1차 판정 (LLM 판정 + 규칙 보정)")
    raised_by_rule: bool = Field(
        default=False,
        description="LLM 은 missing 이라 했지만 이 단위의 수치·이름이 STT 에 있어 vague 로 올림",
    )


class SentenceResult(BaseModel):
    """대본 문장 한 개의 판정. 전달 단위 판정을 모은 문장 판정 + 규칙 결과 + 충돌."""

    sentence_index: int
    text: str
    role: str
    status: SentenceStatus = Field(description="최종 판정 (전달 단위 최종 판정을 모은 것)")
    first_status: SentenceStatus = Field(description="LLM 1차 판정 (전달 단위 1차 판정을 모은 것)")
    units: list[UnitResult] = Field(default_factory=list, description="전달 단위별 판정")
    confidence: str
    evidence: list[int] = Field(description="근거 STT 문장 번호 (범위 밖 번호는 버림)")
    reason: str
    lexical_coverage: float = Field(
        description="이 문장의 내용 형태소가 STT 정렬 구간에 나온 비율 (`align_sentences`)"
    )
    evidence_coverage: float = Field(
        description="이 문장의 내용 형태소가 LLM 근거 STT 문장에 나온 비율 (근거가 이 문장과 관련 있는지)"
    )
    fact_statuses: dict[str, str] = Field(description="이 문장에 있는 사실별 규칙 검증 결과")
    similar_ids: list[str] = Field(
        default_factory=list, description="이 문장 자리의 비슷한 말 (R번호, 판단 보류)"
    )
    conflicts: list[str] = Field(
        default_factory=list, description="규칙 결과와 LLM 판정이 어긋난 이유"
    )
    verified: bool = False
    expression_mode: Literal["verbatim_like", "paraphrased", "undetermined"] | None = Field(
        default=None,
        description="said 문장만: 대본 표현에 가까움 · 바꿔 말함 · 구분 어려움 (lexical_coverage 기준, 감점 아님)",
    )


class KeyPointResult(BaseModel):
    """Key Point 판정 = 근거 문장 판정을 모은 것 (코드, aggregate_status)."""

    key_point_id: str
    content: str
    importance: Importance
    sentence_indices: list[int]
    status: CoverageStatus = Field(description="최종 판정")
    first_status: CoverageStatus = Field(description="LLM 1차 문장 판정을 모은 판정")
    evidence: list[int]
    evidence_text: str
    reason: str
    lexical_coverage: float
    fact_statuses: dict[str, str] = Field(
        description="이 Key Point 에 연결된 사실별 규칙 검증 결과"
    )
    similar_ids: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list, description="근거 문장들의 충돌 이유")
    verified: bool = False


# ── 결과 상세 (선택 필드, 기능 문서 5-6 '장 상세') ─────────────────────
# 기존 점수(`scores`)와 판정은 그대로 두고, 그 위에 상쇄 없는 전달률 · 상태 · 이유 · 말의 구성을 더한다.
# 값을 계산할 수 없으면 0 이 아니라 null 이고, 상태(`state`)와 이유(`reason_codes`)가 까닭을 말한다.

MetricState = Literal[
    "ok", "provisional", "not_applicable", "pending", "failed", "skipped_by_deadline", "estimated"
]
SttIntegrity = Literal["complete", "incomplete", "unknown"]
ZeroKind = Literal["slide_skipped", "all_missing", "missing_or_contradicted"]
LegacyZeroKind = Literal["cancelled_to_zero", "clamped_negative"]
ExpressionMode = Literal["verbatim_like", "paraphrased", "undetermined"]
MixCategory = Literal[
    "script",
    "other_slide",
    "procedural",
    "off_script",
    "related_aside",
    "unrelated",
    "undetermined",
]
MIX_CATEGORIES: tuple[str, ...] = (
    "script", "other_slide", "procedural", "off_script", "related_aside", "unrelated", "undetermined",
)  # fmt: skip


class Metric(BaseModel):
    """지표 한 칸. `value` 가 null 이면 `state` · `reason_codes` 가 까닭이다 (0 과 섞지 않는다).

    `evaluated` · `excluded` 는 그 지표의 단위 개수다: 전달 · 다르게 말함은 핵심 내용, 수치는 핵심 수치,
    대본 재현은 형태소.
    """

    value: float | None
    state: MetricState
    reason_codes: list[str] = Field(default_factory=list)
    evaluated: int = 0
    excluded: int = 0


class MixItem(BaseModel):
    """말의 구성 막대 한 칸 (화면에 그릴 범주)."""

    category: MixCategory
    tokens: int
    share: float


class Repetitions(BaseModel):
    count: int = Field(description="바로 반복한 말의 횟수 (원본 STT 에서 센 관찰값)")
    confidence: Literal["low"] = "low"


class SpeechMix(BaseModel):
    """말의 구성: 정규화한 STT 의 내용 형태소를 범주 하나씩으로 나눈 것. 어떤 점수도 깎지 않는다."""

    state: MetricState
    reason_codes: list[str] = Field(default_factory=list)
    tokens: int = Field(description="분모 — 정규화한 STT 의 내용 형태소 수")
    categories: dict[str, int] = Field(
        description="범주별 형태소 수 (`MIX_CATEGORIES` 일곱 개 모두)"
    )
    display: list[MixItem] = Field(
        description="화면에 그릴 목록. 검증하지 않은 범주는 `off_script` 로 합친다"
    )
    repetitions: Repetitions


class OffscriptSpan(BaseModel):
    """대본 밖 구간 하나. 위치는 `stt_sentences[stt_sentence_index]` 안의 [시작, 끝) 코드 포인트다."""

    id: str = Field(description="O1, O2 … (장마다 1부터)")
    stt_sentence_index: int
    start: int
    end: int
    category: MixCategory
    display_category: MixCategory = Field(description="화면에 쓸 범주 (검증하지 않은 범주는 합침)")
    source: Literal["rule", "llm"]
    other_slide_number: int | None = Field(default=None, description="다른 장 내용이면 그 장 번호")
    tokens: int


class Offscript(BaseModel):
    """대본 밖 구간 목록 (장별 상세 화면의 강조용)."""

    version: int = 1
    state: MetricState
    reason_codes: list[str] = Field(default_factory=list)
    spans: list[OffscriptSpan] = Field(default_factory=list)


class CriticalGap(BaseModel):
    """중요(critical · high) 핵심 내용 중 빠뜨리거나 다르게 말한 것. Take 요약에서는 `slide_number` 가 있다."""

    slide_number: int | None = None
    key_point_id: str
    content: str
    importance: Importance
    status: CoverageStatus
    said_in_slides: list[int] = Field(
        default_factory=list,
        description="그 내용을 다른 장에서 말한 장 번호 (점수로 인정하지 않는다)",
    )


class FactSummary(BaseModel):
    matched: int = 0
    approximate: int = 0
    mismatched: int = 0
    missing: int = 0
    pending: int = Field(default=0, description="발음이 비슷해 확인을 기다리는 수치 (sound_alike)")
    accuracy: Metric
    accuracy_range: dict[str, float] | None = Field(
        default=None,
        description="확인 대기 수치를 모두 맞음(best) · 모두 틀림(worst)으로 가정한 정확도. 대기가 없으면 null",
    )


class Expression(BaseModel):
    """'말함' 문장의 표현 방식 개수. 바꿔 말해도 감점하지 않는다."""

    verbatim_like: int = 0
    paraphrased: int = 0
    undetermined: int = 0


class Fidelity(BaseModel):
    """기존 `script_fidelity` 의 두 성분."""

    recall: Metric
    precision: Metric


class SlideLegacy(BaseModel):
    content_coverage_raw: float | None = Field(
        description="기존 content_coverage 를 0 으로 자르기 전의 값"
    )
    zero_kind: LegacyZeroKind | None = None


class FactCounts(BaseModel):
    matched: int = 0
    approximate: int = 0
    mismatched: int = 0
    missing: int = 0
    pending: int = 0


class Fallback(BaseModel):
    """실패 장의 간이 값 (응답 `failed[].fallback`). 확정 값과 합치지 않고 따로 보인다.

    `lexical`: 평가 기준이 있는 장의 규칙 값. `lexical_no_rubric`: 평가 기준이 없어 대본 글(`script`)로만 낸 값
    (중요도 없음). `lexical_coverage` 는 문장마다 대본 내용 형태소가 STT 짝 구간에 나온 비율을 1 · 0.5 · 0 으로
    바꿔 평균한 간이 전달률이고, `delivery_band` 는 그 3단계다.
    """

    version: int = 1
    method: Literal["lexical", "lexical_no_rubric"]
    delivery_band: Literal["mostly", "about_half", "little"] | None
    lexical_coverage: float | None
    facts: FactCounts | None = None
    fact_accuracy: Metric | None = None
    script_recall: Metric | None = None
    speech_mix: SpeechMix | None = None
    stt_integrity: SttIntegrity
    reason_codes: list[str] = Field(default_factory=list)


class SlideDetail(BaseModel):
    """장 상세 (`evaluations[].detail`). `/takes/rescore` 가 확인 답을 반영해 대본 쪽 지표를 다시 계산한다."""

    version: int = 1
    state: MetricState
    reason_codes: list[str] = Field(default_factory=list)
    stt_integrity: SttIntegrity
    rubric_quality: Literal["full", "sentence_fallback"]
    delivery: Metric = Field(
        description="핵심 내용 전달률: covered 1 · partial 0.5 · missing 0 · contradicted 0 의 중요도 가중 평균 (상쇄 없음)"
    )
    zero_kind: ZeroKind | None = None
    contradiction: Metric = Field(description="같은 가중치로 낸 contradicted 의 몫")
    status_shares: dict[str, float] | None = Field(
        description="covered · partial · missing · contradicted 의 가중 몫 (합 1). 핵심 내용이 없으면 null"
    )
    key_points: dict[str, dict[str, int]] = Field(
        description="중요도(critical · high · normal)별 판정 개수"
    )
    critical_gaps: list[CriticalGap] = Field(default_factory=list)
    legacy: SlideLegacy
    facts: FactSummary
    expression: Expression
    fidelity: Fidelity
    speech_mix: SpeechMix


class SlideEvaluation(BaseModel):
    """Structured Evaluation: 슬라이드 한 장의 평가 결과. 이 모양 그대로 DB 에 저장한다."""

    take_id: str
    script_name: str
    slide_number: int
    rubric_id: str = Field(description="채점에 쓴 평가 기준 (대본 분석 결과의 rubric_id)")
    scores: dict
    sentences: list[SentenceResult] = Field(
        description="대본 문장별 판정 (전달 단위 판정을 모은 문장 판정 + 규칙 + 교차 검증)"
    )
    key_points: list[KeyPointResult] = Field(
        description="Key Point 판정 = 근거 문장 판정을 모은 것"
    )
    critical_facts: list[FactCheck]
    similar_items: list[SimilarItem] = Field(
        description="비슷한 말 — 비율에서 뺀 판단 보류 항목 (위치 포함)"
    )
    fidelity: dict = Field(description="대본 충실도 세부 (recall / precision / fidelity)")
    alignments: list[Alignment]
    verification: dict = Field(description="교차 검증 여부, 대상 문장, 충돌 조건별 문장 번호")
    stt_sentences: list[str] = Field(description="정규화한 STT 문장 (근거 번호 T0, T1 … 의 원문)")
    stt_text: str = Field(
        default="",
        description="정규화한 STT 전체 텍스트 — 사용자 확인 뒤 점수를 다시 계산할 때 쓴다 (`confirm.py`)",
    )
    applied_answers: dict[str, str] = Field(
        default_factory=dict,
        description="그 장의 채점에 이미 반영한 확인 답 {item_id: as_script · as_stt}. 처음 채점에는 비어 있다. Take 단위 confirmations(아직 답하지 않은 항목)와 다르다",
    )
    stt_integrity: SttIntegrity | None = Field(
        default=None,
        description="요청의 STT 끊김 값으로 정한 STT 완전성. 이 필드가 생기기 전의 채점에는 없다",
    )
    detail: SlideDetail | None = Field(default=None, description="장 상세 (선택 필드)")
    offscript: Offscript | None = Field(
        default=None, description="대본 밖 구간 목록 (장별 상세 화면의 강조용)"
    )


ConfirmAnswer = Literal["as_script", "as_stt"]
