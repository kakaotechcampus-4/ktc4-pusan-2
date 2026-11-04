"""`/agent/turn` 한 번 — 스트림 전 검사 → 실행 → 진행 줄 · 끝 줄 (기능 문서 5-3 · 5-4 · 5-5 · 5-6 · 6-9 · 6-12 · 7-3 · 8-3).

- 누가 쓰나: 서비스 계층(연구 모듈은 chat_agent_lab/app.py, 탑재 뒤 api/chat_agent.py)이 요청 본문 바이트와 `TurnDeps`를 넘긴다.
  끄기 스위치 · 동시 실행 자리(`AGENT_BUSY`)는 서비스 계층이 먼저 보고, 이 모듈은 기능 몫(요청 · 서명 · 기본 자료 크기 · 키 길이)을 본다.
- 돌려주는 것: `TurnResponse` — 스트림 전 오류(`PreStreamError`: HTTP 상태 · 코드 · 메시지)이거나, NDJSON 줄을 내는 반복자.
- 스트림 전 검사 순서(걸리면 모델 · 도구 · 행동 0회, P1): 요청 본문 크기(2 MiB, 413) → UTF-8 · JSON(422) · 깊이(32, 413) →
  요청 모양(422) → 서명 키(32바이트 미만이면 503 `AGENT_DISABLED`) → 모델 준비(503 `LLM_UNAVAILABLE`, 자동 목표 확인은 모델 없이 됨) →
  기본 자료 크기(512 KiB, 413) → (이어 부르기) 서명 · 판 · 식별자 검사(409) → 대기 행동과 결과의 짝(409) → 누적 사용량이 줄지 않았는지(409).
- 실행 흐름(`_orchestrate`): (이어 부르기면 먼저 지워진 원천 처리 · 행동 결과 검증 · 반영) → 호출 횟수 · 시작 시간 한도 → 자동 목표 확인 / 행동 결과 반영 / 고른 제안 저장 / 계획 → 조사 →
  행동이면 NEEDS_ACTION(서명), 아니면 답 쓰기 → 판단 확정 → 문장 만들기 → 답 검사 · 한 번 고치기 → 최종 출력.
- 문장 줄(5-4 · 6-1 5번): 답 쓰기 전에 정해진 코드 문장(행동 결과 · 안내)을 먼저 내고, 답 초안이 흘러오는 동안 검사를 통과한
  문장부터 `claim` 줄로 낸다(answer.py). 낸 문장은 서명 상태의 `emitted`에 쌓이고, `_finish`가 어떤 끝에서도 message 의
  앞부분에 그대로 싣는다. 끝 줄을 만들고 나면 아직 줄로 내지 않은 문장을 끝 줄 앞에 마저 낸다.
- 승인(6-1 4번 · 6-2): `ASK` 행동(`SAVE_GOAL` · `REASSESS`)이면 멈추기 전에 `_ask_pause`가 코드가 그린 확인 카드(`ASK_CONFIRM`)를 만들고
  그때까지 확인한 것으로 답 문장을 쓰고 검사(answer.py, 답 쓰기 · 고치기 몫)한 뒤 message = 문장 + 카드, actions = 그 행동 하나로 멈춘다.
  고른 제안(`choice`)의 저장은 `preapproved`를 달고 카드 · 승인 대기 없이 낸다. 이미 낸 문장은 `_finish`가 서명 상태(`emitted`)에 담고,
  이어 부른 호출의 어떤 끝에서도 message 의 앞부분에 그대로 싣는다(그 뒤에 행동 결과 문장 · 나머지 답). 모델 없이 끝나는 길(`_end_without_model`)도
  앞 문장 · 승인한 행동의 결과 문장 · 카드를 먼저 조립하고, 상태가 한도를 넘어 못 서명할 때도 앞 문장의 자료 카드 사본과 이미 실행된 행동의 결과 문장 · 카드를 남기고 새 승인 행동만 버린다.
- 범위 밖(1-3): 계획 · 답 초안이 `OUT_OF_SCOPE`면 `scope_reason`별 고정 문장(templates.py)으로 `REFUSED`, 섞인 요청은 초안의
  `scope_notes` 이유별 고정 문장을 답 문장 뒤에 덧붙여 `DONE`. `SCOPE_RULE`(범위 밖 낱말)에 걸린 모델 문장은 고치기에 보내지 않고 버린다.
- 줄 쓰기(5-4): 이벤트마다 `allow_nan=False`로 완전히 직렬화하고 LF 를 붙인다. 한 줄 2 MiB, 누적 16 MiB(끝 줄 몫 4 KiB 예약).
  `final`이 너무 크면 trace 상세 → 진단 기록 순으로 줄이고, 그래도 크면 `AGENT_OUTPUT_TOO_LARGE` error 줄로 끝낸다. 끝 줄은 정확히 하나다.
- 연결이 끊기면(반복자를 닫으면) 새 모델 · 도구 호출을 하지 않는다(L1-08).
- 고칠 때: 화면 문구는 templates.py, 한도 숫자는 config.py, 검사 규칙은 checks.py, 루프 단계는 loop.py.
"""

from __future__ import annotations

import json
import queue
import threading
import traceback
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePath as Path
from typing import Any

from pydantic import ValidationError

from . import actions as act
from . import checks as chk
from . import claims as clm
from . import compose, memory, prompts, state, templates
from .answer import ClaimLines, Composer, is_general_intro, with_prefix
from .answer import assessment_head as _assessment_head  # noqa: F401  # 시험이 이 이름으로 부른다
from .answer import guide_texts as _guide_texts
from .budget import LIMIT_END_REASONS, Counters
from .diagnostics import DiagRecorder, reduce_to_fit, safe_request_id
from .evidence import canonical_json
from .goals import judge_goal
from .loop import Loop, LoopState, TurnDeps, meta
from .schemas import Action, Assessment, Claim, Diagnostics, FinalData, TurnRequest
from .version import FEATURE_VERSION, TOOL_SCHEMA_VERSION

_Result = tuple[dict[str, Any] | None, str | None, dict[str, Any] | None]


@dataclass
class PreStreamError:
    status: int
    code: str
    message: str

    def body(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message}}


@dataclass
class TurnResponse:
    error: PreStreamError | None = None
    lines: Iterator[str] | None = None


def _err(status: int, code: str) -> TurnResponse:
    return TurnResponse(
        error=PreStreamError(status, code, templates.ERROR_MESSAGES.get(code, code))
    )


def _reject_constant(_: str) -> Any:
    raise ValueError("NaN · Infinity 는 JSON 이 아니다")


