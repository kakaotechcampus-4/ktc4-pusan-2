"""사용자에게 보이는 고정 문구 · 코드 템플릿 (기능 문서 7-9 · 7-10).

- 사용자에게 보이는 글은 이 문구, 코드 템플릿, 검사를 통과한 모델 문장뿐이다(7-1 P12).
- `SYSTEM`: `Claim.kind == "SYSTEM"` 으로 낼 수 있는 문장 목록(코드 enum, 버전 관리). 답 검사는 이 목록 밖의
  SYSTEM 문장을 `SYSTEM_TEXT` 로 거른다(6-9). 문구를 더하거나 바꾸면 `version.FEATURE_VERSION` 을 올린다(9절).
- 판단 첫 문장(객관성 문구)은 `assessment_sentence()`가 유형 · 화자 · 판정으로 고른다(6-3 · 7-8 · 7-9).
- 결론 · 차이 · 목표 달성 · 실행 성공 문장도 코드 템플릿이다(6-9 '결론 · 성공 문장은 코드 템플릿').
- 고칠 때: 문구는 기능 문서 7-9 표와 글자까지 같게 둔다(시험 test_templates.py 가 일부를 대조).
"""

from __future__ import annotations

import re
from enum import StrEnum


class SystemText(StrEnum):
    """`SYSTEM` 문장으로 쓸 수 있는 고정 문구 (7-9 · 7-10). 값이 화면 문구다."""

    #: 범위 밖 이유 OTHER(발표와 무관한 질문)의 문장. 이유별 문장은 아래 CONTENT_COACHING · QA_PRACTICE · PRACTICE_PLAN
    OUT_OF_SCOPE = "발표 연습과 관계없는 질문이에요."
    CONTENT_COACHING = (
        "대본 · 발표자료 · 평가 기준 · 제한 시간을 평가하거나 고쳐 쓰는 일은 도와드리지 않아요. "
        "연습 기록으로 볼 수 있는 대본 의존 · 대본 응시 · 내용 전달도는 같이 볼 수 있어요."
    )
    QA_PRACTICE = (
        "예상 질문 만들기 · 질의응답 연습은 도와드리지 않아요. "
        "연습 기록에서 말 속도 · 시선 같은 전달 방식은 같이 볼 수 있어요."
    )
    PRACTICE_PLAN = (
        "연습 계획 · 일정은 짜 드리지 않아요. "
        "대화 목표를 정해 두면 다음 Take에서 이뤘는지 확인해 드릴 수 있어요."
    )
    #: 범위 밖인데 이유(`scope_reason`)가 없거나 모르는 값일 때의 중립 문장(#904 리뷰). '발표와 관계없다'고 단정하지 않는다
    OUT_OF_SCOPE_UNKNOWN = "이 요청은 발표 연습 기록으로 도와드릴 수 있는 범위 밖이에요."
    NO_GUIDE = "자료에서 근거를 찾지 못해 답하지 않을게요."
    CANNOT_ANSWER = "이 요청에는 답할 수 없어요."
    #: 공식 결과를 바꿔 달라는 요청(답 초안 route CANNOT_DO, 6-2 하지 않는 일)
    CANNOT_CHANGE_RECORD = "점수 · 차원 상태 · 공식 미션 · 리뷰 결과는 바꿀 수 없어요. 기록은 그대로이고 다음 Take에서 달라지는지 볼 수 있어요."
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
    GOAL_CLOSED_ACHIEVED = "대화 목표는 이뤄서 닫혔어요."
    GOAL_CLOSED_NOT_ACHIEVED = "대화 목표는 이루지 못한 채 닫혔어요."
    GOAL_CLOSED_MISMATCH = "대본이나 평가 기준이 바뀌어 대화 목표를 닫았어요. 새 목표가 필요해요."
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
    #: 화면 조작 방법을 물었는데 찾은 사용법 자료가 없을 때(답 초안 usage_unanswered, 6-10 · 7-10)
    USAGE_NO_GUIDE = "이 화면 사용법은 아직 확인된 안내 자료가 없어 답해 드리기 어려워요."
    KNOWLEDGE_NO_SOURCE = "발표법 자료에서 그 말을 뒷받침하는 근거를 찾지 못했어요."
    #: 일반 조언(`GENERAL`) 문장 묶음 바로 앞에 코드가 붙이는 안내(6-10). 일반 조언이 하나도 남지 않으면 뺀다
    GENERAL_INTRO = "자료에서 확인한 내용은 아니고 일반적인 조언이에요."
    #: 가벼운 말(계획 route SMALL_TALK, P-0008 5-1)의 고정 문장. 모델이 쓰지 않는다. 숫자 · 기록 값 · 판정 낱말이 없다.
    #: 할 수 있는 일 목록은 묻는 말(CAPABILITY)에만 쓴다 — 인사에 다시 늘어놓으면 대화창 첫 화면 안내(FE)와 겹친다
    TALK_GREETING = "안녕하세요! 발표 연습을 같이 볼게요."
    TALK_THANKS = "도움이 됐다니 다행이에요. 더 궁금한 점이 있으면 물어봐 주세요."
    TALK_FAREWELL = "수고하셨어요. 다음 연습 뒤에도 기록을 같이 볼게요."
    TALK_PRACTICE = "네, 연습해 봐요."
    #: 할 수 있는 일(FE 첫 화면 안내와 같은 낱말). 사용법 카드가 실리지 않은 동안은 화면 사용법을 말하지 않는다
    TALK_CAPABILITY = "내 연습 기록에서 고칠 점 찾기, Take끼리 비교하기, 다음 연습 목표 잡기, 발표법 안내를 도와드릴 수 있어요."
    TALK_CAPABILITY_USAGE = "내 연습 기록에서 고칠 점 찾기, Take끼리 비교하기, 다음 연습 목표 잡기, 발표법 · 화면 사용법 안내를 도와드릴 수 있어요."
    #: 가벼운 말 뒤에 붙는 다음 걸음 — 기본 자료(범위 피치 · Take 유무)로 코드가 고른다
    NEXT_REVIEW = "최근 Take에서 고칠 점부터 같이 볼까요?"
    NEXT_FIRST_TAKE = "Take를 한 번 연습하면 그 기록을 같이 볼 수 있어요."
    NEXT_PICK_PITCH = "어느 발표를 볼지 말씀해 주시면 그 기록을 같이 볼게요."
    NEXT_FIRST_PITCH = "피치를 만들어 연습하면 그 기록을 같이 볼 수 있어요."
    #: 다른 피치의 Take 를 함께 비교해 달라고 했을 때(1-3). 값은 각각 말해도 좋아짐 · 나빠짐 · 차이를 말하지 않는다
    CROSS_PITCH_NO_COMPARE = "다른 발표의 Take끼리는 비교하지 않아요. 발표마다 따로 볼 수 있어요."
    REASSESS_HYPOTHETICAL = "이 결과는 '이 조건이라면'을 가정한 참고 결과이고 공식 결과는 아니에요."
    SCRIPT_NOT_ALLOWED = "공식 평가에는 대본 사용 허용이 없어요."
    SCRIPT_POINTS_REMOVED = "평가 항목이 빠져 오른 점수예요. 실력이 바뀐 건 아니에요."
    #: 거절 · 만료 · 취소 문장(7-10). BE 가 AI 를 다시 부르지 않고 이미 낸 문장 뒤에 덧붙인다. 글은 코드 템플릿 한 곳에 둔다
    REJECTED_SAVE_GOAL = "목표를 저장하지 않았어요."
    REJECTED_REASSESS = "다시 평가하지 않았어요."
    APPROVAL_EXPIRED = "확인 시간이 지나 실행하지 않았어요. 필요하면 다시 말해 주세요."
    APPROVAL_CANCELLED = "새 메시지를 받아 앞의 확인 요청은 취소했어요."
    #: [취소] 버튼으로 승인 대기를 끝냈을 때(새 메시지 없음, #900 · #910 리뷰)
    APPROVAL_CANCELLED_BY_USER = "확인 요청을 취소했어요."


