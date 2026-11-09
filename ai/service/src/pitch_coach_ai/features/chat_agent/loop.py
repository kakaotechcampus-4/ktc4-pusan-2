"""대화 에이전트 루프 — 계획 → 조사(도구 · 행동) → 답 쓰기 → 답 검사 · 한 번 고치기 (기능 문서 6-1 · 6-2 · 6-3 · 6-9 · 8-3).

- 누가 쓰나: turn.py 가 요청 검사 · 서명 검사를 마친 뒤 `run()`을 부르고, 진행 이벤트(`emit`)를 스트림 줄로 내보낸다.
- 처리 순서
  1. 처음(USER_MESSAGE)이면 계획 호출(구조화)로 할 일 목록 · 판단 초안을 받는다. 무효면 형식 고치기 1회(조사 몫), 그래도 무효면 기본 계획.
     판단은 근거를 읽을 때까지 내보내지 않는다(실행 상태 안에만 둔다).
  2. 조사: 먼저 조사 흐름(`_prefetch_calls`) — 판단 초안이 집은 Take · 장 · 지표 · 차원을 코드가 읽는다(없는 Take 는 FETCH_TAKE,
     말한 Take 가 없으면 읽지 않음, 실행에 최대 4개, 기준선은 끔). 같은 실행 길(캐시 · 한도 · 근거 · 진행 줄)이고 반복 판정에는 세지 않는다.
     그 뒤 모델이 도구를 고르면 실행해 결과를 돌려주고, 행동을 고르면 그 지점에서 멈추고 NEEDS_ACTION 을 낸다(한 응답에서 첫 행동까지만).
     `ASK` 행동(저장 · 다시 평가)이면 멈추기 전에 turn.py 가 그때까지 확인한 것으로 답 문장을 먼저 쓰고 검사해 확인 카드와 함께 낸다(6-1 4번).
     그 문장 · 판단 · 자료 카드는 `LoopState.emitted` 등으로 서명 상태에 실려 이어 부른 뒤 최종 답의 앞부분에 그대로 싣는다(5-5).
     한도(8-2: 모델 호출 18번 중 조사는 16번째 시도까지 · 도구 12번 · 시간 · 토큰)에 닿거나, 같은 (도구, 인자, 범위, 자료 판)을 새 근거 없이
     연속 2회, 최근 네 요청이 A-B-A-B로 번갈아 되풀이(교대 반복, 캐시 적중 포함), 무효 도구 응답이 연속 2회면 조사를 끝낸다.
     교대 반복은 요청마다 판정해 그 응답의 남은 요청은 실행하지 않고, 새 근거가 생긴 요청에서 기록을 비운다. 이미 한도로 정해진 끝난 이유는 덮지 않는다.
     끝난 까닭은 `stop_reason`(TIME · MODEL_CALLS · TOOL_CALLS · TOKENS · 반복 계열)에 남기고 turn.py 가 진단 `end.reason`으로 옮긴다.
  2-1. 모델 한 번(`_call`)마다 입력 · 출력 예약을 응답 뒤 공급자 실측으로 정산한다(budget.py `settle_input` · `settle_output`).
  3. 답 쓰기(구조화, `answer_phase`). 도구 결과와 함께 이번 턴 근거 값 표(`evidence_table`)와 질문별 근거 목록(`questions`)을 준다. 응답 조각은 answer.py 가 받아 판단 확정(코드 판정이 모델 판정을 덮음) → 판단 첫 문장 ·
     결론 · 성공 문장은 코드 템플릿 → 답 검사(6-9)를 하고 통과한 문장부터 문장 줄로 낸다. 걸린 문장은 까닭을 알려 한 번
     고치게 하고(`fix_call`) 다시 검사한다. 그래도 걸린 문장은 지운다.
  4. 최종 출력(`final.data`)을 조립한다. `message.text` 는 검사를 통과한 문장을 LF 로 이은 값이다.
- 할 일 단계 = 실제 실행(progress.py, 설정 `plan_progress`): 계획 단계마다 도구 힌트(`tool`)를 받아 단계 메타(`plan_meta`)를 두고,
  도구 하나가 끝날 때마다(캐시 적중 포함) 그 실행을 단계에 이어 `목적: 실행 → 결과`와 상태를 다시 그려 plan 줄로 낸다(`_emit_plan_display`).
  이 표시 갱신은 계획 수정 한도(PLAN_EDITS)를 쓰지 않는다 — 한도는 모델의 `update_plan`만 센다. 끝 처리는 turn.py `_finish`.
- 상황 신호 · 결정 로그(설정 `decision_log` · `research_signals`): 도구 결과에서 코드가 찾은 신호(비교 불가 · 기본 자료에 없음 · 리뷰 없음 ·
  기록 없음 · 도구 실패)를 단계에 보이고(`FACT_SHOWN:CODE`) 대응을 기다리는 신호(`signals_open`)로 둔다. 그 신호를 본 모델의 다음 선택이
  `update_plan`이면 `PLAN_EDIT`, 다른 기록 도구 · 다른 대상이면 `ALT_TOOL`, 행동이면 `ACTION`, 조사가 끝날 때까지 없으면 `NONE`이다.
  조사가 한도 · 반복으로 멈추면 `BUDGET_LOW` · `STOP_PARTIAL:CODE`. 결정 로그는 서비스 로그(`agent_decision`)에만 남는다(11-5).
- 이어 부르기(ACTION_RESULT): 서명 상태의 루프 데이터(멈추기 전에 낸 문장 포함)를 되살리고, 지워진 원천(`removed_sources`)에 기댄 근거 · 캐시 · 도구 결과를 모델 호출 전에 버린 뒤(이미 낸 문장이 기댄 원천이면 재개를 멈춘다 — `drop_removed`),
  행동 결과를 검사해 반영하고 2부터 잇는다.
- 자동 목표 확인(TAKE_REVIEWED): 모델 없이 코드가 목표를 판정하고 결과 문장 · 카드를 만든다(meta.model null).
- 고칠 때: 한도 숫자는 config.py, 순서 규칙은 budget.py. 사용자에게 보이는 문구는 templates.py.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from . import actions as act
from . import checks as chk
from . import claims as clm
from . import compose, memory, progress, prompts, templates
from .budget import Budget, Counters, LimitHit, estimate_tokens, limit_end_reason
from .config import ChatAgentConfig
from .corpus import sentence_spans
from .diagnostics import DiagRecorder, code_enum, finish_code
from .evidence import EvidenceRegistry
from .goals import judge_goal
from .llm_types import (
    LlmBudgetStop,
    LlmError,
    LlmRefused,
    LlmRequest,
    LlmResult,
    TextSink,
    ToolCall,
    ToolLlm,
    Usage,
)
from .runctx import RunCtx
from .schemas import (
    Assessment,
    Claim,
    GuideCard,
    TraceItem,
    TurnRequest,
)
from .tools import (
    P0_TOOLS,
    ToolResult,
    invalid_result,
    model_tool_definitions,
    result_bytes,
    run_tool,
    validate_args,
)
from .tools.impl import embed_planned_queries
from .values import METRIC_AREA
from .version import FEATURE_VERSION, SCHEMA_VERSION, TOOL_SCHEMA_VERSION

#: 기본 계획(계획이 무효일 때). `tool`은 단계의 도구 힌트(progress.py)이고 응답 `PlanStep`에는 싣지 않는다
DEFAULT_PLAN = [
    {"step": "기록 확인", "status": "DOING", "note": None, "tool": None},
    {"step": "답 쓰기", "status": "TODO", "note": None, "tool": "answer"},
]


#: 계획 route 값(6-1). 모르는 값은 RESEARCH 로 본다
PLAN_ROUTES = ("RESEARCH", "ANSWER_DIRECT", "SMALL_TALK", "OUT_OF_SCOPE")
#: 가벼운 말 종류(P-0008 5-1)
TALK_KINDS = ("GREETING", "THANKS", "CAPABILITY", "PRACTICE_INTENT", "FAREWELL")


def _greeting_only(msg: str, cfg: Any) -> bool:
    """인사 낱말(`small_talk_greeting_words`)을 빼고 나면 문장 부호 · 공백만 남는 말인가("안녕하세요?")."""
    rest = msg
    for w in sorted(cfg.small_talk_greeting_words, key=len, reverse=True):
        rest = rest.replace(w, "")
    return not any(ch.isalnum() for ch in rest)


def _route_of(parsed: dict[str, Any]) -> str:
    route = parsed.get("route")
    return route if route in PLAN_ROUTES else "RESEARCH"


@dataclass
class LoopState:
    """서명 상태(`payload.loop`)로 이어지는 루프 데이터."""

    messages: list[dict[str, Any]] = field(default_factory=list)
    processed_calls: list[str] = field(default_factory=list)
    plan: list[dict[str, Any]] = field(default_factory=list)
    plan_set: bool = False
    trace: list[dict[str, Any]] = field(default_factory=list)
    drafts: list[dict[str, Any]] = field(default_factory=list)
    #: 같은 실행에서 이미 내보낸 판단(그 근거 사본과 함께 최종 답에 남김, 5-5)
    exported: list[dict[str, Any]] = field(default_factory=list)
    #: 승인 대기로 멈추기 전에 이미 낸 문장(`Claim`)과 그 자료 카드 사본. 이어 부른 뒤 최종 답의 앞부분에 그대로 싣는다(5-5 · 5-6)
    emitted: list[dict[str, Any]] = field(default_factory=list)
    emitted_sources: list[dict[str, Any]] = field(default_factory=list)
    pending_action: dict[str, Any] | None = None
    #: 이 실행에서 처리한 행동(id · type · 결과 · 성공 문장)
    executed: list[dict[str, Any]] = field(default_factory=list)
    cards: list[dict[str, Any]] = field(default_factory=list)
    action_seq: int = 0
    repeat_key: str | None = None
    repeat_count: int = 0
    #: 최근 요청 열쇠 네 개(같은 열쇠와 같은 열쇠 규칙, 캐시 적중 포함). 교대 반복(A-B-A-B)을 가린다(5-7)
    recent_keys: list[str] = field(default_factory=list)
    invalid_streak: int = 0
    failed_action_key: str | None = None
    failed_action_count: int = 0
    #: 이 실행이 읽은 원천(피치 · Take) — BE가 재개 때 다시 확인한다(5-6)
    read_sources: list[str] = field(default_factory=list)
    #: 도구 결과 메시지가 기댄 원천(tool_call_id → 원천 id 목록). 원천이 지워지면 그 메시지 내용을 버린다
    message_deps: dict[str, list[str]] = field(default_factory=dict)
    tool_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    registry: dict[str, Any] = field(default_factory=dict)
    counters: dict[str, int] = field(default_factory=dict)
    usage_in: int = 0
    usage_out: int = 0
    usage_missing: bool = False
    #: 공급자가 준 입력 · 출력 토큰 실측값을 하나라도 받았는가(하나도 없으면 그쪽 usage 토큰은 null, 5-5)
    usage_seen_in: bool = False
    usage_seen_out: bool = False
    #: 다시 평가 결과에서 받은 리뷰 규칙 판(답 meta.criteria_versions 에 싣는다)
    review_versions: dict[str, str] = field(default_factory=dict)
    research_done: bool = False
    #: 크기 · 시간 등으로 조사를 끝낸 까닭(진단 · PARTIAL)
    stop_reason: str | None = None
    corpus_version: str | None = None
    #: 이 실행에서 자료 찾기(`search_guides`)가 한 번 이상 성공했는가(결과 0건 포함). 일반 조언(`GENERAL`)의 조건이다(6-10)
    guide_searched: bool = False
    #: 처음 요청의 기억 번호(6-14). 프로필 · 요약 본문은 담지 않는다(5-6)
    memory_version: int | None = None
    #: 실행 중 기억 번호가 한 번이라도 바뀌었는가. 바뀌면 그 실행의 프로필 저장 후보를 내지 않는다
    memory_changed: bool = False
    #: 범위를 바꾼(FETCH_PITCH 성공) 뒤 새 범위 피치의 Take 를 아직 하나도 읽지 않았다. 이 동안에는 범위를 다시 바꾸지
    #: 않는다(6-5: 여러 피치 질문에서 범위만 오가다 한도에 걸리지 않게, 4회차). 그 피치의 Take 를 읽으면(다시 읽기 ·
    #: 캐시 적중 · FETCH_TAKE 성공 포함) 풀린다
    scope_switch_unread: bool = False
    #: 이 실행에서 범위 피치를 다른 피치로 바꾼 적이 있는가(`RunCtx.scope_switched`를 이어 부르기 사이에 잇는다)
    scope_switched: bool = False
    #: 코드가 먼저 읽은 요청(조사 흐름, `_prefetch_calls`)의 열쇠. 이어 부른 뒤 같은 것을 다시 내지 않는다
    prefetched: list[str] = field(default_factory=list)
    #: 이름으로 찾은(`list_pitches` query) 피치가 둘 이상이었다. 범위를 정하지 않고 답하면 어느 발표인지 되묻는다(L2-21)
    ambiguous_name: bool = False
    #: 이름으로 찾은(`list_pitches` query) 피치가 하나도 없었다. 범위를 정하지 않고 답하면 찾을 수 없다고 말한다(L2-21 0개, 8회차 M4)
    name_not_found: bool = False
    #: 이 실행에서 `compare_takes`가 비교할 수 없다고 한 것(앞 · 뒤 Take 번호 · 지표 · 장 · 까닭). 답에 까닭 문장을 코드가 붙인다(7-10)
    not_comparable: list[dict[str, Any]] = field(default_factory=list)
    #: 이 실행에서 질문 임베딩을 이미 불렀는가(실행당 한 번, 6-10). 옛 상태에는 없어 False 로 읽는다
    embed_used: bool = False
    #: 계획이 쓴 자료 검색어(조사 첫머리에 코드가 먼저 찾는다, 6-10). 이어 부른 뒤에도 같은 목록을 쓴다. 옛 상태에는 없어 빈 목록
    guide_queries: list[str] = field(default_factory=list)
    #: 계획을 확정할 때의 판단 초안 사본. 답 쓰기가 `drafts`를 바꿔도 이 사본은 그대로다(기록 안전장치의 대상, 6-1). 옛 상태에는 없어 빈 목록
    plan_drafts: list[dict[str, Any]] = field(default_factory=list)
    #: 할 일 단계 메타(progress.py, `plan`과 같은 순서): 목적 · 도구 힌트 · 실행 · 결과 · 근거 · 도구 기록 순번. 옛 상태에는 없어 빈 목록
    plan_meta: list[dict[str, Any]] = field(default_factory=list)
    #: 결정 로그 줄의 실행 안 순번(마지막으로 쓴 번호)
    decision_seq: int = 0
    #: 대응을 기다리는 상황 신호(progress.open_signal). 조사가 끝날 때 남은 것은 대응 없음(NONE)으로 닫는다
    signals_open: list[dict[str, Any]] = field(default_factory=list)

    def dump(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @classmethod
    def load(cls, d: dict[str, Any]) -> LoopState:
        s = cls()
        for k in cls.__dataclass_fields__:
            if k in d:
                setattr(s, k, d[k])
        return s


@dataclass
class TurnDeps:
    """서비스 계층(연구 모듈은 chat_agent_lab/app.py)이 넘기는 것. 코어는 받은 것만 쓴다."""

    llm: ToolLlm | None
    corpus: Any
    corpus_version: str
    clock_ms: Callable[[], int]
    monotonic: Callable[[], float]
    cfg: ChatAgentConfig
    log: Callable[[dict[str, Any]], None] = lambda _l: None
    #: 질문 임베딩 함수(글, 시간 제한 초 → 벡터, 실패하면 예외). 없으면 키워드 검색만 한다(6-10)
    embed_query: Callable[[Sequence[str], float], Sequence[Sequence[float]]] | None = None


@dataclass
class LoopOutcome:
    """루프의 끝: final 데이터 또는 error 줄."""

    final: dict[str, Any] | None = None
    error: dict[str, str] | None = None
    #: NEEDS_ACTION 이면 서명할 루프 데이터 · 범위 · 판 번호
    loop_state: LoopState | None = None
    scope_pitch_id: str | None = None
    scope_changed: bool = False
    diag_end: tuple[str, str] = ("DONE", "ANSWERED")


class Loop:
    """한 /agent/turn 호출의 루프."""

    def __init__(
        self,
        req: TurnRequest,
        deps: TurnDeps,
        *,
        state: LoopState,
        original_message: str | None,
        scope_pitch_id: str | None,
        counters: Counters,
        diag: DiagRecorder,
        emit: Callable[[str, dict[str, Any]], None],
        corpus_ok: bool,
    ) -> None:
        self.req = req
        self.deps = deps
        self.cfg = deps.cfg
        self.st = state
        self.original = original_message
        #: 계획 단계에서 모델이 고른 범위 밖 이유(route 가 OUT_OF_SCOPE 일 때만, 1-3). 한 호출 안에서만 쓰고 저장하지 않는다
        self.scope_reason: str | None = None
        #: 이 응답의 계획 route(계획을 돌리지 않은 이어 부르기는 None)와 가벼운 말 종류(route 가 SMALL_TALK 일 때만, P-0008 5-1)
        self.route: str | None = None
        self.talk_kind: str | None = None
        self.counters = counters
        self.budget = Budget.start(self.cfg, counters, deps.monotonic)
        self.diag = diag
        self.emit = emit
        registry = (
            EvidenceRegistry.load(req.run.run_id, state.registry)
            if state.registry
            else EvidenceRegistry(req.run.run_id)
        )
        self.ctx = RunCtx.build(
            req,
            cfg=self.cfg,
            registry=registry,
            scope_pitch_id=scope_pitch_id,
            corpus=deps.corpus,
            corpus_version=deps.corpus_version,
            trusted_takes=set(state.read_sources),
        )
        self.ctx.tool_cache = dict(state.tool_cache)
        self.ctx.embed_query = deps.embed_query
        self.ctx.embed_used = state.embed_used
        self.ctx.monotonic = deps.monotonic
        # 임베딩 시간 제한은 조사에 남은 시간(답 몫을 뺀 것) 안으로 줄인다
        self.ctx.research_left = lambda: max(
            0.0, self.budget.left() - self.cfg.reserved_answer_seconds
        )
        if not corpus_ok:
            # 실행 중 자료 판이 바뀌면 검색 캐시만 버리고 지금 판으로 다시 찾는다(5-6 · 6-10)
            self.ctx.tool_cache = {
                k: v
                for k, v in self.ctx.tool_cache.items()
                if json.loads(k).get("tool") != "search_guides"
            }
            # 검색 완료 표시도 함께 버린다: 남겨 두면 같은 검색어가 끝난 것으로 보여 지금 판을 찾지 않는다. 임베딩은 실행당
            # 한 번이라 `embed_used`는 그대로 두고(지금 판은 키워드로 찾는다)
            state.prefetched = [k for k in state.prefetched if not k.startswith('["search_guides"')]
            state.guide_searched = False
        self.ctx.read_sources = set(state.read_sources)
        self.ctx.scope_switched = state.scope_switched
        #: 이어 부르는 사이 결과 판이 바뀐 Take 가 있었는가(옛 근거는 사본으로 낮췄다, 5-5 · L1-32)
        self.record_changed = self._demote_changed_records() if state.registry else False
        self.scope_changed = False
        #: 이미 낸 문장이 지워진 원천에 기댔는가(`drop_removed`가 정한다 — 그러면 재개를 멈춘다)
        self.emitted_removed = False
        self.model_name: str | None = None
        self.model_used = False
        #: 이 호출에서 모델 전송이 한도로 막힌 까닭(진단 끝 사유 TIME · MODEL_CALLS · TOKENS …). 답 단계 실패를 나눌 때 쓴다
        self.limit_end: str | None = None
        #: 답 초안의 코칭 프로필 저장 후보(검사 전, turn.py `_finish`가 종결 응답에서만 검사해 싣는다)
        self.profile_candidates: Any = None
        #: 연결이 끊겼는가(turn.py 가 넣는다). 끊기면 새 모델 · 도구 호출을 하지 않는다(L1-08)
        self.cancelled: Callable[[], bool] = lambda: False
        self._last_model_seq: int | None = None
        #: 문장 줄(`claim`)을 내는 쪽(turn.py 가 넣는다, answer.py `ClaimLines`)
        self.lines: Any = None
        #: 상황 신호를 따라가는가(결정 로그 · 조사 데이터 블록 신호 중 하나라도 켜짐)
        self.tracking = self.cfg.decision_log or self.cfg.research_signals
        memory.sync_version(state, req.memory)

    # ── 모델 호출 공통 ───────────────────────────────────────────────
    def _call(
        self,
        purpose: str,
        messages: list[dict[str, Any]],
        *,
        tools=None,
        schema=None,
        max_attempts: int = 2,
        fix_for: list[int] | None = None,
        sink: TextSink | None = None,
    ) -> LlmResult | LlmError | LimitHit:
        """모델 한 번(내부 재시도 포함). 시도마다 한도를 예약한다. 실패 · 한도는 결과로 돌려준다(예외 없음).

        진단 기록에는 전송 시도마다 MODEL 단계를 남긴다(실패한 앞 시도 포함, 11-5). `fix_for`는 고치기 호출이 고칠
        CHECK 단계의 seq 다.
        """
        kind = "RESEARCH" if purpose == "RESEARCH" else purpose
        est = estimate_tokens(messages, tools)
        max_out = self.cfg.output_tokens_per_call
        hit_holder: list[LimitHit] = []
        reserved = [0]  # 이 호출에서 예약한 전송 시도 수

        def before(_n: int) -> bool:
            if self.cancelled():
                return False  # 연결이 끊기면 재시도도 보내지 않는다(L1-08)
            hit = self.budget.reserve_model(kind, est, max_out)  # type: ignore[arg-type]
            if hit is not None:
                hit_holder.append(hit)
                return False
            reserved[0] += 1
            return True

        llm = self.deps.llm
        if llm is None:
            return LlmError(cause="no_llm")
        if self.cancelled():
            return LlmError(cause="client_disconnected")
        timeout_left = self.budget.call_timeout(kind)  # type: ignore[arg-type]
        # 조사 · 요약 호출의 마감은 지금 + 호출 한도다. 조사의 한도는 이미 답 쓰기 · 고치기 예약(24초)을 뺀
        # min(12, 남은 시간 − 24)초라서, 재시도 조건(대기 + 12초 ≤ 마감 − 지금)이 거의 맞지 않는다.
        # 조사의 max_attempts=2 는 사실상 1회이고 8-3 의 '최대 1회'와 맞다(#886 · #887 리뷰)
        deadline = (
            self.deps.monotonic() + timeout_left
            if purpose in ("RESEARCH", "HISTORY_SUMMARY")
            else self.budget.deadline
        )
        req = LlmRequest(
            purpose=purpose,  # type: ignore[arg-type]
            messages=messages,
            tools=tools,
            response_schema=schema,
            max_output_tokens=max_out,
            deadline=deadline,
            max_attempts=max_attempts,
            text_sink=sink,
        )
        start = self.diag.now_ms()
        try:
            res = llm.invoke(req, before)
        except LlmBudgetStop as e:
            self._count_usage(e.usage)
            # 앞 시도가 실패한 진짜 이유를 남긴다. 한도로 막힌 것은 아래 LIMIT 단계가 따로 기록한다(#887 리뷰)
            codes = e.attempt_errors or [e.code] * len(e.usage)
            self._failed_steps(purpose, llm.model_name, e.usage, codes, start, fix_for)
            if hit_holder:
                self._limit(hit_holder[-1])
                self.limit_end = limit_end_reason(hit_holder[-1].code)
                return hit_holder[-1]
            return e
        except LlmError as e:
            usages = e.usage or [Usage()]  # 보냈지만 사용량을 모르는 시도도 미측정으로 센다(L1-39)
            self._count_usage(usages)
            self._failed_steps(
                purpose, llm.model_name, usages, [e.code] * len(usages), start, fix_for
            )
            return e
        self.model_used = True
        self.model_name = res.model or llm.model_name or self.model_name
        usages = list(res.attempt_usages) or [res.usage]
        self._count_usage(usages)
        # 응답을 받은 시도(마지막)의 입력 · 출력 예약을 실측으로 바꾼다. 실패한 앞 시도 · 실측이 없는 시도는 예약 그대로(8-3)
        if reserved[0]:
            self.budget.settle_input(est, res.usage.input_tokens)
            self.budget.settle_output(max_out, res.usage.output_tokens)
        # 실패한 앞 시도(재시도 전)도 진단 단계로 남긴다
        self._failed_steps(
            purpose, llm.model_name, usages[:-1], list(res.attempt_errors), start, fix_for
        )
        u = res.usage
        detail: dict[str, Any] = {
            "model": res.model,
            "success": True,
            "input_tokens": u.input_tokens,
            "cached_input_tokens": u.cached_input_tokens,
            "output_tokens": u.output_tokens,
            "finish_reason": finish_code(res.finish_reason),
            "tool_call_count": len(res.tool_calls),
        }
        if fix_for:
            detail["fix_for"] = list(fix_for)
        self._last_model_seq = self.diag.step(
            "MODEL", purpose, detail, start_ms=start, ms=max(0, self.diag.now_ms() - start)
        )
        return res

    def _failed_steps(
        self,
        purpose: str,
        model: str | None,
        usages: list[Usage],
        codes: list[str],
        start: int,
        fix_for: list[int] | None,
    ) -> None:
        for i, u in enumerate(usages):
            detail: dict[str, Any] = {
                "model": model,
                "success": False,
                "input_tokens": u.input_tokens,
                "output_tokens": u.output_tokens,
                "finish_reason": None,
                "error_code": code_enum(codes[i] if i < len(codes) else "LLM_ERROR"),
                "tool_call_count": 0,
            }
            if fix_for:
                detail["fix_for"] = list(fix_for)
            self.diag.step(
                "MODEL", purpose, detail, start_ms=start, ms=max(0, self.diag.now_ms() - start)
            )

    def _count_usage(self, usages: list[Usage]) -> None:
        """관측 사용량(5-5): 실측값만 더하고, 입력 · 출력 각각 실측이 하나라도 있었는지와 빠진 시도가 있었는지를 따로 센다."""
        for u in usages:
            if u.input_tokens is None or u.output_tokens is None:
                self.st.usage_missing = True
            if u.input_tokens is not None:
                self.st.usage_seen_in = True
                self.st.usage_in += u.input_tokens
            if u.output_tokens is not None:
                self.st.usage_seen_out = True
                self.st.usage_out += u.output_tokens

    def _limit(self, hit: LimitHit) -> None:
        self.diag.step(
            "LIMIT", hit.code, {"used": hit.used, "limit": hit.limit, "attempted": hit.attempted}
        )

    # ── 모델이 보는 데이터 ───────────────────────────────────────────
    def data_block(self, *, profile: bool = False) -> dict[str, Any]:
        """외부 문자열을 모은 데이터 블록(6-1 · 6-10). 키 · 서명 상태 · 내부 판단 키는 넣지 않는다(L1-22).

        대화 요약 항목은 대화 기록의 일부로 모든 호출에 싣는다. 코칭 프로필은 `profile`(답 쓰기 · 고치기)일 때만, 켜져
        있을 때만 싣는다(6-14). 계획 · 조사 호출은 주장 나누기 · 대상 고르기 · 도구 · 행동 인자를 정하므로 받지 않는다(P14)."""
        ctx, req = self.ctx, self.req
        pitch = ctx.pitch
        takes = []
        for t in sorted(ctx.summaries.values(), key=lambda x: x.take_number, reverse=True):
            takes.append(
                {
                    "take_id": t.take_id,
                    "take_number": t.take_number,
                    "status": t.status,
                    "completed_fully": t.completed_fully,
                    "mode": t.mode,
                    "script_mode": t.script_mode,
                    "script_version_id": t.script_version_id,
                    "review_score": t.review.score if t.review else None,
                    "priority_dimensions": [
                        d
                        for d, s in (t.review.dimensions.items() if t.review else [])
                        if s == "PRIORITY"
                    ],
                    "loaded": t.take_id in ctx.snapshots,
                }
            )
        focus = (
            pitch.focus_take.summary.take_id
            if pitch is not None and pitch.focus_take is not None
            else None
        )
        hist = req.history
        messages = [
            {"role": m.role, "text": m.text[:600], "stale": m.stale_reasons or None}
            for m in hist.messages[-6:]
        ]
        prior = [
            {"quote": a.quote, "type": a.type, "verdict": a.verdict}
            for a in hist.prior_assessments[-10:]
        ]
        screen = req.page.screen
        goal = ctx.goal
        return {
            "user_message": self.original,
            "trigger": req.run.trigger,
            "page": {
                "route": req.page.route,
                "take_id": req.page.take_id,
                "screen": None
                if screen is None
                else {"name": screen.name, "step": getattr(screen, "step", None)},
            },
            "scope": (
                {
                    "pitch_id": pitch.info.pitch_id,
                    "title": pitch.info.title,
                    "script_version": pitch.versions.script_version_label,
                }
                if pitch is not None
                else "ALL"
            ),
            "pitches": [
                {
                    "pitch_id": p.pitch_id,
                    "title": p.title,
                    "take_count": p.take_count,
                    "last_score": p.last_review.score if p.last_review else None,
                    "priority_dimensions": p.last_review.priority_dimensions
                    if p.last_review
                    else [],
                }
                for p in ctx.pitches
            ],
            "pitches_total": ctx.pitches_total,
            "takes": takes,
            "focus_take_id": focus,
            "conversation_goal": (
                {
                    "metric": goal.metric,
                    "slide_number": goal.slide_number,
                    "operator": goal.operator,
                    "base_take_number": goal.base.take_number,
                    "status": goal.status,
                }
                if goal is not None
                else None
            ),
            "history": {
                "summary": [{"kind": s.kind, "text": s.text} for s in hist.summary]
                if hist.summary
                else None,
                "messages": messages,
            },
            "prior_assessments": prior,
            "actions_done": [{"type": e["type"], "status": e["status"]} for e in self.st.executed],
            **(
                {"coaching_profile": prof}
                if profile and (prof := memory.profile_for_model(req.memory))
                else {}
            ),
        }

    def _system(self, phase: str) -> dict[str, str]:
        return {"role": "system", "content": prompts.SYSTEM + "\n\n" + phase}

    # ── 1. 계획 ─────────────────────────────────────────────────────
    def plan_phase(self) -> str:
        """계획 · 판단 초안. 돌려주는 값: route(RESEARCH · ANSWER_DIRECT · SMALL_TALK · OUT_OF_SCOPE). OUT_OF_SCOPE 면
        `scope_reason`, SMALL_TALK 면 `talk_kind`도 남는다."""
        self.route = self._plan_route()
        return self.route

    def _plan_route(self) -> str:
        plan = prompts.PLAN + ("\n" + prompts.PLAN_COUNT_HINT if self.cfg.count_metric_hint else "")
        msgs = [self._system(plan), prompts.data_message(self.data_block())]
        res = self._call("RESEARCH", msgs, schema=prompts.PLAN_SCHEMA)
        if not isinstance(res, LlmResult) or res.parsed is None:
            self._set_plan(DEFAULT_PLAN, edit=False)
            return "RESEARCH"
        parsed = res.parsed
        route = _route_of(parsed)
        drafts = self._parse_drafts(parsed.get("assessments") or [])
        if route == "SMALL_TALK":
            kind = self._small_talk_kind(parsed.get("talk_kind"), drafts)
            if kind is not None:
                # 가벼운 말: 모델은 이 한 번뿐이고 답은 코드 고정 문장이다(P-0008 5-1). 판단 · 할 일 목록은 두지 않는다
                self.talk_kind = kind
                self.st.drafts = []
                return "SMALL_TALK"
            # 코드가 가벼운 말로 보지 않은 말(길다 · 숫자 · 내용 있는 판단)은 기록 · 자료를 조사하는 기존 처리로
            route = "RESEARCH"
        valid, problems = clm.split_check(self.original or "", drafts, self.cfg)
        plan_ok = self._plan_valid(parsed.get("plan") or [], allow_empty=route != "RESEARCH")
        if (problems or not plan_ok) and route != "OUT_OF_SCOPE":
            # 형식 고치기 1회(조사 몫, 6-1). 여러 형식 오류는 한 번에 묶는다
            note = {
                "problems": [p.code if hasattr(p, "code") else str(p) for p in problems][:10],
                "plan_invalid": not plan_ok,
            }
            fix_msgs = msgs + [
                {"role": "assistant", "content": json.dumps(parsed, ensure_ascii=False)},
                {
                    "role": "user",
                    "content": "<format_problems>"
                    + json.dumps(note, ensure_ascii=False)
                    + "</format_problems>\n판단마다 quote 는 사용자 말 원문 그대로 한 절만, 측정 항목을 말한 절마다 판단 하나, 할 일 목록은 1~8단계로 다시 내라.",
                },
            ]
            res2 = self._call("RESEARCH", fix_msgs, schema=prompts.PLAN_SCHEMA)
            if isinstance(res2, LlmResult) and res2.parsed is not None:
                parsed = res2.parsed
                # 고친 계획의 route 로 다시 정한다(처음 route 를 그대로 쓰면 고친 계획과 어긋난다). 고치기에서 가벼운 말 ·
                # 범위 밖으로 바꾼 것은 받지 않는다 — 형식이 틀렸던 말을 고정 문장 · 거절로 끝내지 않게
                route2 = _route_of(parsed)
                route = route2 if route2 in ("RESEARCH", "ANSWER_DIRECT") else route
                drafts = self._parse_drafts(parsed.get("assessments") or [])
                valid, problems = clm.split_check(self.original or "", drafts, self.cfg)
                plan_ok = self._plan_valid(
                    parsed.get("plan") or [], allow_empty=route != "RESEARCH"
                )
        if route == "OUT_OF_SCOPE":
            self.scope_reason = parsed.get("scope_reason")
        self.st.drafts = [d.model_dump(mode="json") for d in drafts]
        self.st.plan_drafts = [dict(d) for d in self.st.drafts]
        self.st.guide_queries = (
            self._clean_guide_queries(parsed.get("guide_queries"))
            if route == "RESEARCH" and self.cfg.plan_guide_queries
            else []
        )
        if route == "RESEARCH":
            self._set_plan(
                parsed.get("plan") if plan_ok and parsed.get("plan") else DEFAULT_PLAN, edit=False
            )
        elif plan_ok and parsed.get("plan"):
            self._set_plan(parsed["plan"], edit=False)
        return route

    def _clean_guide_queries(self, raw: Any) -> list[str]:
        """계획이 쓴 자료 검색어를 거른다: 글자 아닌 것 · 빈 것 · 중복을 빼고, 도구 인자 한도(200자)로 자르고, 개수 상한으로 자른다."""
        if not isinstance(raw, list):
            return []
        out: list[str] = []
        for q in raw:
            if isinstance(q, str) and q.strip() and q.strip()[:200] not in out:
                out.append(q.strip()[:200])
        return out[: self.cfg.max_guide_queries]

    def _small_talk_kind(self, kind: Any, drafts: list[clm.AssessmentDraft]) -> str | None:
        """계획이 고른 가벼운 말을 코드가 한 번 더 본다(P-0008 5-1 · 레드팀 '말 전체가 가벼운 말일 때만').

        받지 않는 것: 모르는 종류, 말이 길거나(`small_talk_max_chars`) 숫자가 있음, 대상(차원 · 지표 · 장 · Take)을 집었거나
        요청이 아닌 판단 초안(주장 · 의견)이 있음. 연습하려는 뜻은 사용자 말에 연습 낱말이 있을 때만 — 없으면 인사로 받는다
        (화면 상태 · 기록 속 지시가 이동 버튼을 만들지 못하게).
        """
        if kind not in TALK_KINDS:
            return None
        msg = self.original or ""
        if len(msg) > self.cfg.small_talk_max_chars or any(ch.isdigit() for ch in msg):
            return None
        # 요청 · 지시 · 질문이 섞인 말은 가벼운 말이 아니다("안녕, 예상 질문 만들어 줘"). 물음표는 CAPABILITY 에서만 받는다
        words = self.cfg.small_talk_request_words
        if kind == "CAPABILITY" and "수 있" in msg:
            # "어떻게 도와줄 수 있어?" · "뭘 알려줄 수 있어?"처럼 할 수 있는 일을 묻는 말의 낱말은 요청으로 세지 않는다
            words = tuple(w for w in words if w not in self.cfg.capability_question_words)
        if any(w in msg for w in words):
            return None
        if (
            kind != "CAPABILITY"
            and ("?" in msg or "？" in msg)
            and not _greeting_only(msg, self.cfg)
        ):
            # 물음표는 할 수 있는 일 질문과 "안녕하세요?"처럼 인사 낱말만 있는 말에서만 받는다
            return None
        for d in drafts:
            sub = d.subject
            if (
                d.type != "REQUEST"
                or sub.dimension
                or sub.metric
                or sub.slide_number
                or sub.take_ids
                or sub.take_numbers
            ):
                return None
        if kind == "PRACTICE_INTENT" and (
            not any(w in msg for w in self.cfg.practice_intent_words)
            or any(w in msg for w in self.cfg.practice_negation_words)
        ):
            return "GREETING"
        return str(kind)

    def _parse_drafts(self, raw: list[Any]) -> list[clm.AssessmentDraft]:
        out = []
        for r in raw:
            try:
                out.append(clm.AssessmentDraft.model_validate(r))
            except ValidationError:
                continue
        return out

    def _plan_valid(self, plan: list[Any], *, allow_empty: bool) -> bool:
        if not plan:
            return allow_empty
        if not (self.cfg.plan_steps_min <= len(plan) <= self.cfg.plan_steps_max):
            return False
        try:
            for s in plan:
                progress.PlanStepDraft.model_validate(s)  # PlanStep + 도구 힌트
        except ValidationError:
            return False
        return True

    def _set_plan(self, plan: list[dict[str, Any]], *, edit: bool) -> bool:
        """모델의 계획(처음 · `update_plan`)을 정해 plan 줄을 낸다. 고치기(`edit`)만 계획 수정 한도(PLAN_EDITS)를 예약한다.
        `plan_progress`가 켜졌으면 단계 메타를 새로 만들고 상태 · 글은 메타로 그린다. 고치기는 합친다: 이미 실행한 단계는 메타 ·
        표시 그대로 앞에 남기고 모델의 새 목록 중 실행하지 않은 단계를 뒤에 잇는다(8단계까지, progress.new_meta). 합치기도 고치기 1회다."""
        if edit and self.st.plan_set:
            hit = self.budget.reserve_plan_edit()
            if hit is not None:
                self._limit(hit)
                return False
        steps, hints = progress.split_steps(plan)
        if self.cfg.plan_progress:
            # 목적에 쓴 숫자 · Take · 장 번호는 그 표기만 지운다(계획 전체를 기본 계획으로 바꾸지 않는다). 숫자는 실행 뒤 결과 쪽에 붙는다
            steps = progress.clean_purposes(steps, hints, self.ctx.registry, self.cfg)
        # 할 일 목록도 화면에 보이는 문자열이다: 근거 없는 숫자 · 금지 주장 · 개인정보가 있으면 쓰지 않는다(6-9 · L1-37)
        if chk.check_display_strings(
            plan=steps,
            registry=self.ctx.registry,
            display_evidence=None,
            cfg=self.cfg,
            assessments={},
        ):
            if self.st.plan_set:
                return False
            steps, hints = progress.split_steps(DEFAULT_PLAN)
        if self.cfg.plan_progress:
            self.st.plan_meta = progress.new_meta(
                steps,
                hints,
                old=self.st.plan_meta if self.st.plan_set else None,
                shown=self.st.plan,
                max_steps=self.cfg.plan_steps_max,
            )
            steps = progress.render(self.st.plan_meta, self.ctx.registry, self.cfg)
        self.st.plan = steps
        self.st.plan_set = True
        self.emit("plan", {"plan": self.st.plan})
        return True

    def _emit_plan_display(self) -> None:
        """표시 갱신(코드): 단계 메타로 상태 · 글을 다시 그려 바뀌었으면 plan 줄을 낸다. 계획 수정 한도(PLAN_EDITS)를 쓰지 않는다."""
        plan = progress.render(self.st.plan_meta, self.ctx.registry, self.cfg)
        if plan != self.st.plan:
            self.st.plan = plan
            self.emit("plan", {"plan": plan})

    def _after_tool(
        self, name: str, args: Any, tr: ToolResult, trace: TraceItem, *, chosen: bool
    ) -> None:
        """도구 하나가 끝난 뒤(캐시 적중 포함): 실행을 할 일 단계에 이어 다시 그리고, 상황 신호 · 결정을 남긴다(progress.py).

        `chosen`: 모델이 고른 호출인가(코드가 먼저 읽은 요청은 신호에 대한 대응이 아니다). 같은 결과를 다시 읽은 캐시 적중은
        새 신호를 내지 않는다(이미 보인 사실)."""
        run = progress.read_run(
            name, args, tr, self.ctx, areas=progress.asked_areas(self.st.plan_drafts, self.cfg)
        )
        step = None
        if self.cfg.plan_progress and self.st.plan_meta:
            step = progress.attach(
                self.st.plan_meta, run, trace.seq, max_steps=self.cfg.plan_steps_max
            )
            self._emit_plan_display()
        if not self.tracking:
            return
        key = progress.call_key(name, args)
        if chosen and name in progress.RECORD_TOOLS:
            self._respond("ALT_TOOL", key=key, step=step, tool_seq=trace.seq)
        if run.signal is not None and tr.outcome != "CACHED":
            self._decide(run.signal, run.facts, "FACT_SHOWN", "CODE", step=step, tool_seq=trace.seq)
            self.st.signals_open.append(progress.open_signal(run, key, step, trace.seq))

    def _decide(
        self,
        signal: str,
        facts: list[str],
        effect: str,
        decider: str,
        *,
        step: int | None = None,
        tool_seq: int | None = None,
    ) -> None:
        """결정 로그 한 줄(`event: agent_decision`)을 서비스 로그에 남긴다(설정 `decision_log`). 진단 기록에는 넣지 않는다."""
        if not self.cfg.decision_log:
            return
        self.st.decision_seq += 1
        self.deps.log(
            progress.decision_line(
                request_id=self.diag.request_id,
                run_id=self.diag.run_id,
                turn_call=self.diag.turn_call,
                seq=self.st.decision_seq,
                signal=signal,
                facts=facts,
                decider=decider,
                effect=effect,
                step=step,
                tool_seq=tool_seq,
            )
        )

    def _respond(
        self,
        effect: str,
        *,
        key: str | None = None,
        step: int | None = None,
        tool_seq: int | None = None,
    ) -> list[str]:
        """모델이 본 신호를 이 선택(`PLAN_EDIT` · `ALT_TOOL` · `ACTION`, 결정 주체 LLM)으로 닫는다. 같은 도구 · 같은 대상을 다시
        부른 것(`key`가 같음)은 그 신호의 대응이 아니다. 돌려주는 값: 닫은 신호 이름들."""
        kept: list[dict[str, Any]] = []
        closed: list[str] = []
        for s in self.st.signals_open:
            if not s["seen"] or (key is not None and s["key"] == key):
                kept.append(s)
                continue
            self._decide(s["signal"], s["facts"], effect, "LLM", step=step, tool_seq=tool_seq)
            closed.append(s["signal"])
        self.st.signals_open = kept
        return closed

    def _action_decision(self, out: dict[str, Any], *, chosen: bool) -> None:
        """행동 도구 하나의 결정: 모델이 고른 행동은 모델이 본 신호의 대응(`ACTION:LLM`)이다. 가져오기(FETCH_TAKE)는
        `NOT_LOADED · ACTION`(코드가 먼저 읽기로 냈으면 CODE), 허용 근거를 통과한 다시 평가는 `USER_DISPUTE · ACTION:LLM`."""
        action = out.get("action")
        atype = getattr(action, "type", None)
        if not chosen:
            if atype == "FETCH_TAKE":
                self._decide("NOT_LOADED", [], "ACTION", "CODE")
            return
        if not out.get("valid"):
            return
        closed = self._respond("ACTION")
        if atype == "FETCH_TAKE" and "NOT_LOADED" not in closed:
            self._decide("NOT_LOADED", [], "ACTION", "LLM")
        elif atype == "REASSESS":
            self._decide("USER_DISPUTE", [], "ACTION", "LLM")

    def _research_end(self) -> None:
        """조사가 끝날 때: 대응이 없던 신호마다 `NONE:LLM`을 남기고, 한도 · 반복으로 멈췄으면 `BUDGET_LOW · STOP_PARTIAL:CODE`를
        남긴다. 한도 · 시간으로 멈췄으면 지금 하던 단계에 `시간 부족`을 단다."""
        reason = self.st.stop_reason
        step = None
        if self.cfg.plan_progress and self.st.plan_meta:
            if reason in progress.LIMIT_STOPS:
                step = progress.mark_short(self.st.plan_meta)
                self._emit_plan_display()
            else:
                step = progress.current_step(self.st.plan_meta)
        if not self.tracking:
            return
        self.close_signals()
        if reason in progress.BUDGET_STOPS:
            self._decide("BUDGET_LOW", [reason], "STOP_PARTIAL", "CODE", step=step)

    def close_signals(self) -> None:
        """대응이 없던 신호마다 `NONE:LLM` 한 줄을 남기고 비운다(조사가 끝날 때, 조사 없이 끝나는 응답은 turn.py 가 끝에서)."""
        for s in self.st.signals_open:
            self._decide(s["signal"], s["facts"], "NONE", "LLM")
        self.st.signals_open = []

    def _research_messages(self) -> list[dict[str, Any]]:
        """조사 호출의 메시지: 지시문(신호 지침은 `research_signals`일 때) · 데이터 블록(대응을 기다리는 신호 포함) · 지난 도구 메시지."""
        prompt = prompts.RESEARCH
        block = self.data_block()
        if self.cfg.research_signals:
            prompt += "\n" + prompts.RESEARCH_SIGNALS_HINT
            if self.st.signals_open:
                block["signals"] = progress.signals_view(self.st.signals_open)
        return [self._system(prompt), prompts.data_message(block), *self.st.messages]

    # ── 2. 조사 ─────────────────────────────────────────────────────
    def research_phase(self) -> dict[str, Any] | None:
        """도구 · 행동 루프. 행동을 내면 {'action': Action, 'card': …}, 조사가 끝나면 None."""
        tools = model_tool_definitions() + act.action_tool_definitions()
        while not self.st.research_done:
            if self.cancelled():
                self.st.research_done, self.st.stop_reason = True, "CLIENT_DISCONNECTED"
                break
            pre = self._prefetch_calls()
            if pre:
                # 조사 흐름: 판단 초안이 집은 기록을 모델이 고르기 전에 코드가 먼저 읽는다(모델 호출 없음)
                calls, said, model_seq = pre, "", None
            else:
                msgs = self._research_messages()
                res = self._call("RESEARCH", msgs, tools=tools)
                for s in self.st.signals_open:
                    s["seen"] = True  # 이 조사 호출이 본 신호다. 이 응답의 선택부터 대응으로 센다
                if isinstance(res, LimitHit):
                    self.st.research_done, self.st.stop_reason = True, limit_end_reason(res.code)
                    break
                if isinstance(res, LlmError):
                    self.st.research_done = True
                    self.st.stop_reason = (
                        "LLM_REFUSED" if isinstance(res, LlmRefused) else "LLM_ERROR"
                    )
                    break
                calls = list(res.tool_calls)
                if not calls:
                    self.st.research_done = True
                    break
                said, model_seq = res.message or "", self._last_model_seq
            if len(calls) > self.cfg.max_tools_per_response:
                self._limit(
                    LimitHit("TOOLS_PER_RESPONSE", len(calls), self.cfg.max_tools_per_response)
                )
            assistant = {"role": "assistant", "content": said, "tool_calls": []}
            results: list[dict[str, Any]] = []
            stop_action: dict[str, Any] | None = None
            any_valid = False
            progressed = False
            #: 교대 반복(A-B-A-B)이 이 응답의 어느 요청에서 확인되면 남은 요청은 실행하지 않는다(5-7)
            alt_stop = False
            for i, call in enumerate(calls):
                if self.cancelled():
                    break  # 연결이 끊기면 같은 응답의 남은 도구 · 행동도 실행하지 않는다(L1-08)
                if call.id in self.st.processed_calls:
                    continue  # 이미 처리한 호출은 다시 실행하지 않는다(6-1 4)
                assistant["tool_calls"].append(
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.name,
                            "arguments": call.raw_args
                            or json.dumps(call.args or {}, ensure_ascii=False),
                        },
                    }
                )
                self.st.processed_calls.append(call.id)
                #: 같은 응답 안에서 한도로 조사가 끝났으면 남은 요청도 실행하지 않는다(6-1 · 8-2)
                if (
                    stop_action is not None
                    or alt_stop
                    or self.st.research_done
                    or i >= self.cfg.max_tools_per_response
                ):
                    results.append(
                        self._tool_msg(
                            call.id,
                            {
                                "ok": False,
                                "error": {
                                    "code": "TOOL_SKIPPED",
                                    "message": "앞 요청에서 멈춰 실행하지 않았다",
                                },
                            },
                        )
                    )
                    continue
                if call.name in act.ACTION_TOOL_ARGS:
                    out = self._handle_action(call, model_seq)
                    if self.tracking:
                        self._action_decision(out, chosen=not pre)
                    any_valid = any_valid or out.get("valid", False)
                    results.append(self._tool_msg(call.id, out["message"]))
                    if out.get("action") is not None:
                        stop_action = {
                            "action": out["action"],
                            "call_id": call.id,
                            "tool_name": call.name,
                            "tool_args": call.args,
                        }
                    if out.get("progressed", False):
                        progressed = True
                        self.st.recent_keys = []  # 새 근거가 생긴 요청에서 반복 기록을 비운다(5-7)
                    continue
                args = validate_args(call.name, call.args)
                if isinstance(args, str):
                    tr = invalid_result(call.name, args)
                    results.append(self._tool_msg(call.id, tr.model_view()))
                    self._trace_and_diag(tr, model_seq, call_ok=False)
                    continue
                any_valid = True
                key = json.dumps(
                    [call.name, args.model_dump(mode="json"), self.ctx.scope_pitch_id],
                    ensure_ascii=False,
                    sort_keys=True,
                )
                if key == self.st.repeat_key:
                    self.st.repeat_count += 1
                else:
                    self.st.repeat_key, self.st.repeat_count = key, 1
                self.st.recent_keys = [*self.st.recent_keys, key][-4:]
                request_progressed = False
                cached = call.name != "update_plan" and self._is_cached(call.name, args)
                if not cached:
                    hit = self.budget.reserve_tool()
                    if hit is not None:
                        self._limit(hit)
                        results.append(
                            self._tool_msg(
                                call.id,
                                {
                                    "ok": False,
                                    "error": {"code": "LIMIT", "message": "도구 호출 한도"},
                                },
                            )
                        )
                        self.st.research_done, self.st.stop_reason = True, "TOOL_CALLS"
                        continue
                if call.name == "update_plan":
                    plan = [s.model_dump() for s in args.steps]  # type: ignore[attr-defined]
                    ok = self._set_plan(plan, edit=True)
                    tr, trace = run_tool(
                        self.ctx,
                        call.name,
                        args,
                        seq=len(self.st.trace) + 1,
                        clock=self.deps.monotonic,
                    )
                    if not ok:
                        tr.ok, tr.error = (
                            False,
                            {"code": "LIMIT", "message": "할 일 목록 고치기 한도"},
                        )
                    elif self.cfg.plan_progress and self.st.plan_meta:
                        # 모델에게는 합친 목록(실행한 단계 + 새 단계, 상태 · 결과 글 포함)을 돌려준다: 지금 상태를 알고 이어 가게
                        tr.data = {"plan": [dict(p) for p in self.st.plan]}
                else:
                    tr, trace = run_tool(
                        self.ctx,
                        call.name,
                        args,
                        seq=len(self.st.trace) + 1,
                        clock=self.deps.monotonic,
                    )
                    if tr.outcome != "CACHED" and tr.ok:
                        progressed = True
                        request_progressed = True
                    if (
                        call.name == "compare_takes"
                        and tr.ok
                        and tr.data
                        and tr.data.get("comparable") is False
                    ):
                        self._note_not_comparable(args, tr.data)
                    if (
                        call.name == "list_pitches"
                        and tr.ok
                        and getattr(args, "query", None)
                        and len((tr.data or {}).get("items") or []) >= 2
                    ):
                        self.st.ambiguous_name = True
                    if (
                        call.name == "list_pitches"
                        and tr.ok
                        and getattr(args, "query", None)
                        and not (tr.data or {}).get("items")
                    ):
                        self.st.name_not_found = True
                    if call.name == "search_guides" and (
                        tr.ok or (tr.error or {}).get("code") == "CORPUS_UNAVAILABLE"
                    ):
                        # 찾은 것이다: 캐시 적중, 그리고 자료 묶음 준비 실패도(결과 0건처럼 일반 조언 규칙, L1-21)
                        self.st.guide_searched = True
                    if (
                        call.name in _TAKE_READ_TOOLS
                        and tr.ok
                        and set(tr.target_ids or []) & set(self.ctx.summaries)
                    ):
                        # 지금 범위 피치의 Take 를 읽었다. list_pitches 의 target_ids 에도 마지막 리뷰 Take id 가
                        # 들지만 그것은 캐시를 버리기 위한 것이라 읽음으로 세지 않는다(6-5)
                        self.st.scope_switch_unread = False
                self.st.trace.append(trace.model_dump())
                self.emit("tool_done", {"trace": trace.model_dump()})
                self._tool_diag(tr, trace, model_seq, args)
                if call.name != "update_plan":
                    self._after_tool(call.name, args, tr, trace, chosen=not pre)
                elif tr.ok and self.tracking and not pre:
                    self._respond("PLAN_EDIT", tool_seq=trace.seq)
                self.st.message_deps[call.id] = list(tr.target_ids) + (
                    [self.ctx.scope_pitch_id] if self.ctx.scope_pitch_id else []
                )
                results.append(self._tool_msg(call.id, tr.model_view()))
                # 교대 반복은 요청마다 본다: 새 근거가 생기면 기록을 비우고 다음 요청부터 다시 센다
                if request_progressed:
                    self.st.recent_keys = []
                elif _alternating(self.st.recent_keys):
                    alt_stop = True
                    if not self.st.research_done:  # 이미 한도로 끝난 이유는 덮지 않는다
                        self.st.research_done, self.st.stop_reason = True, "ALTERNATING"
            self.st.messages.append(assistant)
            self.st.messages.extend(results)
            if pre:
                # 코드가 먼저 읽은 요청은 반복 판정에 넣지 않는다: 모델이 같은 것을 한 번 더 읽어도(캐시) 되풀이로 세지 않는다
                self.st.repeat_key, self.st.repeat_count, self.st.recent_keys = None, 0, []
            if stop_action is not None:
                return stop_action
            if not any_valid:
                self.st.invalid_streak += 1
                if self.st.invalid_streak >= self.cfg.invalid_stop and not self.st.research_done:
                    self.st.research_done, self.st.stop_reason = True, "INVALID_TOOLS"
            else:
                self.st.invalid_streak = 0
            if (
                self.st.repeat_count >= self.cfg.repeat_stop
                and not progressed
                and not self.st.research_done
            ):
                self.st.research_done, self.st.stop_reason = True, "NO_PROGRESS"
        self._research_end()
        return None

    def _prefetch_calls(self) -> list[ToolCall]:
        """조사 흐름(6-1 2번 앞): 계획의 판단 초안이 집은 대상을 모델이 고르기 전에 코드가 먼저 읽을 요청 목록.

        - 대상: `MEASURED` · `REQUEST` 초안의 Take(번호 → 지금 범위 피치의 Take, 없으면 6-3 '판정 대상 Take') · 장 · 지표 · 차원.
          지표가 없으면 차원의 첫 지표를 쓴다. 대상이 없거나 전체 범위(피치 없음)면 읽지 않는다.
        - 읽을 것: 비교(`COMPARE` · `CHANGE`, Take 둘)는 `compare_takes`, 그 밖은 Take 마다 장 · 지표면 `slides`, 차원만이면
          `dimensions`, 문제 주장(`ISSUE`)이나 장을 집은 측정 주장이면 `problem_segments`도. 한 실행에 최대 4개.
        - 기본 자료에 없는 Take 는 먼저 `fetch_take`(AUTO 행동)를 낸다. 가져오기에 실패한 Take 는 다시 내지 않는다.
        - 이미 낸 요청은 `prefetched`에 남겨 이어 부른 뒤에도 다시 내지 않는다. 결과는 모델이 고른 호출과 같은 길(캐시 ·
          한도 · 근거 · 도구 기록)로 처리한다.
        """
        # 공통 한도(`research_prefetch_max`)는 계획한 자료 검색어(`max_guide_queries`개까지)에 먼저 나누고, 남은 몫을 기록 읽기가 쓴다.
        # 자료 검색은 앞에 둔다: 기록을 가져오는 행동(fetch_take)은 한 번에 하나이고 그 뒤 요청은 실행하지 않는다
        guides = self._guide_prefetch_calls()
        return [*guides, *self._prefetch_record_calls()]

    def _guide_prefetch_calls(self) -> list[ToolCall]:
        """계획이 쓴 자료 검색어(`guide_queries`)를 코드가 먼저 찾을 요청 목록(6-10). 기록 요청과 같은 한도(`research_prefetch_max`)로 센다.

        질문 임베딩은 이 검색어들을 한 번에 묶어 부른다(실행당 한 번). 이미 낸 검색어는 `prefetched`에 남겨 다시 내지 않는다.
        자료 묶음이 없으면 내지 않는다(모델이 고른 검색과 같은 길로 처리한다).
        """
        if (
            not self.cfg.plan_guide_queries
            or not self.cfg.research_prefetch
            or not self.st.guide_queries
            or self.deps.corpus is None
        ):
            return []
        todo: list[tuple[str, str]] = []
        for q in self.st.guide_queries[: self.cfg.max_guide_queries]:
            key = json.dumps(["search_guides", {"query": q}], sort_keys=True)
            if key in self.st.prefetched or any(k == key for _, k in todo):
                continue
            if len(self.st.prefetched) + len(todo) >= self.cfg.research_prefetch_max:
                break
            todo.append((q, key))
        if not todo:
            return []
        embed_planned_queries(self.ctx, [q for q, _ in todo])
        out: list[ToolCall] = []
        for q, key in todo:
            self.st.prefetched.append(key)
            out.append(ToolCall(id=self._pf_id(), name="search_guides", args={"query": q}))
        return out

    def _pf_id(self) -> str:
        """코드가 먼저 내는 요청의 호출 id. 자료 판이 바뀌어 검색 완료 표시를 버리면 번호가 되풀이되므로, 이미 처리한 id 와 겹치지 않게 한다."""
        cid = f"pf-{len(self.st.prefetched)}"
        while cid in self.st.processed_calls:
            cid += "r"
        return cid

    def _prefetch_record_calls(self) -> list[ToolCall]:
        """`_prefetch_calls` 의 기록 읽기 부분(위 설명)."""
        if not self.cfg.research_prefetch or self.ctx.pitch is None:
            return []
        if self.st.scope_switched or self.ctx.scope_switched:
            return []  # 범위를 바꾼 실행: 초안의 Take 번호가 어느 피치의 것인지 알 수 없다(새 피치 번호로 대응하지 않는다)
        if len(self.st.prefetched) >= self.cfg.research_prefetch_max:
            return []
        summaries = self.ctx.summaries
        by_number = {s.take_number: tid for tid, s in summaries.items()}
        wanted: list[tuple[str, dict[str, Any]]] = []
        for d in self.st.drafts:
            if d.get("type") not in ("MEASURED", "REQUEST"):
                continue
            sub = d.get("subject") or {}
            metric = sub.get("metric")
            dim = sub.get("dimension")
            if metric not in self.cfg.metric_units:
                metric = None
            if dim not in self.cfg.dimensions:
                dim = None
            if metric is None and dim is not None:
                ms = [m for m in self.cfg.dimensions[dim].metrics if m in self.cfg.metric_units]
                metric = ms[0] if ms else None
            slide = sub.get("slide_number") if isinstance(sub.get("slide_number"), int) else None
            if metric is None and dim is None and slide is None:
                continue
            named = list(sub.get("take_numbers") or [])
            if any(n not in by_number for n in named):
                continue  # 말한 Take 가 이 피치에 없다(다른 Take 로 바꿔 읽지 않는다, L2-22)
            takes = [by_number[n] for n in named]
            if not takes:
                target = self._judged_take()
                takes = [target] if target is not None else []
            if not takes:
                continue
            op = d.get("claim_op")
            compare = sub.get("relation") == "COMPARE" or op == "CHANGE"
            if compare and len(takes) == 1 and op == "CHANGE" and len(named) <= 1:
                # '지난번보다'처럼 비교 상대를 말하지 않은 변화 주장만 바로 앞 Take 와 비교한다(말한 두 Take 는 그대로)
                cur_n = summaries[takes[0]].take_number
                prev = [n for n in by_number if n < cur_n]
                if prev:
                    takes = [by_number[max(prev)], takes[0]]
            takes = sorted(set(takes), key=lambda t: summaries[t].take_number)[:3]
            if compare and len(takes) == 2 and metric is not None:
                wanted.append(
                    (
                        "compare_takes",
                        {
                            "previous_take_id": takes[0],
                            "current_take_id": takes[1],
                            "metric": metric,
                            "slide_number": slide,
                        },
                    )
                )
                continue
            for tid in takes:
                if slide is not None or sub.get("metric") in self.cfg.metric_units:
                    wanted.append(
                        (
                            "get_take_report",
                            {"take_id": tid, "section": "slides", "slide_number": slide},
                        )
                    )
                elif dim is not None:
                    wanted.append(("get_take_report", {"take_id": tid, "section": "dimensions"}))
                if op == "ISSUE" or (d.get("type") == "MEASURED" and slide is not None):
                    wanted.append(
                        ("get_take_report", {"take_id": tid, "section": "problem_segments"})
                    )
        out: list[ToolCall] = []
        for name, args in wanted:
            needed = (
                [args["previous_take_id"], args["current_take_id"]]
                if name == "compare_takes"
                else [args["take_id"]]
            )
            missing = [t for t in needed if t not in self.ctx.snapshots]
            if missing:
                tid = missing[0]
                fkey = json.dumps(["fetch_take", tid])
                if fkey in self.st.prefetched:
                    continue  # 이미 가져오려 했는데 아직 없다 = 실패했다(다시 내지 않는다)
                if len(self.st.prefetched) >= self.cfg.research_prefetch_max:
                    break
                self.st.prefetched.append(fkey)
                # 행동은 한 번에 하나만(6-1). 가져온 뒤 이어 부르면 나머지를 읽는다
                return [
                    *out,
                    ToolCall(
                        id=self._pf_id(),
                        name="fetch_take",
                        args={"take_id": tid, "reason": "질문한 연습 기록 확인"},
                    ),
                ]
            key = json.dumps([name, args], sort_keys=True)
            if key in self.st.prefetched:
                continue
            if len(self.st.prefetched) >= self.cfg.research_prefetch_max:
                break
            self.st.prefetched.append(key)
            out.append(ToolCall(id=self._pf_id(), name=name, args=args))
        return out

    def _judged_take(self) -> str | None:
        """Take 를 말하지 않은 초안의 대상(6-3 '판정 대상 Take'): 지금 보던 Take → 보던 Take(focus) → 가장 최근 완료 Take.
        기본 자료에 없어도 고른다(없으면 조사 흐름이 가져온다)."""
        summaries = self.ctx.summaries
        page = self.req.page.take_id
        if page in summaries:
            return page
        pitch = self.ctx.pitch
        if pitch is not None and pitch.focus_take is not None:
            fid = pitch.focus_take.summary.take_id
            if fid in summaries:
                return fid
        done = [s for s in summaries.values() if s.status == "COMPLETED"]
        return max(done, key=lambda s: s.take_number).take_id if done else None

    def _note_not_comparable(self, args: Any, data: dict[str, Any]) -> None:
        """비교할 수 없다는 결과를 남긴다(같은 두 Take · 지표 · 장 · 까닭은 한 번). 답 쓰기가 까닭 문장을 코드로 그린다."""
        s = self.ctx.summaries
        prev, cur = s.get(args.previous_take_id), s.get(args.current_take_id)
        if prev is None or cur is None:
            return
        rec = {
            # 비교 당시 피치와 두 Take(어느 하나가 지워지면 기록을 버린다 — 범위를 바꾼 뒤에도 비교 당시 피치로 본다)
            "take_ids": [args.previous_take_id, args.current_take_id, self.ctx.scope_pitch_id],
            "previous": prev.take_number,
            "current": cur.take_number,
            "previous_complete": prev.completed_fully,
            "current_complete": cur.completed_fully,
            "metric": args.metric,
            "slide_number": args.slide_number,
            "reason": data.get("reason"),
        }
        if rec not in self.st.not_comparable:
            self.st.not_comparable.append(rec)

    def _is_cached(self, name: str, args: Any) -> bool:
        from .tools import cache_key

        return cache_key(self.ctx, name, args) in self.ctx.tool_cache

    def _tool_msg(self, call_id: str, content: dict[str, Any]) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": call_id,
            "content": json.dumps(content, ensure_ascii=False, separators=(",", ":")),
        }

    def _trace_and_diag(self, tr: ToolResult, model_seq: int | None, *, call_ok: bool) -> None:
        trace = TraceItem(
            seq=len(self.st.trace) + 1,
            tool=tr.name if tr.name in P0_TOOLS else "invalid",
            args_summary=tr.args_summary or "무효 호출",
            result_summary=tr.result_summary or "실행하지 않음",
            ms=0,
            outcome="ERROR",
        )
        self.st.trace.append(trace.model_dump())
        self.emit("tool_done", {"trace": trace.model_dump()})
        # 무효 호출도 TOOL 단계로 남긴다(11-5, #887 리뷰). 모르는 도구 이름은 `code` 가 5-7 의 도구 이름이어야 해서
        # 단계를 만들지 않는다(_tool_diag 가 거른다). 이때 모델이 보낸 이름은 진단 기록에 쓰지 않는다
        self._tool_diag(tr, trace, model_seq)

    def _tool_diag(
        self, tr: ToolResult, trace: TraceItem, model_seq: int | None, args: Any = None
    ) -> None:
        if tr.name not in P0_TOOLS:
            return
        # 질의 글자 수만 남기고 질의 글은 남기지 않는다(`search_guides` 만, 11-5)
        query_chars = len(args.query) if tr.name == "search_guides" and args is not None else None
        self.st.embed_used = self.ctx.embed_used  # 질문 임베딩은 실행당 한 번(서명 상태로 이어진다)
        self.diag.step(
            "TOOL",
            tr.name,
            {
                "target_ids": tr.target_ids or None,
                "revisions": tr.revisions or None,
                "outcome": trace.outcome,
                "result_bytes": result_bytes(tr),
                "query_chars": query_chars,
                "error_code": code_enum(tr.error["code"]) if tr.error else None,
                **tr.diag,
            },
            ms=trace.ms,
            parent_seq=model_seq,
        )

    def _handle_action(self, call: Any, model_seq: int | None) -> dict[str, Any]:
        """행동 도구 하나를 처리한다. 같은 실패 행동 · 인자가 연속 2회면 더 실행하지 않는다(5-8 · L2-31)."""
        key = _action_key(call.name, call.args)
        if key == self.st.failed_action_key and self.st.failed_action_count >= 1:
            self.st.failed_action_count += 1
            self.st.research_done, self.st.stop_reason = True, "REPEATED_FAILED_ACTION"
            return {
                "message": {
                    "ok": False,
                    "error": {
                        "code": "REPEATED",
                        "message": "같은 실패 행동은 다시 실행하지 않는다",
                    },
                }
            }
        if call.name == "fetch_pitch" and self._switched_without_reading():
            return {
                "message": {
                    "ok": False,
                    "error": {
                        "code": "SCOPE_NOT_READ",
                        "message": "방금 바꾼 범위 피치의 기록을 아직 읽지 않았다. 그 피치의 Take 를 먼저 읽고, "
                        "피치마다 따로 답한다(피치가 다르면 수치를 비교하지 않는다)",
                    },
                },
                "valid": False,
            }
        self.st.action_seq += 1
        p = act.propose(
            call.name,
            call.args,
            ctx=self.ctx,
            budget=self.budget,
            seq=self.st.action_seq,
            original_message=self.original,
            trigger=self.req.run.trigger,
        )
        atype = act.ACTION_TOOL_TYPE.get(call.name, "FETCH_PITCH")
        if p.result == "TOOL_ERROR":
            return {"message": p.tool_message, "valid": False}
        self.diag.step(
            "ACTION",
            atype,
            {
                "action_id": p.action.action_id if p.action else None,
                "autonomy": act.AUTONOMY.get(atype),
                "authorization": p.authorization,
                "result": p.result,
                "reason_code": code_enum(p.reason_code),
            },
            parent_seq=model_seq,
        )
        if p.result == "PROPOSED" and p.action is not None:
            return {
                "message": {"ok": True, "data": {"requested": atype}},
                "action": p.action,
                "valid": True,
            }
        if p.card is not None:
            self.st.cards.append(p.card)
        return {"message": p.tool_message, "valid": True, "progressed": p.card is not None}

    def _switched_without_reading(self) -> bool:
        """범위를 바꾼 뒤 새 범위 피치의 Take 를 하나도 읽지 않았는가(읽을 Take 가 없는 피치는 아니다)."""
        return self.st.scope_switch_unread and bool(self.ctx.summaries)

    def _demote_changed_records(self) -> bool:
        """이어 부를 때 Take 결과 판이 서명 때와 다르면 그 Take 의 옛 근거를 사본으로 낮춘다(새 문장의 근거로 못 씀)."""
        reg = self.ctx.registry
        changed = False
        for eid, ver in list(reg.versions.items()):
            snap = self.ctx.snapshots.get(ver.take_id)
            if snap is None:
                continue
            now = {k: v for k, v in snap.revisions.model_dump().items() if v is not None}
            if now != dict(ver.results):
                stale = {eid}
                grew = True
                while grew:  # 그 근거를 입력으로 쓴 계산 근거(그 계산을 입력으로 쓴 것 포함)까지
                    more = {i for i, e in reg.items.items() if set(e.inputs or []) & stale} - stale
                    grew = bool(more)
                    stale |= more
                reg.keep_as_copy(sorted(stale))
                changed = True
        return changed

    # ── 이어 부르기 · 지워진 원천 ─────────────────────────────────────
    def drop_removed(self, removed: list[str], *, fetch_failed: bool = False) -> bool:
        """지워진 원천에 기댄 근거 · 캐시 · 도구 결과 메시지를 버린다(5-6 · L1-32 · L4-38). 떼어 낼 수 없으면 False.

        `fetch_failed`: 대기 행동의 결과가 데이터 없는 실패다(가져오기 행동이면 대상을 읽지 못했다)."""
        if not removed:
            return True
        rs = set(removed)
        self.ctx.registry.drop_sources(rs)
        # 비교할 수 없다는 기록도 그 Take 에 기댄다(지워진 기록의 완료 여부 · 버전을 다시 말하지 않게)
        self.st.not_comparable = [
            r for r in self.st.not_comparable if not set(r.get("take_ids") or []) & rs
        ]
        self.ctx.tool_cache = {
            k: v for k, v in self.ctx.tool_cache.items() if not (set(v.get("target_ids", [])) & rs)
        }
        for m in self.st.messages:
            if (
                m.get("role") == "tool"
                and set(self.st.message_deps.get(m.get("tool_call_id", ""), [])) & rs
            ):
                # 구조화된 근거만 버리면 이전 도구 메시지에 지운 수치가 남는다 → 내용 자체를 버린다
                m["content"] = json.dumps(
                    {
                        "ok": False,
                        "read_state": "NOT_FOUND",
                        "error": {
                            "code": "SOURCE_DELETED",
                            "message": "근거 기록이 지워져 결과를 버렸다",
                        },
                    },
                    ensure_ascii=False,
                )
            if m.get("role") == "assistant":
                m["content"] = ""  # 모델의 중간 글에도 지운 수치가 있을 수 있다
        for idx, m in enumerate(self.st.messages):
            # 행동 결과 메시지(가져온 Take · 다시 평가 결과)도 그 행동의 대상에 기대어 있다
            if m.get("role") == "user" and set(self.st.message_deps.get(f"msg:{idx}", [])) & rs:
                m["content"] = (
                    "<action_result>"
                    + json.dumps(
                        {
                            "ok": False,
                            "error": {
                                "code": "SOURCE_DELETED",
                                "message": "근거 기록이 지워져 결과를 버렸다",
                            },
                        },
                        ensure_ascii=False,
                    )
                    + "</action_result>"
                )
        self.st.cards = [
            c
            for c in self.st.cards
            if c.get("take_id") not in rs
            and (c.get("goal") or {}).get("base", {}).get("take_id") not in rs
        ]
        exported = [a for a in self.st.exported if not set(_assessment_sources(a)) & rs]
        # 이미 낸 문장이 지워진 원천에 기댄 것(근거가 버려졌거나 판단이 버려진 것)이면 재개를 멈춘다(5-5 · 5-6 · L4-38):
        # 그 문장만 빼고 이어 가면 최종 답의 앞부분이 이미 화면에 낸 문장과 달라진다. 낸 문장 · 판단은 손대지 않고 그대로 둬
        # 호출자가 모델 없이 그 문장 그대로 끝내게 한다(지운 원천의 값은 모델에 다시 주지 않는다)
        kept_ids = {a["assessment_id"] for a in exported}
        self.emitted_removed = any(
            not all(self.ctx.registry.get(e) is not None for e in c.get("evidence_ids") or [])
            or (c.get("assessment_id") is not None and c["assessment_id"] not in kept_ids)
            for c in self.st.emitted
        )
        if not self.emitted_removed:
            self.st.exported = exported
        pending = self.st.pending_action
        if self.emitted_removed:
            return False
        # 대기 행동의 대상이 지워졌으면 멈춘다. 다만 가져오기 행동(FETCH_TAKE · FETCH_PITCH)이 실패해 대상을 읽지 못했으면
        # 떼어 낼 것이 없으므로 멈추지 않고 그 실패를 받아 "해당 연습 기록을 가져오지 못했어요"로 이어 간다(L2-22, 4회차
        # fail-action). 가져온 뒤 지워졌으면(성공 결과에 지운 값이 있다) 멈춘다(L4-38)
        if (
            pending is not None
            and set(_action_targets(pending)) & rs
            and not (fetch_failed and pending.get("type") in _FETCH_ACTIONS)
        ):
            return False
        self.ctx.read_sources -= rs
        return True

    def apply_action_result(self, result: Any) -> act.Applied:
        pending = self.st.pending_action
        assert pending is not None
        applied = act.apply_result(pending, result, ctx=self.ctx)
        call_id = pending.get("_call_id")
        content = applied.tool_message
        self.st.messages.append(self._tool_msg_replace(call_id, content))
        self.st.message_deps[f"msg:{len(self.st.messages) - 1}"] = _action_targets(pending)
        rec = {
            "action_id": pending["action_id"],
            "type": pending["type"],
            "status": "SUCCEEDED" if applied.ok else ("MISMATCH" if applied.mismatch else "FAILED"),
            "success_text": applied.success_text,
            "reason_code": applied.reason_code,
            "failed_code": applied.failed_code,
            "official_differs": applied.official_differs,
            # 대본 허용으로 다시 평가해 점수가 올랐으면 "평가 항목이 빠져 오른 점수예요"를 붙인다(7-10)
            "script_points_removed": _script_points_removed(applied.card),
        }
        if applied.review_versions:
            self.st.review_versions.update(applied.review_versions)
        if applied.ok and pending["type"] == "FETCH_PITCH":
            self.st.scope_switch_unread = True
            self.st.scope_switched = self.st.scope_switched or self.ctx.scope_switched
        elif applied.ok and pending["type"] == "FETCH_TAKE":
            self.st.scope_switch_unread = False
        if pending["type"] == "FETCH_TAKE" and self.cfg.plan_progress and self.st.plan_meta:
            # 가져오기도 할 일 단계의 실행이다(`Take {n} 가져오기` → 가져옴 · 확인 실패)
            run = progress.fetch_run(
                (pending.get("args") or {}).get("take_id"), applied.ok, self.ctx
            )
            progress.attach(self.st.plan_meta, run, None, max_steps=self.cfg.plan_steps_max)
            self._emit_plan_display()
        self.st.executed.append(rec)
        if applied.card is not None:
            self.st.cards.append(applied.card)
        if applied.scope_changed:
            self.scope_changed = True
        if applied.ok:
            # 다른 행동이 성공하면 '연속' 실패가 끊긴다(5-8: 같은 실패 행동 · 인자가 연속 2회면 멈춤)
            self.st.failed_action_key, self.st.failed_action_count = None, 0
        if not applied.ok and not applied.mismatch:
            key = _action_key(pending.get("_tool_name"), pending.get("_tool_args"))
            if key == self.st.failed_action_key:
                self.st.failed_action_count += 1
            else:
                self.st.failed_action_key, self.st.failed_action_count = key, 1
        self.st.pending_action = None
        return applied

    def _tool_msg_replace(self, call_id: str | None, content: dict[str, Any]) -> dict[str, Any]:
        """행동 도구 호출에 대한 결과 메시지. 멈출 때 넣어 둔 '요청함' 메시지 뒤에 실제 결과를 덧붙인다."""
        return {
            "role": "user",
            "content": "<action_result>"
            + json.dumps({"tool_call_id": call_id, **content}, ensure_ascii=False)
            + "</action_result>",
        }

    # ── 3. 답 쓰기 · 검사 ────────────────────────────────────────────
    def answer_phase(
        self,
        *,
        route: str,
        pending: Any = None,
        sink: TextSink | None = None,
        already_said: list[str] | None = None,
    ) -> dict[str, Any]:
        """답 초안 호출(구조화). 돌려주는 값: `{failed, refused, llm_error}` 또는 `{failed: False, draft, messages}`.
        판단 확정 · 문장 만들기 · 검사 · 고치기는 answer.py 가 한다.

        - `pending`: 승인 대기로 멈추기 전 답을 쓸 때의 `ASK` 행동. 모델에게 그 행동이 아직 실행되지 않았음을 알린다.
        - `sink`: 응답 조각을 받아 검사를 통과한 문장부터 문장 줄로 내는 쪽(answer.py `Composer`, 5-4).
        - `already_said`: 모델에게 알릴 '이미 보인 문장'. 없으면 서명 상태의 낸 문장(이 답 직전에 코드가 낸 앞 문장은 넣지 않는다).
        """
        research = [
            {"tool": t["tool"], "args": t["args_summary"], "result": t["result_summary"]}
            for t in self.st.trace
        ]
        tool_views = [json.loads(m["content"]) for m in self.st.messages if m.get("role") == "tool"]
        action_views = [m["content"] for m in self.st.messages if m.get("role") == "user"]
        table = self._evidence_table()
        research_block = {
            "trace": research,
            "results": tool_views[-20:],
            "actions": action_views[-5:],
            "drafts": self.st.drafts,
            # 맥락 구성(6-1 3번): 이번 턴에 읽은 근거 값 표와 질문별로 맞는 근거. 모델이 숫자 · 대상을 이 표에서 고르게 한다
            "evidence_table": table,
            "questions": self._question_checklist(table),
        }
        said = already_said if already_said is not None else [c["text"] for c in self.st.emitted]
        if said:
            # 이미 사용자에게 보인 문장: 같은 내용을 되풀이하지 않고 이어질 내용만 쓰게 한다
            research_block["already_said"] = said
        if pending is not None:
            research_block["pending_action"] = {"type": pending.type}
        msgs = [
            self._system(prompts.ANSWER),
            # 코칭 프로필은 답 쓰기 · 고치기 호출의 데이터 칸에만(고치기는 이 메시지를 그대로 쓴다, 6-14)
            prompts.data_message(self.data_block(profile=True)),
            {
                "role": "user",
                "content": "<research>\n"
                + json.dumps(research_block, ensure_ascii=False, separators=(",", ":"))
                + "\n</research>",
            },
        ]
        cands = self._guide_candidates() if self.cfg.answer_guide_candidates else []
        if cands:
            # 문장 후보는 맨 끝에 둔다(긴 입력의 가운데에 묻히지 않게). 고치기는 이 메시지를 빼고 걸린 문장에 이은 카드의 후보만 준다
            msgs.append(
                {
                    "role": "user",
                    "content": GUIDE_CANDIDATES_TAG
                    + json.dumps(cands, ensure_ascii=False, separators=(",", ":"))
                    + "\n</guide_candidates>",
                }
            )
        res = self._call("ANSWER", msgs, schema=prompts.ANSWER_SCHEMA, sink=sink)
        if not isinstance(res, LlmResult) or res.parsed is None:
            refused = isinstance(res, LlmRefused)
            return {
                "failed": True,
                "refused": refused,
                "llm_error": isinstance(res, LlmError) and not refused,
            }
        return {"failed": False, "draft": res.parsed, "messages": msgs}

    def _evidence_table(self) -> list[dict[str, Any]]:
        """이번 턴에 읽은 최신 기록 · 화면 근거를 한 줄씩(최대 40줄): id · Take · 장 · 무엇(지표 · 문제 이름) · 화면 표기 값."""
        reg = self.ctx.registry
        rows = [self._evidence_row(eid) for eid in reg.items]
        return [r for r in rows if r is not None][-40:]

    def _evidence_row(self, eid: str) -> dict[str, Any] | None:
        """근거 표의 한 줄(id · Take · 장 · 무엇 · 화면 표기 값). 지금 판이 아니거나 코드가 계산한 값이면 None."""
        reg = self.ctx.registry
        ev = reg.items.get(eid)
        if ev is None or not reg.is_current(eid) or ev.origin == "COMPUTED":
            return None
        if ev.metric:
            what = compose.metric_label(ev.metric)
        elif ev.path.endswith(".issue_type") and isinstance(ev.value, str):
            what = "문제 구간: " + clm.ISSUE_LABEL.get(ev.value, ev.value)
        else:
            # Take 수 · 리뷰 상태 · 대화 목표 · 영역별 문제 구간 수('없음'도 근거다, 6-8)는 읽을 수 있는 이름으로
            what = compose.evidence_what(ev) or ev.path.rsplit(".", 1)[-1]
        shown = compose.value_display(self.ctx, ev)
        return {
            "id": eid,
            "take": ev.take_number,
            "slide": ev.slide_number,
            "what": what,
            # 화면 표기를 만들 수 없는 값(지표가 없는 화면 숫자 등)은 저장된 값 그대로 · 단위와 함께 보인다(값이 없다고 읽히지 않게)
            "value": shown if shown is not None else ev.value,
            **({"unit": ev.unit} if shown is None and ev.unit else {}),
            **({"saved": ev.saved} if ev.origin == "SCREEN" else {}),
        }

    def _card_sentences(self, source_id: str) -> list[dict[str, str]]:
        """자료 카드의 문장을 원문 그대로 한 줄씩(`{source_id, title, sentence}`). 카드가 없으면 빈 목록."""
        card = getattr(self.deps.corpus, "get", lambda _s: None)(source_id)
        if card is None:
            return []
        return [
            {"source_id": source_id, "title": card.title, "sentence": card.text[s:e].strip()}
            for s, e in sentence_spans(card.text)
            if card.text[s:e].strip()
        ]

    def shown_quotes(self) -> dict[str, str]:
        """이 실행에서 이미 보인 자료 카드의 보이는 인용(자료 id → quote): 앞 응답이 낸 카드(서명 상태) + 이 응답의 문장 줄이 보낸 카드.
        한 카드는 한 응답에서 한 번만 보내고 인용도 그때 정해지므로(answer.py `ClaimLines`), 뒤 문장은 이 문장만 인용할 수 있다."""
        cards = [*self.st.emitted_sources, *(self.lines.sent_cards.values() if self.lines else [])]
        return {g["source_id"]: g["quote"] for g in cards if g.get("source_id") and g.get("quote")}

    def _guide_candidates(self) -> list[dict[str, str]]:
        """답 쓰기에 줄 자료 문장 후보: 이 실행의 자료 검색이 돌려준 카드(찾은 순서, 중복 없음)의 문장을 원문 그대로.

        카드는 통째로만 싣고 합이 `max_guide_candidates` 를 넘기 전에 멈춘다(첫 카드가 혼자 넘으면 앞에서부터 자른다).
        검색 결과는 도구 캐시(이어 불러도 남는다)에서 읽는다.
        """
        order: list[str] = []
        for k, v in self.ctx.tool_cache.items():
            if json.loads(k).get("tool") != "search_guides" or not v.get("ok"):
                continue
            order.extend(s for s in v.get("source_ids") or [] if s not in order)
        out: list[dict[str, str]] = []
        cap = self.cfg.max_guide_candidates
        shown = self.shown_quotes()
        for sid in order:
            sents = self._card_sentences(sid)
            if sid in shown:
                # 이미 보인 카드는 보인 인용 문장만 후보로 준다(한 카드는 한 문장만 인용, answer.py 인용 고정)
                only = [x for x in sents if x["sentence"][:300] == shown[sid][:300]]
                sents = only or sents
            if out and len(out) + len(sents) > cap:
                break
            out.extend(sents)
            if len(out) >= cap:
                break
        return out[:cap]

    def _fix_materials(self, claim: dict[str, Any]) -> dict[str, Any]:
        """고치기에 걸린 문장 하나에 더해 줄 자료: 그 문장에 이미 이은 근거의 화면 표기 값, GUIDE 문장이면 이미 이은 카드의 문장 후보.
        새 근거 · 자료 id 는 주지 않는다(문장에 이어 있지 않은 것은 담지 않는다)."""
        extra: dict[str, Any] = {}
        rows = [r for r in (self._evidence_row(e) for e in claim.get("evidence_ids") or []) if r]
        if rows:
            extra["values"] = rows
        if claim.get("kind") == "GUIDE":
            sents = [
                {"source": x["source_id"], "sentence": x["sentence"]}
                for sid in claim.get("source_ids") or []
                for x in self._card_sentences(sid)
            ]
            if sents:
                extra["card_sentences"] = sents[: self.cfg.max_guide_candidates]
        return extra

    def _evidence_metrics(self, eid: str) -> set[str]:
        """근거가 말하는 지표: 지표 값은 그 지표, 문제 구간은 그 문제의 지표(`issue_metrics`), 차원 상태는 그 차원의 지표."""
        ev = self.ctx.registry.get(eid)
        if ev is None:
            return set()
        if ev.metric:
            return {ev.metric}
        if ev.path.endswith(".issue_type") and isinstance(ev.value, str):
            return set(self.cfg.issue_metrics.get(ev.value, ()))
        if (area := compose.segment_area(ev)) is not None:
            # 영역의 문제 구간 0곳은 그 영역 지표들의 질문에 답한다('2장 시선 문제 구간' → 시선 영역 0곳)
            return {m for m, a in METRIC_AREA.items() if a == area}
        dim = ev.path.rsplit(".dimensions.", 1)[-1] if ".dimensions." in ev.path else None
        return set(self.cfg.dimensions[dim].metrics) if dim in self.cfg.dimensions else set()

    def _question_checklist(self, table: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """판단 초안(기록 질문 · 주장)마다 그 대상(Take · 장 · 지표 · 차원)에 맞는 근거 id. 답이 질문을 빠뜨리지 않게 한다."""
        out: list[dict[str, Any]] = []
        num_of = {tid: s.take_number for tid, s in self.ctx.summaries.items()}
        for d in self.st.drafts:
            if d.get("type") not in ("MEASURED", "REQUEST"):
                continue
            sub = d.get("subject") or {}
            takes = set(sub.get("take_numbers") or []) | {
                num_of[t] for t in sub.get("take_ids") or [] if t in num_of
            }
            slide = sub.get("slide_number")
            metrics: set[str] = set()
            if sub.get("metric"):
                metrics.add(sub["metric"])
            elif sub.get("dimension") in self.cfg.dimensions:
                metrics |= set(self.cfg.dimensions[sub["dimension"]].metrics)
            if not (takes or slide or metrics):
                continue
            if not takes:
                target = self._judged_take()
                if target in num_of:
                    takes = {num_of[target]}
            ids = [
                r["id"]
                for r in table
                if (not takes or r["take"] in takes)
                and (slide is None or r["slide"] == slide)
                and (not metrics or self._evidence_metrics(r["id"]) & metrics)
            ]
            out.append({"quote": d.get("quote"), "evidence_ids": ids[:8]})
        return out

    def fix_call(
        self,
        msgs: list[dict[str, Any]],
        draft_claims: list[dict[str, Any]],
        failures: list[Any],
        fix_for: list[int] | None = None,
    ) -> dict[int, str | None] | None:
        """걸린 문장만 한 번 고치게 한다(6-9). 실패하면 None(원본에서 통과한 문장만 남긴다)."""
        items = [
            {
                "index": f.claim_index,
                "text": draft_claims[f.claim_index]["text"],
                "check": f.check_id,
                "reason": f.reason,
                **(
                    self._fix_materials(draft_claims[f.claim_index])
                    if self.cfg.fix_materials
                    else {}
                ),
            }
            for f in failures
            if f.claim_index is not None and f.claim_index < len(draft_claims)
        ]
        if not items:
            return None
        fix_msgs = [
            self._system(prompts.FIX),
            # 답 쓰기의 문장 후보(전체 카드)는 고치기에 주지 않는다. 걸린 문장에 이은 카드의 후보만 문장 옆에 붙는다
            *(
                m
                for m in msgs[1:]
                if not str(m.get("content", "")).startswith(GUIDE_CANDIDATES_TAG)
            ),
            {
                "role": "user",
                "content": "<fix>\n" + json.dumps(items, ensure_ascii=False) + "\n</fix>",
            },
        ]
        res = self._call(
            "FIX", fix_msgs, schema=prompts.FIX_SCHEMA, max_attempts=1, fix_for=fix_for
        )
        if not isinstance(res, LlmResult) or res.parsed is None:
            return None
        out: dict[int, str | None] = {}
        for c in res.parsed.get("claims") or []:
            if isinstance(c, dict) and isinstance(c.get("index"), int):
                t = c.get("text")
                out[c["index"]] = t if isinstance(t, str) and t.strip() else None
        return out

    # ── 4. 대화 요약 (6-14) ──────────────────────────────────────────
    def summary_phase(self) -> list[Any] | None:
        """요청에 `history.overflow`가 있으면 '앞 요약 + 밀린 메시지'를 새 요약 항목으로 만든다(굴리는 요약, 모델 1번).

        답 쓰기 · 고치기가 끝난 뒤에만 부르고, 모델 호출 · 시간(호출 한 번 + 끝 줄 몫) · 출력이 남을 때만 보낸다
        (`Budget.summary_room`). 재시도 없음. 못 만들면 None(BE 는 다음 턴에 같은 메시지를 다시 보낸다). 거르기는 호출자가
        memory.check_summary_items 로 한다. 프로필은 주지 않는다.
        """
        ov = self.req.history.overflow
        if ov is None or self.cancelled() or not self.budget.summary_room():
            return None
        prev = self.req.history.summary or []
        block = {
            "prev_summary": [{"kind": s.kind, "text": s.text} for s in prev],
            # 밀린 메시지는 자르지 않고 그대로 준다(BE 는 보낸 메시지 전체를 덮은 것으로 기록한다). 입력이 한도를 넘으면
            # 예약에서 막혀 요약을 만들지 않고(null) 다음 턴에 다시 보낸다
            "overflow": [{"role": m.role, "text": m.text} for m in ov.messages],
        }
        msgs = [self._system(prompts.HISTORY_SUMMARY), prompts.data_message(block)]
        res = self._call(
            "HISTORY_SUMMARY", msgs, schema=prompts.HISTORY_SUMMARY_SCHEMA, max_attempts=1
        )
        if not isinstance(res, LlmResult) or res.parsed is None:
            return None
        items = res.parsed.get("items")
        return items if isinstance(items, list) else None


#: 답 쓰기에 주는 자료 문장 후보 메시지의 머리 표시(고치기가 이 메시지를 빼는 데도 쓴다)
GUIDE_CANDIDATES_TAG = "<guide_candidates>\n"


def _script_points_removed(card: dict[str, Any] | None) -> bool:
    if not card or card.get("type") != "REASSESS_RESULT":
        return False
    before_card, after_card = card.get("before") or {}, card.get("after") or {}
    before_score, after_score = before_card.get("score"), after_card.get("score")
    # 원인을 확인한다: 대본 허용으로 뒤(after, 6-6 의 B)에서 대본 차원이 '평가하지 않음'으로 빠지고 점수가 올랐을 때만
    before_dim = (before_card.get("dimensions") or {}).get("script_independence")
    after_dim = (after_card.get("dimensions") or {}).get("script_independence")
    return (
        (card.get("change") or {}).get("script_used") is True
        and after_dim == "NOT_EVALUABLE"
        and before_dim != "NOT_EVALUABLE"
        and isinstance(before_score, int)
        and isinstance(after_score, int)
        and after_score > before_score
    )


def _alternating(keys: list[str]) -> bool:
    """최근 네 요청이 A-B-A-B(첫째 = 셋째, 둘째 = 넷째, 두 요청은 서로 다름)인가(5-7 '호출 제한')."""
    if len(keys) < 4:
        return False
    a, b, c, d = keys[-4:]
    return a == c and b == d and a != b


def _action_key(name: Any, args: Any) -> str:
    """같은 실패 행동인지 가리는 열쇠: 행동 이름과 인자(모델이 쓰는 설명 reason 은 뺀다, 5-8 · L2-31)."""
    a = {k: v for k, v in args.items() if k != "reason"} if isinstance(args, dict) else args
    return json.dumps([name, a], ensure_ascii=False, sort_keys=True, default=str)


def _assessment_sources(a: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for rec in (a.get("evidence_basis") or {}).get("records", []):
        out.append(rec.get("pitch_id"))
        out.extend(t.get("take_id") for t in rec.get("takes", []))
    return [x for x in out if x]


#: 읽기만 하는 가져오기 행동(대상이 지워지면 실패 결과로 이어 간다, L2-22)
_FETCH_ACTIONS = ("FETCH_TAKE", "FETCH_PITCH")

#: 범위 피치의 Take 를 실제로 읽는 도구. 범위를 바꾼 뒤 이 도구로 그 피치 Take 를 읽어야 다시 바꿀 수 있다(6-5).
#: list_pitches 는 피치 목록만 보여 주므로 넣지 않는다(FETCH_TAKE 성공은 행동 결과에서 따로 센다)
_TAKE_READ_TOOLS = frozenset({"list_takes", "get_take_report", "compare_takes"})


def _action_targets(action: dict[str, Any]) -> list[str]:
    args = action.get("args") or {}
    out = [args.get("pitch_id"), args.get("take_id")]
    goal = args.get("goal") or {}
    if isinstance(goal, dict):
        out.append((goal.get("base") or {}).get("take_id"))
    return [x for x in out if x]


def meta(
    *,
    model: str | None,
    prompt_version: str,
    corpus_version: str,
    cfg: ChatAgentConfig,
    extra: dict[str, str] | None = None,
) -> dict[str, Any]:
    cv = {"chat_agent": cfg.criteria_version()}
    cv.update(extra or {})
    return {
        "schema_version": SCHEMA_VERSION,
        "feature_version": FEATURE_VERSION,
        "model": model,
        "prompt_version": prompt_version,
        "corpus_version": corpus_version,
        "criteria_versions": cv,
    }


__all__ = [
    "Loop",
    "LoopState",
    "LoopOutcome",
    "TurnDeps",
    "meta",
    "DEFAULT_PLAN",
    "Assessment",
    "Claim",
    "GuideCard",
    "judge_goal",
    "chk",
    "templates",
    "TOOL_SCHEMA_VERSION",
]
