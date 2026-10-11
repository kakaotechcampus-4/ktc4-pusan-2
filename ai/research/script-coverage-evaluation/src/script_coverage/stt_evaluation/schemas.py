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
    confirmations: dict[str, str] = Field(
        default_factory=dict,
        description="반영한 사용자 확인 (비슷한 말 item_id → as_script / as_stt). 처음 채점에는 비어 있다",
    )
    created_at: str


ConfirmAnswer = Literal["as_script", "as_stt"]