#: 범위 밖 이유(1-3) → 고정 문장(7-10). 모델이 고른 이유를 코드가 이 문장으로 바꾼다(거절 문장은 모델이 쓰지 않는다)
SCOPE_REASONS = ("CONTENT_COACHING", "QA_PRACTICE", "PRACTICE_PLAN", "OTHER")
SCOPE_TEXT: dict[str, SystemText] = {
    "CONTENT_COACHING": SystemText.CONTENT_COACHING,
    "QA_PRACTICE": SystemText.QA_PRACTICE,
    "PRACTICE_PLAN": SystemText.PRACTICE_PLAN,
    "OTHER": SystemText.OUT_OF_SCOPE,
}


def scope_text(reason: object) -> SystemText:
    """범위 밖 이유 → 고정 문장. 이유가 없거나 모르는 값이면 중립 문장(`OTHER`의 '발표와 관계없다'로 단정하지 않는다:
    "대본 고쳐 줘"에 그 문장이 나가면 사실과 다르다)."""
    return (
        SCOPE_TEXT.get(reason, SystemText.OUT_OF_SCOPE_UNKNOWN)
        if isinstance(reason, str)
        else SystemText.OUT_OF_SCOPE_UNKNOWN
    )


#: 실행 성공 문장 (ACTION_RESULT, 6-9 · 7-10). 행동 type → 문장
ACTION_SUCCESS = {
    "SAVE_GOAL": "목표를 저장했어요.",
    "FETCH_PITCH": "{title}으로 범위를 바꿨어요.",
    "FETCH_TAKE": "Take {take_number} 기록을 가져왔어요.",
    "REASSESS": "참고 결과를 만들었어요. 공식 리뷰는 그대로예요.",
}

