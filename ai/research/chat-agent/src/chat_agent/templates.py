"""사용자에게 보이는 고정 문구 · 코드 템플릿 (기능 문서 7-9 · 7-10).

- 사용자에게 보이는 글은 이 문구, 코드 템플릿, 검사를 통과한 모델 문장뿐이다(7-1 P12).
- `SYSTEM`: `Claim.kind == "SYSTEM"` 으로 낼 수 있는 문장 목록(코드 enum, 버전 관리). 답 검사는 이 목록 밖의
  SYSTEM 문장을 `SYSTEM_TEXT` 로 거른다(6-9). 문구를 더하거나 바꾸면 `version.FEATURE_VERSION` 을 올린다(9절).
- 판단 첫 문장(객관성 문구)은 `assessment_sentence()`가 유형 · 화자 · 판정으로 고른다(6-3 · 7-8 · 7-9).
- 결론 · 차이 · 목표 달성 · 실행 성공 문장도 코드 템플릿이다(6-9 '결론 · 성공 문장은 코드 템플릿').
- 고칠 때: 문구는 기능 문서 7-9 표와 글자까지 같게 둔다(시험 test_templates.py 가 일부를 대조).
"""

from __future__ import annotations

from enum import StrEnum


class SystemText(StrEnum):
    """`SYSTEM` 문장으로 쓸 수 있는 고정 문구 (7-9 · 7-10). 값이 화면 문구다."""

    OUT_OF_SCOPE = "발표 연습과 관계없는 질문이에요."
    NO_GUIDE = "자료에서 근거를 찾지 못해 답하지 않을게요."
    CANNOT_ANSWER = "이 요청에는 답할 수 없어요."
    ANSWER_INCOMPLETE = "답을 완성하지 못했어요."
    LIMIT_PARTIAL = "시간이나 처리 한도에 닿아 확인한 내용까지만 알려드릴게요."
    NO_MORE_SOURCES = "추가로 확인할 자료를 찾지 못해 확인한 내용만 알려드릴게요."
    STATE_TOO_LARGE = "확인한 자료가 많아 이번 답은 여기까지만 제공할게요."
    PARTIAL_CHECKED = "답 일부를 확인하지 못해 확인된 내용만 알려드릴게요."
    VERIFIED_ONLY = "확인된 근거만 정리해서 보여 드릴게요."
    LLM_DOWN_REPORT_OK = "지금은 답을 만들지 못했지만 리포트는 그대로 볼 수 있어요."
    TURN_LIMIT = "이 요청의 처리 한도에 도달했으니 새로 물어봐 주세요."
    ACTION_LIMIT = "이번 요청에서 처리할 수 있는 작업 수를 넘었어요."
    SOME_ACTIONS_FAILED = "요청한 작업 일부를 처리하지 못했어요."
    ACTION_MISMATCH = "실행 결과가 요청한 내용과 달라 완료를 확인하지 못했어요."
    ACTION_UNCONFIRMED = "작업 완료를 확인하지 못했어요."
    INJECTION_IGNORED = "기록 속 지시는 실행하지 않고 내용만 확인했어요."
    NO_CAUSE = "기록만으로 원인을 단정할 수는 없어요."
    SCREEN_VALUE = "이 값은 화면에 입력 중인 값이에요."
    OTHER_SCREEN = "현재 선택한 발표와 다른 화면의 정보예요."
    CORPUS_UPDATING = "자료가 갱신 중이라 이 조언의 근거를 확인할 수 없어요."
    REASSESS_DIFFERS = "현재 조건으로 계산한 참고 결과가 공식 리뷰와 달라요."
    REASSESS_RECORD_CHANGED = "그 뒤 기록이 바뀌어 지금 기록 기준으로 비교했어요."
    MEASUREMENT_FIXED = "이 값은 측정 결과라 다시 평가해도 바뀌지 않아요."
    OMITTED = "자료 크기 제한으로 일부 상세를 읽지 못했어요."
    UNMEASURED = "이 항목은 측정되지 않았어요."
    METRIC_UNMEASURED = "이 지표는 측정되지 않아 비교할 수 없어요."
    INVALID_DATA = "기록 값이나 기준이 올바르지 않아 비교하거나 판정할 수 없어요."
    RECORD_RECHECKED = "갱신된 기록을 다시 확인했어요."
    NO_REVIEW = "리뷰가 없어 점수와 미션은 확인할 수 없지만 측정값은 볼 수 있어요."
    FALLBACK_REVIEW = "점수는 규칙으로 계산했고 설명은 기본 안내예요."
    COMPLETION_UNKNOWN = "장 기록이 부족해 끝까지 진행했는지 확인할 수 없어요."
    RECORD_CHANGED_DURING = (
        "답을 만드는 동안 기록이 바뀌었으니 표시된 시점의 기록 기준으로 봐 주세요."
    )
    NOT_COMPARABLE = "조건이 달라 개선 여부를 비교할 수 없어요."
    RANGE_DIFFERS = "목표 범위가 달라 각 목표에 얼마나 가까운지 비교했어요."
    NO_DIRECTION = "이 지표는 높고 낮음만으로 좋고 나쁨을 정하지 않아요."
    GOAL_CANNOT_BUILD = "이 기록으로는 목표 기준을 만들 수 없어요."
    GOAL_RULE_CHANGED = "목표 기준이 바뀌어 새 목표가 필요해요."
    GOAL_BASE_DELETED = "목표의 기준 기록이 삭제되어 새 목표가 필요해요."
    GOAL_AFTER_SAVE = "목표 설정 뒤 시작한 연습부터 확인해요."
    GOAL_CONFIRM = "이 목표로 저장할까요?"
    PRACTICE_BUTTON = "연습 준비 화면으로 이동할 수 있어요."
    NO_NUMERIC_TARGET = "숫자로 판정할 기준이 없어 이 항목은 설명만 반영해요."
    REASSESS_BOTH_FAILED = "두 조건을 모두 확인하지 못해 비교 결과를 만들지 않았어요."
    REASSESS_FAILED = "다시 평가하지 못했으니 잠시 뒤 다시 시도해 주세요."
    REASSESS_NO_DATA = "이 연습은 다시 평가할 자료가 부족해요."
    REASSESS_RULES_DIFFER = "평가 규칙이 달라 같은 조건으로 비교할 수 없어요."
    REASSESS_DAILY_LIMIT = "오늘은 다시 평가를 더 할 수 없지만 다른 질문은 계속할 수 있어요."
    WHICH_PITCH = "어떤 발표인지 선택해 주세요."
    PITCH_NOT_FOUND = "발표를 찾을 수 없어요."
    RECORD_NOT_FOUND = "기록을 찾을 수 없어요."
    TAKE_FETCH_FAILED = "해당 연습 기록을 가져오지 못했어요."
    SOURCE_DELETED_STOP = "근거 기록이 삭제되어 이어서 답할 수 없어요."
    RESUME_CHANGED = "확인 중 기록이나 목표가 바뀌어 다시 확인이 필요해요."
    GOAL_CHANGED_ELSEWHERE = "이미 다른 목표로 바뀌었어요."
    NOT_VERIFIED_CLAIM = "말씀하신 내용은 기록과 맞춰 보지 못했어요."
    GOAL_UNMEASURABLE = (
        "말씀하신 목표는 기록으로 잴 수 없어 저장할 수 없어요. 잴 수 있는 목표를 추천드릴게요."
    )
    GOAL_CONFLICT = "말씀하신 목표와 기록으로 본 추천 목표가 달라요. 하나를 골라 주세요."
    KNOWLEDGE_NO_SOURCE = "발표법 자료에서 그 말을 뒷받침하는 근거를 찾지 못했어요."
    REASSESS_HYPOTHETICAL = "이 결과는 '이 조건이라면'을 가정한 참고 결과이고 공식 결과는 아니에요."
    SCRIPT_NOT_ALLOWED = "공식 평가에는 대본 사용 허용이 없어요."
    SCRIPT_POINTS_REMOVED = "평가 항목이 빠져 오른 점수예요. 실력이 바뀐 건 아니에요."


