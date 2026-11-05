"""행동 — 정의 · 허용 근거 검사 · 행동 만들기 · 결과 검사 · 다시 평가 비교 · 목표 제안 (기능 문서 5-8 · 6-2 · 6-3 · 6-6 · 6-7).

- 누가 쓰나: loop.py. 모델이 행동 도구(`fetch_pitch` · `fetch_take` · `reassess` · `save_goal`)를 고르면 `propose()`가
  행동을 만들거나 낮추거나 버린다. 이어 부를 때 `apply_result()`가 BE의 결과를 검사해 문맥에 반영한다.
- 모델은 `type` · 인자 · `reason` · `authorized_by` 만 낸다. `action_id` · 자율 등급 · 순번 · `idempotency_key` 는 코드가 만든다(5-8 · L1-23).
  목표 값(기준값 · 판 · 범위 · operator · min_delta)도 모델 값을 받지 않고 기준 Take 의 실제 기록으로 코드가 만든다(L4-11).
- 자율 등급(6-2): `FETCH_PITCH` · `FETCH_TAKE` = `AUTO`, `SAVE_GOAL` · `REASSESS` = `ASK`(확인 카드로 승인받은 뒤 BE 가 실행),
  `START_PRACTICE` = `BUTTON`(버튼 카드만, 실행 없음). `ASK` 행동에는 `args_hash`(행동 `type` + 확정 `args`의 정렬 JSON SHA-256)를
  붙이고, 확인 카드(`ASK_CONFIRM`)는 대기 행동의 `args`로 코드가 그린다(`ask_confirm_card`). 승인 뒤 결과의 `executed_args_hash`가
  대기 행동의 `args_hash`와 다르면 이어 부르기를 409 로 거절한다(`match_result`).
- 허용 근거 검사(6-2, `SAVE_GOAL`): ① 처음 사용자 말 원문의 부분 문자열 ② 인용을 포함한 절 전체에 부정 · 취소 · 조건 ·
  남의 말 인용이 없음 ③ 인용에 행동 낱말 + 절에 요청형 표현. 이제 이 검사는 '확인 카드를 낼 근거가 있나'를 정한다: 통과하면 `ASK` 행동
  (확인 카드), 하나라도 어기면 실행하지 않고 목표 제안 카드로 낮춘다. `REASSESS`는 허용 근거 검사 없이 `ASK`(확인 카드가 승인 근거),
  `START_PRACTICE`는 허용 근거와 상관없이 언제나 버튼 카드다. 이 검사는 간접 표현을 완벽히 가리지 못한다(15-2): 잘못 판별해도
  실행되지 않고 원치 않는 확인 카드가 뜰 뿐이다.
- 다시 평가 확인 카드(6-6): 바꾸기 전 조건은 지금 피치 기준이 아니라 대상 Take 가 리뷰받은 때의 조건(Take 요약 `standard_version`이 가리키는 판, `_take_conditions`)이고,
  바꾼 뒤 조건은 행동 인자 그대로다. 사용자 기준 문구(`display`)도 표시 문자열 검사(6-9)를 통과하면 싣는다. 바꾸기 전 조건을 확인할 수 없으면(Take 요약 판 필드 누락 · 판이 다름 · 바꾸는 항목의 지금 기준 값 누락, 명시적 null · 빈 값과 다름) 카드를 그리지 못해
  행동을 내지 않는다(사용자가 본 것과 실행되는 것이 달라지면 안 된다).
- 고른 목표 제안(`choice`)의 `SAVE_GOAL`은 `preapproved`를 달고 확인 카드 없이 BE 가 해시를 다시 계산해 바로 실행한다.
- 고칠 때: 낱말 목록은 config.py(바꾸면 FEATURE_VERSION). 행동 모양을 바꾸면 TOOL_SCHEMA_VERSION.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from . import claims as clm
from . import templates
from .budget import Budget
from .claims import goal_conflict
from .compose import metric_label
from .config import ChatAgentConfig
from .evidence import canonical_json
from .goals import build_goal_draft
from .runctx import RunCtx
from .schemas import (
    Action,
    ActionResult,
    AskConfirmCard,
    CriteriaTarget,
    Goal,
    GoalDraft,
    GoalOption,
    GoalProposalCard,
    PitchContext,
    Preapproved,
    ReassessResultCard,
    ReassessSide,
    SelectedProposal,
    StartPracticeCard,
    TakeSnapshot,
)

#: 행동마다 코드에 고정한 자율 등급(6-2). 모델이 바꾸지 못한다
AUTONOMY: dict[str, str] = {
    "FETCH_PITCH": "AUTO",
    "FETCH_TAKE": "AUTO",
    "REASSESS": "ASK",
    "SAVE_GOAL": "ASK",
    "START_PRACTICE": "BUTTON",
}
#: 사용자 말이 없는 턴(`TAKE_REVIEWED`)에서 내지 않는 행동(저장 · 다시 평가 · 이동, 6-2)
NEEDS_USER_TURN = frozenset({"SAVE_GOAL", "REASSESS", "START_PRACTICE"})
#: 행동 결과 `error.code` 닫힌 목록(5-8). BE 는 실패한 행동에 이 코드만 싣고, AI 는 목록 코드만 고정 안내로 바꾼다(목록 밖은
#: 일반 안내). 행동마다 쓰는 코드: 모든 행동 `NOT_FOUND` · `NOT_AUTHORIZED` · `NOT_ALLOWED` · `INVALID_ACTION` ·
#: `IDEMPOTENCY_CONFLICT` · `APPROVAL_REQUIRED` · `APPROVAL_ARGS_CHANGED` · `PREAPPROVAL_MISMATCH`, `REASSESS`
#: `NO_OFFICIAL_REVIEW` · `INVALID_CHANGE` · `REASSESS_PER_TURN` · `REASSESS_DAILY_LIMIT` · `RULES_DIFFER` · `REVIEW_FAILED` ·
#: `REVIEW_TAKE_MISMATCH` · `REVIEW_REQUEST_CHANGED`, `SAVE_GOAL` `GOAL_VERSION_CONFLICT` · `GOAL_METRIC_NOT_ALLOWED` ·
#: `GOAL_OPERATOR_MISMATCH` · `GOAL_BASE_MISMATCH`
ACTION_ERROR_CODES = frozenset(
    {
        "NOT_FOUND",
        "NOT_AUTHORIZED",
        "NOT_ALLOWED",
        "INVALID_ACTION",
        "IDEMPOTENCY_CONFLICT",
        "APPROVAL_REQUIRED",
        "APPROVAL_ARGS_CHANGED",
        "PREAPPROVAL_MISMATCH",
        "NO_OFFICIAL_REVIEW",
        "INVALID_CHANGE",
        "REASSESS_PER_TURN",
        "REASSESS_DAILY_LIMIT",
        "RULES_DIFFER",
        "REVIEW_FAILED",
        "REVIEW_TAKE_MISMATCH",
        "REVIEW_REQUEST_CHANGED",
        "GOAL_VERSION_CONFLICT",
        "GOAL_METRIC_NOT_ALLOWED",
        "GOAL_OPERATOR_MISMATCH",
        "GOAL_BASE_MISMATCH",
    }
)

#: 목표 지표 → 차원(목표 충돌 판정 · 전문가 추천 안)
GOAL_METRIC_DIM = {
    "audience_ratio": "audience_gaze",
    "filler_per_min": "fluency",
    "cpm": "pacing_rhythm",
    "duration_ms": "pacing_rhythm",
}
#: 우선 개선 차원 → 추천할 목표 지표(전문가 추천 안, 6-3)
DIM_GOAL_METRIC = {
    "audience_gaze": "audience_ratio",
    "fluency": "filler_per_min",
    "pacing_rhythm": "cpm",
}


# ── 모델에 주는 행동 도구 ─────────────────────────────────────────────────


class _A(BaseModel):
    # strict: 형 변환("3" → 3 · "true" → True)도 틀린 인자로 본다(L1-15 · L1-23)
    model_config = ConfigDict(extra="forbid", strict=True)


class FetchPitchArgs(_A):
    pitch_id: str | None = None
    name: str | None = Field(default=None, max_length=100)
    reason: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def _one(self) -> FetchPitchArgs:
        if (self.pitch_id is None) == (self.name is None):
            raise ValueError("pitch_id · name 중 정확히 하나")
        return self


class FetchTakeArgs(_A):
    take_id: str
    reason: str = Field(default="", max_length=200)


class ReassessChange(_A):
    script_used: bool | None = None
    targets: list[CriteriaTarget] | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def _one(self) -> ReassessChange:
        if (self.script_used is None) == (self.targets is None):
            raise ValueError("허용 키 정확히 하나(script_used 또는 targets)")
        return self


class ReassessArgs(_A):
    take_id: str
    change: ReassessChange
    reason: str = Field(default="", max_length=200)


class SaveGoalArgs(_A):
    metric: str
    slide_number: int | None = Field(default=None, ge=1)
    base_take_id: str
    #: 이번 사용자 말에서 저장을 요청한 부분 그대로
    authorized_by: str | None = Field(default=None, max_length=300)
    reason: str = Field(default="", max_length=200)


ACTION_TOOL_ARGS: dict[str, type[BaseModel]] = {
    "fetch_pitch": FetchPitchArgs,
    "fetch_take": FetchTakeArgs,
    "reassess": ReassessArgs,
    "save_goal": SaveGoalArgs,
}
ACTION_TOOL_TYPE = {
    "fetch_pitch": "FETCH_PITCH",
    "fetch_take": "FETCH_TAKE",
    "reassess": "REASSESS",
    "save_goal": "SAVE_GOAL",
}
ACTION_DESCRIPTIONS = {
    "fetch_pitch": "다른 피치의 자세한 자료를 가져와 대화 범위를 그 피치로 바꾼다(BE가 실행). pitch_id 또는 name 하나.",
    "fetch_take": "기본 자료에 없는 Take(같은 피치) 결과 전체를 가져온다(BE가 실행).",
    "reassess": "바꾼 조건(대본 사용 script_used 또는 내 기준 targets 하나)으로 리뷰를 다시 불러 참고 결과를 만든다. 공식 결과는 바뀌지 않는다. 측정값 이의(카메라가 낮았다 등)에는 쓰지 않는다.",
    "save_goal": "대화 목표를 저장한다. 지표는 audience_ratio · filler_per_min · cpm · duration_ms 중 하나. 기준값은 코드가 base_take_id 의 기록으로 정한다. authorized_by 에는 이번 사용자 말에서 저장을 요청한 부분을 글자 그대로 넣는다.",
}


def action_tool_definitions() -> list[dict[str, Any]]:
    out = []
    for name, model in ACTION_TOOL_ARGS.items():
        schema = model.model_json_schema()
        schema.pop("title", None)
        out.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": ACTION_DESCRIPTIONS[name],
                    "parameters": schema,
                },
            }
        )
    return out


# ── 허용 근거 (6-2) ──────────────────────────────────────────────────────

_SENT_SPLIT = re.compile(r"[.?!\n。？！]+")
#: 끝이 "면"이지만 조건이 아닌 낱말(명사 · 접속 부사 "그러면"). 낱말 전체가 같을 때만 예외다: 끝 비교로 보면
#: "3장이면"처럼 `~이면` 조건이 명사로 빠진다. 그래서 "이면(裏面)"은 넣지 않는다(조건이 훨씬 흔하다)
_NOUN_MYEON = ("화면", "장면", "측면", "전면", "표면", "정면", "평면", "방면", "그러면")


def _clause_of(message: str, quote: str) -> str | None:
    """인용을 포함한 원문 절(문장). 인용이 문장 경계를 넘으면 인용이 걸친 문장들을 합쳐 본다."""
    start = message.find(quote)
    if start < 0:
        return None
    end = start + len(quote)
    bounds = [0]
    for m in _SENT_SPLIT.finditer(message):
        bounds.append(m.end())
    bounds.append(len(message))
    s = max(b for b in bounds if b <= start)
    e = min(b for b in bounds if b >= end)
    return message[s:e]


def _has_condition(clause: str, cfg: ChatAgentConfig) -> bool:
    for word in re.findall(r"[가-힣]+", clause):
        if word in _NOUN_MYEON:
            continue
        if word.endswith("거든"):
            return True
        if word.endswith("면") and len(word) >= 2:
            return True
    return False


def authorize(
    action_type: str, authorized_by: str | None, original: str | None, cfg: ChatAgentConfig
) -> str | None:
    """허용 근거 검사. 통과면 None, 아니면 까닭 코드(진단 `reason_code`).

    예: authorize("SAVE_GOAL", "그걸 목표로 잡고", "…그걸 목표로 잡고 다음 연습 준비해 줘", cfg) → None
        authorize("SAVE_GOAL", "목표 저장", "목표 저장하지 마", cfg) → "NEGATION"
    """
    if not original:
        return "NO_USER_MESSAGE"
    if not authorized_by or not authorized_by.strip():
        return "NO_QUOTE"
    q = authorized_by.strip()
    if q not in original:
        return "NOT_SUBSTRING"
    clause = _clause_of(original, q) or q
    if any(w in clause for w in cfg.negation_words):
        return "NEGATION"
    if any(m in clause for m in cfg.quotation_markers):
        return "QUOTED_SPEECH"
    if _has_condition(clause, cfg):
        return "CONDITION"
    q_at = clause.find(q)
    spots = [q_at + i for w in cfg.action_words.get(action_type, ()) if (i := q.find(w)) >= 0]
    if not spots:
        return "NO_ACTION_WORD"
    pos = min(spots)
    forms = cfg.request_forms.get(action_type, ())
    verb = _word_at(clause, pos)
    words = re.findall(r"[가-힣]+", clause[pos:])
    # 동사 뒤에서 절을 끊는 말(~는데 · ~지만 · ~니까 …)이나 보조 용언(~고 있 · ~어 놓)이 오면 그 동사는 요청이 아니다
    # ("목표를 정해 놓았는데 … 알려줘", "목표로 잡고 있었는데 결과를 알려줘")
    if any(w.endswith(_CLAUSE_BREAKS) for w in words) or any(
        w.startswith(_AUX_STEMS) for w in words[1:2]
    ):
        return "NO_REQUEST_FORM"
    if verb.endswith("고"):
        # "목표로 잡고 연습하러 가자"처럼 '~고'로 이은 복합 요청은 문장 끝 동사의 요청형이 앞 동사에도 걸린다(6-2 · 13-2)
        forms = tuple({f for fs in cfg.request_forms.values() for f in fs})
    # 요청형은 명령 · 권유라 절 끝에 온다: 마지막 두 낱말 안에 있어야 한다
    tail = " ".join(words[-2:])
    if not words or not any(f in tail for f in forms):
        return "NO_REQUEST_FORM"
    return None


#: 절을 끊는 연결 어미(뒤의 요청형이 앞 동사에 걸리지 않음)
_CLAUSE_BREAKS = (
    "는데",
    "은데",
    "던데",
    "지만",
    "니까",
    "으니",
    "어서",
    "아서",
    "해서",
    "면서",
    "더니",
)
#: 동사 바로 뒤에 오면 서술(진행 · 완료)이 되는 보조 용언 줄기
_AUX_STEMS = ("있", "놓", "놨", "뒀", "두었", "봤", "싶")


def _word_at(text: str, pos: int) -> str:
    """pos 가 든 한글 낱말 전체."""
    s = pos
    while s > 0 and "가" <= text[s - 1] <= "힣":
        s -= 1
    e = pos
    while e < len(text) and "가" <= text[e] <= "힣":
        e += 1
    return text[s:e]


# ── 행동 만들기 ──────────────────────────────────────────────────────────


@dataclass
class Proposal:
    """`propose()`의 결과 하나."""

    #: PROPOSED(행동을 낸다) · DOWNGRADED(제안 카드 · 버튼으로 낮춤) · DROPPED(버림) · TOOL_ERROR(모델에 오류로 돌려줌)
    result: Literal["PROPOSED", "DOWNGRADED", "DROPPED", "TOOL_ERROR"]
    action: Action | None = None
    card: dict[str, Any] | None = None
    reason_code: str | None = None
    authorization: Literal["PASS", "FAIL", "NOT_REQUIRED"] = "NOT_REQUIRED"
    #: 모델에 돌려줄 결과(행동을 내지 않았을 때)
    tool_message: dict[str, Any] = field(default_factory=dict)


#: 사용자 말 속 Take 지칭: 'Take 5' · '테이크 5' 번호와 ID 모양의 낱말('p9-t1'). ID 는 영문자와 숫자가 모두 든 하이픈 낱말만
#: 본다: 'Q-A' · 'co-presenter' · 'B-roll' 같은 말을 Take ID 로 보면 말한 Take 가 없다고 다시 평가를 막는다(#895 리뷰)
_NAMED_TAKE_NUM = re.compile(r"(?:take|테이크)\s*(\d+)", re.IGNORECASE)
_NAMED_ID = re.compile(
    r"(?<![A-Za-z0-9-])(?=[A-Za-z0-9-]*[A-Za-z])(?=[A-Za-z0-9-]*\d)"
    r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+(?![A-Za-z0-9-])"
)


def named_take_ids(message: str | None, ctx: RunCtx) -> set[str] | None:
    """사용자 말이 Take 를 번호 · ID 로 집어 말했으면 그 Take ID 들(찾지 못한 것은 빠진다), 말하지 않았으면 None.

    예: "Take 4 다시 평가해 줘" → {Take 4의 ID}, "p9-t1 Take를 다시 평가해 줘"(범위에 없는 ID) → 빈 집합,
    "대본 보고 한 걸로 다시 평가해 줘" → None(지금 화면의 Take 를 쓸 수 있다).
    """
    if not message:
        return None
    nums = {int(n) for n in _NAMED_TAKE_NUM.findall(message)}
    ids = set(_NAMED_ID.findall(message))
    if not nums and not ids:
        return None
    return {tid for tid, summ in ctx.summaries.items() if summ.take_number in nums or tid in ids}


def unresolved_take_numbers(message: str | None, ctx: RunCtx) -> set[int]:
    """사용자 말이 번호로 집어 말한 Take 가운데 범위 피치에 없는 번호(지워졌거나 없는 Take). 예: Take 4 가 지워졌으면
    "Take 4 3장 값 알려 줘" → {4}."""
    if not message:
        return set()
    nums = {int(n) for n in _NAMED_TAKE_NUM.findall(message)}
    return nums - {s.take_number for s in ctx.summaries.values()}


def action_args_hash(action_type: str, args: dict[str, Any]) -> str:
    """`args_hash`(5-8): 행동 `type`과 확정한 `args`를 정렬한 JSON(`canonical_json`)의 SHA-256, `"sha256:<hex>"` 꼴.

    열쇠 순서와 상관없이 같은 내용이면 같은 값이다. `executed_args_hash`(BE 가 실행한 `args`)도 같은 규칙이다.
    예: action_args_hash("REASSESS", {"take_id": "t-5", "change": {"script_used": True}}) → "sha256:…"(64자리 16진수)
    """
    blob = canonical_json({"type": action_type, "args": args})
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def make_action(
    *,
    run_id: str,
    seq: int,
    action_type: str,
    args: dict[str, Any],
    reason: str,
    authorized_by: str | None = None,
    preapproved: Preapproved | None = None,
) -> Action:
    """코드가 만드는 행동 하나: id · 자율 등급 · 순번 · `idempotency_key`, `ASK`면 `args_hash`(5-8 · L1-23)."""
    autonomy = AUTONOMY[action_type]
    return Action(
        action_id=f"{run_id}:a{seq}",
        type=action_type,  # type: ignore[arg-type]
        args=args,
        autonomy=autonomy,  # type: ignore[arg-type]
        args_hash=action_args_hash(action_type, args) if autonomy == "ASK" else None,
        idempotency_key=f"{run_id}:{seq}",
        reason=reason,
        authorized_by=authorized_by,
        preapproved=preapproved,
    )


def _goal_options_hash(goal: GoalDraft | None) -> str | None:
    if goal is None:
        return None
    return hashlib.sha256(canonical_json(goal.model_dump(mode="json")).encode("utf-8")).hexdigest()[
        :16
    ]


def propose(
    tool_name: str,
    raw_args: dict[str, Any] | None,
    *,
    ctx: RunCtx,
    budget: Budget,
    seq: int,
    original_message: str | None,
    trigger: str,
) -> Proposal:
    """모델이 고른 행동 도구 하나를 행동 · 제안 카드 · 오류로 바꾼다."""
    model = ACTION_TOOL_ARGS.get(tool_name)
    if model is None or raw_args is None:
        return Proposal(
            result="TOOL_ERROR",
            reason_code="BAD_ARGS",
            tool_message=_err("TOOL_INVALID", "BAD_ARGS"),
        )
    try:
        a = model.model_validate(raw_args)
    except ValidationError:
        return Proposal(
            result="TOOL_ERROR",
            reason_code="BAD_ARGS",
            tool_message=_err("TOOL_INVALID", "BAD_ARGS"),
        )
    atype = ACTION_TOOL_TYPE[tool_name]
    run_id = ctx.req.run.run_id
    if trigger == "TAKE_REVIEWED" and atype in NEEDS_USER_TURN:
        # 사용자 말이 없는 턴에서는 저장 · 이동 행동을 내지 않는다(6-2)
        return Proposal(
            result="DROPPED",
            reason_code="NO_USER_TURN",
            tool_message=_err("NOT_ALLOWED", "자동 목표 확인 턴에서는 저장하지 않는다"),
        )

    args: dict[str, Any]
    authorized_by: str | None = None
    authorization: Literal["PASS", "FAIL", "NOT_REQUIRED"] = "NOT_REQUIRED"
    if isinstance(a, FetchPitchArgs):
        args = {"pitch_id": a.pitch_id, "name": a.name}
        if a.pitch_id is not None and a.pitch_id == ctx.scope_pitch_id:
            return Proposal(
                result="TOOL_ERROR",
                reason_code="ALREADY_IN_SCOPE",
                tool_message=_err("ALREADY_IN_SCOPE", "이미 지금 범위의 피치다"),
            )
    elif isinstance(a, FetchTakeArgs):
        if ctx.pitch is None:
            return Proposal(
                result="TOOL_ERROR",
                reason_code="NO_SCOPE",
                tool_message=_err("NO_SCOPE", "범위 피치가 없다. 먼저 fetch_pitch"),
            )
        if a.take_id in ctx.snapshots:
            return Proposal(
                result="TOOL_ERROR",
                reason_code="ALREADY_LOADED",
                tool_message=_err("ALREADY_LOADED", "이미 읽을 수 있는 Take 다"),
            )
        if a.take_id in ctx.not_found:
            return Proposal(
                result="TOOL_ERROR",
                reason_code="NOT_FOUND",
                tool_message=_err("NOT_FOUND", "이 실행에서 찾을 수 없던 Take 다"),
            )
        named = named_take_ids(original_message, ctx)
        missing = ctx.not_found or unresolved_take_numbers(original_message, ctx)
        if named is not None and missing and a.take_id in ctx.summaries and a.take_id not in named:
            # 사용자가 Take 를 집어 말했는데 그 Take 를 가져오지 못했거나(이 실행의 가져오기 실패) 범위 피치에 없으면(지워짐 ·
            # 번호 없음), 말하지 않은 Take 를 대신 가져와 그것으로 답하지 않는다(다시 평가와 같은 규칙, 4회차 fail-action ·
            # 6회차 stale-deleted: 지워진 Take 4 대신 Take 3 을 가져와 답함). 지워진 Take 는 기본 자료에서도 빠져 `named`에
            # 없을 수 있으므로 실패한 Take 가 `named`에 있는지는 보지 않는다. 범위 피치에 없는 Take(말한 그 Take 일 수 있다)를
            # 가져오는 것은 막지 않는다(BE 가 없다고 돌려준다, L2-22)
            return Proposal(
                result="TOOL_ERROR",
                reason_code="NOT_NAMED_TAKE",
                tool_message=_err(
                    "NOT_FOUND",
                    "사용자가 말한 Take 를 찾지 못했다(지워졌거나 없음). 다른 Take 로 바꿔 답하지 않고 찾을 수 없다고 답한다",
                ),
            )
        args = {"pitch_id": ctx.scope_pitch_id, "take_id": a.take_id}
    elif isinstance(a, ReassessArgs):
        if a.take_id not in ctx.summaries:
            return Proposal(
                result="TOOL_ERROR",
                reason_code="NOT_IN_SCOPE",
                tool_message=_err("NOT_FOUND", "범위 피치의 Take 가 아니다"),
            )
        named = named_take_ids(original_message, ctx)
        if named is not None and a.take_id not in named:
            # 사용자가 말한 Take 를 찾지 못했는데 다른 Take(예: 지금 화면의 Take)로 바꿔 다시 평가하지 않는다
            return Proposal(
                result="TOOL_ERROR",
                reason_code="NOT_NAMED_TAKE",
                tool_message=_err(
                    "NOT_FOUND",
                    "사용자가 말한 Take 가 아니다. 그 Take 를 찾지 못했으면 다시 평가하지 않는다",
                ),
            )
        if clm.measurement_dispute(original_message or "", ctx.cfg):
            # 다시 평가는 입력을 바꾸는 조건(대본 사용 · 내 기준)을 사용자가 말했을 때만 한다. 측정값 이의('카메라가 낮았어')는
            # 다시 평가로 바뀌지 않는다(6-6, 4회차 ra-measurement)
            return Proposal(
                result="TOOL_ERROR",
                reason_code="CONDITION_NOT_STATED",
                tool_message=_err(
                    "CONDITION_NOT_STATED",
                    "사용자가 이 조건(대본 사용 · 내 기준)을 말하지 않았다. 측정값 이의는 다시 평가하지 않는다",
                ),
            )
        if a.change.script_used is not None:
            change: dict[str, Any] = {"script_used": a.change.script_used}
        else:
            targets = _sourced_targets(
                [t.model_dump() for t in a.change.targets or []],
                original=original_message,
                ctx=ctx,
                take_id=a.take_id,
            )
            if targets is None:
                return Proposal(
                    result="TOOL_ERROR",
                    reason_code="DISPLAY_UNSOURCED",
                    tool_message=_err(
                        "DISPLAY_UNSOURCED",
                        "기준 문구(display)는 사용자가 말한 글자 그대로나 그 Take 기준의 문구만 쓴다",
                    ),
                )
            change = {"targets": targets}
        args = {"take_id": a.take_id, "change": change}
    else:
        assert isinstance(a, SaveGoalArgs)
        return _propose_goal(a, ctx=ctx, budget=budget, seq=seq, original_message=original_message)

    hit = budget.reserve_action(atype)
    if hit is not None:
        return Proposal(
            result="DROPPED", reason_code=hit.code, tool_message=_err("LIMIT", hit.code)
        )
    action = make_action(
        run_id=run_id,
        seq=seq,
        action_type=atype,
        args=args,
        reason=getattr(a, "reason", "") or atype,
        authorized_by=authorized_by,
    )
    return Proposal(result="PROPOSED", action=action, authorization=authorization)


def _err(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


def _sourced_targets(
    targets: list[dict[str, Any]], *, original: str | None, ctx: RunCtx, take_id: str
) -> list[dict[str, Any]] | None:
    """다시 평가 '바꾼 뒤' 기준의 문구(`display`)는 출처가 있을 때만 싣는다(6-9 · L1-37, #888 · #909 리뷰).

    문구는 모델이 채운 행동 인자라, 확인 카드 · 결과 카드(코드 카드라 숫자 출처 검사를 받지 않음)에 그대로 보이면 근거 없는
    숫자 · 칭찬이 나갈 수 있다. 출처: 처음 사용자 말 원문의 부분 문자열이거나 그 Take 기준(바꾸기 전 조건)의 문구와 같음.
    출처가 없으면 숫자 목표가 있는 칸은 문구를 비우고(숫자 조건이 실제 조건), 문구만 있는 칸이면 None(행동을 내지 않는다:
    보이지 않는 조건은 승인받지 않는다).
    예: 사용자 "시선 70% 넘으면 통과로 다시 봐 줘", display "Take 5 시선 90%였어요"(metric 있음) → display None
    """
    known: set[str] = set()
    cond = _take_conditions(ctx, take_id, "targets")
    if cond is not None:
        known = {str(_target_view(t).get("display") or "").strip() for t in cond[1]} - {""}
    out: list[dict[str, Any]] = []
    for t in targets:
        d = (t.get("display") or "").strip()
        if not d or (original is not None and d in original) or d in known:
            out.append(t)
        elif t.get("metric") is not None:
            out.append({**t, "display": None})
        else:
            return None
    return out


def _propose_goal(
    a: SaveGoalArgs, *, ctx: RunCtx, budget: Budget, seq: int, original_message: str | None
) -> Proposal:
    cfg = ctx.cfg
    run_id = ctx.req.run.run_id
    base = ctx.snapshots.get(a.base_take_id)
    if base is None:
        code = "NOT_LOADED" if a.base_take_id in ctx.summaries else "NOT_FOUND"
        return Proposal(
            result="TOOL_ERROR", reason_code=code, tool_message=_err(code, "기준 Take 결과가 없다")
        )
    draft = build_goal_draft(
        base, a.metric, a.slide_number, pitch_id=ctx.scope_pitch_id or "", cfg=cfg
    )
    review = base.results.review or {}
    dims = review.get("dimensions") or {}
    requested_dim = GOAL_METRIC_DIM.get(a.metric)
    conflict = requested_dim is not None and goal_conflict(requested_dim, dims)
    # 고른 제안(choice)은 내용 · 근거가 그대로이고 같은 목표일 때만 허용 근거다(6-2 6번 · L6-22).
    # 그대로면 다시 따지지 않고, 바뀌었거나 다른 목표면 보통 허용 근거 검사로 간다(→ 새 제안)
    chosen_ok = False
    if ctx.req.choice is not None and not isinstance(draft, str):
        valid, chosen = proposal_still_valid(ctx)
        chosen_ok = valid and chosen is not None and _same_goal(chosen, draft)
    if not chosen_ok and (isinstance(draft, str) or conflict):
        # 잴 수 없거나 기록과 어긋나는 목표는 저장하지 않고 사용자 안 · 추천 안을 나란히 보인다(6-3 · L6-08 · L6-23)
        card = goal_proposal_card(
            ctx=ctx,
            base=base,
            user_draft=None if isinstance(draft, str) else draft,
            user_reason=a.reason or "말씀하신 목표",
            dims=dims,
            seq=seq,
        )
        return Proposal(
            result="DOWNGRADED",
            card=card,
            reason_code="UNMEASURABLE" if isinstance(draft, str) else "GOAL_CONFLICT",
            tool_message={
                "ok": True,
                "data": {
                    "proposal": "목표 제안 카드를 만들었다(저장하지 않음)",
                    "reason": "UNMEASURABLE" if isinstance(draft, str) else "GOAL_CONFLICT",
                },
            },
        )
    assert not isinstance(draft, str)
    if chosen_ok:
        authorization: Literal["PASS", "FAIL", "NOT_REQUIRED"] = "PASS"
        quote = a.authorized_by
    else:
        quote = a.authorized_by
        why = authorize("SAVE_GOAL", quote, original_message, cfg)
        authorization = "PASS" if why is None else "FAIL"
        if why is not None:
            card = goal_proposal_card(
                ctx=ctx,
                base=base,
                user_draft=draft,
                user_reason=a.reason or "말씀하신 목표",
                dims=dims,
                seq=seq,
            )
            return Proposal(
                result="DOWNGRADED",
                card=card,
                reason_code=why,
                authorization="FAIL",
                tool_message={
                    "ok": True,
                    "data": {
                        "proposal": "저장 요청이 분명하지 않아 목표 제안 카드로 보였다",
                        "reason": why,
                    },
                },
            )
    hit = budget.reserve_action("SAVE_GOAL")
    if hit is not None:
        return Proposal(
            result="DROPPED", reason_code=hit.code, tool_message=_err("LIMIT", hit.code)
        )
    args = {"expected_goal_version": ctx.goal_version, "goal": draft.model_dump(mode="json")}
    sp = ctx.req.context.selected_proposal
    action = make_action(
        run_id=run_id,
        seq=seq,
        action_type="SAVE_GOAL",
        args=args,
        reason=a.reason or "대화 목표 저장",
        authorized_by=quote.strip() if quote else None,
        # 고른 제안을 그대로 저장하면 그 클릭이 승인이다: 확인 카드 없이 BE 가 해시를 다시 계산해 실행한다(6-2)
        preapproved=preapproval(sp) if chosen_ok and sp is not None else None,
    )
    return Proposal(result="PROPOSED", action=action, authorization=authorization)


def goal_proposal_card(
    *,
    ctx: RunCtx,
    base: TakeSnapshot,
    user_draft: GoalDraft | None,
    user_reason: str,
    dims: dict[str, Any],
    seq: int,
) -> dict[str, Any]:
    """사용자 안 · 전문가 추천 안을 담은 GOAL_PROPOSAL 카드(6-3). 잴 수 없는 사용자 안은 goal null(선택 버튼 없음)."""
    cfg = ctx.cfg
    options: list[GoalOption] = []
    options.append(
        GoalOption(
            label="USER",
            goal=user_draft,
            reason=user_reason
            if user_draft is not None
            else "말씀하신 목표는 기록으로 잴 수 없어요",
            evidence_ids=[],
            content_hash=_goal_options_hash(user_draft),
        )
    )
    expert: GoalDraft | None = None
    for dim, state in dims.items():
        if state == "PRIORITY" and dim in DIM_GOAL_METRIC:
            d = build_goal_draft(
                base, DIM_GOAL_METRIC[dim], None, pitch_id=ctx.scope_pitch_id or "", cfg=cfg
            )
            if not isinstance(d, str) and (user_draft is None or d.metric != user_draft.metric):
                expert = d
                break
    if expert is not None:
        options.append(
            GoalOption(
                label="EXPERT",
                goal=expert,
                reason="리뷰에서 우선 개선인 차원이에요",
                evidence_ids=[],
                content_hash=_goal_options_hash(expert),
            )
        )
    basis = ctx.registry.basis_for([], corpus_version=ctx.corpus_version)
    basis = basis.model_copy(update={"fingerprint": _proposal_fingerprint(ctx, base)})
    card = GoalProposalCard(
        proposal_id=f"{ctx.req.run.run_id}:gp{seq}",
        options=options,
        evidence_basis=basis,
    )
    return card.model_dump(mode="json")


def _proposal_fingerprint(ctx: RunCtx, base: TakeSnapshot) -> str:
    """목표 제안의 근거 지문: 기준 Take 의 결과 판 · 규칙 판 + 피치의 지금 대본 · 발표자료 · 평가 기준 버전.

    고른 뒤 이 값이 바뀌면 다시 제안한다(6-3 '제안을 고른 뒤' · L6-22: 결과 판이 그대로여도 대본 · 평가 기준이 바뀌면 새 제안).
    """
    rv = ctx.version_of(base)
    fp_src = canonical_json(
        {
            "take": rv.take_id,
            "results": rv.results,
            "rules": rv.rule_versions,
            "pv": rv.script_version_id,
            "presentation": rv.presentation_version_id,
            "standard": rv.standard_version,
        }
    )
    return hashlib.sha256(fp_src.encode("utf-8")).hexdigest()[:16]


def _same_goal(a: GoalDraft, b: GoalDraft) -> bool:
    return canonical_json(a.model_dump(mode="json")) == canonical_json(b.model_dump(mode="json"))


# ── 결과 검사 · 반영 (5-8 '결과 검사') ─────────────────────────────────────


class ResultMismatch(Exception):
    """결과가 요청한 행동과 맞지 않는다(id · type · 개수 · 대상). turn.py 는 409, 내용 불일치는 루프가 끝낸다."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def match_result(pending: dict[str, Any] | None, results: list[ActionResult]) -> ActionResult:
    """대기 행동 1개에 결과 정확히 1개(P0). 요청하지 않은 id · 다른 type · 두 개는 ResultMismatch(→ 409).

    `ASK` 행동은 승인 뒤 BE 가 저장한 `args`로 실행하므로 결과(`SUCCEEDED` · `FAILED` · `AMBIGUOUS` 모두)에
    `executed_args_hash`가 있어야 하고 대기 행동의 `args_hash`와 같아야 한다(없거나 다르면 사용자가 본 카드와 다른 인자로
    실행됐을 수 있다, 5-8 · 6-2).
    """
    if pending is None:
        raise ResultMismatch("NO_PENDING_ACTION")
    if len(results) != 1:
        raise ResultMismatch("RESULT_COUNT")
    r = results[0]
    if r.action_id != pending["action_id"] or r.type != pending["type"]:
        raise ResultMismatch("RESULT_MISMATCH")
    if pending.get("autonomy") == "ASK":
        expected = pending.get("args_hash")
        got = r.executed_args_hash
        if got is None:
            raise ResultMismatch("ARGS_HASH_MISSING")
        if got != expected:
            raise ResultMismatch("ARGS_HASH_MISMATCH")
    return r


@dataclass
class Applied:
    """결과를 문맥에 반영한 결과."""

    ok: bool
    #: 모델에 돌려줄 도구 결과
    tool_message: dict[str, Any]
    #: 성공 문장(코드가 검증된 결과로 그림, ACTION_RESULT)
    success_text: str | None = None
    card: dict[str, Any] | None = None
    #: 결과가 요청과 달라 실행을 끝내야 하는가(L1-30)
    mismatch: bool = False
    scope_changed: bool = False
    reason_code: str | None = None
    #: BE 가 준 실패 코드(FAILED 일 때, 안내 문구 고르기용)
    failed_code: str | None = None
    #: 다시 평가 A · B 의 규칙 판(답 meta.criteria_versions 에 싣는다, 5-5)
    review_versions: dict[str, str] | None = None
    #: 다시 평가 결과가 공식 리뷰와 다른가
    official_differs: bool = False


def apply_result(pending: dict[str, Any], r: ActionResult, *, ctx: RunCtx) -> Applied:
    """검사한 행동 결과를 문맥에 반영한다. status · data · error 가 서로 맞지 않으면 mismatch."""
    atype = pending["type"]
    args = pending["args"]
    if r.status == "SUCCEEDED" and (r.data is None or r.error is not None):
        return Applied(
            ok=False,
            tool_message=_err("RESULT_INVALID", "성공인데 data 가 없다"),
            mismatch=True,
            reason_code="SUCCEEDED_WITHOUT_DATA",
        )
    if r.status == "FAILED" and (r.error is None or r.data is not None):
        # 실패에는 error 만 있다. 실패인데 data 가 있으면(L1-30 'FAILED+성공 data') 결과를 믿지 않는다
        return Applied(
            ok=False,
            tool_message=_err("RESULT_INVALID", "실패 결과의 error · data 가 맞지 않는다"),
            mismatch=True,
            reason_code="FAILED_WITHOUT_ERROR" if r.error is None else "FAILED_WITH_DATA",
        )
    if r.status == "AMBIGUOUS" and atype != "FETCH_PITCH":
        return Applied(
            ok=False,
            tool_message=_err("RESULT_INVALID", "AMBIGUOUS 는 FETCH_PITCH 만"),
            mismatch=True,
            reason_code="BAD_STATUS",
        )
    if r.status == "FAILED":
        code = r.error.code if r.error else "FAILED"
        if atype == "FETCH_TAKE" and code == "NOT_FOUND":
            ctx.not_found.add(str(args.get("take_id")))  # 같은 실행에서 다시 찾지 않는다(L1-17)
        return Applied(
            failed_code=code,
            ok=False,
            tool_message={
                "ok": False,
                "error": {"code": code, "message": "행동이 실패했다. 다른 길을 고른다"},
            },
            reason_code="ACTION_FAILED",
        )
    data = r.data or {}
    if atype == "FETCH_PITCH":
        if r.status == "AMBIGUOUS":
            cands = data.get("candidates") or []
            view = [
                {"pitch_id": c.get("pitch_id"), "title": c.get("title")}
                for c in cands
                if isinstance(c, dict)
            ]
            return Applied(
                ok=False,
                tool_message={
                    "ok": False,
                    "read_state": "AMBIGUOUS",
                    "data": {"candidates": view},
                    "error": {
                        "code": "AMBIGUOUS",
                        "message": "후보가 여럿이다. 사용자에게 고르게 한다",
                    },
                },
                reason_code="AMBIGUOUS",
            )
        try:
            pitch = PitchContext.model_validate(data.get("pitch"))
        except ValidationError:
            return Applied(
                ok=False,
                tool_message=_err("RESULT_INVALID", "피치 자료 모양이 틀렸다"),
                mismatch=True,
                reason_code="BAD_DATA",
            )
        if args.get("pitch_id") is not None and pitch.info.pitch_id != args["pitch_id"]:
            return Applied(
                ok=False,
                tool_message=_err("RESULT_INVALID", "다른 피치"),
                mismatch=True,
                reason_code="TARGET_MISMATCH",
            )
        moved = pitch.info.pitch_id != ctx.scope_pitch_id
        ctx.set_pitch(pitch)
        ctx.touch(pitch.info.pitch_id)
        text = templates.fill(templates.ACTION_SUCCESS["FETCH_PITCH"], title=pitch.info.title)
        return Applied(
            ok=True,
            tool_message={
                "ok": True,
                "data": {
                    "pitch_id": pitch.info.pitch_id,
                    "title": pitch.info.title,
                    "takes": len(pitch.takes),
                },
            },
            success_text=text,
            # 같은 피치를 다시 가져온 것은 범위 전환이 아니다(판 번호를 올리지 않는다, 5-6)
            scope_changed=moved,
        )
    if atype == "FETCH_TAKE":
        try:
            snap = TakeSnapshot.model_validate(data.get("take"))
        except ValidationError:
            return Applied(
                ok=False,
                tool_message=_err("RESULT_INVALID", "Take 자료 모양이 틀렸다"),
                mismatch=True,
                reason_code="BAD_DATA",
            )
        if snap.summary.take_id != args["take_id"]:
            return Applied(
                ok=False,
                tool_message=_err("RESULT_INVALID", "다른 Take"),
                mismatch=True,
                reason_code="TARGET_MISMATCH",
            )
        ctx.add_snapshot(snap, trusted=True)
        ctx.touch(snap.summary.take_id)
        text = templates.ACTION_SUCCESS["FETCH_TAKE"].format(take_number=snap.summary.take_number)
        return Applied(
            ok=True,
            tool_message={
                "ok": True,
                "data": {
                    "take_id": snap.summary.take_id,
                    "take_number": snap.summary.take_number,
                    "loaded": True,
                },
            },
            success_text=text,
        )
    if atype == "SAVE_GOAL":
        try:
            goal = Goal.model_validate(data.get("goal"))
        except ValidationError:
            return Applied(
                ok=False,
                tool_message=_err("RESULT_INVALID", "목표 모양이 틀렸다"),
                mismatch=True,
                reason_code="BAD_DATA",
            )
        requested = GoalDraft.model_validate(args["goal"])
        saved_draft = GoalDraft.model_validate(
            goal.model_dump(exclude={"version", "status", "created_at"})
        )
        if (
            saved_draft.model_dump(mode="json") != requested.model_dump(mode="json")
            or goal.status != "OPEN"
            or goal.version != int(args["expected_goal_version"]) + 1
        ):
            # 저장 결과의 버전 · 내용이 요청과 다르면 성공이라고 말하지 않고 끝낸다(5-8 · L1-30)
            return Applied(
                ok=False,
                tool_message=_err("RESULT_MISMATCH", "저장 결과가 요청과 다르다"),
                mismatch=True,
                reason_code="GOAL_MISMATCH",
            )
        ctx.goal = goal
        ctx.goal_version = goal.version
        card = {
            "type": "GOAL_SAVED",
            "action_id": pending["action_id"],
            "goal": goal.model_dump(mode="json"),
            "reason": pending.get("reason") or "대화 목표",
            "evidence_ids": [],
            "undo": True,
        }
        return Applied(
            ok=True,
            tool_message={"ok": True, "data": {"saved": True, "goal_version": goal.version}},
            success_text=templates.ACTION_SUCCESS["SAVE_GOAL"],
            card=card,
        )
    if atype == "REASSESS":
        return _apply_reassess(pending, data, ctx=ctx)
    return Applied(
        ok=False,
        tool_message=_err("RESULT_INVALID", "모르는 행동"),
        mismatch=True,
        reason_code="UNKNOWN_ACTION",
    )


_RULE_FIELDS = ("score", "dimensions", "criteria_results")


def _side(resp: dict[str, Any]) -> ReassessSide:
    crit = [
        {k: c.get(k) for k in ("display", "metric", "operator", "target", "observed", "status")}
        for c in resp.get("criteria_results") or []
        if isinstance(c, dict)
    ]
    return ReassessSide(
        score=resp.get("score"),
        dimensions=dict(resp.get("dimensions") or {}),
        criteria_results=crit,
    )


def _score_ok(v: Any) -> bool:
    return v is None or (isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 100)


def _dims_ok(d: Any) -> bool:
    return (
        isinstance(d, dict)
        and bool(d)
        and all(isinstance(k, str) and isinstance(v, str) for k, v in d.items())
    )


def _review_ok(x: Any) -> bool:
    """다시 평가 A · B 결과에 비교할 규칙 칸과 판 정보가 모두 있고 형이 맞는가(5-8 결과 모양 · 6-6)."""
    if not isinstance(x, dict) or "score" not in x:
        return False
    meta = x.get("meta")
    crit = x.get("criteria_results")
    cv = meta.get("criteria_versions") if isinstance(meta, dict) else None
    return (
        _score_ok(x["score"])
        and _dims_ok(x.get("dimensions"))
        and isinstance(crit, list)
        and all(isinstance(c, dict) for c in crit)
        and isinstance(meta, dict)
        and isinstance(meta.get("schema_version"), str)
        and bool(meta["schema_version"])
        and (
            cv is None
            or (
                isinstance(cv, dict)
                and all(isinstance(k, str) and isinstance(v, str) and v for k, v in cv.items())
            )
        )
    )  # 규칙 판(criteria_versions)이 빠지거나 다르면 아래에서 RULES_DIFFER 로 안내한다(6-6 · L4-20)


def _official_ok(x: Any) -> bool:
    dims = x.get("dimensions") if isinstance(x, dict) else None
    return (
        isinstance(x, dict)
        and "score" in x
        and _score_ok(x["score"])
        and isinstance(dims, dict)
        and all(isinstance(k, str) and isinstance(v, str) for k, v in dims.items())
    )


def _apply_reassess(pending: dict[str, Any], data: dict[str, Any], *, ctx: RunCtx) -> Applied:
    """A(지금 기록 그대로) · B(허용 칸 하나만 바꿈)의 규칙 칸만 비교한다(6-6). 문장 · 다음 미션은 비교하지 않는다."""
    before, after, official = data.get("before"), data.get("after"), data.get("official")
    if not (_review_ok(before) and _review_ok(after) and _official_ok(official)):
        # 빠진 칸을 빈 값으로 채워 비교하지 않는다: 완전한 A · B · 공식 결과만 비교한다(6-6 · L1-30 · L4-20)
        return Applied(
            ok=False,
            tool_message=_err("RESULT_INVALID", "A · B 결과가 없다"),
            mismatch=True,
            reason_code="BAD_DATA",
        )
    ta, tb = before.get("take_id"), after.get("take_id")
    if ta != pending["args"]["take_id"] or tb != pending["args"]["take_id"]:
        return Applied(
            ok=False,
            tool_message=_err("RESULT_INVALID", "다른 Take 의 결과"),
            mismatch=True,
            reason_code="TARGET_MISMATCH",
        )
    cva = (before.get("meta") or {}).get("criteria_versions")
    cvb = (after.get("meta") or {}).get("criteria_versions")
    same_schema = (before.get("meta") or {}).get("schema_version") == (after.get("meta") or {}).get(
        "schema_version"
    )
    if not cva or not cvb or cva != cvb or not same_schema:
        # 규칙 버전이 다르거나 빠지면 같은 조건 비교가 아니다(6-6 6번 · L4-20)
        return Applied(
            ok=False,
            tool_message={
                "ok": False,
                "error": {
                    "code": "RULES_DIFFER",
                    "message": templates.SystemText.REASSESS_RULES_DIFFER.value,
                },
            },
            reason_code="RULES_DIFFER",
        )
    a, b = _side(before), _side(after)
    changed: list[str] = []
    if a.score != b.score:
        changed.append("score")
    for dim in sorted(set(a.dimensions) | set(b.dimensions)):
        if a.dimensions.get(dim) != b.dimensions.get(dim):
            changed.append(f"dimensions.{dim}")
    if [c.get("status") for c in a.criteria_results] != [
        c.get("status") for c in b.criteria_results
    ]:
        changed.append("criteria_results")
    official_differs = False
    if isinstance(official, dict):
        official_differs = (
            official.get("score") != a.score
            or dict(official.get("dimensions") or {}) != a.dimensions
        )
    card = ReassessResultCard(
        take_id=ta,
        change=pending["args"]["change"],
        before=a,
        after=b,
        changed_fields=changed,
        official_differs=official_differs,
    ).model_dump(mode="json")
    view = {
        "changed_fields": changed,
        "before": {"score": a.score, "dimensions": a.dimensions},
        "after": {"score": b.score, "dimensions": b.dimensions},
        "official_differs": official_differs,
        "note": templates.SystemText.REASSESS_HYPOTHETICAL.value,
    }
    return Applied(
        ok=True,
        tool_message={"ok": True, "data": view},
        success_text=templates.ACTION_SUCCESS["REASSESS"],
        card=card,
        review_versions={str(k): str(v) for k, v in (cva or {}).items()},
        official_differs=official_differs,
    )


def start_practice_action(
    *,
    ctx: RunCtx,
    authorized_by: str | None,
    seq: int,
    budget: Budget,
) -> tuple[Action | None, dict[str, Any], str | None]:
    """마지막 답의 연습 준비 이동(6-2): 허용 근거와 상관없이 언제나 [연습 준비로] 버튼 카드만 낸다(자동 이동 없음).

    행동은 `autonomy:"BUTTON"`으로 `DONE`의 `actions`에 싣고 BE 는 실행하지 않는다(누르는 것이 승인이고 FE 가 옮긴다).
    행동 수 한도에 막히면 행동 없이 카드만 남긴다. 사용자가 이번 말에서 요청한 인용은 그대로 남긴다.
    """
    pid = ctx.scope_pitch_id
    if pid is None:
        return None, {}, "NO_SCOPE"
    card = StartPracticeCard(pitch_id=pid).model_dump(mode="json")
    if budget.reserve_action("START_PRACTICE") is not None:
        return None, card, "LIMIT"
    action = make_action(
        run_id=ctx.req.run.run_id,
        seq=seq,
        action_type="START_PRACTICE",
        args={"pitch_id": pid},
        reason="연습 준비 화면으로 이동",
        authorized_by=(authorized_by or "").strip() or None,
    )
    return action, card, None


def preapproval(sp: SelectedProposal) -> Preapproved:
    """고른 목표 제안의 선승인 표시(5-8): 제안 id · 표시했던 내용의 해시."""
    return Preapproved(by="CHOICE", proposal_id=sp.proposal_id, content_hash=sp.content_hash)


# ── 승인 확인 카드 (5-2 `ASK_CONFIRM` · 6-2) ─────────────────────────────────


def _goal_summary(goal: GoalDraft, cfg: ChatAgentConfig) -> str:
    base = goal.base
    if goal.operator == "in_range":
        rng = base.range
        lo = (
            ""
            if rng is None or rng.low is None
            else clm.format_metric_value(goal.metric, rng.low, cfg)
        )
        hi = (
            ""
            if rng is None or rng.high is None
            else clm.format_metric_value(goal.metric, rng.high, cfg)
        )
        value_text = f"{lo}~{hi}"
    elif goal.metric in cfg.metric_units:
        value_text = clm.format_metric_value(goal.metric, base.value, cfg)
    else:
        value_text = str(base.value)
    return templates.GOAL_SUMMARY[goal.operator].format(
        take_number=base.take_number,
        where=f"{goal.slide_number}장 " if goal.slide_number else "",
        label=metric_label(goal.metric),
        value=value_text,
    )


def _target_view(t: CriteriaTarget | dict[str, Any]) -> dict[str, Any]:
    d = t.model_dump() if isinstance(t, CriteriaTarget) else t
    # 사용자가 쓴 문구(display)도 싣는다: 숫자 목표가 없는 기준(예: "자연스럽게 말하기")은 문구만이 실제 조건이다.
    # 문구가 표시 문자열 검사(6-9)에 걸리면 카드를 그리지 못한다(`_card_blocked`가 막아 행동을 내지 않는다)
    return {k: d.get(k) for k in ("display", "metric", "operator", "value")}


def _take_conditions(
    ctx: RunCtx, take_id: str, need: Literal["script_used", "targets"] | None = None
) -> tuple[bool | None, list[Any]] | None:
    """대상 Take 가 리뷰를 받을 때의 평가 조건 `(script_used, targets)`. 확인할 수 없으면 None.

    Take 요약의 `standard_version`(평가 기준 판)이 BE 가 그 Take 의 리뷰 요청 `user_criteria`를 만든 판이다(리뷰 입력 5-2).
    필드가 **명시적 null** 이면 그 Take 는 기준 없이 리뷰됐다(`user_criteria` null → 빈 조건). 필드가 **누락**됐으면 판을 모르는 것이라
    확인할 수 없다(None). 값이 있으면 지금 피치 기준이 같은 판일 때만 그 기준이 Take 의 조건이다. 판이 다르면(Take 뒤에 기준이
    바뀜) Take 의 옛 조건을 알 수 없다 — 지금 기준을 '바뀌기 전'으로 보이면 사용자가 본 것과 실행되는 것이 달라지므로 None 이다(6-6).
    같은 판이어도 바꾸는 항목(`need`)의 값이 지금 기준에 누락됐으면(빈 값과 다름) 확인할 수 없다.
    """
    summ = ctx.summaries.get(take_id)
    if summ is None or "standard_version" not in summ.model_fields_set:
        return None
    if summ.standard_version is None:
        return None, []
    crit = ctx.pitch.criteria if ctx.pitch is not None else None
    if crit is None or crit.standard_version != summ.standard_version:
        return None
    if need is not None and need not in crit.model_fields_set:
        return None
    return crit.script_used, list(crit.targets)


def ask_confirm_card(
    pending: dict[str, Any], *, ctx: RunCtx, expires_at: str
) -> dict[str, Any] | None:
    """대기 행동의 `args`만으로 그린 `ASK_CONFIRM` 카드(모델 문장 없음). 그릴 수 없으면 None(행동을 내지 않는다).

    `SAVE_GOAL`: before = 지금 대화 목표(없으면 null), after = 저장할 목표. `REASSESS`: before = 대상 Take 의 실제 리뷰 조건
    (`_take_conditions`, 확인할 수 없으면 None), after = 참고 조건(행동 인자 그대로). `args_hash`는 대기 행동의 것을 그대로 싣는다.
    """
    atype, args, ahash = pending["type"], pending["args"], pending.get("args_hash")
    if atype not in ("SAVE_GOAL", "REASSESS") or not ahash:
        return None
    notes: list[str] = []
    before: dict[str, Any] | None
    if atype == "SAVE_GOAL":
        try:
            goal = GoalDraft.model_validate(args["goal"])
        except (ValidationError, KeyError):
            return None
        before = ctx.goal.model_dump(mode="json") if ctx.goal is not None else None
        after = goal.model_dump(mode="json")
        summary = _goal_summary(goal, ctx.cfg)
        if before is not None:
            notes.append(templates.SAVE_GOAL_NOTE_REPLACE)
        notes.append(templates.SAVE_GOAL_NOTE_UNDO)
    else:
        summ = ctx.summaries.get(args.get("take_id"))
        if summ is None:
            return None
        change = args["change"]
        cond = _take_conditions(
            ctx, summ.take_id, "script_used" if "script_used" in change else "targets"
        )
        if cond is None:
            return None  # 바뀌기 전 조건을 확인할 수 없으면 카드를 그리지 않는다(행동을 내지 않음)
        now_script, now_targets = cond
        take_view = {"take_id": summ.take_id, "take_number": summ.take_number}
        if "script_used" in change:
            before = {**take_view, "script_used": now_script}
            after = {**take_view, "script_used": change["script_used"]}
            key = "script_used_true" if change["script_used"] else "script_used_false"
            text = templates.REASSESS_CHANGE_TEXT[key]
        else:
            now = [_target_view(t) for t in now_targets]
            new = [_target_view(t) for t in change.get("targets") or []]
            before = {**take_view, "targets": now}
            after = {**take_view, "targets": new}
            text = templates.REASSESS_CHANGE_TEXT["targets"].format(count=len(new))
        summary = templates.fill(
            templates.REASSESS_SUMMARY, take_number=str(summ.take_number), change=text
        )
        notes.append(templates.REASSESS_NOTE)
    return AskConfirmCard(
        action_id=pending["action_id"],
        action_type=atype,
        title=templates.ASK_CONFIRM_TITLE[atype],
        summary=summary,
        before=before,
        after=after,
        notes=notes,
        expires_at=expires_at,
        args_hash=ahash,
    ).model_dump(mode="json")


def proposal_still_valid(ctx: RunCtx) -> tuple[bool, GoalDraft | None]:
    """고른 목표 제안(`selected_proposal`)이 지금 기록과 그대로인가(6-3 '제안을 고른 뒤')."""
    sp = ctx.req.context.selected_proposal
    if sp is None:
        return False, None
    goal = sp.goal
    if _goal_options_hash(goal) != sp.content_hash:
        return False, None
    base = ctx.snapshots.get(goal.base.take_id)
    if base is None:
        return False, None
    if _proposal_fingerprint(ctx, base) != sp.evidence_basis.fingerprint:
        return False, None
    rebuilt = build_goal_draft(
        base, goal.metric, goal.slide_number, pitch_id=goal.pitch_id, cfg=ctx.cfg
    )
    if isinstance(rebuilt, str):
        return False, None
    if json.dumps(rebuilt.model_dump(mode="json"), sort_keys=True) != json.dumps(
        goal.model_dump(mode="json"), sort_keys=True
    ):
        return False, None
    return True, goal