def handle_turn(
    body: bytes,
    deps: TurnDeps,
    *,
    secret: bytes | None,
    previous_secret: bytes | None = None,
    request_id: str | None = None,
) -> TurnResponse:
    """요청 본문 하나를 처리한다. 스트림 전 오류면 `error`, 아니면 `lines`(NDJSON 줄 반복자)."""
    cfg = deps.cfg
    if len(body) > cfg.max_request_bytes:
        return _err(413, "AGENT_CONTEXT_TOO_LARGE")
    try:
        raw = json.loads(body.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError):
        return _err(422, "VALIDATION_ERROR")
    if not state.json_depth_ok(raw, cfg.max_json_depth):
        return _err(413, "AGENT_CONTEXT_TOO_LARGE")
    try:
        req = TurnRequest.model_validate(raw)
    except ValidationError:
        return _err(422, "VALIDATION_ERROR")
    if secret is None or len(secret) < cfg.min_secret_bytes:
        # 켜졌는데 서명 키가 없거나 짧으면 대화 경로만 끈다(11-1)
        return _err(503, "AGENT_DISABLED")
    if deps.llm is None and req.run.trigger != "TAKE_REVIEWED":
        return _err(503, "LLM_UNAVAILABLE")
    if len(canonical_json(raw.get("context")).encode("utf-8")) > cfg.max_context_bytes:
        return _err(413, "AGENT_CONTEXT_TOO_LARGE")

    payload: state.StatePayload | None = None
    loop_state = LoopState()
    counters = Counters()
    scope = req.run.scope_pitch_id
    original = req.user_message
    turn_count = 1
    corpus_ok = True
    used = req.budget_used.model_dump()
    if req.run.trigger == "ACTION_RESULT":
        assert req.resume is not None
        try:
            payload = state.verify(
                req.resume.state,
                secret=secret,
                previous_secret=previous_secret,
                now_ms=deps.clock_ms(),
                current_prompt_version=prompts.PROMPT_VERSION,
                expected_ids={
                    "run_id": req.run.run_id,
                    "conversation_id": req.run.conversation_id,
                    "user_id": req.run.user_id,
                    "state_revision": req.run.state_revision,
                },
                cfg=cfg,
            )
        except state.StateInvalid:
            return _err(409, "AGENT_STATE_INVALID")
        loop_state = LoopState.load(payload.loop)
        try:
            act.match_result(loop_state.pending_action, req.resume.action_results)
        except act.ResultMismatch:
            return _err(409, "AGENT_STATE_INVALID")
        counters = Counters.load(loop_state.counters)
        for k, v in used.items():
            if v < getattr(counters, k):
                # 누적 사용량은 줄 수 없다(재개로 한도를 우회하지 못하게, L1-31). 크면 큰 값으로 합친다(5-3)
                return _err(409, "AGENT_STATE_INVALID")
            setattr(counters, k, v)
        scope = payload.scope_pitch_id
        original = payload.original_user_message
        turn_count = payload.turn_count + 1
        corpus_ok = payload.versions.corpus == deps.corpus_version
    else:
        for k, v in used.items():
            setattr(counters, k, v)
    counters.turn_count = turn_count

    diag = DiagRecorder(
        request_id=safe_request_id(request_id, run_id=req.run.run_id, turn_call=turn_count),
        run_id=req.run.run_id,
        turn_call=turn_count,
        versions={
            "feature_version": FEATURE_VERSION,
            "prompt_version": prompts.PROMPT_VERSION,
            "tool_schema": TOOL_SCHEMA_VERSION,
            "corpus_version": deps.corpus_version,
        },
        monotonic=deps.monotonic,
        log=deps.log,
    )
    lp = Loop(
        req,
        deps,
        state=loop_state,
        original_message=original,
        scope_pitch_id=scope,
        counters=counters,
        diag=diag,
        emit=lambda _e, _d: None,
        corpus_ok=corpus_ok,
    )
    return TurnResponse(lines=_stream(lp, req, deps, payload=payload, secret=secret))


# ── 줄 쓰기 ────────────────────────────────────────────────────────────────


class _OutputTooLarge(Exception):
    pass