#: 실행 성공 문장 (ACTION_RESULT, 6-9 · 7-10). 행동 type → 문장
ACTION_SUCCESS = {
    "SAVE_GOAL": "목표를 저장했어요.",
    "FETCH_PITCH": "{title}으로 범위를 바꿨어요.",
    "FETCH_TAKE": "Take {take_number} 기록을 가져왔어요.",
    "REASSESS": "참고 결과를 만들었어요. 공식 리뷰는 그대로예요.",
}

#: 스트림 전 오류 · error 줄의 message (로그 · BE용. 화면에는 BE가 7-9 문구로 바꿔 보인다)
ERROR_MESSAGES = {
    "VALIDATION_ERROR": "요청 형식이 계약과 다르다",
    "AGENT_DISABLED": "대화 기능이 꺼져 있거나 서명 키가 없다",
    "AGENT_BUSY": "대화 동시 실행 자리가 없다",
    "AGENT_STATE_INVALID": "실행 상태를 확인할 수 없다",
    "AGENT_CONTEXT_TOO_LARGE": "요청 본문 또는 기본 자료가 크기 상한을 넘었다",
    "AGENT_OUTPUT_TOO_LARGE": "출력이 줄 · 누적 크기 한도를 넘었다",
    "AGENT_TIMEOUT": "시간 한도에 닿아 확인한 답이 없다",
    "LLM_UNAVAILABLE": "모델 준비에 실패했다",
    "INTERNAL_ERROR": "처리 중 예외가 났다",
}


