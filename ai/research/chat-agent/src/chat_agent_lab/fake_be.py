"""가짜 BE 실행기 — BE 턴 실행기 · 행동 실행 · 저장을 흉내 내 `/agent/turn`을 처음부터 끝까지 돌린다 (기능 문서 5 · 6-6 · 12-2 · 12-3, 연구 전용).

- 누가 쓰나: 통합 시험(tests/lab/test_fake_be.py) · 평가 시나리오 실행기(evals/). BE 팀에는 참조 구현으로 함께 준다(12-3 'BE 시험').
  실제 BE 구현이 아니라 시험 · 연구 도구다. 계약(5절)대로 AI를 부르고 행동을 실행하는지 보는 데만 쓴다.
- 하는 일
  1. 저장소: 사용자 → 피치(`fixtures.PitchRecord`) → Take(`TakeRecord`, 저장된 코치 결과 · 공식 리뷰), 대화(범위 · 대화 목표와 판 번호 ·
     이전 판 · 메시지 · 판단 · 목표 제안 카드 · 다시 평가 참고 결과), 실행(호출 기록 · 행동 기록 · 서명 상태 · 누적량).
  2. 메시지 받기(`send_message`): 대화 기록(요약 항목 + 최근 메시지 20개 + 앞 판단 50개 + 요약 뒤로 밀린 메시지 최대 10개와 앞 요약의
     판 번호)과 기본 자료(5-3 `context`), 기억(`memory`: 설정이 꺼졌거나 '기억 없이' 대화면 꺼짐, 아니면 지금 코칭 프로필과 기억 번호)을
     모아 `/agent/turn`을 부른다. 기억 설정 · 항목 지우기 · 전체 지우기는 기억 번호를 올린다(6-14).
  3. 스트림 검사(12-3 '스트림 검사' · '최종 응답 검사'): 줄마다 모양 · `run_id` · 1부터 연속인 `seq`, 끝 줄 정확히 하나 · 마지막,
     `final` 모양(5-5), 빈 DONE · 등록되지 않은 행동 · 자율 등급 위조. 어기면 행동을 실행하지 않고 실행을 `AGENT_UPSTREAM_ERROR`로 끝낸다.
     진단 기록은 본체와 따로 검사해(`request_id` · `run_id` · `turn_call`) 틀리면 진단 기록만 버린다.
     실행 이벤트(L2-46): 진행 줄 · 문장 줄(`claim`)을 받은 순서대로 실행 전체에서 이어지는 `event_seq`로 저장하고, `events_after`가
     `after_seq` 커서 뒤의 이벤트만 돌려준다. 문장 줄은 번호가 실행 전체에서 이어져야 하고, 끝 줄 `message.claims`의 앞부분은
     이 실행에서 받은 문장과 글자 · 종류 · 근거까지 같아야 한다(L2-44). 어기거나 끝 줄 없이 끊기거나 `error` 끝 줄이면, 받은 문장이
     있을 때 그 문장 · 판단 · 근거로 `PARTIAL`을 남기고 행동은 실행하지 않는다(L2-45). 받은 문장이 없으면 위처럼 실패다.
  4. `NEEDS_ACTION`: 행동 종류 · 고정 자율 등급(`AUTO` · `ASK` · `BUTTON`) · 인자 키를 검사하고 `ASK` 행동은 `args_hash`를 같은 규칙으로
     다시 계산해 본다. 읽기(`AUTO`)와 선승인 `SAVE_GOAL`은 사용자 · 피치 소유권을 확인해 바로 실행하고(5-8 · 12-3 '행동 실행'),
     그 밖의 `ASK` 행동은 `args` · `args_hash` · 대기 시작 시각을 저장하고 실행을 `WAITING_APPROVAL`에서 멈춘 채 돌려준다.
     같은 `(사용자, 종류, idempotency_key)`는 한 번만 실행하고 처음 결과를 다시 쓴다. 그다음 `ACTION_RESULT`로 이어 부른다:
     서명 상태 · 결과 1개 · `budget_used`(직전 final 값 + 행동 실행 시간) · 이 실행이 가져온 Take 를 다시 실은 `loaded_takes` ·
     이 실행에 보낸 피치 · Take 중 지워졌거나 소유가 바뀐 것의 `removed_sources`. 범위는 검증된 final 의 `scope_pitch_id`로 확정하고,
     바뀌었으면 `state_revision`을 1 올린다(5-6 '범위 바꾸기'). `TAKE_REVIEWED` 실행의 범위는 대화 범위에 저장하지 않는다.
     승인 흐름(12-3 '승인 · 거절' · '상태 전이'): `approve(run_id, action_id, args_hash)`는 해시가 저장값과 다르면 409(`ApprovalError`, 실행 안 함,
     대기 그대로), 맞으면 대기를 먼저 선점하고 **저장한 `args`로만** 실행해 `ACTION_RESULT`에 `executed_args_hash`를 싣고 이어 부른다
     (같은 승인 재요청은 처음 결과, 다시 평가 하루 횟수는 승인 때 한 번). `reject` · 만료(대기 시작부터 30분, `expire_due`) · 대기 중 새 메시지
     (`send_message`가 새 요청을 먼저 검사하고 유효해 접수할 때 대기를 `CANCELLED`로 끝냄) · `cancel`은 **AI를 부르지 않는다**: 행동 `REJECTED` · `EXPIRED` · `CANCELLED`,
     실행 `DONE` · `EXPIRED` · `CANCELLED`, 이미 낸 문장 뒤에 코어 `SystemText`의 고정 문장을 덧붙여 메시지로 저장한다. 먼저 확정한 전이가 이기고
     취소 · 만료가 확정된 뒤 온 승인은 실행하지 않는다(`AGENT_CANCELLED` · `AGENT_EXPIRED`). 선승인 `SAVE_GOAL`(`preapproved`)은 실제 `args.goal`에서
     내용 해시를 다시 계산해 저장된 제안의 `content_hash` · 제안 id · 고른 안과 모두 같을 때만 실행하고 다르면 `FAILED`(`PREAPPROVAL_MISMATCH`)로 실행하지 않는다.
     `START_PRACTICE`(`BUTTON`)는 실행하지 않고 카드만 저장하며, 사용자가 누른 것은 `click_practice`가 이동 기록으로만 남긴다.
  5. 끝 줄: 답 · 판단 · 카드 · 근거를 메시지로 추가 저장하고(고치지 않음), 목표 제안 카드를 남긴다. 대화 요약은 이 실행이 보낸 앞
     요약의 판 번호가 지금과 같을 때만 바꾸고 덮은 메시지 수를 늘린다(L2-49). 코칭 프로필 저장 후보는 사용자 말로 시작한 실행 · 설정 켜짐 ·
     '기억 없이'가 아님 · 기억 번호가 실행을 시작할 때와 같을 때만, AI 와 같은 검사(memory.py)를 다시 해 통과한 것만 저장하고 그 값으로
     `MEMORY_SAVED` 카드를 답 메시지에 붙인다(L2-47 · L2-48).
     자동 목표 확인 결과가 끝(terminal)이면 목표 상태를 바꾼다(목표 판 번호가 그대로일 때만).
  6. 자동 목표 확인(`auto_goal_check`): 목표가 `OPEN`인 대화에서 목표 저장 뒤 시작한 Take 의 리뷰를 Take 번호 순으로 `TAKE_REVIEWED`로 보낸다.
     `(대화, 목표 판, Take, 리뷰 판)`마다 한 번이다.
  7. 다시 평가(`REASSESS`, 6-6): 지금 기록으로 리뷰 요청 A 를 만들고, A 의 깊은 복사본에서 허용 칸 하나만 바꾼 B 를 만들어 실제 리뷰 코드
     (`pitch_coach_ai.features.review.core.review_take`, LLM 없이 — 규칙 칸은 LLM 과 상관없이 같다)를 두 번 부른다. 공식 리뷰는 고치지 않는다.
- 단순화(실제 BE와 다른 점): DB · 트랜잭션 · 행 잠금 · `row_revision` · 임대 · 재시작 복구 · 대화 자리 · 하루 턴 한도 · 202 접수 ·
  HTTP 스트림이 없다(한 프로세스에서 부른다). 승인 · 거절 · 취소 · 만료 · 새 메시지의 '대기 확인 → 결정'은 한 잠금(`threading.RLock`) 안의
  조건부 전이로 흉내 낸다(먼저 잠근 쪽이 대기를 비우고 이긴다. 이긴 뒤의 실행은 잠금 밖에서 한다). 만료는 시계를 흘린 뒤 `expire_due`(또는 승인 · 거절 · 새 메시지가 올 때 먼저)로 확정한다. 승인 대기 시간을 실행의 120초에서 빼는 것은 흉내 내지 않는다. 최신 아님 사유(6-8)는 근거가 지워진 답 가리기(`SOURCE_DELETED`)만 한다.
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
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Protocol

from pydantic import ValidationError

from chat_agent import memory as memory_rules
from chat_agent.actions import ACTION_ERROR_CODES, action_args_hash
from chat_agent.config import DEFAULT_CONFIG
from chat_agent.schemas import ClaimLineData, FinalData, StreamLine
from chat_agent.templates import SystemText

from . import fixtures as fx
from .app import AgentApp

KST = timezone(timedelta(hours=9))
#: BE가 행동마다 고정해 두는 자율 등급(6-2). final 의 값이 다르면 위조로 보고 실행하지 않는다
FIXED_AUTONOMY = {
    "FETCH_PITCH": "AUTO",
    "FETCH_TAKE": "AUTO",
    "REASSESS": "ASK",
    "SAVE_GOAL": "ASK",
    "START_PRACTICE": "BUTTON",
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
#: 승인 대기 시간(대기 시작부터, 12-3 '만료 · 시간 초과')
APPROVAL_WAIT_MS = 30 * 60 * 1000
#: 거절 · 만료 · 취소 때 이미 낸 문장 뒤에 덧붙이는 고정 문장(7-10). 글은 코어 `SystemText` 한 곳에 있다
CLOSING_TEXT = {
    ("REJECTED", "SAVE_GOAL"): SystemText.REJECTED_SAVE_GOAL,
    ("REJECTED", "REASSESS"): SystemText.REJECTED_REASSESS,
    ("EXPIRED", "SAVE_GOAL"): SystemText.APPROVAL_EXPIRED,
    ("EXPIRED", "REASSESS"): SystemText.APPROVAL_EXPIRED,
    ("CANCELLED", "SAVE_GOAL"): SystemText.APPROVAL_CANCELLED,
    ("CANCELLED", "REASSESS"): SystemText.APPROVAL_CANCELLED,
}
#: [취소] 버튼으로 끝낸 승인 대기(새 메시지가 없었으므로 '새 메시지를 받아' 문장을 쓰지 않는다)
CANCEL_BUTTON_TEXT = SystemText.APPROVAL_CANCELLED_BY_USER
#: 행동 끝 상태 → 실행 끝 상태(12-3 상태 전이). 거절은 실행이 `DONE`
RUN_STATUS_AFTER = {"REJECTED": "DONE", "EXPIRED": "EXPIRED", "CANCELLED": "CANCELLED"}
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


class ApprovalError(Exception):
    """승인 · 거절 요청을 받아들이지 못한 BE 응답(12-3 '승인 · 거절'). `http`는 상태 코드, `code`는 오류 코드."""

    def __init__(self, http: int, code: str) -> None:
        super().__init__(f"{http} {code}")
        self.http = http
        self.code = code


def content_hash(goal: Any) -> str:
    """목표 제안 `content_hash` 다시 계산(5-8 `preapproved`): `canonical_json(goal)`의 SHA-256 앞 16자리(코어 `actions._goal_options_hash`와 같은 규칙)."""
    return _hash(goal)[:16]


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
    #: 대화 요약 항목(6-14) · 판 번호 · 요약이 덮은 메시지 수(앞에서부터)
    summary: list[dict[str, Any]] | None = None
    summary_version: int = 0
    summary_covered: int = 0
    #: 요약이 덮은 메시지들이 기댄 피치 · Take id(지워지면 요약을 가린다, 12-1 · L2-49)
    summary_sources: set[str] = field(default_factory=set)
    #: '기억 없이' 대화: 코칭 프로필을 읽지도 쓰지도 않는다(기록 · 요약은 보관, 6-14 · L2-48)
    no_memory: bool = False
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
    #: 이 호출에서 받은 문장 줄(`claim`)의 data(검사를 통과한 것만, 받은 순서)
    claims: list[dict[str, Any]] = field(default_factory=list)
    #: 받은 문장으로 `PARTIAL`을 남겨야 한다(끝 줄 없이 끊김 · `error` 끝 줄 · 앞부분 불일치 등, L2-44 · L2-45)
    partial_from_claims: bool = False
    #: 호출 시작부터 첫 문장 줄 · 마지막 줄까지 걸린 시간(ms, 실제 시계. 평가의 첫 문장 시간)
    first_claim_ms: int | None = None
    end_ms: int | None = None


@dataclass
class ActionRecord:
    """행동 한 번의 실행 기록(12-1 '행동')."""

    action: dict[str, Any]
    result: dict[str, Any]
    elapsed_ms: int = 0
    #: 실제로 효과를 냈는가(같은 키의 재시도 · 검사 거절 · 거절 · 만료 · 취소면 False)
    executed: bool = False
    #: `ASK` 행동을 어떻게 확정했는가: `APPROVED`(승인) · `PREAPPROVED`(고른 안의 선승인) · `REJECTED` · `EXPIRED` · `CANCELLED`.
    #: `AUTO` · `BUTTON` 행동과 확정 전이면 None. 승인 없이 실행한 `ASK` 행동은 평가에서 금지된 일이다(14-1)
    decision: str | None = None
    #: 승인 때 보관한 `args_hash`(`APPROVED`) 또는 선승인 행동의 `args_hash`(`PREAPPROVED`). 평가가 결과의 `executed_args_hash`와 대조한다
    approved_hash: str | None = None
    #: 결정 없이 `ASK` 행동을 실행하려 했으면 `NO_DECISION`(실행은 막혔고 평가에서 금지된 일로 센다)
    blocked: str | None = None


@dataclass
class PendingApproval:
    """승인 대기 한 건(12-1 '행동' · 12-3 '승인 대기'): BE 가 `NEEDS_ACTION`에서 저장한 `args` · `args_hash`와 시작 시각."""

    action: dict[str, Any]
    args: dict[str, Any]
    args_hash: str
    card: dict[str, Any]
    #: 대기 시작 시각(ms, 가짜 BE 시계). 여기서 30분이 지나면 만료
    started_ms: int
    #: 멈추기 전에 낸 `NEEDS_ACTION` final(이미 낸 문장 · 판단 · 근거). 거절 · 만료 · 취소 때 이 위에 고정 문장을 덧붙여 저장한다
    final: dict[str, Any]


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
    #: 실행 이벤트(12-3 · L2-46): 진행 줄(`plan` · `tool_done`)과 문장 줄(`claim`)을 실행 전체에서 이어지는 순번
    #: `event_seq`(1부터, 이어 부르기에서도 이어짐)로 받은 순서대로 저장한 것. 저장한 뒤 바꾸지 않는다
    events: list[dict[str, Any]] = field(default_factory=list)
    state: dict[str, Any] | None = None
    state_revision: int = 0
    budget_used: dict[str, int] = field(default_factory=lambda: dict(ZERO_BUDGET))
    #: 승인 대기 중인 행동(`status == "WAITING_APPROVAL"`일 때만). 승인 · 거절 · 만료 · 취소가 원자적으로 비우고 선점한다
    pending: PendingApproval | None = None
    #: 확정한 승인 대기의 `action_id` → 결정. 같은 `action_id`로 두 번 오면 처음 결과를 돌려준다
    decided: dict[str, str] = field(default_factory=dict)
    #: 승인 때 보관한 `args_hash`(`action_id` → 해시). 실행 직전에 실제 `args`의 해시와 다시 대조한다
    approved_hash: dict[str, str] = field(default_factory=dict)
    #: 거절 · 만료 · 취소로 끝났을 때 저장한 메시지(이미 낸 문장 + 고정 문장, AI 를 다시 부르지 않는다)
    closed_message: dict[str, Any] | None = None
    #: 승인 대기 응답(`NEEDS_ACTION`)에서 받아 이미 저장한 코칭 프로필의 `MEMORY_SAVED` 카드. 이 실행의 답 메시지가
    #: 저장될 때(승인 뒤 끝 · 거절 · 만료 · 취소) 그 메시지에 붙인다(6-14, #900 리뷰)
    memory_cards: list[dict[str, Any]] = field(default_factory=list)
    #: 이 실행에서 저장한 코칭 프로필 key(같은 실행의 끝 응답에 같은 값이 다시 실려도 한 번만)
    memory_saved: set[str] = field(default_factory=set)
    #: 이 실행에 보낸 피치 · Take id. 이어 부를 때 다시 확인해 지워진 것을 removed_sources 로 보낸다
    sent_sources: set[str] = field(default_factory=set)
    #: 이 실행이 FETCH_TAKE 로 가져온 Take(이어 부를 때 loaded_takes 로 다시 싣는다)
    fetched_takes: list[str] = field(default_factory=list)
    reassess_count: int = 0
    #: 승인 결정 때 예약한 다시 평가 횟수의 결과: `RESERVED` · `PER_TURN`(이 실행에서 이미 승인함) · `DAILY_LIMIT`
    reassess_slot: str | None = None
    review_event: dict[str, Any] | None = None
    #: 실행을 시작할 때의 기억 번호. 프로필 저장은 지금 번호가 이것과 같을 때만(6-14 · L2-47)
    memory_version: int | None = None

    @property
    def final(self) -> dict[str, Any] | None:
        return self.calls[-1].final if self.calls else None

    @property
    def finals(self) -> list[dict[str, Any]]:
        return [c.final for c in self.calls if c.final is not None]

    @property
    def requests(self) -> list[dict[str, Any]]:
        return [c.request for c in self.calls]

    @property
    def progress(self) -> list[dict[str, Any]]:
        """진행 줄 이벤트(`plan` · `tool_done`)."""
        return [e for e in self.events if e["event"] in ("plan", "tool_done")]

    @property
    def claim_lines(self) -> list[dict[str, Any]]:
        """이 실행에서 받은 문장 줄의 data(모든 호출, 받은 순서)."""
        return [e["data"] for e in self.events if e["event"] == "claim"]


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
        #: 사용자별 기억 설정(켜짐 · 기억 번호)과 코칭 프로필(key → 항목, 6-14 · 12-1 '기억 설정')
        self.memory_settings: dict[str, dict[str, Any]] = {}
        self.profiles: dict[str, dict[str, dict[str, Any]]] = {}
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
        #: `settle`의 `EXPIRE`가 시계를 흘린 시간(초). 평가 실행기가 장애 주입 시간에서 빼려고 읽고 0으로 되돌린다
        self.expired_seconds = 0.0
        self._ids: dict[str, int] = {}
        #: 승인 · 거절 · 취소 · 만료 · 새 메시지의 '대기 확인 → 결정'을 묶는 잠금(12-3 '상태 전이')
        self._lock = threading.RLock()

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
        self, user_id: str = "u-3", scope_pitch_id: str | None = "p-1", *, no_memory: bool = False
    ) -> Conversation:
        if scope_pitch_id is not None and self.owned_pitch(scope_pitch_id, user_id) is None:
            raise LookupError("NOT_FOUND")  # 없는 · 남의 피치는 같은 404
        conv = Conversation(self._next("c"), user_id, scope_pitch_id, no_memory=no_memory)
        self.conversations[conv.conversation_id] = conv
        return conv

    # ── 기억 설정 · 코칭 프로필 (6-14 · 12-3 B16 · B17) ───────────────────
    def memory_setting(self, user_id: str) -> dict[str, Any]:
        """사용자별 기억 설정(기본 켜짐, 번호 0)."""
        return self.memory_settings.setdefault(user_id, {"enabled": True, "version": 0})

    def _bump_memory(self, user_id: str) -> None:
        self.memory_setting(user_id)["version"] += 1

    def set_memory_enabled(self, user_id: str, enabled: bool) -> None:
        """설정 '코칭 프로필 기억' 켜기 · 끄기. 바꾸면 기억 번호를 올린다."""
        self.memory_setting(user_id)["enabled"] = enabled
        self._bump_memory(user_id)

    def profile_items(self, user_id: str) -> list[dict[str, Any]]:
        order = {k: i for i, k in enumerate(memory_rules.PROFILE_KEYS)}
        items = self.profiles.get(user_id, {}).values()
        return [copy.deepcopy(i) for i in sorted(items, key=lambda i: order[i["key"]])]

    def delete_profile_item(self, user_id: str, item_id: str) -> None:
        """항목 하나를 바로 실제로 지우고 기억 번호를 올린다(없는 · 남의 항목은 404)."""
        items = self.profiles.get(user_id, {})
        key = next((k for k, v in items.items() if v["item_id"] == item_id), None)
        if key is None:
            raise LookupError("NOT_FOUND")
        del items[key]
        self._bump_memory(user_id)

    def delete_all_profile(self, user_id: str) -> None:
        """전체 지우기: 항목을 모두 지우고 기억 번호를 올린다."""
        self.profiles.pop(user_id, None)
        self._bump_memory(user_id)

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
        approval: str | None = None,
    ) -> Run:
        """사용자 메시지 하나를 받아 실행을 승인 대기나 끝까지 돌린다(12-3 '턴 실행기'). 입력이 틀리면 ValueError(BE 422), 없는 대상은 LookupError(404).

        승인 대기(`WAITING_APPROVAL`)에서 멈추면 그대로 돌려준다(사용자가 `approve` · `reject`를 부른다). `approval`을 주면
        `settle`로 그 결정까지 이어서 한다(시험 · 평가 편의). 새 요청이 유효하고 이 대화가 승인 대기 중이면 접수할 때 대기 행동 · 실행을
        `CANCELLED`로 끝내고 취소 문장을 덧붙여 저장한다(AI 를 부르지 않는다, L2-43). 요청이 무효(없는 제안 id 등)면 대기를 그대로 둔다.
        """
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValueError("VALIDATION_ERROR")
        conv = (
            self.conversations[conversation_id]
            if conversation_id is not None
            else self.new_conversation(user_id, scope_pitch_id)
        )
        if conv.user_id != user_id:
            raise LookupError("NOT_FOUND")
        self.expire_due()
        # 새 요청(선택지 등)을 먼저 검사한다. 무효면 기존 승인 대기는 그대로 둔다
        page_take = self.owned_take(page_take_id, user_id) if page_take_id else None
        page = {
            "route": f"/takes/{page_take_id}" if page_take else "/",
            "pitch_id": page_take[0].pitch_id if page_take else conv.scope_pitch_id,
            "take_id": page_take_id if page_take else None,
            "screen": screen,
        }
        selected = self._selected_proposal(conv, choice) if choice is not None else None
        with self._lock:
            # 접수는 한 잠금 안에서: 진행 중인 실행(승인이 대기를 막 선점한 것 포함)이 있으면 409(12-3), 대기만 있으면 취소 뒤 접수.
            # 새 실행 생성까지 같은 잠금에 묶어 승인 실행과 새 실행이 한 대화에서 겹치지 않게 한다
            if any(
                r.conversation_id == conv.conversation_id and r.status == "RUNNING"
                for r in self.runs.values()
            ):
                raise ApprovalError(409, "AGENT_RUN_IN_PROGRESS")
            # 유효한 새 메시지를 접수할 때 대기를 취소한다(AI 를 부르지 않는다, L2-43)
            waiting = self.waiting_run(conv.conversation_id)
            if waiting is not None:
                self._close(waiting, conv, "CANCELLED")
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
            "memory": self._memory(conv),
        }
        run = self._drive(run, conv, req)
        return self.settle(run, approval) if approval is not None else run

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
            "memory": self._memory(conv),
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
            memory_version=self.memory_setting(conv.user_id)["version"],
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
        # 앞 요약 뒤로 쌓여 최근 20개 밖으로 밀린 메시지(최대 10개)와 앞 요약의 판 번호(6-14 · L2-49)
        pushed = conv.messages[
            conv.summary_covered : max(0, len(conv.messages) - MAX_HISTORY_MESSAGES)
        ]
        overflow = (
            {
                "messages": [
                    {
                        "role": m.role,
                        # 4,000자를 넘는 이전 답은 대화 기록처럼 문장을 앞에서부터 줄여 싣는다(5-3)
                        "text": MASKED_TEXT
                        if any(self._gone(s, conv.user_id) for s in m.source_ids)
                        else self._clip(m)[0],
                    }
                    for m in pushed[: DEFAULT_CONFIG.max_overflow_messages]
                ],
                "prev_summary_version": conv.summary_version,
            }
            if pushed
            else None
        )
        summary = copy.deepcopy(conv.summary)
        if summary and any(self._gone(s, conv.user_id) for s in conv.summary_sources):
            # 요약이 덮은 답의 근거가 지워졌으면 요약을 가린다(L2-37 · L2-49)
            summary = [{"kind": "TOPIC", "text": MASKED_TEXT}]
        return {
            "summary": summary,
            "messages": messages,
            "prior_assessments": prior[-MAX_PRIOR_ASSESSMENTS:],
            "overflow": overflow,
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
        """AI 를 부르고 `NEEDS_ACTION`이면 행동을 실행해 이어 부른다. `ASK` 행동(선승인 아님)은 승인 대기에서 멈춘다."""
        while True:
            call = self._call(run, req)
            final = call.final
            if final is None:
                if call.partial_from_claims:
                    # 끝 줄 없이 끊김 · error 끝 줄 · 앞부분 불일치여도 받은 문장은 PARTIAL 로 남긴다(L2-44 · L2-45)
                    self._store_received(run, conv)
                    return run
                if run.claim_lines:
                    # 앞 호출에서 이미 보인 문장(승인 전 답 등)은 이 호출이 실패해도 메시지로 남긴다(P13 · L1-41, #915 리뷰).
                    # 실행은 오류 코드 규칙(5-9)대로 FAILED 이고, 화면은 실행 오류 문구를 따로 보인다
                    self._store_received(run, conv, status="FAILED")
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
            if action["autonomy"] == "ASK" and action.get("preapproved") is None:
                # 같은 말의 기억 요청은 승인 결과와 상관없이 지금 저장한다(거절 · 만료 · 취소여도 사라지지 않게, 6-14)
                run.memory_cards += self._store_profile(run, conv, final.get("profile_updates"))
                self._wait_for_approval(run, action, final)
                return run
            req = self._run_action(run, conv, action)
            if req is None:
                return run

    def _emitted(self, run: Run) -> bool:
        """이 실행에서 이미 문장을 낸 final 이 있는가(마감 · 한도로 끊겨도 그 문장으로 `PARTIAL`)."""
        return any(f.get("message") is not None for f in run.finals)

    def _run_action(
        self, run: Run, conv: Conversation, action: dict[str, Any]
    ) -> dict[str, Any] | None:
        """행동 하나를 실행해 이어 부를 요청을 만든다. 호출 상한에 닿았으면 실행을 끝내고 None."""
        if len(run.calls) >= self.max_calls:
            # 한 실행 6번(8-2): 앞서 받은 답이 있으면 PARTIAL, 없으면 AGENT_TIMEOUT
            if run.claim_lines:
                self._store_received(run, self.conversations[run.conversation_id])
            elif self._emitted(run):
                run.status = "PARTIAL"
            else:
                self._fail(run, "AGENT_TIMEOUT")
            return None
        if self.before_action is not None:
            self.before_action(self, run, action)
        rec = self._execute(run, conv, action)
        if self.before_resume is not None:
            self.before_resume(self, run, rec)
        return self._resume_request(run, conv, rec)

    # ── 승인 대기 · 승인 · 거절 · 만료 · 취소 (12-3 '승인 · 거절' · '상태 전이') ──────────────
    def _wait_for_approval(self, run: Run, action: dict[str, Any], final: dict[str, Any]) -> None:
        """`ASK` 행동: `args` · `args_hash`를 저장하고 실행을 `WAITING_APPROVAL`로 둔다. 카드는 AI 가 그린 그대로 화면에 돌려준다."""
        card = next(
            c
            for c in final["message"]["cards"]
            if c["type"] == "ASK_CONFIRM" and c["action_id"] == action["action_id"]
        )
        run.pending = PendingApproval(
            action=copy.deepcopy(action),
            args=copy.deepcopy(action["args"]),
            args_hash=action["args_hash"],
            card=copy.deepcopy(card),
            started_ms=self.clock.clock_ms(),
            final=copy.deepcopy(final),
        )
        run.status = "WAITING_APPROVAL"

    def _store_received(self, run: Run, conv: Conversation, *, status: str = "PARTIAL") -> None:
        """이 실행에서 받은 문장 줄로 `PARTIAL` 메시지를 남긴다(L2-44 · L2-45): 문장 · 판단 · 근거 · 자료 사본을 그대로 보존하고
        행동은 실행하지 않는다. 근거 표시 · 계획 · 기록은 앞 응답의 끝 줄에서 가져온다(없으면 비움)."""
        lines = run.claim_lines
        claims = [copy.deepcopy(d["claim"]) for d in lines]

        def uniq(key: str, field_: str) -> list[dict[str, Any]]:
            out: dict[str, dict[str, Any]] = {}
            for d in lines:
                for x in d[key]:
                    out.setdefault(x[field_], copy.deepcopy(x))
            return list(out.values())

        last = run.finals[-1] if run.finals else {}
        received = {
            "status": "PARTIAL",
            "message": {
                "text": "\n".join(c["text"] for c in claims),
                "claims": claims,
                "cards": [],
                "sources": uniq("sources", "source_id"),
            },
            "evidence": uniq("evidence", "evidence_id"),
            "assessments": uniq("assessments", "assessment_id"),
            "basis": copy.deepcopy(last.get("basis") or {"pitches": []}),
            "plan": copy.deepcopy(last.get("plan") or []),
            "trace": copy.deepcopy(last.get("trace") or []),
            "meta": copy.deepcopy(last.get("meta")),
            "history_summary": None,
            "goal_result": None,
        }
        self._store_final(run, conv, received, status=status)

    def events_after(self, run_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        """실행 조회(12-3 · L2-46): `after_seq` 커서 뒤의 실행 이벤트만 `event_seq` 순서대로. 같은 커서로 다시 읽으면 같은 것과
        그 뒤에 저장된 것을 돌려준다(저장된 이벤트는 바뀌지 않는다)."""
        run = self.runs[run_id]
        return [copy.deepcopy(e) for e in run.events if e["event_seq"] > after_seq]

    def waiting_run(self, conversation_id: str) -> Run | None:
        """이 대화에서 승인 대기 중인 실행(대화마다 끝나지 않은 실행은 하나, 12-3)."""
        return next(
            (
                r
                for r in self.runs.values()
                if r.conversation_id == conversation_id and r.status == "WAITING_APPROVAL"
            ),
            None,
        )

    def expire_due(self) -> list[Run]:
        """대기 시작부터 30분을 넘긴 승인 대기를 `EXPIRED`로 끝낸다(AI 를 부르지 않는다). 끝낸 실행을 돌려준다."""
        now = self.clock.clock_ms()
        out: list[Run] = []
        with self._lock:
            for run in list(self.runs.values()):
                pend = run.pending
                if (
                    run.status == "WAITING_APPROVAL"
                    and pend is not None
                    and now - pend.started_ms >= APPROVAL_WAIT_MS
                ):
                    self._close(run, self.conversations[run.conversation_id], "EXPIRED")
                    out.append(run)
        return out

    def approve(
        self, run_id: str, action_id: str, args_hash: str, *, user_id: str | None = None
    ) -> Run:
        """승인(12-3): 화면에 보인 `args_hash`가 저장값과 같을 때만, 승인 때 보관한 `args` 사본으로 실행해 이어 부른다. 틀리면 `ApprovalError`.

        이미 결정된 `action_id`의 재요청은 그 행동의 이전 결과만 돌려준다(지금 다른 행동이 대기 중이어도 건드리지 않는다).
        """
        run, conv, pend = self._decide(run_id, action_id, args_hash, user_id, "APPROVED")
        if pend is None:
            return run  # 이미 결정된 행동의 재요청: 처음 결과
        # 보관한 args 로 실행 요청을 만든다. 대기 중 `pend.action["args"]`가 바뀌었어도 승인한 인자만 쓴다
        action = copy.deepcopy(pend.action)
        action["args"] = copy.deepcopy(pend.args)
        req = self._run_action(run, conv, action)
        if req is None:
            stored = any(m.run_id == run.run_id for m in conv.messages)
            if run.status == "PARTIAL" and not stored:
                # 호출 한도에 닿아 이어 부르지 못해도 승인 대기 때 받은 문장 · 판단 · 근거는 대화에 남긴다. 받은 문장 줄로
                # 이미 저장했으면(`_run_action`) 다시 저장하지 않는다(메시지 · 기억 카드가 두 번 생기지 않게)
                self._store_final(run, conv, copy.deepcopy(pend.final), status="PARTIAL")
            return run
        return self._drive(run, conv, req)

    def reject(
        self, run_id: str, action_id: str, args_hash: str, *, user_id: str | None = None
    ) -> Run:
        """거절(12-3): AI 를 부르지 않는다. 행동 `REJECTED` · 실행 `DONE`, 이미 낸 문장 뒤에 거절 문장을 덧붙여 저장."""
        run, _conv, _pend = self._decide(run_id, action_id, args_hash, user_id, "REJECTED")
        return run

    def cancel(self, run_id: str, *, user_id: str | None = None) -> Run:
        """[취소]: 승인 대기 중이면 행동 `CANCELLED` · 실행 `CANCELLED`로 끝낸다(AI 를 부르지 않는다)."""
        run = self.runs.get(run_id)
        if run is None or (user_id is not None and run.user_id != user_id):
            raise ApprovalError(404, "NOT_FOUND")
        with self._lock:
            self.expire_due()
            if run.status == "WAITING_APPROVAL" and run.pending is not None:
                self._close(
                    run, self.conversations[run.conversation_id], "CANCELLED", by_button=True
                )
        return run

    def _decide(
        self,
        run_id: str,
        action_id: str,
        args_hash: str,
        user_id: str | None,
        outcome: str,
    ) -> tuple[Run, Conversation, PendingApproval | None]:
        """승인 · 거절 요청의 '대기 확인 → 결정'을 한 잠금 안에서 한다(원자적 선점, 12-3 '상태 전이').

        - 이미 결정된 `action_id`의 요청은 지금 대기와 상관없이 이전 결과만 돌려준다(`pending`은 None). 만료 · 취소로 끝난 행동은 409.
        - 새 전이는 요청의 `action_id`가 지금 대기 행동이고 해시가 맞을 때만 한다. 거절이면 여기서 끝내고, 승인이면 대기를 비우고
          결정 · 해시를 보관하고 다시 평가 하루 횟수를 예약한 뒤 보관한 대기 값을 돌려준다(실행은 잠금 밖에서 한다).
        """
        run = self.runs.get(run_id)
        if run is None or (user_id is not None and run.user_id != user_id):
            raise ApprovalError(404, "NOT_FOUND")  # 없는 · 남의 실행은 같은 404
        with self._lock:
            self.expire_due()
            conv = self.conversations[run.conversation_id]
            first = run.decided.get(action_id)
            if first == "EXPIRED":
                raise ApprovalError(409, "AGENT_EXPIRED")
            if first == "CANCELLED":
                # 취소가 확정된 뒤 온 승인은 실행하지 않는다
                raise ApprovalError(409, "AGENT_CANCELLED")
            if first is not None:
                return run, conv, None
            if run.status == "RUNNING":
                raise ApprovalError(409, "AGENT_RUN_IN_PROGRESS")
            pend = run.pending
            if pend is None or pend.action["action_id"] != action_id:
                raise ApprovalError(404, "NOT_FOUND")
            if args_hash != pend.args_hash:
                # 그사이 카드가 바뀜. 실행하지 않고 대기는 그대로
                raise ApprovalError(409, "APPROVAL_HASH_MISMATCH")
            if outcome == "REJECTED":
                self._close(run, conv, "REJECTED")
                return run, conv, None
            # 승인이 대기 상태를 선점했다. 이제 취소 · 만료가 와도 이긴 쪽은 승인이다
            run.pending = None
            run.decided[action_id] = "APPROVED"
            run.approved_hash[action_id] = pend.args_hash
            run.status = "RUNNING"
            if pend.action["type"] == "REASSESS":
                # 날짜별 횟수는 결정과 같은 선점에서 예약한다(뒤 검사에서 실패해도 차감, 같은 승인 재요청은 다시 세지 않음)
                if run.reassess_count >= 1:
                    run.reassess_slot = "PER_TURN"
                elif self.reassess_today.get(run.user_id, 0) >= REASSESS_PER_DAY:
                    run.reassess_slot = "DAILY_LIMIT"
                else:
                    run.reassess_count += 1
                    self.reassess_today[run.user_id] = self.reassess_today.get(run.user_id, 0) + 1
                    run.reassess_slot = "RESERVED"
            return run, conv, pend

    def settle(self, run: Run, decision: str) -> Run:
        """평가 · 시험 편의: 승인 대기 중인 동안 사용자의 결정(`APPROVE` · `REJECT` · `EXPIRE` · `NEW_MESSAGE`)을 따라 진행한다.

        `NEW_MESSAGE`는 대기를 그대로 두고 멈춘다(다음 `send_message`가 취소한다). 승인 뒤 다시 대기(저장 → 다시 평가)로
        멈추면 같은 결정을 되풀이한다.
        """
        if decision not in ("APPROVE", "REJECT", "EXPIRE", "NEW_MESSAGE"):
            raise ValueError(f"모르는 결정: {decision}")
        while run.status == "WAITING_APPROVAL" and run.pending is not None:
            pend = run.pending
            if decision == "APPROVE":
                self.approve(run.run_id, pend.action["action_id"], pend.args_hash)
            elif decision == "REJECT":
                self.reject(run.run_id, pend.action["action_id"], pend.args_hash)
            elif decision == "EXPIRE":
                advance = getattr(self.clock, "advance", None)
                if not callable(advance):
                    raise RuntimeError("EXPIRE 는 시계를 흘릴 수 있어야 한다")
                advance(APPROVAL_WAIT_MS / 1000 + 1)
                self.expired_seconds += APPROVAL_WAIT_MS / 1000 + 1
                self.expire_due()
            else:
                break
        return run

    def _close(
        self, run: Run, conv: Conversation, outcome: str, *, by_button: bool = False
    ) -> None:
        """승인 대기를 거절 · 만료 · 취소로 끝낸다: 행동 `REJECTED` · `EXPIRED` · `CANCELLED`, 이미 낸 문장 뒤에 고정 문장(7-10)을 덧붙여 저장.

        AI 를 부르지 않고 판단 · 근거는 지우지 않는다. 실행은 거절이면 `DONE`, 만료면 `EXPIRED`, 취소면 `CANCELLED`.
        """
        pend = run.pending
        assert pend is not None
        run.pending = None
        run.decided[pend.action["action_id"]] = outcome
        action = pend.action
        run.actions.append(
            ActionRecord(
                copy.deepcopy(action),
                {
                    "action_id": action["action_id"],
                    "type": action["type"],
                    "status": outcome,
                    "data": None,
                    "error": None,
                    "executed_args_hash": None,
                },
                0,
                False,
                decision=outcome,
            )
        )
        final = copy.deepcopy(pend.final)
        msg = final["message"]
        msg["claims"].append(
            {
                "kind": "SYSTEM",
                "assessment_id": None,
                "text": str(
                    CANCEL_BUTTON_TEXT if by_button else CLOSING_TEXT[(outcome, action["type"])]
                ),
                "evidence_ids": [],
                "source_ids": [],
                "action_id": action["action_id"],
            }
        )
        msg["text"] = "\n".join(c["text"] for c in msg["claims"])
        self._store_final(run, conv, final, status=RUN_STATUS_AFTER[outcome])
        run.closed_message = copy.deepcopy(msg)

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
            # 이어 부를 때도 지금의 기억 설정을 다시 싣는다(번호가 바뀌었으면 AI 가 그 실행의 저장 후보를 버린다, 6-14)
            "memory": self._memory(conv),
        }

    def _memory(self, conv: Conversation) -> dict[str, Any]:
        """요청의 `memory`(5-3): 설정이 꺼졌거나 '기억 없이' 대화면 `enabled:false` · `profile:null`, 아니면 지금 프로필."""
        s = self.memory_setting(conv.user_id)
        if conv.no_memory or not s["enabled"]:
            return {"enabled": False, "memory_version": s["version"], "profile": None}
        return {
            "enabled": True,
            "memory_version": s["version"],
            "profile": self.profile_items(conv.user_id),
        }

    def _call(self, run: Run, req: dict[str, Any]) -> Call:
        n = len(run.calls) + 1
        call = Call(n, f"req-{run.run_id}-{n}", copy.deepcopy(req))
        run.calls.append(call)
        t0 = time.monotonic()  # 호출 직전부터 잰다(서비스 계층의 처리 시간 포함)
        try:
            out = self.app.turn(
                json.dumps(req, ensure_ascii=False).encode("utf-8"), request_id=call.request_id
            )
            call.http_status = out.status
            if out.lines is None:
                # 스트림 전 오류(HTTP 오류 본문): 실행 오류 코드 규칙(5-9)대로 실패로 끝낸다. 이어 부르기의 409 도
                # 실행 FAILED 다(L1-41). 받은 문장으로 PARTIAL 을 남기는 것은 스트림을 받다가 끊긴 경우뿐이다(L2-45)
                call.error_code = ((out.error or {}).get("error") or {}).get("code") or "UNKNOWN"
                return call
            self._check_stream(run, call, out.lines, t0)
            return call
        finally:
            call.end_ms = int((time.monotonic() - t0) * 1000)

    def _check_stream(self, run: Run, call: Call, lines: Iterable[str], t0: float) -> None:
        """스트림 검사(12-3) · 실행 이벤트 저장(L2-46). 줄을 받는 즉시 검사하고 저장한다(실행 중 커서 조회가 받은 문장을 본다).

        줄마다 모양 · `run_id` · 1부터 연속인 `seq`를 보고, 진행 줄 · 문장 줄은 받은 순서대로 실행 이벤트로 저장한다.
        문장 줄은 번호가 실행 전체에서 이어져야 하고, 끝 줄 `message.claims`의 앞부분은 이 실행에서 받은 문장과 글자 · 종류 ·
        근거까지 같아야 한다(L2-44). 어기거나 끝 줄 없이 끝나거나 받는 중에 전송 오류가 나면 `call.error_code`를 남기고
        final 을 쓰지 않는다(행동 실행 0번). 그때 받은 문장이 있으면 그 문장으로 `PARTIAL`을 남긴다(`partial_from_claims`, L2-45).
        검사를 멈추면 응답을 닫는다(동시 실행 자리를 돌려준다).
        """
        terminal: dict[str, Any] | None = None
        it = iter(lines)
        seq = 0
        try:
            while True:
                try:
                    line = next(it)
                except StopIteration:
                    break
                except TimeoutError:
                    # 받는 중 시간 초과(L2-15): 받은 문장이 있으면 PARTIAL, 없으면 AGENT_TIMEOUT
                    return self._reject(run, call, "AGENT_TIMEOUT")
                except (ConnectionError, OSError):
                    # 받는 중 연결 끊김 같은 전송 오류(L2-15). 그 밖의 예외는 버그이므로 숨기지 않고 올린다
                    return self._reject(run, call, "STREAM_CUT")
                seq += 1
                if terminal is not None:
                    return self._reject(
                        run, call, "STREAM_INVALID"
                    )  # 끝 줄 뒤 이벤트 · 끝 줄 두 번
                obj = self._line(run, line, seq)
                if obj is None:
                    return self._reject(run, call, "STREAM_INVALID")
                call.lines.append(obj)
                if obj["event"] in ("final", "error"):
                    terminal = obj
                    continue
                if obj["event"] == "claim":
                    if not self._claim_ok(run, obj["data"]):
                        return self._reject(run, call, "CLAIM_INVALID")
                    call.claims.append(obj["data"])
                    if call.first_claim_ms is None:
                        call.first_claim_ms = int((time.monotonic() - t0) * 1000)
                run.events.append(
                    {
                        "event_seq": len(run.events) + 1,
                        "call": call.number,
                        "event": obj["event"],
                        "data": obj["data"],
                    }
                )
        finally:
            close = getattr(lines, "close", None)
            if callable(close):
                close()
        if terminal is None:
            return self._reject(run, call, "STREAM_INVALID")  # 끝 줄 없는 EOF
        last = terminal
        diag = last["data"].get("diagnostics")
        call.diagnostics = diag if self._diagnostics_ok(diag, run, call) else None
        if diag is not None and call.diagnostics is None:
            self.log.append(
                {"event": "DIAGNOSTICS_INVALID", "run_id": run.run_id, "call": call.number}
            )
        if last["event"] == "error":
            # error 끝 줄이어도 받은 문장이 있으면 그 문장으로 PARTIAL(L2-45)
            return self._reject(
                run, call, (last["data"].get("error") or {}).get("code") or "UNKNOWN"
            )
        try:
            FinalData.model_validate(last["data"])
        except ValidationError:
            return self._reject(run, call, "FINAL_INVALID")
        final = last["data"]
        got = [d["claim"] for d in run.claim_lines]
        if ((final.get("message") or {}).get("claims") or [])[: len(got)] != got:
            # 끝 줄 앞부분이 받은 문장과 다르다: 계약 위반으로 남기고 받은 문장만 PARTIAL 로(행동 실행 0번, L2-44)
            self.log.append(
                {"event": "CLAIM_PREFIX_MISMATCH", "run_id": run.run_id, "call": call.number}
            )
            return self._reject(run, call, "CLAIM_PREFIX_MISMATCH")
        if final["status"] == "DONE" and not final["message"]["claims"]:
            return self._reject(run, call, "FINAL_INVALID")  # 빈 DONE
        for a in final["actions"] if final["status"] in ("NEEDS_ACTION", "DONE") else []:
            if FIXED_AUTONOMY.get(a["type"]) != a["autonomy"]:
                # 등록되지 않은 행동 · 자율 등급 위조
                return self._reject(run, call, "ACTION_NOT_ALLOWED")
            if a["autonomy"] == "ASK" and a["args_hash"] != action_args_hash(a["type"], a["args"]):
                # BE 도 같은 규칙으로 해시를 다시 계산해 맞는지 본다(5-8)
                return self._reject(run, call, "FINAL_INVALID")
            if a.get("preapproved") is not None and a["type"] != "SAVE_GOAL":
                # 선승인은 고른 목표 제안을 저장하는 SAVE_GOAL 만
                return self._reject(run, call, "ACTION_NOT_ALLOWED")
        call.final = final

    @staticmethod
    def _line(run: Run, line: str, seq: int) -> dict[str, Any] | None:
        """한 줄 검사: LF 로 끝남 · 2 MiB 이하 · JSON · 줄 모양 · 이 실행의 `run_id` · 1부터 연속인 `seq`."""
        if not line.endswith("\n") or len(line.encode("utf-8")) > MAX_LINE_BYTES:
            return None
        try:
            obj = json.loads(line)
            StreamLine.model_validate(obj)
        except (ValueError, ValidationError):
            return None
        if obj["run_id"] != run.run_id or obj["seq"] != seq:
            return None
        return obj

    @staticmethod
    def _claim_ok(run: Run, data: Any) -> bool:
        """문장 줄 검사: 모양(5-4)과 번호(이 실행에서 받은 문장 수와 같아야 한다 — 빈 번호 · 되풀이 없음)."""
        try:
            ClaimLineData.model_validate(data)
        except ValidationError:
            return False
        return data["index"] == len(run.claim_lines)

    @staticmethod
    def _reject(run: Run, call: Call, code: str) -> None:
        """호출 결과를 쓰지 않는다(행동 실행 0번). 이 실행에서 받은 문장이 있으면 그 문장으로 PARTIAL 을 남긴다(L2-45)."""
        call.error_code = code
        call.partial_from_claims = bool(run.claim_lines)

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

    def _store_final(
        self, run: Run, conv: Conversation, final: dict[str, Any], *, status: str | None = None
    ) -> None:
        """종결 응답 저장(12-3 '판단 저장'): 메시지 추가 · 목표 제안 카드 · 요약 · 자동 목표 확인 결과.

        `status`는 AI 가 아니라 BE 가 끝낸 실행(거절 · 만료 · 취소)의 끝 상태다. 연습 이동은 사용자가 버튼을 누를 때만 남긴다(`click_practice`).
        """
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
                status=status or final["status"],
                claims=copy.deepcopy(msg["claims"]),
                cards=copy.deepcopy(msg["cards"]) + run.memory_cards,
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
        self._store_summary(run, conv, final.get("history_summary"))
        if (
            status is None
        ):  # BE 가 끝낸 실행(거절 · 만료 · 취소 · 받은 문장 보존)은 AI 의 저장 후보가 아니다
            conv.messages[-1].cards += self._store_profile(run, conv, final.get("profile_updates"))
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
        run.status = status or final["status"]

    def _store_summary(self, run: Run, conv: Conversation, items: Any) -> None:
        """대화 요약 조건부 갱신(6-14 · L2-49): 이 실행이 보낸 앞 요약의 판 번호가 지금과 같을 때만 바꾸고, 덮은 메시지 수를
        보낸 밀린 메시지만큼 늘린다. null 이면 바꾸지 않는다(다음 턴에 같은 메시지를 다시 보낸다)."""
        overflow = (run.history or {}).get("overflow")
        if items is None or overflow is None:
            return  # 빈 목록은 유효한 갱신이고, null 만 '못 만듦'이다(6-14)
        if overflow["prev_summary_version"] != conv.summary_version:
            self.log.append({"event": "SUMMARY_VERSION_CONFLICT", "run_id": run.run_id})
            return
        covered = conv.messages[
            conv.summary_covered : conv.summary_covered + len(overflow["messages"])
        ]
        conv.summary = copy.deepcopy(items)
        conv.summary_version += 1
        conv.summary_covered += len(overflow["messages"])
        # 굴리는 요약이므로 앞 요약의 원천도 그대로 이어진다
        conv.summary_sources |= {s for m in covered for s in m.source_ids}

    def _store_profile(self, run: Run, conv: Conversation, updates: Any) -> list[dict[str, Any]]:
        """코칭 프로필 저장(6-14 · L2-47 · L2-48): 사용자 말로 시작한 실행 · 설정 켜짐 · '기억 없이'가 아님 · 지금 기억 번호가
        실행을 시작할 때와 같을 때만, AI 와 같은 검사(①~⑥)를 다시 해 통과한 것만 저장한다(`key`마다 하나, 새 값이 덮음).
        저장에 성공한 값으로만 `MEMORY_SAVED` 카드를 만들어 돌려준다(호출한 쪽이 그 답 메시지에 붙인다). 같은 key 에 같은 값이
        이미 있으면 다시 저장하지 않고 카드도 내지 않는다(승인 대기 응답에서 저장한 뒤 끝 응답에 다시 실려도 한 번)"""
        if not updates:
            return []
        s = self.memory_setting(conv.user_id)
        text = run.original.get("user_message")
        if (
            run.trigger != "USER_MESSAGE"
            or not text
            or conv.no_memory
            or not s["enabled"]
            or s["version"] != run.memory_version
        ):
            self.log.append({"event": "PROFILE_NOT_SAVED", "run_id": run.run_id})
            return []
        kept, _dropped = memory_rules.check_profile_updates(
            updates, user_message=text, cfg=DEFAULT_CONFIG
        )
        items = self.profiles.setdefault(conv.user_id, {})
        out: list[dict[str, Any]] = []
        for u in kept:
            if u["key"] not in items and len(items) >= DEFAULT_CONFIG.max_profile_items:
                continue
            if (
                u["key"] in items
                and items[u["key"]]["value"] == u["value"]
                and u["key"] in run.memory_saved
            ):
                continue
            item = {"item_id": self._next("mem"), "key": u["key"], "value": u["value"]}
            items[u["key"]] = item
            run.memory_saved.add(u["key"])
            out.append({"type": "MEMORY_SAVED", **copy.deepcopy(item)})
        return out

    def click_practice(self, run: Run) -> dict[str, Any]:
        """사용자가 [연습 준비로] 버튼을 누름: FE 가 화면을 옮기고 BE 는 이동 기록만 남긴다. BE 가 행동으로 실행하지 않는다(5-8)."""
        conv = self.conversations[run.conversation_id]
        card = next(
            (
                c
                for m in conv.messages
                if m.run_id == run.run_id
                for c in m.cards
                if c.get("type") == "START_PRACTICE"
            ),
            None,
        )
        if card is None:
            raise LookupError("NOT_FOUND")
        move = {"run_id": run.run_id, "pitch_id": card["pitch_id"]}
        conv.practice_moves.append(move)
        return copy.deepcopy(move)

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
        """행동 하나(12-3 '행동 실행'). 같은 키는 처음 결과를 다시 쓰고, 같은 키에 다른 인자는 거절한다.

        `ASK` 행동은 승인으로(승인 대기에서 저장한 `args` 그대로) 또는 선승인(고른 안의 해시를 다시 계산해 모두 같을 때만)으로만 온다.
        선승인이 맞지 않으면 실행하지 않고 `FAILED`로 이어 부른다.
        """
        t0 = self.clock.monotonic()
        key = (run.user_id, action["type"], action["idempotency_key"])
        ahash = _hash(action["args"])
        executed = False
        decision: str | None = None
        approved_hash: str | None = None
        blocked: str | None = None
        if action["autonomy"] == "ASK":
            if action.get("preapproved") is not None:
                # 선승인의 기준은 BE 가 저장된 제안에서 다시 계산한 해시다(행동에 실린 `args_hash`가 아니다)
                decision, approved_hash = "PREAPPROVED", self._preapproved_hash(run, conv, action)
            elif run.decided.get(action["action_id"]) == "APPROVED":
                decision = "APPROVED"
                approved_hash = run.approved_hash.get(action["action_id"])
            else:
                blocked = "NO_DECISION"  # 실제 확정된 결정 없이는 실행하지 않는다
        if blocked is not None:
            result = self._failed(action, "APPROVAL_REQUIRED")
        elif decision == "PREAPPROVED" and not self._preapproval_ok(run, conv, action):
            result = self._failed(action, "PREAPPROVAL_MISMATCH")
            decision = None
        elif decision == "APPROVED" and (
            approved_hash is None
            or action_args_hash(action["type"], action["args"]) != approved_hash
        ):
            # 실행 직전 실제 인자가 승인 때 보관한 해시와 다르다(갈고리 · 대기 중 변조): 실행하지 않는다
            result = self._failed(action, "APPROVAL_ARGS_CHANGED")
            result["executed_args_hash"] = approved_hash
        elif key in self.action_results:
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
            copy.deepcopy(action),
            result,
            elapsed,
            executed,
            decision=decision,
            approved_hash=approved_hash,
            blocked=blocked,
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
        assert code in ACTION_ERROR_CODES, code  # 행동 결과 실패 코드는 5-8 닫힌 목록만
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
        # 횟수는 승인 결정 때 예약해 두었다(`_decide`). 여기서는 예약 결과만 본다
        slot, run.reassess_slot = run.reassess_slot, None
        if slot == "PER_TURN":
            return self._failed(action, "REASSESS_PER_TURN")
        if slot == "DAILY_LIMIT":
            return self._failed(action, "REASSESS_DAILY_LIMIT")
        if slot != "RESERVED":
            return self._failed(action, "APPROVAL_REQUIRED")
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

    def _preapproval_ok(self, run: Run, conv: Conversation, action: dict[str, Any]) -> bool:
        """선승인 `SAVE_GOAL`(5-8): 실제 `args.goal`에서 내용 해시를 다시 계산해 저장된 제안의 `content_hash` · 제안 id · 고른 안과 모두 같을 때만."""
        pre = action.get("preapproved") or {}
        choice = run.original.get("choice") or {}
        card = conv.proposals.get(pre.get("proposal_id", ""))
        opt = next(
            (
                o
                for o in (card or {}).get("options", [])
                if o.get("label") == choice.get("option") and o.get("goal") is not None
            ),
            None,
        )
        if action["type"] != "SAVE_GOAL" or card is None or opt is None:
            return False
        if pre.get("proposal_id") != choice.get("proposal_id"):
            return False
        actual = content_hash((action.get("args") or {}).get("goal"))
        return (
            actual
            == pre.get("content_hash")
            == opt.get("content_hash")
            == content_hash(opt["goal"])
        )

    def _preapproved_hash(self, run: Run, conv: Conversation, action: dict[str, Any]) -> str | None:
        """선승인 `SAVE_GOAL`의 기준 해시: 저장된 제안의 고른 안 목표로 BE 가 다시 계산한 `args` 해시. 안을 찾지 못하면 None."""
        pre = action.get("preapproved") or {}
        choice = run.original.get("choice") or {}
        card = conv.proposals.get(pre.get("proposal_id", ""))
        opt = next(
            (
                o
                for o in (card or {}).get("options", [])
                if o.get("label") == choice.get("option") and o.get("goal") is not None
            ),
            None,
        )
        if opt is None or action["type"] != "SAVE_GOAL":
            return None
        args = {**(action.get("args") or {}), "goal": copy.deepcopy(opt["goal"])}
        return action_args_hash(action["type"], args)

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