class _Writer:
    """NDJSON 줄 쓰기와 크기 한도(5-4)."""

    def __init__(self, run_id: str, cfg: Any) -> None:
        self.run_id = run_id
        self.cfg = cfg
        self.seq = 0
        self.total = 0

    def _encode(self, event: str, data: dict[str, Any]) -> str:
        body = {"event": event, "run_id": self.run_id, "seq": self.seq + 1, "data": data}
        return json.dumps(body, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"

    def line(self, event: str, data: dict[str, Any], *, terminal: bool = False) -> str:
        try:
            out = self._encode(event, data)
        except ValueError as e:  # NaN 등 JSON 이 아닌 값
            raise _OutputTooLarge() from e
        n = len(out.encode("utf-8"))
        budget = self.cfg.max_response_bytes - (0 if terminal else self.cfg.tail_reserve_bytes)
        if n > self.cfg.max_line_bytes or self.total + n > budget:
            raise _OutputTooLarge()
        self.seq += 1
        self.total += n
        return out

    def error_line(self, code: str, diagnostics: dict[str, Any] | None) -> str:
        data: dict[str, Any] = {
            "error": {"code": code, "message": templates.ERROR_MESSAGES.get(code, code)}
        }
        if diagnostics is not None:
            data["diagnostics"] = diagnostics
        try:
            return self.line("error", data, terminal=True)
        except _OutputTooLarge:
            data.pop("diagnostics", None)
            out = self._encode("error", data)  # 끝 줄 몫(4 KiB)에 들어가는 작은 줄
            self.seq += 1
            return out


def _stream(
    lp: Loop, req: TurnRequest, deps: TurnDeps, *, payload: Any, secret: bytes
) -> Iterator[str]:
    """루프를 다른 스레드에서 돌리고 진행 줄을 바로 내보낸다. 반복자를 닫으면 새 호출을 멈춘다."""
    q: queue.Queue[tuple[str, Any]] = queue.Queue()
    cancelled = threading.Event()
    writer = _Writer(req.run.run_id, deps.cfg)
    lp.emit = lambda event, data: q.put(("progress", (event, data)))
    lp.cancelled = cancelled.is_set

    def work() -> None:
        try:
            q.put(("end", _orchestrate(lp, req, deps, payload=payload, secret=secret)))
        except Exception as exc:  # noqa: BLE001  # 반복자 안에서 잡아 error 한 줄로 끝낸다(L1-08)
            # 로그에는 예외 종류와 마지막 위치(파일:줄 · 함수)만 남긴다. 예외 메시지는 사용자 글을 담을 수 있어 남기지 않는다
            tb = traceback.extract_tb(exc.__traceback__)
            where = [f"{Path(f.filename).name}:{f.lineno} {f.name}" for f in tb[-3:]]
            q.put(("crash", {"cause": type(exc).__name__, "where": where}))

    threading.Thread(target=work, name="chat-agent-turn", daemon=True).start()
    try:
        while True:
            kind, item = q.get()
            if kind == "progress":
                event, data = item
                try:
                    yield writer.line(event, data)
                except _OutputTooLarge:
                    cancelled.set()
                    yield writer.error_line("AGENT_OUTPUT_TOO_LARGE", None)
                    return
            elif kind == "end":
                final, err, diag_json = item
                yield (
                    writer.error_line(err, diag_json)
                    if err is not None
                    else _final_line(writer, final)
                )
                return
            else:
                deps.log({"event": "agent_crash", "run_id": req.run.run_id, **item})
                yield writer.error_line("INTERNAL_ERROR", None)
                return
    finally:
        cancelled.set()


def _final_line(writer: _Writer, final: dict[str, Any]) -> str:
    """final 줄. 너무 크면 trace 상세 → 진단 기록 순으로 줄이고, 그래도 크면 AGENT_OUTPUT_TOO_LARGE."""
    try:
        return writer.line("final", final, terminal=True)
    except _OutputTooLarge:
        pass
    slim = dict(final)
    slim["trace"] = [
        {**t, "args_summary": t["tool"], "result_summary": t["outcome"]}
        for t in final.get("trace", [])
    ]
    try:
        return writer.line("final", slim, terminal=True)
    except _OutputTooLarge:
        pass
    diag = slim.get("diagnostics")
    slim["diagnostics"] = None
    if diag is not None:
        # 진단 기록은 남은 자리에 맞춰 11-5 순서로 줄이고 실제 줄 크기로 확인한다(통째로 빼는 것은 마지막)
        full = Diagnostics.model_validate(diag)
        bare = len(writer._encode("final", slim).encode("utf-8"))
        room = min(writer.cfg.max_line_bytes, writer.cfg.max_response_bytes - writer.total) - bare
        for _ in range(6):
            if room <= 0:
                break
            slim["diagnostics"] = reduce_to_fit(full, room).model_dump(mode="json")
            try:
                return writer.line("final", slim, terminal=True)
            except _OutputTooLarge:
                room = int(room * 0.8)
        slim["diagnostics"] = None
    try:
        return writer.line("final", slim, terminal=True)
    except _OutputTooLarge:
        return writer.error_line("AGENT_OUTPUT_TOO_LARGE", None)


# ── 실행 흐름 ──────────────────────────────────────────────────────────────


def _orchestrate(
    lp: Loop, req: TurnRequest, deps: TurnDeps, *, payload: Any, secret: bytes
) -> _Result:
    """돌려주는 값: (final 데이터, error 코드, error 줄에 실을 진단 기록)."""
    cfg = deps.cfg
    budget = lp.budget
    # 문장 줄 번호 · 이미 보낸 근거 사본은 서명 상태의 낸 문장에서 잇는다(5-4). 지워진 원천 처리보다 먼저 만든다
    lp.lines = ClaimLines(lp)

    def finish(**kw: Any) -> _Result:
        return _finish(lp, req, deps, secret=secret, payload=payload, **kw)

    trigger = req.run.trigger
    if trigger == "ACTION_RESULT":
        # 이어 부를 때는 어떤 한도 종료보다 먼저 지워진 원천을 처리하고 행동 결과를 검증 · 반영한다.
        # 한도로 모델 없이 끝나도 승인된 행동의 결과 문장 · 카드와 삭제 중단이 지워지지 않게 하려는 것이다(5-5 · 6-1)
        if not lp.drop_removed(req.context.removed_sources):
            return finish(
                status="PARTIAL", reason="OTHER", system=[templates.SystemText.SOURCE_DELETED_STOP]
            )
        assert req.resume is not None
        applied = lp.apply_action_result(req.resume.action_results[0])
        if applied.mismatch:
            return finish(
                status="PARTIAL", reason="OTHER", system=[templates.SystemText.ACTION_MISMATCH]
            )

    # 7번째 호출 · 시작부터 시간 없음: 모델 없이 끝낸다(8-3 · L1-04 · L1-31)
    hit = budget.turn_limit_hit()
    if hit is not None:
        lp._limit(hit)
        if trigger == "ACTION_RESULT":
            return _end_without_model(
                lp,
                finish,
                "LIMIT",
                notes=[*_action_notes(lp), templates.SystemText.TURN_LIMIT.value],
            )
        return finish(status="PARTIAL", reason="LIMIT", system=[templates.SystemText.TURN_LIMIT])
    if budget.exhausted_at_start():
        lp._limit(budget._hit("TURN_TIME", lp.counters.elapsed_ms, int(cfg.turn_seconds * 1000)))
        return _end_without_model(lp, finish, "TIME", notes=_action_notes(lp))

    if trigger == "TAKE_REVIEWED":
        return _take_reviewed(lp, req, finish)

    if trigger == "ACTION_RESULT":
        if budget.answer_slots_short():
            return _slots_short_end(lp, finish)
    else:
        if req.choice is None and budget.answer_slots_short():
            return _slots_short_end(lp, finish)
        if req.choice is not None:
            # 제안 카드에서 고른 목표: 내용 · 근거가 그대로면 다시 따지지 않고 저장한다. 선택이 허용 근거다(6-3 '제안을 고른 뒤')
            action = _choice_action(lp, req)
            if action is not None:
                return _needs_action(
                    lp, req, action=action, call_id=None, tool_name="save_goal", finish=finish
                )
        route = lp.plan_phase()
        if route == "OUT_OF_SCOPE":
            return finish(
                status="REFUSED",
                reason="OUT_OF_SCOPE",
                system=[templates.scope_text(lp.scope_reason)],
            )
        if route == "ANSWER_DIRECT":
            lp.st.research_done = True

    stop = lp.research_phase() if not lp.st.research_done else None
    if lp.cancelled():
        return finish(error="INTERNAL_ERROR", reason="CLIENT_DISCONNECTED")
    if stop is not None:
        return _needs_action(
            lp,
            req,
            action=stop["action"],
            call_id=stop["call_id"],
            tool_name=stop.get("tool_name"),
            tool_args=stop.get("tool_args"),
            finish=finish,
        )
    return _answer(lp, req, finish)


def _choice_action(lp: Loop, req: TurnRequest) -> Action | None:
    ok, goal = act.proposal_still_valid(lp.ctx)
    if not ok or goal is None:
        return None  # 바뀌었으면 계획부터 다시 해 새 제안을 낸다
    if lp.budget.reserve_action("SAVE_GOAL") is not None:
        return None
    lp.st.action_seq += 1
    sp = req.context.selected_proposal
    assert sp is not None  # proposal_still_valid 가 True 면 고른 제안이 있다
    # 고른 클릭이 승인이다: 확인 카드 · 승인 대기 없이 `preapproved` 를 달고 낸다. BE 가 내용 해시를 다시 계산해 바로 실행한다(6-2)
    return act.make_action(
        run_id=req.run.run_id,
        seq=lp.st.action_seq,
        action_type="SAVE_GOAL",
        args={"expected_goal_version": lp.ctx.goal_version, "goal": goal.model_dump(mode="json")},
        reason="고른 목표 제안 저장",
        preapproved=act.preapproval(sp),
    )


def _needs_action(
    lp: Loop,
    req: TurnRequest,
    *,
    action: Action,
    call_id: str | None,
    tool_name: str | None,
    finish: Callable[..., _Result],
    tool_args: Any = None,
) -> _Result:
    pending = action.model_dump(mode="json")
    pending["_call_id"] = call_id
    pending["_tool_name"] = tool_name or action.type.lower()
    pending["_tool_args"] = tool_args
    lp.st.pending_action = pending
    if action.autonomy == "ASK" and action.preapproved is None:
        return _ask_pause(lp, req, action, finish)
    # 읽기(AUTO) 행동과 선승인 저장은 확인 카드가 없다. 이 실행에서 이미 낸 문장이 있으면 `_finish`가 message 앞에 싣는다(5-5)
    return finish(status="NEEDS_ACTION", reason="NEEDS_ACTION", action=action)


def _iso_utc(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat(timespec="seconds")


def _ask_pause(
    lp: Loop, req: TurnRequest, action: Action, finish: Callable[..., _Result]
) -> _Result:
    """`ASK` 행동 앞에서 멈춘다(6-1 4번): 확인 카드를 코드가 그리고, 그때까지 확인한 것으로 답 문장을 먼저 써서 검사한 뒤
    `NEEDS_ACTION`(message = 이미 낸 문장 + 통과한 문장 + 확인 카드, actions = 그 행동 하나, state 서명)으로 넘긴다.

    사용자가 거절해도 질문에 대한 답이 남게 하려는 것이다. 이 답 쓰기도 답 쓰기 · 고치기 몫(응답마다 2번 · 24초)을 쓰고,
    검사를 통과한 문장부터 문장 줄로 낸다(5-4). 모델이 안 되면(한도 · 오류 · 거절) 코드가 확정한 판단 문장만 낸다.
    """
    pending = lp.st.pending_action
    assert pending is not None
    expires = _iso_utc(lp.deps.clock_ms() + lp.cfg.approval_wait_seconds * 1000)
    card = act.ask_confirm_card(pending, ctx=lp.ctx, expires_at=expires)
    if card is None or _card_blocked(lp, card):
        # 카드를 그릴 수 없거나 표시 검사에 걸리면 승인받을 수 없으므로 행동을 내지 않는다
        lp.st.pending_action = None
        return finish(
            status="PARTIAL", reason="OTHER", system=[templates.SystemText.ACTION_UNCONFIRMED]
        )
    ans, cmp = _start_answer(lp, req, route="ASK", pending=action, pre=_pre_claims(lp))
    draft = {} if ans.get("failed") else ans["draft"]
    comp = cmp.finish(draft, ans.get("messages") or [])
    # 낸 문장 · 판단 · 자료 사본은 `_finish`가 서명 상태에 담는다: 이어 부른 뒤 최종 답이 앞부분에 그대로 싣는다(5-5 · 5-6)
    return finish(
        status="NEEDS_ACTION",
        reason="NEEDS_ACTION",
        action=action,
        claims=comp.final_claims,
        assessments=comp.all_assessments,
        sources=_sources(comp.final_claims),
        extra_cards=[card],
    )


def _card_blocked(lp: Loop, card: dict[str, Any]) -> bool:
    """확인 카드를 가리는 표시 검사(개인정보 · 금지 주장 · 링크 · 주입 문장, 코드가 그린 카드는 숫자 출처를 따지지 않는다).

    카드에 실린 사용자 기준 문구(`display`)가 걸리면 카드를 그리지 못하고 행동도 내지 않는다(보이지 않는 조건은 승인받지 않는다)."""
    fails = chk.check_display_strings(
        cards=[card], registry=lp.ctx.registry, display_evidence=None, cfg=lp.cfg, assessments={}
    )
    return any(f.check_id in ("PII", "FORBIDDEN_CLAIM", "DISPLAY_TEXT") for f in fails)


def _finalize(
    lp: Loop, req: TurnRequest, extra_prior: list[Assessment] | None = None
) -> clm.FinalizeResult | None:
    # 초안이 없어도 사용자 말이 있으면 확정한다: 측정 절을 덮는 판단이 없으면 '확인하지 못했어요' 문장을 만든다(L6-16)
    drafts = [_question_as_request(d, lp.original or "") for d in lp._parse_drafts(lp.st.drafts)]
    if not lp.original:
        return None
    ctx = lp.ctx
    kwargs: dict[str, Any] = {}
    if ctx.pitch is not None and ctx.pitch.criteria is not None:
        kwargs["script_used"] = ctx.pitch.criteria.script_used
    focus = (
        ctx.pitch.focus_take.summary.take_id
        if ctx.pitch is not None and ctx.pitch.focus_take is not None
        else None
    )
    return clm.finalize(
        drafts,
        user_message=lp.original or "",
        prior=[*req.history.prior_assessments, *(extra_prior or [])],
        takes=ctx.snapshots,
        focus_take_id=focus,
        pitch_id=ctx.scope_pitch_id or "",
        registry=ctx.registry,
        version_of=ctx.version_of,
        corpus_version=ctx.corpus_version,
        cfg=lp.cfg,
        id_prefix=f"{req.run.run_id}:as{lp.counters.turn_count}-",
        guide_texts=_guide_texts(lp),
        **kwargs,
    )


#: 값 · 정보를 묻는 말. 이 말이 들고 물음표로 끝난, 주장한 값 · 방향이 없는 측정 초안만 요청으로 본다
#: ("대본 보고 한 발표였어" 같은 상태 주장 · "잘 봤지?"처럼 평가를 내세운 말은 그대로)
_INFO_QUESTION = (
    "몇",
    "얼마",
    "어느 정도",
    "어떻게",
    "어땠",
    "어때",
    "어떤",
    "어디",
    "무슨",
    "뭐",
    "무엇",
    "왜",
)


def _question_as_request(d: clm.AssessmentDraft, original: str) -> clm.AssessmentDraft:
    """정보만 묻는 질문('3장 시선 몇 %였어?' · '졸업 발표 3장 어땠어?')은 주장이 아니다: 측정 초안을 요청으로 바꾼다.

    그대로 두면 주장한 값 · 방향이 없어 '이 기록으로는 확인할 수 없어요'로 판정되고, 그 뒤 답이 값을 말해 모순이 된다.
    바꾸는 것은 값 없는 측정 초안 중 값 주장(`VALUE`)이거나 긍정 · 부정이 없는(`NEUTRAL`) 것이고, 묻는 말(몇 · 어땠 …)이
    있고, 그 절이 물음표로 끝나며, 절을 끊는 말(~는데 · ~지만)이 없을 때뿐이다("몇 번이나 나왔는데 문제없었어"처럼
    평가가 섞인 말 · "잘 봤지?"처럼 평가를 내세운 질문은 그대로 판정한다).
    """
    if not (d.type == "MEASURED" and d.value is None):
        return d
    if not (d.claim_op == "VALUE" or d.polarity == "NEUTRAL"):
        return d
    q = d.quote.strip()
    if not any(w in q for w in _INFO_QUESTION) or any(b in q for b in ("는데", "은데", "지만")):
        return d
    at = original.find(q)
    after = original[at + len(q) :].lstrip()[:1] if at >= 0 else ""
    if not (q.endswith("?") or after == "?"):
        return d
    return d.model_copy(
        update={"type": "REQUEST", "claim_op": None, "polarity": "NEUTRAL", "verdict": "SUPPORTED"}
    )


def _end_without_model(
    lp: Loop, finish: Callable[..., _Result], reason: str, *, notes: list[str] | None = None
) -> _Result:
    """모델 없이 끝낸다(시간 · 호출 몫 부족): 이미 낸 문장 · 행동 결과 문장 · 검증된 근거 템플릿으로, 없으면 `AGENT_TIMEOUT`(8-3).

    승인 뒤 이어 부른 호출이면 멈추기 전에 낸 문장(`_finish`가 앞에 그대로 싣는다)과, 검증된 성공 결과로 코드가 쓴 행동 결과 문장
    · 그 카드를 먼저 조립한다. 낸 문장이 하나라도 있으면 `AGENT_TIMEOUT`으로 버리지 않는다(5-5 · 6-1).
    """
    partial = compose.template_partial(lp.ctx, list(lp.ctx.registry.items))
    notes_claims = [Claim(kind="SYSTEM", text=n) for n in notes or []]
    said_actions = {d.get("action_id") for d in lp.st.emitted}
    result_claims = [
        Claim(kind="ACTION_RESULT", text=e["success_text"], action_id=e["action_id"])
        for e in lp.st.executed
        if e.get("status") == "SUCCEEDED"
        and e.get("type")
        in (
            "SAVE_GOAL",
            "REASSESS",
        )  # 사용자가 승인한 행동의 결과만(읽기 행동은 모델이 쓸 재료일 뿐)
        and e.get("success_text")
        and e["action_id"] not in said_actions
    ]
    if partial or notes_claims or result_claims or lp.st.emitted:
        # 한도 안내는 행동 결과 문장 뒤에 둔다(앞 문장 → 결과 문장 → 안내 순서)
        limit = (
            []
            if any(n == templates.SystemText.TURN_LIMIT.value for n in notes or [])
            else [Claim(kind="SYSTEM", text=templates.SystemText.LIMIT_PARTIAL.value)]
        )
        return finish(
            status="PARTIAL",
            reason=reason,
            claims=[*result_claims, *notes_claims, *limit, *partial],
        )
    return finish(error="AGENT_TIMEOUT", reason=reason)


def _slots_short_end(lp: Loop, finish: Callable[..., _Result]) -> _Result:
    """이어 부를 때 남은 호출 수로 답 쓰기 · 고치기 두 몫을 남길 수 없다: 모델 없이 끝낸다(8-3)."""
    c, cfg = lp.counters, lp.cfg
    lp._limit(lp.budget._hit("MODEL_CALLS", c.model_calls, cfg.max_model_calls))
    return _end_without_model(lp, finish, "MODEL_CALLS", notes=_action_notes(lp))


def _answer(lp: Loop, req: TurnRequest, finish: Callable[..., _Result]) -> _Result:
    ctx = lp.ctx
    stop_reason = lp.st.stop_reason
    if stop_reason == "LLM_REFUSED":
        # 공급자가 조사 단계에서 거절하면 답을 쓰지 않고 거절로 끝낸다(L1-12)
        return finish(status="REFUSED", reason="OTHER", system=[templates.SystemText.CANNOT_ANSWER])
    stopped = _RESEARCH_STOP.get(stop_reason or "")
    notes = _action_notes(lp)
    pre = _pre_claims(lp, notes=True, stopped=stopped)
    ans, cmp = _start_answer(lp, req, route="ANSWER", pre=pre)
    if ans.get("failed"):
        if ans.get("refused"):
            if lp.st.emitted:
                # 이 실행에서 이미 낸 문장(앞 문장 · 이 답의 문장 줄 · 앞 응답)이 있으면 거절로 바꾸지 않는다:
                # 낸 문장 뒤에 안내를 붙여 부분 답(6-1 5번)
                return finish(
                    status="PARTIAL", reason="OTHER", system=[templates.SystemText.PARTIAL_CHECKED]
                )
            return finish(
                status="REFUSED", reason="OTHER", system=[templates.SystemText.CANNOT_ANSWER]
            )
        # 한도 때문에 못 썼으면 어느 한도인지 나눠 남긴다(11-5). 한도가 아니면 모델 오류
        limit = (
            stop_reason
            if stop_reason in LIMIT_END_REASONS
            else lp.limit_end or ("TIME" if lp.budget.left() <= 0 else None)
        )
        partial = compose.template_partial(ctx, list(ctx.registry.items))
        if partial or lp.st.emitted:
            # 이미 낸 문장이 하나라도 있으면 오류로 버리지 않는다(5-4 · 5-5). 조사 중단 안내를 냈으면 안내를 겹치지 않는다
            said_stop = stopped is not None and any(c.text == stopped[1].value for c in cmp.pre)
            return finish(
                status="PARTIAL",
                reason=limit or "LLM_ERROR",
                system=[] if said_stop else [templates.SystemText.PARTIAL_CHECKED],
                claims=partial,
            )
        return finish(
            error="AGENT_TIMEOUT" if limit else "INTERNAL_ERROR",
            reason=limit or "LLM_ERROR",
        )
    draft = ans["draft"]
    route = draft.get("route")
    if route == "OUT_OF_SCOPE":
        return finish(
            status="REFUSED",
            reason="OUT_OF_SCOPE",
            system=[templates.scope_text(draft.get("scope_reason"))],
        )
    no_guide = route == "NO_GUIDE"

    comp = cmp.finish(draft, ans["messages"])
    out, body, all_assessments = comp.out, list(comp.body), comp.all_assessments
    if out and all(c.kind == "GENERAL" or is_general_intro(c) for c in out):
        # 일반 조언만 쓴 답은 자료를 찾지 못한 답이다(route 를 ANSWER 로 썼어도): 일반 조언이 모두 걸리면 거절(6-10 · L4-28)
        no_guide = True

    # 4. 연습 준비 이동 · 대화 요약
    actions: list[Action] = []
    sp = draft.get("start_practice")
    if isinstance(sp, dict):
        lp.st.action_seq += 1
        action, card, _why = act.start_practice_action(
            ctx=ctx,
            authorized_by=sp.get("authorized_by")
            if isinstance(sp.get("authorized_by"), str)
            else None,
            seq=lp.st.action_seq,
            budget=lp.budget,
        )
        if card:
            lp.st.cards.append(card)
        if action is not None:
            actions.append(action)
    # 코칭 프로필 저장 후보: 검사 · 자격(처음 사용자 말 · 켜짐 · 번호 그대로)은 `_finish`가 종결 응답에서만 본다(6-14)
    lp.profile_candidates = draft.get("profile_updates")

    # 5. 상태 정하기 (앞 문장은 이미 냈고, `_finish`가 낸 문장을 최종 답의 앞부분에 그대로 싣는다)
    has_result = any(c.kind == "ACTION_RESULT" for c in comp.pre)
    if not body and not has_result and actions and not out and not no_guide:
        # "바로 발표할래"처럼 연습 이동만 요청해 허용된 답: 문장이 없어도 이동이 답이다(6-2 START_PRACTICE).
        # 모델이 쓴 문장이 검사로 모두 빠졌거나 자료를 찾지 못한 답은 그대로 PARTIAL · 거절이다
        body = [Claim(kind="SYSTEM", text=templates.SystemText.PRACTICE_BUTTON.value)]
    if not body and not has_result:
        if notes:
            # 행동 결과 안내만 있는 답(예: 다른 탭에서 목표가 바뀌어 저장하지 못함): 안내를 그대로 낸다
            return finish(
                status="PARTIAL",
                reason="OTHER",
                claims=comp.pre,
                assessments=all_assessments,
            )
        if no_guide:
            return finish(
                status="REFUSED",
                reason="NO_EVIDENCE",
                system=[templates.SystemText.NO_GUIDE],
                assessments=all_assessments,
            )
        partial = compose.template_partial(ctx, [e for c in out for e in c.evidence_ids])
        if partial:
            return finish(
                status="PARTIAL",
                reason="CHECK_ALL_DROPPED",
                system=[templates.SystemText.VERIFIED_ONLY],
                claims=partial,
                assessments=all_assessments,
            )
        return finish(
            status="PARTIAL",
            reason="CHECK_ALL_DROPPED",
            system=[templates.SystemText.PARTIAL_CHECKED],
            assessments=all_assessments,
        )
    final_claims = [*comp.pre, *body]
    # 섞인 요청의 범위 밖 부분: 이유별 고정 문장을 답 문장 뒤에 덧붙인다(1-3 · 7-10)
    for reason in _scope_notes(draft):
        note = templates.scope_text(reason).value
        if all(c.text != note for c in final_claims):
            final_claims.append(Claim(kind="SYSTEM", text=note))
    if stopped is not None:
        # 조사가 끝까지 가지 못했으면 확인한 데까지만 말한다(8-2 · L1-11 · L1-12 · L1-16). 안내는 앞 문장으로 이미 냈다
        return finish(
            status="PARTIAL",
            reason=stopped[0],
            claims=final_claims,
            assessments=all_assessments,
            sources=_sources(final_claims),
        )
    if no_guide and not any(c.kind == "GENERAL" for c in final_claims):
        # 자료를 찾지 못했고 일반 조언도 없으면(기록 답만 남음) 자료 근거가 없다고 덧붙인다. 일반 조언이 있으면 그 앞의
        # 고정 안내(GENERAL_INTRO)가 이미 자료에서 확인한 내용이 아니라고 말한다(6-10)
        final_claims.append(
            Claim(kind="SYSTEM", text=templates.SystemText.KNOWLEDGE_NO_SOURCE.value)
        )
    return finish(
        status="DONE",
        reason="ANSWERED",
        claims=final_claims,
        assessments=all_assessments,
        actions=actions,
        sources=_sources(final_claims),
    )


def _start_answer(
    lp: Loop,
    req: TurnRequest,
    *,
    route: str,
    pre: list[Claim],
    pending: Action | None = None,
) -> tuple[dict[str, Any], Composer]:
    """앞 문장을 내고 답 초안을 부른다. 응답 조각은 `Composer`가 받아 검사를 통과한 문장부터 문장 줄로 낸다(5-4 · 6-1 5번).

    모델에게 알리는 '이미 보인 문장'은 이 답 전에 낸 문장만이다(바로 앞에 낸 코드 앞 문장 때문에 답을 비우지 않게)."""
    said = [d["text"] for d in lp.st.emitted]
    cmp = Composer(
        lp,
        pre=pre,
        finalize=lambda extra: _finalize(lp, req, extra_prior=extra),
        lines=lp.lines,
    )
    cmp.send_pre()
    ans = lp.answer_phase(route=route, pending=pending, sink=cmp, already_said=said)
    cmp.close()
    return ans, cmp


def _pre_claims(
    lp: Loop, *, notes: bool = False, stopped: tuple[str, Any] | None = None
) -> list[Claim]:
    """모델을 부르기 전에 정해지는 앞 문장(5-4: 코드 템플릿 문장은 확정되는 즉시 먼저 낸다). 이미 낸 문장은 뺀다.

    순서(`notes`면): 조사 중단 안내 → 기록이 바뀐 안내 → 행동 안내(7-10) → 행동 결과(이 실행에서 성공한 행동의 성공
    문장). 안내를 앞에 두는 것은 그 뒤 문장을 읽는 법을 먼저 알리기 위해서다. 승인 대기 전 답(`notes` 아님)은 행동 결과만.
    """
    said_actions = {d.get("action_id") for d in lp.st.emitted}
    said = {d["text"] for d in lp.st.emitted}
    out: list[Claim] = []
    if notes:
        system = [stopped[1].value] if stopped is not None else []
        if lp.record_changed:
            system.append(templates.SystemText.RECORD_CHANGED_DURING.value)
        system += _action_notes(lp)
        out += [Claim(kind="SYSTEM", text=t) for t in dict.fromkeys(system) if t not in said]
    out += [
        Claim(kind="ACTION_RESULT", text=e["success_text"], action_id=e["action_id"])
        for e in lp.st.executed
        if e.get("status") == "SUCCEEDED"
        and e.get("success_text")
        and e["action_id"] not in said_actions
    ]
    return out


def _scope_notes(draft: dict[str, Any]) -> list[str]:
    """답 초안의 `scope_notes` → 알려진 이유만, 중복 없이, 앞에서 3개까지(1-3)."""
    raw = draft.get("scope_notes")
    out: list[str] = []
    for r in raw if isinstance(raw, list) else []:
        if isinstance(r, str) and r in templates.SCOPE_TEXT and r not in out:
            out.append(r)
    return out[:3]


def _action_notes(lp: Loop) -> list[str]:
    """이 실행에서 처리한 행동 결과에 붙일 고정 안내(7-10): 실패 · 후보 여럿 · 규칙 판 다름 · 공식 리뷰와 다름."""
    st = templates.SystemText
    out: list[str] = []
    for e in lp.st.executed:
        t, status, reason, code = (
            e.get("type"),
            e.get("status"),
            e.get("reason_code"),
            e.get("failed_code"),
        )
        note = None
        if t == "FETCH_PITCH" and reason == "AMBIGUOUS":
            note = st.WHICH_PITCH
        elif status == "FAILED" and t == "FETCH_PITCH":
            note = st.PITCH_NOT_FOUND
        elif status == "FAILED" and t == "FETCH_TAKE":
            note = st.TAKE_FETCH_FAILED
        elif t == "REASSESS" and "RULES_DIFFER" in (reason, code):
            note = st.REASSESS_RULES_DIFFER
        elif status == "FAILED" and t == "REASSESS":
            note = {
                "DAILY_LIMIT": st.REASSESS_DAILY_LIMIT,
                "REASSESS_DAILY_LIMIT": st.REASSESS_DAILY_LIMIT,
                "NO_DATA": st.REASSESS_NO_DATA,
            }.get(code or "", st.REASSESS_FAILED)
        elif status == "FAILED" and t == "SAVE_GOAL":
            note = (
                st.GOAL_CHANGED_ELSEWHERE
                if code
                in (
                    "GOAL_CHANGED",
                    "GOAL_VERSION_CONFLICT",
                    "VERSION_CONFLICT",
                    "STALE_GOAL_VERSION",
                )
                else st.ACTION_UNCONFIRMED
            )
        elif status == "SUCCEEDED" and t == "REASSESS" and e.get("official_differs"):
            note = st.REASSESS_DIFFERS
        if note is not None and note.value not in out:
            out.append(note.value)
        removed = status == "SUCCEEDED" and t == "REASSESS" and e.get("script_points_removed")
        if removed and st.SCRIPT_POINTS_REMOVED.value not in out:
            out.append(st.SCRIPT_POINTS_REMOVED.value)
    return out


#: 조사 종료 사유 → (진단 끝 사유, 안내 문구)
_RESEARCH_STOP = {
    "TIME": ("TIME", templates.SystemText.LIMIT_PARTIAL),
    "MODEL_CALLS": ("MODEL_CALLS", templates.SystemText.LIMIT_PARTIAL),
    "TOOL_CALLS": ("TOOL_CALLS", templates.SystemText.LIMIT_PARTIAL),
    "TOKENS": ("TOKENS", templates.SystemText.LIMIT_PARTIAL),
    "LIMIT": ("LIMIT", templates.SystemText.LIMIT_PARTIAL),
    "LLM_ERROR": ("LLM_ERROR", templates.SystemText.PARTIAL_CHECKED),
    "INVALID_TOOLS": ("REPEAT", templates.SystemText.PARTIAL_CHECKED),
    "NO_PROGRESS": ("REPEAT", templates.SystemText.NO_MORE_SOURCES),
    "ALTERNATING": ("REPEAT", templates.SystemText.NO_MORE_SOURCES),
    "REPEATED_FAILED_ACTION": ("REPEAT", templates.SystemText.SOME_ACTIONS_FAILED),
}


def _sources(claims: list[Claim]) -> list[str]:
    return list(dict.fromkeys(s for c in claims for s in c.source_ids))


def _take_reviewed(lp: Loop, req: TurnRequest, finish: Callable[..., _Result]) -> _Result:
    """자동 목표 확인(5-1 · 6-4): 모델 없이 코드가 판정한다. 근거가 부족해도 goal_result 를 UNKNOWN 으로 명시한다(L1-39)."""
    ctx = lp.ctx
    ev = req.review_event
    assert ev is not None
    goal = ctx.goal

    def unknown(reason: str) -> _Result:
        gr = {
            "goal_version": ev.goal_version,
            "take_id": ev.take_id,
            "review_revision": ev.review_revision,
            "status": "UNKNOWN",
            "observed": None,
            "reason": reason,
            "terminal": False,
        }
        return finish(
            status="DONE",
            reason="ANSWERED",
            system=[templates.SystemText.GOAL_AFTER_SAVE],
            goal_result=gr,
        )

    if goal is None or goal.version != ev.goal_version or goal.status != "OPEN":
        return unknown("GOAL_CHANGED")
    snap = ctx.snapshots.get(ev.take_id)
    if snap is None:
        return unknown("NOT_LOADED")
    ctx.touch(snap.summary.take_id, ctx.scope_pitch_id)
    j = judge_goal(goal, snap, registry=ctx.registry, version_of=ctx.version_of, cfg=lp.cfg)
    reason = j.reason_code or j.status
    gr = {
        "goal_version": goal.version,
        "take_id": ev.take_id,
        "review_revision": ev.review_revision,
        "status": j.status,
        "observed": j.observed,
        "reason": reason,
        "terminal": j.terminal,
    }
    text = compose.goal_sentence(ctx, goal, j, snap.summary.take_number)
    claim = Claim(kind="RECORD", text=text, evidence_ids=list(j.evidence_ids))
    fails = (
        chk.check_claims([claim], registry=ctx.registry, code_sentences={text}, cfg=lp.cfg)
        if j.evidence_ids
        else [True]
    )
    card = {
        "type": "GOAL_RESULT",
        "goal_version": goal.version,
        "take_id": ev.take_id,
        "status": j.status,
        "observed": j.observed,
        "reason": reason,
    }
    lp.st.cards.append(card)
    if fails:
        sys_text = (
            templates.SystemText.METRIC_UNMEASURED
            if j.status == "UNKNOWN"
            else templates.SystemText.VERIFIED_ONLY
        )
        return finish(status="DONE", reason="ANSWERED", system=[sys_text], goal_result=gr)
    return finish(status="DONE", reason="ANSWERED", claims=[claim], goal_result=gr)


# ── 최종 출력 조립 · 서명 ────────────────────────────────────────────────


def _waits_for_approval(actions: list[Action]) -> bool:
    """사용자의 승인을 기다리는 행동(확인 카드가 있는 `ASK`)인가: 그러면 서명 상태 유효기간이 35분이다(5-6)."""
    return bool(actions) and actions[0].autonomy == "ASK" and actions[0].preapproved is None


#: 검증된 결과로 코드가 만든 카드 종류
_CODE_CARDS = ("REASSESS_RESULT", "GOAL_RESULT", "START_PRACTICE", "ASK_CONFIRM")

_CARD_DEFAULT_REASON = {"USER": "말씀하신 목표", "EXPERT": "기록으로 본 추천 목표"}


def _safe_cards(
    lp: Loop, cards: list[dict[str, Any]], amap: dict[str, Any]
) -> list[dict[str, Any]]:
    """카드 문자열도 같은 출처 · 금지 주장 · 개인정보 검사를 받는다(6-9 · L1-37).

    걸리면 모델이 쓴 설명(`reason`)만 기본 문구로 바꾸고, 그래도 걸리면 숨긴다.
    """
    ctx, cfg = lp.ctx, lp.cfg
    out: list[dict[str, Any]] = []
    for card in cards:
        ids = list(card.get("evidence_ids") or []) + [
            e for o in card.get("options") or [] for e in o.get("evidence_ids") or []
        ]

        def bad(c: dict[str, Any], ids: list[str] = ids) -> bool:
            fails = chk.check_display_strings(
                cards=[c],
                registry=ctx.registry,
                display_evidence={"cards:0": ids},
                cfg=cfg,
                assessments=amap,
            )
            if c.get("type") in _CODE_CARDS:
                # 검증된 행동 결과로 코드가 만든 카드(다시 평가 · 목표 판정 · 연습 이동)는 숫자 출처 대신
                # 금지 주장 · 개인정보만 본다(사용자 기준 문구의 "30%" 같은 숫자로 카드가 통째로 숨지 않게)
                fails = [f for f in fails if f.check_id in ("PII", "FORBIDDEN_CLAIM")]
            return bool(fails)

        if not bad(card):
            out.append(card)
            continue
        fixed = json.loads(json.dumps(card))
        if fixed.get("type") == "GOAL_PROPOSAL":
            for o in fixed.get("options") or []:
                o["reason"] = _CARD_DEFAULT_REASON.get(o.get("label"), "목표 제안")
        elif fixed.get("type") == "GOAL_SAVED":
            fixed["reason"] = templates.ACTION_SUCCESS["SAVE_GOAL"]
        if not bad(fixed):
            out.append(fixed)
        else:
            lp.diag.step(
                "CHECK", "DISPLAY_TEXT", {"field": "cards", "round": 0, "result": "DROPPED"}
            )
    return out


def _safe_trace(lp: Loop, trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """trace 요약에 외부 문자열(피치 제목 등)이 들어가므로 같은 검사를 하고, 걸리면 도구 이름 · 결과만 남긴다."""
    ctx, cfg = lp.ctx, lp.cfg
    out = []
    for t in trace:
        if chk.check_display_strings(
            trace=[t], registry=ctx.registry, display_evidence=None, cfg=cfg, assessments={}
        ):
            t = {**t, "args_summary": t["tool"], "result_summary": t["outcome"]}
        out.append(t)
    return out


def _history_summary(lp: Loop) -> list[dict[str, str]] | None:
    """요청에 밀린 메시지가 있으면 모델 1번으로 새 요약을 만들고 코드가 거른다(6-14). 못 만들거나 남은 항목이 없으면 None."""
    raw = lp.summary_phase()
    if raw is None:
        return None
    kept, dropped = memory.check_summary_items(raw, cfg=lp.cfg)
    for d in dropped:
        lp.diag.step("CHECK", "MEMORY_RULE", {"field": d.field, "round": 0, "result": "DROPPED"})
    return kept or None


def _profile_updates(lp: Loop, req: TurnRequest, payload: Any) -> list[dict[str, str]] | None:
    """코칭 프로필 저장 후보(6-14): 사용자 말로 시작한 실행 · 기억 켜짐 · 실행 중 기억 번호가 그대로일 때만, 처음 사용자 말
    기준으로 ①~⑥을 검사해 통과한 것만. 번호가 한 번이라도 바뀐 실행은 후보를 모두 버린다(AI · BE)."""
    raw = lp.profile_candidates
    if not raw:
        return None
    trigger = payload.trigger if payload is not None else req.run.trigger
    if (
        trigger != "USER_MESSAGE"
        or not req.memory.enabled
        or lp.st.memory_changed
        or not lp.original
    ):
        for _ in raw if isinstance(raw, list) else [raw]:  # 버린 후보마다 한 단계(11-5)
            lp.diag.step(
                "CHECK",
                "MEMORY_RULE",
                {"field": "profile_updates", "round": 0, "result": "DROPPED"},
            )
        return None
    kept, dropped = memory.check_profile_updates(raw, user_message=lp.original, cfg=lp.cfg)
    for d in dropped:
        lp.diag.step("CHECK", "MEMORY_RULE", {"field": d.field, "round": 0, "result": "DROPPED"})
    return kept or None


def _finish(
    lp: Loop,
    req: TurnRequest,
    deps: TurnDeps,
    *,
    secret: bytes,
    payload: Any,
    status: str | None = None,
    reason: str = "ANSWERED",
    system: list[Any] | None = None,
    claims: list[Claim] | None = None,
    assessments: list[Any] | None = None,
    actions: list[Action] | None = None,
    action: Action | None = None,
    goal_result: dict[str, Any] | None = None,
    sources: list[str] | None = None,
    extra_cards: list[dict[str, Any]] | None = None,
    error: str | None = None,
) -> _Result:
    cfg = deps.cfg
    ctx = lp.ctx
    st = lp.st
    lines = lp.lines if lp.lines is not None else ClaimLines(lp)
    #: 이 실행에서 이미 낸 문장 수(앞 응답 + 이 응답의 문장 줄). 끝 줄 message 의 앞부분은 이 문장들과 같다(5-4)
    n_sent = len(st.emitted)
    terminal = error is None and status in ("DONE", "PARTIAL", "REFUSED")
    # 코칭 프로필 저장 후보(6-14): 종결 응답에서만(모델 호출 없음)
    profile_updates = _profile_updates(lp, req, payload) if terminal else None
    c = lp.counters

    def usage_now() -> dict[str, Any]:
        lp.budget.sync_elapsed()
        return {
            "model_calls": c.model_calls,
            "tool_calls": c.tool_calls,
            "elapsed_ms": c.elapsed_ms,
            "input_tokens": st.usage_in if st.usage_seen_in else None,
            "output_tokens": st.usage_out if st.usage_seen_out else None,
            "measured": not st.usage_missing,
            "missing_reason": "PROVIDER_USAGE_MISSING" if st.usage_missing else None,
        }

    usage = usage_now()
    if error is not None:
        d = lp.diag.finish(error, reason, usage=usage, max_bytes=cfg.max_diagnostics_bytes)
        return None, error, d.model_dump(mode="json")
    claim_list: list[Claim] = [Claim(kind="SYSTEM", text=s.value) for s in (system or [])] + list(
        claims or []
    )
    assessments_out = [
        a.model_dump(mode="json") if hasattr(a, "model_dump") else a for a in (assessments or [])
    ]
    # 이미 낸 문장(멈추기 전 응답 · 이 응답의 문장 줄)은 언제나 최종 답(message)의 앞부분이다: 글자 · 종류 · 근거까지 그대로
    # 싣고, 그 문장 · 문장 줄이 실은 판단도 함께 싣는다. 어떤 끝(모델 없이 끝내기 · 부분 답 · 거절)에서도 지워지지 않게
    # 한 곳에서 한다(5-4 · 5-5)
    claim_list = with_prefix(st.emitted, claim_list)
    have = {a["assessment_id"] for a in assessments_out}
    for a in [*(st.exported if n_sent else []), *lines.sent_assessments]:
        if a["assessment_id"] not in have:
            assessments_out.append(a)
            have.add(a["assessment_id"])
    sources = list(
        dict.fromkeys([*(sources or []), *(s for c in claim_list for s in c.source_ids)])
    )
    if req.run.trigger == "TAKE_REVIEWED":
        assessments_out = []
    amap = {a["assessment_id"]: Assessment.model_validate(a) for a in assessments_out}
    plan = st.plan
    if status == "DONE":
        plan = [
            {**p, "status": "DONE" if p["status"] in ("DOING", "TODO") else p["status"]}
            for p in plan
        ]
    # NEEDS_ACTION 의 message 에는 확인 카드(`extra_cards`)만 싣는다. 모인 카드(st.cards)는 최종 답까지 상태에 남는다
    cards = _safe_cards(lp, st.cards if status != "NEEDS_ACTION" else extra_cards or [], amap)
    msg = None
    act_list = [action] if action is not None else list(actions or [])
    ask_wait = bool(act_list) and act_list[0].autonomy == "ASK"
    if claim_list or extra_cards or ask_wait or status != "NEEDS_ACTION":
        if not claim_list and status != "NEEDS_ACTION":
            claim_list = [Claim(kind="SYSTEM", text=templates.SystemText.PARTIAL_CHECKED.value)]
        text, ordered = chk.assemble_message(claim_list)
        # 멈추기 전에 낸 문장의 자료 카드는 서명 상태의 사본을 그대로 쓰고, 새 자료만 지금 자료 묶음에서 그린다
        carried = {g["source_id"]: g for g in st.emitted_sources}
        src_ids = sources
        fresh = {
            g.source_id: g.model_dump(mode="json")
            for g in compose.guide_cards(ctx, [i for i in src_ids if i not in carried])
        }
        msg = {
            "text": text,
            "claims": [cl.model_dump(mode="json") for cl in ordered],
            "cards": cards,
            "sources": [carried.get(i) or fresh[i] for i in src_ids if i in carried or i in fresh],
        }
    ev_ids = [e for cl in claim_list for e in cl.evidence_ids]
    ev_ids += [e for a in assessments_out for e in a.get("evidence_ids", [])]
    ev_ids += [e for cd in cards for e in cd.get("evidence_ids") or []]
    ev_ids += [
        e for cd in cards for o in cd.get("options") or [] for e in o.get("evidence_ids") or []
    ]
    tail_sent = False
    history_summary = None
    if terminal:
        if msg is not None:
            # 답 문장은 요약보다 먼저 다 낸다(6-1 8번: 사용자는 요약을 기다리지 않는다)
            for d in msg["claims"][n_sent:]:
                lines.send(Claim.model_validate(d), assessments_out, record=False)
            tail_sent = True
        # 대화 요약(6-14): 답 쓰기 · 고치기 뒤 남은 몫에서. 이 호출도 사용량에 들어가므로 사용량을 다시 센다
        history_summary = _history_summary(lp)
        usage = usage_now()
    final: dict[str, Any] = {
        "status": status,
        "message": msg,
        "actions": [a.model_dump(mode="json") for a in act_list],
        "state": None,
        "scope_pitch_id": ctx.scope_pitch_id,
        "plan": plan,
        "trace": _safe_trace(lp, st.trace),
        "basis": ctx.basis(sources or []),
        "evidence": compose.referenced(ctx, ev_ids),
        "history_summary": history_summary,
        "budget_used": c.budget_used(),
        "usage": usage,
        "assessments": assessments_out,
        "goal_result": goal_result,
        "meta": meta(
            extra=dict(st.review_versions) or None,
            model=lp.model_name if lp.model_used else None,
            prompt_version=prompts.PROMPT_VERSION,
            corpus_version=deps.corpus_version,
            cfg=cfg,
        ),
        "diagnostics": None,
    }
    if status == "NEEDS_ACTION":
        # 지금까지 보인 문장 · 판단 · 자료 카드 사본: 이어 부른 호출의 최종 답이 앞부분에 그대로 싣는다(5-5 · 5-6)
        prior_sources = list(
            st.emitted_sources
        )  # 상태가 너무 커 못 서명하면 앞 문장의 자료 카드로 쓴다
        st.emitted = [c.model_dump(mode="json") for c in claim_list]
        st.exported = [dict(a) for a in assessments_out]
        st.emitted_sources = list(msg["sources"]) if msg else []
        st.registry = ctx.registry.dump()
        st.counters = c.dump()
        st.tool_cache = ctx.tool_cache
        st.read_sources = sorted(ctx.read_sources)
        st.corpus_version = deps.corpus_version
        waiting = _waits_for_approval(act_list)
        issued, expires = state.issue_times(deps.clock_ms(), ask=waiting, cfg=cfg)
        pl = state.StatePayload(
            run_id=req.run.run_id,
            conversation_id=req.run.conversation_id,
            user_id=req.run.user_id,
            # 범위 전환(FETCH_PITCH 성공)을 반영한 상태만 판 번호를 올린다(5-6 '범위 바꾸기')
            state_revision=req.run.state_revision + (1 if lp.scope_changed else 0),
            scope_pitch_id=ctx.scope_pitch_id,
            trigger=payload.trigger if payload is not None else req.run.trigger,
            original_user_message=lp.original,
            versions=state.RunVersions(
                feature=FEATURE_VERSION,
                prompt=prompts.PROMPT_VERSION,
                tool_schema=TOOL_SCHEMA_VERSION,
                corpus=deps.corpus_version,
            ),
            issued_at=issued,
            expires_at=expires,
            turn_count=c.turn_count,
            loop=st.dump(),
        )
        try:
            final["state"] = state.sign(pl, secret, cfg)
        except state.StateTooLarge:
            # 잘라 내지 않고 행동 없이 PARTIAL 로 끝낸다. 범위 전환도 적용하지 않는다(5-6 · L1-26)
            lp._limit(lp.budget._hit("STATE_SIZE", cfg.max_state_bytes, cfg.max_state_bytes))
            sys_claim = Claim(kind="SYSTEM", text=templates.SystemText.STATE_TOO_LARGE.value)
            # 이미 낸 문장(앞 응답 · 이 응답의 문장 줄)은 그대로 남기고, 아직 내지 않은 문장 중에서는 이미 실행 · 검증된
            # 행동의 결과 문장만 남긴다(새 승인 행동만 버린다, 5-4 · 5-5 · 6-1). 남긴 문장이 쓰는 자료 카드 사본도 남긴다
            done = [c for c in claim_list[n_sent:] if c.kind == "ACTION_RESULT"]
            kept_claims = [*claim_list[:n_sent], *done, sys_claim]
            # 이 응답에서 실행 · 검증된 행동의 결과 문장(이미 냈든 아니든)이 있으면 그 결과 카드도 남긴다
            results_now = any(c.kind == "ACTION_RESULT" for c in kept_claims[lines.base :])
            keep_src = {i for c in kept_claims for i in c.source_ids}
            pool = {g["source_id"]: g for g in [*prior_sources, *(msg["sources"] if msg else [])]}
            final.update(
                status="PARTIAL",
                actions=[],
                state=None,
                scope_pitch_id=payload.scope_pitch_id
                if payload is not None
                else req.run.scope_pitch_id,
                message={
                    "text": "\n".join(c.text for c in kept_claims),
                    "claims": [c.model_dump(mode="json") for c in kept_claims],
                    "cards": _safe_cards(lp, st.cards, amap) if results_now else [],
                    "sources": [g for sid, g in pool.items() if sid in keep_src],
                },
            )
            reason = "LIMIT"
    if profile_updates:
        final["profile_updates"] = profile_updates
    try:
        # 본체를 먼저 검사하고 진단 기록은 끝을 안 뒤 한 번만 만든다(진단 기록은 코드가 닫힌 모양으로 만든다)
        FinalData.model_validate({**final, "diagnostics": None})
    except ValidationError as e:
        deps.log(
            {
                "event": "agent_final_invalid",
                "run_id": req.run.run_id,
                "errors": [list(x.get("loc", ())) for x in e.errors()][:10],
            }
        )
        # 실패해도 그때까지의 단계 · 사용량(요약 호출 포함)은 error 줄의 진단 기록으로 남긴다
        d = lp.diag.finish(
            "INTERNAL_ERROR", "OTHER", usage=usage, max_bytes=cfg.max_diagnostics_bytes
        )
        return None, "INTERNAL_ERROR", d.model_dump(mode="json")
    d = lp.diag.finish(final["status"], reason, usage=usage, max_bytes=cfg.max_diagnostics_bytes)
    final["diagnostics"] = d.model_dump(mode="json")
    # 아직 줄로 내지 않은 문장(보류했다 통과한 문장 · 끝에 붙는 안내 · 모델 없이 만든 답)도 끝 줄 앞에 문장 줄로 낸다.
    # 그래서 문장 줄 번호는 실행 전체 답에서의 자리와 같고 빈 번호가 없다(5-4)
    for d in [] if tail_sent else (final["message"] or {}).get("claims", [])[n_sent:]:
        lines.send(Claim.model_validate(d), final["assessments"], record=False)
    return final, None, None


__all__ = ["handle_turn", "TurnResponse", "PreStreamError"]