# ── 승인 확인 카드 (5-2 `ASK_CONFIRM` · 6-2) ─────────────────────────────────
#: 카드 글은 모델이 쓰지 않는다. 대기 행동의 `args`로 코드가 이 템플릿을 채운다(표시 문자열 검사 6-9를 거친다)
ASK_CONFIRM_TITLE = {"SAVE_GOAL": "대화 목표 저장", "REASSESS": "다시 평가"}
#: 목표 요약 문장(지표 이름 · 장 · 기준 Take · 방향). 값 뒤 조사를 쓰지 않는 꼴로 둔다
GOAL_SUMMARY = {
    "gt_base": "Take {take_number}의 {where}{label}({value})보다 높이는 목표예요.",
    "lt_base": "Take {take_number}의 {where}{label}({value})보다 낮추는 목표예요.",
    "in_range": "Take {take_number} 기준 {where}{label}의 목표 범위({value}) 안에 드는 목표예요.",
}
SAVE_GOAL_NOTE_REPLACE = "저장하면 지금 대화 목표가 이 목표로 바뀌어요."
SAVE_GOAL_NOTE_UNDO = "저장한 뒤에도 되돌릴 수 있어요."
REASSESS_SUMMARY = "Take {take_number} 기록을 {change}으로 다시 평가해 참고 결과를 만들어요."
REASSESS_CHANGE_TEXT = {
    "script_used_true": "대본을 보고 한 발표",
    "script_used_false": "대본을 보지 않은 발표",
    "targets": "내 평가 기준 {count}개",
}
REASSESS_NOTE = "참고 결과이고 공식 결과는 그대로예요"

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
#: 대본 사용 주장: 기록은 화면 대본 표시만 알려 준다. 실제로 봤는지는 단정하지 않는다(6-3 · L6-06)
SCRIPT_UNKNOWN = "실제로 대본을 봤는지는 기록으로 확인할 수 없어요."
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
        return fill(OTHER_SPEAKER, speaker=speaker_label or "다른 분", evidence=evidence)
    if type_ == "PREFERENCE":
        if not evidence:
            return UNVERIFIABLE.format(quote=q)
        if verdict == "SUPPORTED":
            return PREFERENCE_FITS.format(evidence=evidence)
        return PREFERENCE.format(evidence=evidence)
    if pressure and evidence and verdict in ("CONTRADICTED", "PARTLY"):
        return fill(PRESSURE, evidence=evidence)
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

#: 닫힌 대화 목표의 상태 → 고정 문장(7-10). 닫은 Take 를 요청 자료로는 알 수 없어 Take · 값 없이 상태만 말한다
GOAL_CLOSED_TEXT = {
    "ACHIEVED": SystemText.GOAL_CLOSED_ACHIEVED,
    "NOT_ACHIEVED": SystemText.GOAL_CLOSED_NOT_ACHIEVED,
    "BASELINE_MISMATCH": SystemText.GOAL_CLOSED_MISMATCH,
}


# ── 조사 ─────────────────────────────────────────────────────────────────
#: 숫자를 읽을 때의 끝소리 받침(0 영 · 1 일 · 3 삼 · 6 육 · 7 칠 · 8 팔). ㄹ 은 8
_DIGIT_FINAL = {"0": 21, "1": 8, "2": 0, "3": 16, "4": 0, "5": 0, "6": 1, "7": 8, "8": 8, "9": 0}


def _final(word: str) -> int:
    """마지막 글자의 받침 번호(0 = 없음, 8 = ㄹ). 한글 · 숫자가 아니면 받침 없음으로 본다(예: '%' → 퍼센트)."""
    w = word.rstrip(" )]}\"'.")
    if not w:
        return 0
    ch = w[-1]
    if "가" <= ch <= "힣":
        return (ord(ch) - 0xAC00) % 28
    return _DIGIT_FINAL.get(ch, 0)


_SLOT = re.compile(r"\{(\w+)\}(으로|예요)?")


def fill(template: str, **values: str) -> str:
    """템플릿을 채우며 값 뒤 조사를 받침에 맞춘다: '{x}으로' → 받침 없거나 ㄹ이면 '로', '{x}예요' → 받침 있으면 '이에요'.

    7-9 · 7-10 문구는 받침 있는 꼴로 적혀 있다("{title}으로", "{evidence}예요"). 값에 따라 "회사 소개 발표로",
    "우선 개선이에요"처럼 읽히게 한다. 템플릿의 자리만 한 번 채우고 넣은 값은 다시 해석하지 않는다(제목에 '{'가 있어도 됨).
    """

    def one(m: re.Match[str]) -> str:
        v = str(values[m.group(1)])
        tail = m.group(2)
        if tail == "으로":
            return v + ("로" if _final(v) in (0, 8) else "으로")
        if tail == "예요":
            return v + ("이에요" if _final(v) else "예요")
        return v

    return _SLOT.sub(one, template)
