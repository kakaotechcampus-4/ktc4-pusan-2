"""가짜 BE 실행기 — BE 턴 실행기 · 행동 실행 · 저장을 흉내 내 `/agent/turn`을 처음부터 끝까지 돌린다 (기능 문서 5 · 6-6 · 12-2 · 12-3, 연구 전용).

- 누가 쓰나: 통합 시험(tests/lab/test_fake_be.py) · 평가 시나리오 실행기(evals/). BE 팀에는 참조 구현으로 함께 준다(12-3 'BE 시험').
  실제 BE 구현이 아니라 시험 · 연구 도구다. 계약(5절)대로 AI를 부르고 행동을 실행하는지 보는 데만 쓴다.
- 하는 일
  1. 저장소: 사용자 → 피치(`fixtures.PitchRecord`) → Take(`TakeRecord`, 저장된 코치 결과 · 공식 리뷰), 대화(범위 · 대화 목표와 판 번호 ·
     이전 판 · 메시지 · 판단 · 목표 제안 카드 · 다시 평가 참고 결과), 실행(호출 기록 · 행동 기록 · 서명 상태 · 누적량).
  2. 메시지 받기(`send_message`): 대화 기록(요약 + 최근 메시지 20개 + 앞 판단 50개)과 기본 자료(5-3 `context`)를 모아 `/agent/turn`을 부른다.
  3. 스트림 검사(12-3 '스트림 검사' · '최종 응답 검사'): 줄마다 모양 · `run_id` · 1부터 연속인 `seq`, 끝 줄 정확히 하나 · 마지막,
     `final` 모양(5-5), 빈 DONE · 등록되지 않은 행동 · 자율 등급 위조. 어기면 행동을 실행하지 않고 실행을 `AGENT_UPSTREAM_ERROR`로 끝낸다.
     진단 기록은 본체와 따로 검사해(`request_id` · `run_id` · `turn_call`) 틀리면 진단 기록만 버린다.
  4. `NEEDS_ACTION`: 행동 종류 · 고정 자율 등급 · 인자 키를 검사하고 사용자 · 피치 소유권을 확인해 실행한다(5-8 · 12-3 '행동 실행').
     같은 `(사용자, 종류, idempotency_key)`는 한 번만 실행하고 처음 결과를 다시 쓴다. 그다음 `ACTION_RESULT`로 이어 부른다:
     서명 상태 · 결과 1개 · `budget_used`(직전 final 값 + 행동 실행 시간) · 이 실행이 가져온 Take 를 다시 실은 `loaded_takes` ·
     이 실행에 보낸 피치 · Take 중 지워졌거나 소유가 바뀐 것의 `removed_sources`. 범위는 검증된 final 의 `scope_pitch_id`로 확정하고,
     바뀌었으면 `state_revision`을 1 올린다(5-6 '범위 바꾸기'). `TAKE_REVIEWED` 실행의 범위는 대화 범위에 저장하지 않는다.
  5. 끝 줄: 답 · 판단 · 카드 · 근거를 메시지로 추가 저장하고(고치지 않음), 목표 제안 카드 · 연습 준비 이동 · 대화 요약을 남긴다.
     자동 목표 확인 결과가 끝(terminal)이면 목표 상태를 바꾼다(목표 판 번호가 그대로일 때만).
  6. 자동 목표 확인(`auto_goal_check`): 목표가 `OPEN`인 대화에서 목표 저장 뒤 시작한 Take 의 리뷰를 Take 번호 순으로 `TAKE_REVIEWED`로 보낸다.
     `(대화, 목표 판, Take, 리뷰 판)`마다 한 번이다.
  7. 다시 평가(`REASSESS`, 6-6): 지금 기록으로 리뷰 요청 A 를 만들고, A 의 깊은 복사본에서 허용 칸 하나만 바꾼 B 를 만들어 실제 리뷰 코드
     (`pitch_coach_ai.features.review.core.review_take`, LLM 없이 — 규칙 칸은 LLM 과 상관없이 같다)를 두 번 부른다. 공식 리뷰는 고치지 않는다.
- 단순화(실제 BE와 다른 점): DB · 트랜잭션 · 행 잠금 · `row_revision` · 임대 · 재시작 복구 · 대화 자리 · 하루 턴 한도 · 취소 · 202 접수 ·
  HTTP 스트림이 없다(한 프로세스 · 한 스레드에서 차례로 부른다). 최신 아님 사유(6-8)는 근거가 지워진 답 가리기(`SOURCE_DELETED`)만 한다.
  `build_review_request`는 저장된 Take 결과 · 이전 Take 3개 · 피치 미션 · 평가 기준으로 흉내 낸다(실제 BE 함수를 부르지 않는다).
  '목표 저장 뒤 시작한 Take'는 저장 시각 대신 '저장 때 그 피치의 가장 큰 Take 번호보다 큰 번호'로 가린다.
  '이 실행이 읽은 원천'은 행동 기록만이 아니라 이 실행에 보낸 피치 · Take 전부로 넓게 잡는다(지운 것을 놓치지 않으려는 보수적 선택).
- 고칠 때: 계약(5절) · BE 할 일(12-3)이 바뀌면 여기와 tests/lab/test_fake_be.py 를 같이 고친다. 성능 · 동시성은 다루지 않는다.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Protocol

from pydantic import ValidationError

from chat_agent.actions import action_args_hash
from chat_agent.schemas import FinalData, StreamLine

from . import fixtures as fx
from .app import AgentApp

KST = timezone(timedelta(hours=9))
#: BE가 행동마다 고정해 두는 자율 등급(6-2). final 의 값이 다르면 위조로 보고 실행하지 않는다
FIXED_AUTONOMY = {
    "FETCH_PITCH": "AUTO",
    "FETCH_TAKE": "AUTO",
    "REASSESS": "ASK",
    "SAVE_GOAL": "ASK",
}
#: 행동별 인자 키(5-8). 정확히 이 키만 받는다
ACTION_ARG_KEYS = {
    "FETCH_PITCH": {"pitch_id", "name"},
    "FETCH_TAKE": {"pitch_id", "take_id"},
    "REASSESS": {"take_id", "change"},
    "SAVE_GOAL": {"expected_goal_version", "goal"},
}
#: 대화 목표 지표(P0 네 개) → 코치 Take 결과 영역(6-4)
GOAL_METRIC_AREA = {
    "audience_ratio": "GAZE",
    "filler_per_min": "FILLER",
    "cpm": "SPEED",
    "duration_ms": "TIME",
}
RANGE_METRICS = {"cpm", "duration_ms"}
#: REASSESS 에서 바꿀 수 있는 칸(6-6)
REASSESS_KEYS = {"script_used", "targets"}
#: A 의 user_criteria 가 null 일 때 B 가 시작하는 빈 기준(6-6 2번)
EMPTY_CRITERIA: dict[str, Any] = {"display_criteria": [], "targets": [], "script_used": None}
#: 지워진 근거에 기댄 답을 가리는 문구(12-1 · 12-2)
MASKED_TEXT = "근거 기록이 지워져 이 답을 가렸어요"
#: BE 실행 오류 코드로 그대로 두는 AI 오류(5-9 'BE의 AI 오류 해석'). 그 밖은 AGENT_UPSTREAM_ERROR
PASS_THROUGH_ERRORS = {
    "AGENT_DISABLED",
    "AGENT_BUSY",
    "AGENT_STATE_INVALID",
    "AGENT_CONTEXT_TOO_LARGE",
    "AGENT_OUTPUT_TOO_LARGE",
    "AGENT_TIMEOUT",
}
MAX_HISTORY_MESSAGES = 20
MAX_HISTORY_TEXT = 4000
MAX_PRIOR_ASSESSMENTS = 50
MAX_PITCHES = 20
MAX_LINE_BYTES = 2 * 1024 * 1024
MAX_DIAGNOSTICS_BYTES = 65_536
REASSESS_PER_DAY = 30
ZERO_BUDGET = {
    "model_calls": 0,
    "tool_calls": 0,
    "elapsed_ms": 0,
    "input_tokens": 0,
    "output_tokens": 0,
}


class Clock(Protocol):
    def monotonic(self) -> float: ...

    def clock_ms(self) -> int: ...


class _RealClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def clock_ms(self) -> int:
        return int(time.time() * 1000)


def _hash(obj: Any) -> str:
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def default_review(request: dict[str, Any]) -> dict[str, Any]:
    """실제 리뷰 코드(`review_take`)를 LLM 없이 부른다. 규칙 칸(점수 · 차원 · 사용자 기준 판정)은 LLM 과 상관없다(6-6 3번)."""
    from pitch_coach_ai.features.review.core import review_take

    return review_take(request, None).model_dump(mode="json")


# ── 저장소 모양 ──────────────────────────────────────────────────────────


@dataclass
class StoredMessage:
    """대화 메시지 한 행(12-1 '메시지'). 추가만 하고 고치지 않는다."""

    message_id: str
    role: str
    text: str
    run_id: str | None = None
    status: str | None = None
    claims: list[dict[str, Any]] = field(default_factory=list)
    cards: list[dict[str, Any]] = field(default_factory=list)
    assessments: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)
    basis: dict[str, Any] | None = None
    plan: list[dict[str, Any]] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)
    meta: dict[str, Any] | None = None
    #: 근거가 된 피치 · Take id(지워지면 이 답을 가린다, 12-2)
    source_ids: set[str] = field(default_factory=set)
    #: 자동 메시지(목표 확인)인가. 보관 기한을 늘리지 않는다(12-2)
    auto: bool = False


@dataclass
class Conversation:
    conversation_id: str
    user_id: str
    scope_pitch_id: str | None
    goal: dict[str, Any] | None = None
    goal_version: int = 0
    #: 이전 판(되돌리기용): (판 번호, 그 판의 목표 또는 None)
    goal_history: list[tuple[int, dict[str, Any] | None]] = field(default_factory=list)
    #: 목표 저장 때 그 피치의 가장 큰 Take 번호. 이보다 큰 번호가 '저장 뒤 시작한 Take'(단순화)
    goal_after_take: int = 0
    messages: list[StoredMessage] = field(default_factory=list)
    summary: str | None = None
    #: 메시지와 함께 저장한 목표 제안 카드(proposal_id → 카드). choice 가 오면 selected_proposal 로 싣는다
    proposals: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: 자동 목표 확인 중복 방지 키 (목표 판, take_id, 리뷰 판)
    goal_checks: set[tuple[int, str, int]] = field(default_factory=set)
    #: 다시 평가 참고 결과(공식 칸과 따로, 12-1)
    reassess_results: list[dict[str, Any]] = field(default_factory=list)
    #: 연습 준비 이동 지시(START_PRACTICE)
    practice_moves: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Call:
    """`/agent/turn` 호출 하나의 기록(12-1 'AI 호출 기록')."""

    number: int
    request_id: str
    request: dict[str, Any]
    http_status: int = 0
    lines: list[dict[str, Any]] = field(default_factory=list)
    final: dict[str, Any] | None = None
    #: 스트림 전 오류 코드 · error 끝 줄 코드 · BE 검사 실패 코드
    error_code: str | None = None
    #: 검사를 통과한 진단 기록(틀리면 None, 본체 처리와 상관없다)
    diagnostics: dict[str, Any] | None = None


@dataclass
class ActionRecord:
    """행동 한 번의 실행 기록(12-1 '행동')."""

    action: dict[str, Any]
    result: dict[str, Any]
    elapsed_ms: int = 0
    #: 실제로 효과를 냈는가(같은 키의 재시도 · 검사 거절이면 False)
    executed: bool = False
    #: `ASK` 행동을 사용자가 승인했는가. 임시: 승인 흐름을 붙이기 전까지 가짜 BE 는 `ASK` 행동을 자동 승인한다
    approved: bool = False


@dataclass
class Run:
    run_id: str
    conversation_id: str
    user_id: str
    trigger: str
    #: 처음 `/agent/turn` 요청을 다시 만들 원래 입력(사용자 말 · page · choice)
    original: dict[str, Any]
    history: dict[str, Any]
    status: str = "RUNNING"
    error_code: str | None = None
    calls: list[Call] = field(default_factory=list)
    actions: list[ActionRecord] = field(default_factory=list)
    progress: list[dict[str, Any]] = field(default_factory=list)
    state: dict[str, Any] | None = None
    state_revision: int = 0
    budget_used: dict[str, int] = field(default_factory=lambda: dict(ZERO_BUDGET))
    #: REASSESS 를 담은 NEEDS_ACTION 의 판단 메시지(실행 조회 interim_message)
    interim_message: dict[str, Any] | None = None
    #: 이 실행에 보낸 피치 · Take id. 이어 부를 때 다시 확인해 지워진 것을 removed_sources 로 보낸다
    sent_sources: set[str] = field(default_factory=set)
    #: 이 실행이 FETCH_TAKE 로 가져온 Take(이어 부를 때 loaded_takes 로 다시 싣는다)
    fetched_takes: list[str] = field(default_factory=list)
    reassess_count: int = 0
    review_event: dict[str, Any] | None = None

    @property
    def final(self) -> dict[str, Any] | None:
        return self.calls[-1].final if self.calls else None

    @property
    def finals(self) -> list[dict[str, Any]]:
        return [c.final for c in self.calls if c.final is not None]

    @property
    def requests(self) -> list[dict[str, Any]]:
        return [c.request for c in self.calls]


# ── 가짜 BE ──────────────────────────────────────────────────────────────


class FakeBe:
    """BE 턴 실행기 대역. `app`(서비스 계층 대역)으로 `/agent/turn`을 부르고 행동을 실행한다.

    Args:
        app: `AgentApp`. 시험은 가짜 모델 · 고정 키 · `clock`과 같은 시계를 넣어 만든다.
        pitches: 저장소에 넣을 피치(기본: `fixtures.sample_pitch()` 하나).
        clock: 행동 실행 시간을 재는 시계(`monotonic` · `clock_ms`). `advance(초)`가 있으면 행동마다 `action_ms`만큼 흘린다.
        action_ms: 행동 하나에 걸린 것으로 칠 시간(가짜 시계일 때).
        max_calls: 한 실행의 `/agent/turn` 호출 상한(8-2, 처음 포함 6번).
        review: 리뷰 호출 함수(요청 dict → 응답 dict). 기본은 실제 리뷰 코드. 장애 주입 시험에서만 바꾼다.
    """

    def __init__(
        self,
        app: AgentApp,
        *,
        pitches: list[fx.PitchRecord] | None = None,
        clock: Clock | None = None,
        action_ms: int = 0,
        max_calls: int = 6,
        review: Callable[[dict[str, Any]], dict[str, Any]] = default_review,
    ) -> None:
        self.app = app
        self.clock: Clock = clock or _RealClock()
        self.action_ms = action_ms
        self.max_calls = max_calls
        self.review_fn = review
        self.pitches: dict[str, fx.PitchRecord] = {}
        for p in pitches if pitches is not None else [fx.sample_pitch()]:
            self.add_pitch(p)
        self.conversations: dict[str, Conversation] = {}
        self.runs: dict[str, Run] = {}
        #: (사용자, 행동 종류, idempotency_key) → (인자 해시, 결과)
        self.action_results: dict[tuple[str, str, str], tuple[str, dict[str, Any]]] = {}
        #: 리뷰 호출 기록(다시 평가 A · B): {"take_id", "side", "request"}
        self.review_calls: list[dict[str, Any]] = []
        self.reassess_today: dict[str, int] = {}
        #: BE 로그(진단 기록 버림 등). 본문 · 측정값은 남기지 않는다
        self.log: list[dict[str, Any]] = []
        #: 행동을 실행하기 직전에 부르는 갈고리(시험이 다른 탭의 목표 변경 등을 끼워 넣는다)
        self.before_action: Callable[[FakeBe, Run, dict[str, Any]], None] | None = None
        #: 행동을 실행한 뒤 이어 부르기 전에 부르는 갈고리(시험이 삭제 · 기록 변경 · 키 교체를 끼워 넣는다)
        self.before_resume: Callable[[FakeBe, Run, ActionRecord], None] | None = None
        #: 장애 주입: 이어 부를 때 누적량을 0 으로 되돌려 보낸다(한도 우회 시도, L2-31)
        self.reset_budget_on_resume = False
        self._ids: dict[str, int] = {}

    # ── 저장소 ───────────────────────────────────────────────────────
    def _next(self, prefix: str) -> str:
        self._ids[prefix] = self._ids.get(prefix, 0) + 1
        return f"{prefix}-{self._ids[prefix]}"

    def add_pitch(self, p: fx.PitchRecord) -> fx.PitchRecord:
        self.pitches[p.pitch_id] = p
        return p

    def add_take(self, pitch_id: str, take: fx.TakeRecord) -> fx.TakeRecord:
        """새 Take(결과 · 리뷰가 저장된 것)를 더한다. 리뷰가 없으면 자동 목표 확인이 기다린다."""
        self.pitches[pitch_id].takes.append(take)
        return take

    def delete_take(self, take_id: str) -> None:
        """Take 삭제(기존 삭제 코드 흉내). 파생 사본은 읽을 때 가린다(12-2 ① '읽을 때 가림')."""
        for p in self.pitches.values():
            p.takes = [t for t in p.takes if t.take_id != take_id]

    def delete_pitch(self, pitch_id: str) -> None:
        self.pitches.pop(pitch_id, None)

    def owned_pitch(self, pitch_id: str | None, user_id: str) -> fx.PitchRecord | None:
        p = self.pitches.get(pitch_id) if pitch_id else None
        return p if p is not None and p.user_id == user_id else None

    def owned_take(self, take_id: str, user_id: str) -> tuple[fx.PitchRecord, fx.TakeRecord] | None:
        for p in self.pitches.values():
            t = p.take(take_id)
            if t is not None:
                return (p, t) if p.user_id == user_id else None
        return None

    def _gone(self, source_id: str, user_id: str) -> bool:
        """지워졌거나 소유가 바뀌었는가(피치 · Take id 모두)."""
        if source_id in self.pitches:
            return self.pitches[source_id].user_id != user_id
        return self.owned_take(source_id, user_id) is None

    def user_pitches(self, user_id: str) -> list[fx.PitchRecord]:
        mine = [p for p in self.pitches.values() if p.user_id == user_id]
        return sorted(mine, key=lambda p: p.last_practiced_at or "", reverse=True)

    def use_rule_reviews(self) -> None:
        """저장된 공식 리뷰를 실제 리뷰 코드의 결과로 바꾼다(가상 리뷰 대신 '같은 기록 → 같은 규칙 결과'인 상태로 시작)."""
        for p in self.pitches.values():
            for t in p.takes:
                if t.take_result is not None:
                    t.review = self.review_fn(self.build_review_request(p, t))

    def build_review_request(self, p: fx.PitchRecord, t: fx.TakeRecord) -> dict[str, Any]:
        """BE `build_review_request(db, take_id)`의 흉내: 지금 저장된 기록으로 리뷰 요청을 만든다(6-6 2번)."""
        prev = sorted(
            (x for x in p.takes if x.take_number < t.take_number and x.take_result is not None),
            key=lambda x: x.take_number,
            reverse=True,
        )[:3]
        crit = p.criteria
        return {
            "take_result": copy.deepcopy(t.take_result),
            "script_coverage": copy.deepcopy(t.script_coverage),
            "previous_takes": [
                {"take_result": copy.deepcopy(x.take_result), "take_scores": None} for x in prev
            ]
            or None,
            "previous_recurring_issues": None,
            "missions": [
                {
                    "mission_id": m["mission_id"],
                    "area": m.get("area"),
                    "slide_number": m.get("slide_number"),
                    "target": m["target"],
                }
                for m in p.missions
            ]
            or None,
            "user_criteria": (
                {
                    k: copy.deepcopy(crit.get(k))
                    for k in ("display_criteria", "targets", "script_used")
                }
                if crit
                else None
            ),
        }

    def new_conversation(
        self, user_id: str = "u-3", scope_pitch_id: str | None = "p-1"
    ) -> Conversation:
        if scope_pitch_id is not None and self.owned_pitch(scope_pitch_id, user_id) is None:
            raise LookupError("NOT_FOUND")  # 없는 · 남의 피치는 같은 404
        conv = Conversation(self._next("c"), user_id, scope_pitch_id)
        self.conversations[conv.conversation_id] = conv
        return conv

    # ── 메시지 받기 · 자동 목표 확인 ───────────────────────────────────
    def send_message(
        self,
        conversation_id: str | None,
        text: str,
        *,
        user_id: str = "u-3",
        scope_pitch_id: str | None = "p-1",
        page_take_id: str | None = None,
        choice: dict[str, Any] | None = None,
        screen: dict[str, Any] | None = None,
    ) -> Run:
        """사용자 메시지 하나를 받아 실행을 끝까지 돌린다(12-3 '턴 실행기'). 입력이 틀리면 ValueError(BE 422), 없는 대상은 LookupError(404)."""
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValueError("VALIDATION_ERROR")
        conv = (
            self.conversations[conversation_id]
            if conversation_id is not None
            else self.new_conversation(user_id, scope_pitch_id)
        )
        if conv.user_id != user_id:
            raise LookupError("NOT_FOUND")
        page_take = self.owned_take(page_take_id, user_id) if page_take_id else None
        page = {
            "route": f"/takes/{page_take_id}" if page_take else "/",
            "pitch_id": page_take[0].pitch_id if page_take else conv.scope_pitch_id,
            "take_id": page_take_id if page_take else None,
            "screen": screen,
        }
        selected = self._selected_proposal(conv, choice) if choice is not None else None
        history = self._history(conv)
        conv.messages.append(StoredMessage(self._next("m"), "user", text))
        run = self._new_run(
            conv,
            "USER_MESSAGE",
            original={"user_message": text, "page": page, "choice": choice},
            history=history,
        )
        ctx = self._context(conv, run, focus_take_id=page["take_id"])
        ctx["selected_proposal"] = selected
        req = {
            "run": self._run_block(run, conv.scope_pitch_id, "USER_MESSAGE"),
            "user_message": text,
            "choice": choice,
            "review_event": None,
            "page": page,
            "history": history,
            "context": ctx,
            "resume": None,
            "budget_used": dict(ZERO_BUDGET),
        }
        return self._drive(run, conv, req)

    def auto_goal_check(self) -> list[Run]:
        """자동 목표 확인 한 주기(12-3): 목표가 OPEN 인 대화마다 저장 뒤 시작한 Take 의 리뷰를 번호 순으로 판정한다."""
        runs: list[Run] = []
        for conv in list(self.conversations.values()):
            while conv.goal is not None and conv.goal.get("status") == "OPEN":
                goal = conv.goal
                p = self.owned_pitch(goal["pitch_id"], conv.user_id)
                if p is None:
                    break
                nxt = None
                for t in sorted(p.takes, key=lambda x: x.take_number):
                    if t.take_number <= conv.goal_after_take:
                        continue
                    if t.review is None:
                        if t.status == "COMPLETED":
                            break  # 앞 번호의 리뷰를 기다린다(6-4 판정 순서)
                        continue  # 끝나지 않은 · 실패한 Take 는 건너뛴다
                    key = (int(goal["version"]), t.take_id, int(t.revisions.get("REVIEW") or 0))
                    if key not in conv.goal_checks:
                        nxt = (t, key)
                        break
                if nxt is None:
                    break
                conv.goal_checks.add(nxt[1])
                runs.append(self._take_reviewed(conv, p, nxt[0]))
        return runs

    def _take_reviewed(self, conv: Conversation, p: fx.PitchRecord, t: fx.TakeRecord) -> Run:
        goal = conv.goal
        assert goal is not None
        event = {
            "take_id": t.take_id,
            "review_revision": int(t.revisions.get("REVIEW") or 0),
            "goal_version": int(goal["version"]),
        }
        history = self._history(conv)
        page = {"route": "", "pitch_id": p.pitch_id, "take_id": None, "screen": None}
        run = self._new_run(
            conv,
            "TAKE_REVIEWED",
            original={"user_message": None, "page": page, "choice": None},
            history=history,
        )
        run.review_event = event
        # 실행 범위는 대화 범위가 아니라 목표의 피치다(6-5)
        ctx = self._context(conv, run, focus_take_id=t.take_id, scope_pitch_id=p.pitch_id)
        req = {
            "run": self._run_block(run, p.pitch_id, "TAKE_REVIEWED"),
            "user_message": None,
            "choice": None,
            "review_event": event,
            "page": page,
            "history": history,
            "context": ctx,
            "resume": None,
            "budget_used": dict(ZERO_BUDGET),
        }
        return self._drive(run, conv, req)

    # ── 요청 만들기 ───────────────────────────────────────────────────
    def _new_run(
        self, conv: Conversation, trigger: str, *, original: dict[str, Any], history: dict[str, Any]
    ) -> Run:
        run = Run(
            self._next("run"),
            conv.conversation_id,
            conv.user_id,
            trigger,
            original=copy.deepcopy(original),
            history=history,
        )
        self.runs[run.run_id] = run
        return run

    def _run_block(self, run: Run, scope: str | None, trigger: str) -> dict[str, Any]:
        return {
            "run_id": run.run_id,
            "conversation_id": run.conversation_id,
            "user_id": run.user_id,
            "scope_pitch_id": scope,
            "trigger": trigger,
            "state_revision": run.state_revision,
        }

    def _context(
        self,
        conv: Conversation,
        run: Run,
        *,
        focus_take_id: str | None,
        scope_pitch_id: str | None = None,
    ) -> dict[str, Any]:
        """기본 자료(5-3 · 12-3 '기본 자료 모으기'). 모든 자료에 사용자 소유권과 피치 관계를 건다."""
        user = conv.user_id
        mine = self.user_pitches(user)
        scope = scope_pitch_id if scope_pitch_id is not None else conv.scope_pitch_id
        p = self.owned_pitch(scope, user)
        pitch_ctx = None
        loaded: list[dict[str, Any]] = []
        if p is not None:
            focus = focus_take_id if focus_take_id and p.take(focus_take_id) else None
            pitch_ctx = fx.pitch_context(p, focus_take_id=focus)
            run.sent_sources.add(p.pitch_id)
            run.sent_sources.update(t["take_id"] for t in pitch_ctx["takes"])
            if pitch_ctx["focus_take"] is not None:
                run.sent_sources.add(pitch_ctx["focus_take"]["summary"]["take_id"])
            for tid in run.fetched_takes:
                t = p.take(tid)
                if t is not None:
                    loaded.append(fx.take_snapshot(t))
        return {
            "pitches": [fx.pitch_summary(x) for x in mine[:MAX_PITCHES]],
            "pitches_total": len(mine),
            "pitch": pitch_ctx,
            "conversation_goal": copy.deepcopy(conv.goal),
            "goal_version": conv.goal_version,
            "loaded_takes": loaded,
            "removed_sources": [],
            "selected_proposal": None,
        }

    def _selected_proposal(self, conv: Conversation, choice: dict[str, Any]) -> dict[str, Any]:
        """고른 목표 제안을 이 대화에 저장된 카드에서 찾는다. 없거나 다른 대화의 것이면 404(12-3 '메시지 보내기')."""
        card = conv.proposals.get(choice.get("proposal_id", ""))
        opt = next(
            (
                o
                for o in (card or {}).get("options", [])
                if o.get("label") == choice.get("option") and o.get("goal") is not None
            ),
            None,
        )
        if card is None or opt is None:
            raise LookupError("NOT_FOUND")
        return {
            "proposal_id": card["proposal_id"],
            "option": opt["label"],
            "goal": copy.deepcopy(opt["goal"]),
            "content_hash": opt["content_hash"],
            "evidence_basis": copy.deepcopy(card["evidence_basis"]),
        }

    def _history(self, conv: Conversation) -> dict[str, Any]:
        """대화 기록(5-3 `history`): 요약 + 최근 메시지 20개 + 앞 판단 50개. 지워진 근거에 기댄 답은 가리고 그 판단은 싣지 않는다."""
        messages: list[dict[str, Any]] = []
        for m in conv.messages[-MAX_HISTORY_MESSAGES:]:
            masked = any(self._gone(s, conv.user_id) for s in m.source_ids)
            text, truncated = (MASKED_TEXT, False) if masked else self._clip(m)
            messages.append(
                {
                    "role": m.role,
                    "text": text,
                    "truncated": truncated,
                    "basis": None if masked else copy.deepcopy(m.basis),
                    "stale_reasons": ["SOURCE_DELETED"] if masked else [],
                }
            )
        prior: list[dict[str, Any]] = []
        for m in conv.messages:
            if any(self._gone(s, conv.user_id) for s in m.source_ids):
                continue
            prior.extend(copy.deepcopy(a) for a in m.assessments)
        return {
            "summary": conv.summary,
            "messages": messages,
            "prior_assessments": prior[-MAX_PRIOR_ASSESSMENTS:],
        }

    @staticmethod
    def _clip(m: StoredMessage) -> tuple[str, bool]:
        """4,000자를 넘는 이전 답은 claims 를 앞에서부터 들어가는 만큼만 싣고 truncated 로 표시한다(5-3)."""
        if len(m.text) <= MAX_HISTORY_TEXT:
            return m.text, False
        out: list[str] = []
        for c in m.claims:
            nxt = "\n".join([*out, c.get("text", "")])
            if len(nxt) > MAX_HISTORY_TEXT:
                break
            out.append(c.get("text", ""))
        return "\n".join(out), True

    # ── 부르기 · 스트림 검사 ──────────────────────────────────────────
    def _drive(self, run: Run, conv: Conversation, req: dict[str, Any]) -> Run:
        while True:
            call = self._call(run, req)
            final = call.final
            if final is None:
                return self._fail(run, call.error_code or "AGENT_UPSTREAM_ERROR")
            scope_changed = False
            if run.trigger != "TAKE_REVIEWED" and final["scope_pitch_id"] != conv.scope_pitch_id:
                # 범위는 검증된 final 로만 확정한다(FETCH_PITCH 성공만으로는 바꾸지 않음, 5-6 · 6-5)
                conv.scope_pitch_id = final["scope_pitch_id"]
                scope_changed = True
            run.budget_used = dict(final["budget_used"])
            if final["status"] != "NEEDS_ACTION":
                self._store_final(run, conv, final)
                return run
            run.state = final["state"]
            if scope_changed:
                run.state_revision += 1
            action = final["actions"][0]
            if action["autonomy"] == "ASK" and final.get("message") is not None:
                run.interim_message = {
                    **copy.deepcopy(final["message"]),
                    "assessments": copy.deepcopy(final["assessments"]),
                    "evidence": copy.deepcopy(final["evidence"]),
                }
            if len(run.calls) >= self.max_calls:
                # 한 실행 6번(8-2): 앞서 받은 답이 있으면 PARTIAL, 없으면 AGENT_TIMEOUT
                if run.interim_message is not None:
                    run.status = "PARTIAL"
                    return run
                return self._fail(run, "AGENT_TIMEOUT")
            if self.before_action is not None:
                self.before_action(self, run, action)
            rec = self._execute(run, conv, action)
            if self.before_resume is not None:
                self.before_resume(self, run, rec)
            req = self._resume_request(run, conv, rec)

    def _resume_request(self, run: Run, conv: Conversation, rec: ActionRecord) -> dict[str, Any]:
        """ACTION_RESULT 이어 부르기(12-3 '이어 부를 때 자료'): 기본 자료를 다시 모으고 지워진 원천을 알린다."""
        removed = sorted(s for s in run.sent_sources if self._gone(s, run.user_id))
        ctx = self._context(conv, run, focus_take_id=run.original["page"].get("take_id"))
        ctx["removed_sources"] = removed
        budget = dict(run.budget_used)
        # 누적량은 직전 final 값에 행동 실행 시간을 더해 단조 증가로 보낸다(관측 usage 로 만들지 않음)
        budget["elapsed_ms"] = int(budget["elapsed_ms"]) + rec.elapsed_ms
        if self.reset_budget_on_resume:
            budget = dict(ZERO_BUDGET)
        return {
            "run": self._run_block(run, conv.scope_pitch_id, "ACTION_RESULT"),
            "user_message": None,
            "choice": None,
            "review_event": None,
            "page": copy.deepcopy(run.original["page"]),
            "history": run.history,
            "context": ctx,
            "resume": {"state": run.state, "action_results": [copy.deepcopy(rec.result)]},
            "budget_used": budget,
        }

    def _call(self, run: Run, req: dict[str, Any]) -> Call:
        n = len(run.calls) + 1
        call = Call(n, f"req-{run.run_id}-{n}", copy.deepcopy(req))
        run.calls.append(call)
        out = self.app.turn(
            json.dumps(req, ensure_ascii=False).encode("utf-8"), request_id=call.request_id
        )
        call.http_status = out.status
        if out.lines is None:
            call.error_code = ((out.error or {}).get("error") or {}).get("code") or "UNKNOWN"
            return call
        self._check_stream(run, call, list(out.lines))
        run.progress.extend(ln for ln in call.lines if ln["event"] in ("plan", "tool_done"))
        return call

    def _check_stream(self, run: Run, call: Call, raw: list[str]) -> None:
        """스트림 검사(12-3): 어기면 call.error_code 를 남기고 final 을 쓰지 않는다(행동 실행 0번)."""
        parsed: list[dict[str, Any]] = []
        for i, line in enumerate(raw, start=1):
            if not line.endswith("\n") or len(line.encode("utf-8")) > MAX_LINE_BYTES:
                call.error_code = "STREAM_INVALID"
                return
            try:
                obj = json.loads(line)
                StreamLine.model_validate(obj)
            except (ValueError, ValidationError):
                call.error_code = "STREAM_INVALID"
                return
            if obj["run_id"] != run.run_id or obj["seq"] != i:
                call.error_code = "STREAM_INVALID"
                return
            parsed.append(obj)
        call.lines = parsed
        ends = [i for i, ln in enumerate(parsed) if ln["event"] in ("final", "error")]
        if ends != [len(parsed) - 1]:
            call.error_code = "STREAM_INVALID"  # 끝 줄 없는 EOF · 중복 · 끝 줄 뒤 이벤트
            return
        last = parsed[-1]
        diag = last["data"].get("diagnostics")
        call.diagnostics = diag if self._diagnostics_ok(diag, run, call) else None
        if diag is not None and call.diagnostics is None:
            self.log.append(
                {"event": "DIAGNOSTICS_INVALID", "run_id": run.run_id, "call": call.number}
            )
        if last["event"] == "error":
            call.error_code = (last["data"].get("error") or {}).get("code") or "UNKNOWN"
            return
        try:
            FinalData.model_validate(last["data"])
        except ValidationError:
            call.error_code = "FINAL_INVALID"
            return
        final = last["data"]
        if final["status"] == "DONE" and not final["message"]["claims"]:
            call.error_code = "FINAL_INVALID"  # 빈 DONE
            return
        if final["status"] == "NEEDS_ACTION":
            a = final["actions"][0]
            if FIXED_AUTONOMY.get(a["type"]) != a["autonomy"]:
                call.error_code = "ACTION_NOT_ALLOWED"  # 등록되지 않은 행동 · 자율 등급 위조
                return
        call.final = final

    @staticmethod
    def _diagnostics_ok(diag: Any, run: Run, call: Call) -> bool:
        if not isinstance(diag, dict):
            return False
        size = len(json.dumps(diag, ensure_ascii=False).encode("utf-8"))
        return (
            size <= MAX_DIAGNOSTICS_BYTES
            and diag.get("request_id") == call.request_id
            and diag.get("run_id") == run.run_id
            and diag.get("turn_call") == call.number
        )

    def _fail(self, run: Run, code: str) -> Run:
        run.status = "FAILED"
        run.error_code = code if code in PASS_THROUGH_ERRORS else "AGENT_UPSTREAM_ERROR"
        return run

    def _store_final(self, run: Run, conv: Conversation, final: dict[str, Any]) -> None:
        """종결 응답 저장(12-3 '판단 저장'): 메시지 추가 · 목표 제안 카드 · 이동 지시 · 요약 · 자동 목표 확인 결과."""
        msg = final["message"]
        sources = {e.get("take_id") for e in final["evidence"]} | {
            e.get("pitch_id") for e in final["evidence"]
        }
        for bp in final["basis"].get("pitches", []):
            sources.add(bp.get("pitch_id"))
            sources.update(t.get("take_id") for t in bp.get("takes", []))
        for a in final["assessments"]:
            for rec in (a.get("evidence_basis") or {}).get("records", []):
                sources.add(rec.get("pitch_id"))
                sources.update(t.get("take_id") for t in rec.get("takes", []))
        sources.discard(None)
        sources.discard("")
        conv.messages.append(
            StoredMessage(
                self._next("m"),
                "assistant",
                msg["text"],
                run_id=run.run_id,
                status=final["status"],
                claims=copy.deepcopy(msg["claims"]),
                cards=copy.deepcopy(msg["cards"]),
                assessments=copy.deepcopy(final["assessments"]),
                evidence=copy.deepcopy(final["evidence"]),
                sources=copy.deepcopy(msg["sources"]),
                basis=copy.deepcopy(final["basis"]),
                plan=copy.deepcopy(final["plan"]),
                trace=copy.deepcopy(final["trace"]),
                meta=copy.deepcopy(final["meta"]),
                source_ids={s for s in sources if isinstance(s, str)},
                auto=run.trigger == "TAKE_REVIEWED",
            )
        )
        for card in msg["cards"]:
            if card.get("type") == "GOAL_PROPOSAL":
                conv.proposals[card["proposal_id"]] = copy.deepcopy(card)
            elif card.get("type") == "START_PRACTICE":
                conv.practice_moves.append({"run_id": run.run_id, "pitch_id": card["pitch_id"]})
        if final.get("history_summary"):
            conv.summary = final["history_summary"]
        gr = final.get("goal_result")
        goal = conv.goal
        if (
            run.trigger == "TAKE_REVIEWED"
            and gr is not None
            and gr["terminal"]
            and goal is not None
            and goal["version"] == gr["goal_version"]
            and goal["status"] == "OPEN"
        ):
            # 목표 판이 그대로이고 OPEN 일 때만 한 번 바꾼다(6-4). 판 번호는 올리지 않는다
            conv.goal = {**goal, "status": gr["status"]}
        run.status = final["status"]

    def superseded_by(self, conv: Conversation) -> dict[str, str | None]:
        """메시지 목록의 `superseded_by`(12-3 '판단 저장'): 이 대화의 판단마다 그것을 supersedes 로 가리킨 새 판단의 ID."""
        ids = [a["assessment_id"] for m in conv.messages for a in m.assessments]
        out: dict[str, str | None] = dict.fromkeys(ids)
        for m in conv.messages:
            for a in m.assessments:
                for old in a.get("supersedes") or []:
                    if old in out:
                        out[old] = a["assessment_id"]
        return out

    # ── 행동 실행 ─────────────────────────────────────────────────────
    def _execute(self, run: Run, conv: Conversation, action: dict[str, Any]) -> ActionRecord:
        """행동 하나(12-3 '행동 실행'). 같은 키는 처음 결과를 다시 쓰고, 같은 키에 다른 인자는 거절한다."""
        t0 = self.clock.monotonic()
        key = (run.user_id, action["type"], action["idempotency_key"])
        ahash = _hash(action["args"])
        executed = False
        if key in self.action_results:
            prev_hash, prev = self.action_results[key]
            result = (
                copy.deepcopy(prev)
                if prev_hash == ahash
                else self._failed(action, "IDEMPOTENCY_CONFLICT")
            )
        elif FIXED_AUTONOMY.get(action["type"]) != action["autonomy"] or set(
            action["args"]
        ) != ACTION_ARG_KEYS.get(action["type"]):
            result = self._failed(action, "INVALID_ACTION")
        else:
            handler = {
                "FETCH_PITCH": self._fetch_pitch,
                "FETCH_TAKE": self._fetch_take,
                "REASSESS": self._reassess,
                "SAVE_GOAL": self._save_goal,
            }[action["type"]]
            result = handler(run, conv, action)
            executed = result["status"] != "FAILED"
            self.action_results[key] = (ahash, copy.deepcopy(result))
        advance = getattr(self.clock, "advance", None)
        if self.action_ms and callable(advance):
            advance(self.action_ms / 1000)
        elapsed = max(0, round((self.clock.monotonic() - t0) * 1000))
        rec = ActionRecord(
            copy.deepcopy(action), result, elapsed, executed, approved=action["autonomy"] == "ASK"
        )
        run.actions.append(rec)
        return rec

    @staticmethod
    def _ok(
        action: dict[str, Any], data: dict[str, Any], status: str = "SUCCEEDED"
    ) -> dict[str, Any]:
        return {
            "action_id": action["action_id"],
            "type": action["type"],
            "status": status,
            "data": data,
            "error": None,
            # `ASK` 행동(승인 뒤 실행)의 결과에는 BE 가 실행한 args 의 해시를 싣는다(5-8)
            "executed_args_hash": action_args_hash(action["type"], action["args"])
            if action.get("autonomy") == "ASK"
            else None,
        }

    @staticmethod
    def _failed(action: dict[str, Any], code: str) -> dict[str, Any]:
        return {
            "action_id": action["action_id"],
            "type": action["type"],
            "status": "FAILED",
            "data": None,
            "error": {"code": code, "message": code},
            # 실패한 `ASK` 행동의 결과에도 해시를 싣는다(5-8)
            "executed_args_hash": action_args_hash(action["type"], action["args"])
            if action.get("autonomy") == "ASK"
            else None,
        }

    def _send_pitch(self, run: Run, p: fx.PitchRecord) -> dict[str, Any]:
        pc = fx.pitch_context(p, focus_take_id=None)
        run.sent_sources.add(p.pitch_id)
        run.sent_sources.update(t["take_id"] for t in pc["takes"])
        if pc["focus_take"] is not None:
            run.sent_sources.add(pc["focus_take"]["summary"]["take_id"])
        return pc

    def _fetch_pitch(self, run: Run, conv: Conversation, action: dict[str, Any]) -> dict[str, Any]:
        """이 사용자의 피치를 id 또는 이름으로 찾는다. 0개 FAILED · 2개 이상 AMBIGUOUS(범위는 바꾸지 않음)."""
        args = action["args"]
        mine = self.user_pitches(run.user_id)
        if args.get("pitch_id") is not None:
            found = [p for p in mine if p.pitch_id == args["pitch_id"]]
        else:
            name = (args.get("name") or "").strip()
            found = [p for p in mine if p.title == name] or [
                p for p in mine if name and name in p.title
            ]
        if not found:
            return self._failed(action, "NOT_FOUND")
        if len(found) > 1:
            return self._ok(
                action, {"candidates": [fx.pitch_summary(p) for p in found]}, status="AMBIGUOUS"
            )
        return self._ok(action, {"pitch": self._send_pitch(run, found[0])})

    def _fetch_take(self, run: Run, conv: Conversation, action: dict[str, Any]) -> dict[str, Any]:
        """범위 피치의 Take 를 TakeSnapshot 으로 싣는다. 없는 · 남의 · 다른 피치의 Take 는 같은 NOT_FOUND."""
        args = action["args"]
        p = self.owned_pitch(args["pitch_id"], run.user_id)
        if p is None or args["pitch_id"] != conv.scope_pitch_id:
            return self._failed(action, "NOT_FOUND")
        t = p.take(args["take_id"])
        if t is None:
            return self._failed(action, "NOT_FOUND")
        if t.take_id not in run.fetched_takes:
            run.fetched_takes.append(t.take_id)
        run.sent_sources.add(t.take_id)
        return self._ok(action, {"take": fx.take_snapshot(t)})

    def _reassess(self, run: Run, conv: Conversation, action: dict[str, Any]) -> dict[str, Any]:
        """다시 평가(6-6 · 12-3 '다시 평가 BE 처리'): 지금 기록으로 만든 A 와 허용 칸 하나만 바꾼 B 를 실제 리뷰 코드로 부른다."""
        args = action["args"]
        found = self.owned_take(args["take_id"], run.user_id)
        if found is None:
            return self._failed(action, "NOT_FOUND")
        p, t = found
        if t.review is None:
            return self._failed(action, "NO_OFFICIAL_REVIEW")
        change = args["change"]
        if not isinstance(change, dict) or len(change) != 1 or set(change) - REASSESS_KEYS:
            return self._failed(action, "INVALID_CHANGE")
        if run.reassess_count >= 1:
            return self._failed(action, "REASSESS_PER_TURN")
        if self.reassess_today.get(run.user_id, 0) >= REASSESS_PER_DAY:
            return self._failed(action, "REASSESS_DAILY_LIMIT")
        run.reassess_count += 1
        self.reassess_today[run.user_id] = self.reassess_today.get(run.user_id, 0) + 1
        a_req = self.build_review_request(p, t)
        a_hash = _hash(a_req)
        b_req = copy.deepcopy(a_req)
        crit = b_req["user_criteria"] or copy.deepcopy(EMPTY_CRITERIA)
        (field_name,) = change
        crit[field_name] = copy.deepcopy(change[field_name])
        b_req["user_criteria"] = crit
        sides: dict[str, dict[str, Any]] = {}
        for side, body in (("A", a_req), ("B", b_req)):
            self.review_calls.append(
                {"take_id": t.take_id, "side": side, "request": copy.deepcopy(body)}
            )
            try:
                sides[side] = self.review_fn(copy.deepcopy(body))
            except (ValueError, ValidationError):
                return self._failed(action, "REVIEW_FAILED")  # 한쪽이라도 실패하면 둘 다 버린다
        if _hash(a_req) != a_hash:
            return self._failed(action, "REVIEW_REQUEST_CHANGED")
        before, after = sides["A"], sides["B"]
        if before.get("take_id") != t.take_id or after.get("take_id") != t.take_id:
            return self._failed(action, "REVIEW_TAKE_MISMATCH")
        cva = (before.get("meta") or {}).get("criteria_versions")
        cvb = (after.get("meta") or {}).get("criteria_versions")
        if not cva or cva != cvb:
            return self._failed(
                action, "RULES_DIFFER"
            )  # 규칙 버전이 다르면 실패하고 저장하지 않는다
        off = t.review
        official = {
            "score": off.get("score"),
            "dimensions": copy.deepcopy(off.get("dimensions") or {}),
            "criteria_results": copy.deepcopy(off.get("criteria_results") or []),
            "meta": {
                "criteria_versions": copy.deepcopy(
                    (off.get("meta") or {}).get("criteria_versions")
                ),
                "schema_version": (off.get("meta") or {}).get("schema_version"),
            },
        }
        conv.reassess_results.append(
            {
                "action_id": action["action_id"],
                "take_id": t.take_id,
                "change": copy.deepcopy(change),
                "change_hash": _hash(change),
                "before": copy.deepcopy(before),
                "after": copy.deepcopy(after),
                "official_review_revision": t.revisions.get("REVIEW"),
            }
        )
        return self._ok(action, {"before": before, "after": after, "official": official})

    def _save_goal(self, run: Run, conv: Conversation, action: dict[str, Any]) -> dict[str, Any]:
        """대화 목표 저장(12-3): 판 번호 조건부 저장 → 다시 읽은 값. 기준값 · 판 · 버전은 저장된 기록으로 다시 맞춰 본다."""
        if run.trigger == "TAKE_REVIEWED":
            return self._failed(action, "NOT_ALLOWED")
        args = action["args"]
        if args["expected_goal_version"] != conv.goal_version:
            return self._failed(action, "GOAL_VERSION_CONFLICT")
        if not self._authorized(run, conv, action):
            return self._failed(action, "NOT_AUTHORIZED")
        goal = args["goal"]
        why = self._verify_goal(run.user_id, goal)
        if why is not None:
            return self._failed(action, why)
        conv.goal_history.append((conv.goal_version, copy.deepcopy(conv.goal)))
        conv.goal_version += 1
        stored = {
            **copy.deepcopy(goal),
            "version": conv.goal_version,
            "status": "OPEN",
            "created_at": datetime.fromtimestamp(self.clock.clock_ms() / 1000, tz=UTC)
            .astimezone(KST)
            .isoformat(timespec="seconds"),
        }
        conv.goal = stored
        p = self.pitches[goal["pitch_id"]]
        conv.goal_after_take = max((t.take_number for t in p.takes), default=0)
        return self._ok(action, {"goal": copy.deepcopy(stored)})

    def _authorized(self, run: Run, conv: Conversation, action: dict[str, Any]) -> bool:
        """허용 근거를 이 실행의 처음 사용자 원문과 대조한다(6-2). 고른 제안(choice)이면 그 안과 같은 목표인지 본다."""
        choice = run.original.get("choice")
        if choice is not None:
            card = conv.proposals.get(choice.get("proposal_id", ""))
            opt = next(
                (
                    o
                    for o in (card or {}).get("options", [])
                    if o.get("label") == choice.get("option")
                ),
                None,
            )
            return opt is not None and _hash(opt.get("goal")) == _hash(action["args"]["goal"])
        quote = (action.get("authorized_by") or "").strip()
        original = run.original.get("user_message") or ""
        return bool(quote) and quote in original

    def _verify_goal(self, user_id: str, goal: dict[str, Any]) -> str | None:
        """BE가 실제 근거로 목표 기준을 다시 맞춰 본다(6-7). 다르면 거절 코드."""
        metric = goal.get("metric")
        area = GOAL_METRIC_AREA.get(metric or "")
        if area is None:
            return "GOAL_METRIC_NOT_ALLOWED"
        if (goal.get("operator") == "in_range") != (metric in RANGE_METRICS):
            return "GOAL_OPERATOR_MISMATCH"
        p = self.owned_pitch(goal.get("pitch_id"), user_id)
        base = goal.get("base") or {}
        t = p.take(base.get("take_id", "")) if p is not None else None
        if p is None or t is None or t.take_result is None:
            return "NOT_FOUND"
        if base.get("take_number") != t.take_number:
            return "GOAL_BASE_MISMATCH"
        if base.get("revision") != t.revisions.get("TAKE_RESULT"):
            return "GOAL_BASE_MISMATCH"
        rule = (t.take_result.get("criteria") or {}).get(area, {}).get("criteria_version")
        if base.get("metric_rule_version") != rule:
            return "GOAL_BASE_MISMATCH"
        if (goal.get("script_version_id"), goal.get("standard_version")) != (
            t.script_version_id,
            t.standard_version,
        ):
            return "GOAL_BASE_MISMATCH"
        a = t.take_result["areas"][area]
        n = goal.get("slide_number")
        if n is None:
            value = (a.get("take") or {}).get(metric)
        else:
            row = next((s for s in a.get("slides") or [] if s.get("slide_number") == n), None)
            value = (row or {}).get("metrics", {}).get(metric)
        bv = base.get("value")
        if (
            not isinstance(value, (int, float))
            or not isinstance(bv, (int, float))
            or not math.isclose(float(value), float(bv), rel_tol=0, abs_tol=1e-9)
        ):
            return "GOAL_BASE_MISMATCH"
        return None


__all__ = [
    "ActionRecord",
    "Call",
    "Conversation",
    "FakeBe",
    "MASKED_TEXT",
    "Run",
    "StoredMessage",
    "default_review",
]