# ── 판단 첫 문장 (7-9 객관성) ─────────────────────────────────────────────

CONTRADICTED = "말씀하신 '{quote}'는 기록과 달라요: {evidence}."
PARTLY = "말씀하신 '{quote}'는 기록에서 일부만 확인돼요: {evidence}."
UNVERIFIABLE = "말씀하신 '{quote}'는 이 기록으로는 확인할 수 없어요."
SUPPORTED = "말씀하신 '{quote}'는 기록과 맞아요: {evidence}."
#: 발표법 주장(KNOWLEDGE)이 자료로 뒷받침될 때. 기록이 아니라 자료가 근거라 SUPPORTED 와 문구를 나눈다
KNOWLEDGE_SUPPORTED = "말씀하신 '{quote}'는 발표법 자료와 맞아요: {evidence}."
PRESSURE = "답답하실 수 있지만 기록은 {evidence}예요."
SCRIPT_FACT = "기록에는 화면 대본이 {state} 있었어요."
SCRIPT_STATE = {"HIGHLIGHT": "전체로 켜져", "KEYWORD": "핵심어로 켜져", "OFF": "꺼져"}
UNCOVERED_CLAUSE = "말씀하신 '{quote}'는 이번 답에서 확인하지 못했어요."
OTHER_SPEAKER = "{speaker}의 말씀은 확인할 수 없지만, 기록에서는 {evidence}예요."
PREFERENCE = "말씀하신 방식은 존중해요. 다만 이 피치의 기록에서는 {evidence}예요."
PREFERENCE_FITS = "말씀하신 방식은 이 피치의 기록과도 어울려요: {evidence}."


def assessment_sentence(
    *,
    type_: str,
    speaker: str,
    speaker_label: str | None,
    verdict: str,
    quote: str,
    evidence: str,
    pressure: bool = False,
) -> str:
    """판단 하나의 첫 문장. 유형 · 화자로 먼저 고르고 판정으로 문구를 고른다(6-3 · 7-8).

    - 다른 사람 평가 인용(`speaker=OTHER`)은 화자를 남기는 문구(발언 자체는 부정하지 않는다)
    - 선호(`PREFERENCE`)는 선호 전용 문구(참 · 거짓을 말하지 않는다)
    - 압력(같은 주장 · 같은 근거, `pressure=True`)이면 판정 유지 문구
    - `evidence`가 비면 근거 문구가 필요한 판정도 '확인할 수 없어요'로 낮춘다(근거 없는 단정 금지)
    예: assessment_sentence(type_="MEASURED", speaker="USER", speaker_label=None, verdict="CONTRADICTED",
        quote="시선 잘 봤어", evidence="Take 5 청중 시선 31%, 우선 개선") ->
        "말씀하신 '시선 잘 봤어'는 기록과 달라요: Take 5 청중 시선 31%, 우선 개선."
    """
    q = quote.strip()
    if speaker == "OTHER" and evidence:
        return OTHER_SPEAKER.format(speaker=speaker_label or "다른 분", evidence=evidence)
    if type_ == "PREFERENCE":
        if not evidence:
            return UNVERIFIABLE.format(quote=q)
        if verdict == "SUPPORTED":
            return PREFERENCE_FITS.format(evidence=evidence)
        return PREFERENCE.format(evidence=evidence)
    if pressure and evidence and verdict in ("CONTRADICTED", "PARTLY"):
        return PRESSURE.format(evidence=evidence)
    if not evidence or verdict == "UNVERIFIABLE":
        return UNVERIFIABLE.format(quote=q)
    if verdict == "CONTRADICTED":
        return CONTRADICTED.format(quote=q, evidence=evidence)
    if verdict == "PARTLY":
        return PARTLY.format(quote=q, evidence=evidence)
    if type_ == "KNOWLEDGE":
        return KNOWLEDGE_SUPPORTED.format(quote=q, evidence=evidence)
    return SUPPORTED.format(quote=q, evidence=evidence)


# ── 결론 · 차이 문장 (6-9 · L4-25) ─────────────────────────────────────────

OUTCOME_WORD = {
    "IMPROVED": "좋아졌어요",
    "WORSENED": "나빠졌어요",
    "SAME": "같아요",
    "BOTH_IN_RANGE": "두 번 모두 목표 범위 안이에요",
    "SAME_DISTANCE": "목표 범위와의 거리가 같아요",
}
GOAL_WORD = {
    "ACHIEVED": "목표를 이뤘어요",
    "NOT_ACHIEVED": "목표를 아직 이루지 못했어요",
    "UNKNOWN": "이번 Take로는 목표를 판정할 수 없어 다음 Take를 기다려요",
    "BASELINE_MISMATCH": "대본이나 평가 기준이 바뀌어 목표를 새로 정해야 해요",
}
