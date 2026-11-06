"""`/agent/turn` 한 번 — 스트림 전 검사 → 실행 → 진행 줄 · 끝 줄 (기능 문서 5-3 · 5-4 · 5-5 · 5-6 · 6-9 · 6-12 · 7-3 · 8-3).

- 누가 쓰나: 서비스 계층(연구 모듈은 chat_agent_lab/app.py, 탑재 뒤 api/chat_agent.py)이 요청 본문 바이트와 `TurnDeps`를 넘긴다.
  끄기 스위치 · 동시 실행 자리(`AGENT_BUSY`)는 서비스 계층이 먼저 보고, 이 모듈은 기능 몫(요청 · 서명 · 기본 자료 크기 · 키 길이)을 본다.
- 돌려주는 것: `TurnResponse` — 스트림 전 오류(`PreStreamError`: HTTP 상태 · 코드 · 메시지)이거나, NDJSON 줄을 내는 반복자.
- 스트림 전 검사 순서(걸리면 모델 · 도구 · 행동 0회, P1): 요청 본문 크기(2 MiB, 413) → UTF-8 · JSON(422) · 깊이(32, 413) →
  요청 모양(422) → 서명 키(32바이트 미만이면 503 `AGENT_DISABLED`) → 모델 준비(503 `LLM_UNAVAILABLE`, 자동 목표 확인은 모델 없이 됨) →
  기본 자료 크기(512 KiB, 413) → (이어 부르기) 서명 · 판 · 식별자 검사(409) → 대기 행동과 결과의 짝(409) → 누적 사용량이 줄지 않았는지(409).
- 실행 흐름(`_orchestrate`): 호출 횟수 · 시작 시간 한도 → 자동 목표 확인 / 행동 결과 반영 / 고른 제안 저장 / 계획 → 조사 →
  행동이면 NEEDS_ACTION(서명), 아니면 답 쓰기 → 판단 확정 → 문장 만들기 → 답 검사 · 한 번 고치기 → 최종 출력.
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
from pathlib import PurePath as Path
from typing import Any

from pydantic import ValidationError

from . import actions as act
from . import checks as chk
from . import claims as clm
from . import compose, prompts, state, templates
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

    def finish(**kw: Any) -> _Result:
        return _finish(lp, req, deps, secret=secret, payload=payload, **kw)

    # 7번째 호출 · 시작부터 시간 없음: 모델 없이 끝낸다(8-3 · L1-04 · L1-31)
    hit = budget.turn_limit_hit()
    if hit is not None:
        lp._limit(hit)
        return finish(status="PARTIAL", reason="LIMIT", system=[templates.SystemText.TURN_LIMIT])
    if budget.exhausted_at_start():
        lp._limit(budget._hit("TURN_TIME", lp.counters.elapsed_ms, int(cfg.turn_seconds * 1000)))
        return _end_without_model(lp, finish, "TIME")

    trigger = req.run.trigger
    if trigger == "TAKE_REVIEWED":
        return _take_reviewed(lp, req, finish)

    if trigger == "ACTION_RESULT":
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
    seq = lp.st.action_seq
    run_id = req.run.run_id
    return Action(
        action_id=f"{run_id}:a{seq}",
        type="SAVE_GOAL",
        args={"expected_goal_version": lp.ctx.goal_version, "goal": goal.model_dump(mode="json")},
        autonomy="AUTO_UNDO",
        args_hash=None,
        idempotency_key=f"{run_id}:{seq}",
        reason="고른 목표 제안 저장",
        authorized_by=None,
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
    interim: list[Claim] = []
    newly: list[Assessment] = []
    if action.type == "REASSESS" and lp.st.drafts and not lp.st.exported:
        # 다시 평가 전 판단: 판단을 먼저 확정해 보인다. BE가 interim_message 로 먼저 보이고 결과를 그 뒤에 붙인다(6-3 · L6-07)
        result = _finalize(lp, req)
        outcomes = result.outcomes if result is not None else []
        code_sentences: set[str] = set()
        cands: list[tuple[Claim, Assessment]] = []
        for o in outcomes:
            text = _assessment_head(o)
            a = o.assessment
            c = compose._claim(
                "ASSESSMENT",
                text,
                assessment_id=a.assessment_id,
                evidence_ids=list(a.evidence_ids),
                source_ids=list(a.source_ids),
            )
            if c is not None:
                code_sentences.add(c.text)
                cands.append((c, a))
        amap = {a.assessment_id: a for _, a in cands}
        first = {o.assessment.assessment_id: o.sentence for o in outcomes}
        fails = (
            chk.check_claims(
                [c for c, _ in cands],
                registry=lp.ctx.registry,
                assessments=amap,
                first_sentences=first,
                guide_texts=_guide_texts(lp),
                code_sentences=code_sentences,
                cfg=lp.cfg,
            )
            if cands
            else []
        )
        bad = {f.claim_index for f in fails}
        for i, (c, a) in enumerate(cands):
            if i in bad:
                continue
            interim.append(c)
            newly.append(a)
            lp.st.exported.append(a.model_dump(mode="json"))
            lp.st.exported_sentences[a.assessment_id] = c.text
    return finish(
        status="NEEDS_ACTION",
        reason="NEEDS_ACTION",
        action=action,
        claims=interim,
        assessments=newly,
    )


def _assessment_head(o: Any) -> str:
    """판단 문장의 코드 부분: 첫 문장(템플릿) + 사실 문장. SUPPORTED 인 요청은 첫 문장이 비어 있어 근거 문구로 그린다."""
    head = " ".join(x for x in [o.sentence, *o.extra_sentences] if x)
    if head:
        return head
    a = o.assessment
    if a.verdict == "SUPPORTED" and o.evidence_phrase and a.type != "REQUEST":
        # 요청 · 정보 질문은 사실 주장이 아니므로 '기록과 맞아요'를 그리지 않는다(6-3 REQUEST)
        tpl = templates.KNOWLEDGE_SUPPORTED if a.type == "KNOWLEDGE" else templates.SUPPORTED
        return tpl.format(quote=a.quote, evidence=o.evidence_phrase)
    return ""


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


def _guide_texts(lp: Loop) -> dict[str, str]:
    """이번 실행에서 읽은 자료의 본문만(checks · claims 의 전제)."""
    corpus = lp.ctx.corpus
    out: dict[str, str] = {}
    if corpus is None:
        return out
    for sid in lp.ctx.registry.sources:
        t = corpus.text_of(sid)
        if t:
            out[sid] = t
    return out


def _end_without_model(
    lp: Loop, finish: Callable[..., _Result], reason: str, *, notes: list[str] | None = None
) -> _Result:
    """모델 없이 끝낸다(시간 · 호출 몫 부족): 검증된 근거를 템플릿으로, 없으면 `AGENT_TIMEOUT`(8-3)."""
    partial = compose.template_partial(lp.ctx, list(lp.ctx.registry.items))
    notes_claims = [Claim(kind="SYSTEM", text=n) for n in notes or []]
    if partial or notes_claims:
        return finish(
            status="PARTIAL",
            reason=reason,
            system=[templates.SystemText.LIMIT_PARTIAL] if not notes_claims else [],
            claims=[*notes_claims, *partial],
        )
    return finish(error="AGENT_TIMEOUT", reason=reason)


def _slots_short_end(lp: Loop, finish: Callable[..., _Result]) -> _Result:
    """이어 부를 때 남은 호출 수로 답 쓰기 · 고치기 두 몫을 남길 수 없다: 모델 없이 끝낸다(8-3)."""
    c, cfg = lp.counters, lp.cfg
    lp._limit(lp.budget._hit("MODEL_CALLS", c.model_calls, cfg.max_model_calls))
    return _end_without_model(lp, finish, "MODEL_CALLS", notes=_action_notes(lp))


def _answer(lp: Loop, req: TurnRequest, finish: Callable[..., _Result]) -> _Result:
    cfg = lp.cfg
    ctx = lp.ctx
    stop_reason = lp.st.stop_reason
    if stop_reason == "LLM_REFUSED":
        # 공급자가 조사 단계에서 거절하면 답을 쓰지 않고 거절로 끝낸다(L1-12)
        return finish(status="REFUSED", reason="OTHER", system=[templates.SystemText.CANNOT_ANSWER])
    ans = lp.answer_phase(route="ANSWER")
    if ans.get("failed"):
        if ans.get("refused"):
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
        if partial:
            return finish(
                status="PARTIAL",
                reason=limit or "LLM_ERROR",
                system=[templates.SystemText.PARTIAL_CHECKED],
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

    # 1. 판단 확정(답 단계의 초안이 있으면 그것, 없으면 계획 단계의 초안). 이미 내보낸 판단은 다시 확정하지 않는다
    answer_drafts = [d for d in draft.get("assessments") or [] if isinstance(d, dict)]
    if answer_drafts and not lp.st.exported:
        lp.st.drafts = answer_drafts
    exported = [Assessment.model_validate(a) for a in lp.st.exported]
    # 내보낸 판단은 다시 확정하지 않는다. 다만 이어 부르는 사이 기록 판이 바뀌었으면 새 자료로 다시 판단해
    # 앞 판단을 대체(supersedes)로 잇는다(앞 판단과 그 근거 사본은 그대로 남김, 5-5 · 6-3 · L1-32)
    refinal = bool(exported) and lp.record_changed
    result = (
        _finalize(lp, req, extra_prior=exported if refinal else None)
        if lp.original and (not exported or refinal)
        else None
    )
    outcomes = result.outcomes if result is not None else []
    all_assessments = exported + [o.assessment for o in outcomes]
    amap = {a.assessment_id: a for a in all_assessments}
    first: dict[str, str] = {o.assessment.assessment_id: o.sentence for o in outcomes}

    # 2. 문장 만들기. model_flags: 모델이 쓴 글이 든 문장인가(고칠 수 있는가)
    out: list[Claim] = []
    model_flags: list[bool] = []
    code_sentences: set[str] = set()

    def add(c: Claim | None, by_model: bool) -> None:
        if c is not None:
            out.append(c)
            model_flags.append(by_model)
            if not by_model:
                code_sentences.add(c.text)

    draft_claims = [c for c in draft.get("claims") or [] if isinstance(c, dict)]
    expl: dict[int, str] = {}
    for c in draft_claims:
        if c.get("kind") == "ASSESSMENT" and isinstance(c.get("assessment_index"), int):
            expl.setdefault(c["assessment_index"], (c.get("text") or "").strip())
    heads: dict[str, str] = {}
    for aid, sent in lp.st.exported_sentences.items():
        if aid in amap:
            heads[aid] = sent
            first.setdefault(aid, sent)
            a = amap[aid]
            add(
                compose._claim(
                    "ASSESSMENT",
                    sent,
                    assessment_id=aid,
                    evidence_ids=list(a.evidence_ids),
                    source_ids=list(a.source_ids),
                ),
                False,
            )
    for i, o in enumerate(outcomes):
        a = o.assessment
        head = _assessment_head(o)
        if head:
            heads[a.assessment_id] = head
            code_sentences.add(head)
        body = expl.get(i, "")
        text = f"{head} {body}".strip() if body and len(head) + len(body) < 299 else head
        if text:
            c = compose._claim(
                "ASSESSMENT",
                text,
                assessment_id=a.assessment_id,
                evidence_ids=list(a.evidence_ids),
                source_ids=list(a.source_ids),
            )
            add(c, text != head)
    if result is not None:
        for s in [*result.uncovered_sentences, *result.dropped_notice]:
            add(compose._claim("SYSTEM", s), False)
    model_texts: list[tuple[str, dict[str, Any]]] = []
    for c in draft_claims:
        kind = c.get("kind")
        if kind == "CONCLUSION":
            r = compose.conclusion_sentence(ctx, c.get("comparison_evidence_id") or "")
            if r is not None:
                add(compose._claim("RECORD", r[0], evidence_ids=r[1]), False)
        elif kind in ("RECORD", "GUIDE"):
            model_texts.append((kind, c))
    for kind, c in model_texts:
        text = (c.get("text") or "").strip()
        if text in code_sentences:
            continue  # 코드 문장과 글자만 같은 모델 문장은 버린다(코드 문장의 검사 면제를 흉내 내지 못하게)
        add(
            compose._claim(
                kind,
                text,
                evidence_ids=list(c.get("evidence_ids") or []),
                source_ids=list(c.get("source_ids") or []),
            ),
            True,
        )
    succeeded: dict[str, str] = {}
    for e in lp.st.executed:
        if e.get("status") == "SUCCEEDED" and e.get("success_text"):
            succeeded[e["action_id"]] = e["success_text"]
            add(
                Claim(kind="ACTION_RESULT", text=e["success_text"], action_id=e["action_id"]), False
            )

    # 3. 답 검사 → 한 번 고치기 → 다시 검사(6-9)
    approved = {
        sid
        for sid in ctx.registry.sources
        if ctx.corpus is not None and ctx.corpus.is_approved_cause_source(sid)
    }
    guide_texts = _guide_texts(lp)

    def recheck(cl: list[Claim]) -> list[chk.CheckFailure]:
        return chk.check_claims(
            cl,
            registry=ctx.registry,
            assessments=amap,
            first_sentences=first,
            guide_texts=guide_texts,
            succeeded_actions=succeeded,
            approved_cause_sources=approved,
            code_sentences=code_sentences,
            cfg=cfg,
        )

    failures = recheck(out)
    answer_seq = lp._last_model_seq
    check_seqs: list[int] = []
    # 범위 밖 낱말(SCOPE_RULE)이 걸린 문장은 고치기에 보내지 않고 바로 버린다(6-9). 한 문장에 다른 검사도 걸렸어도 같다
    scope_dropped = {f.claim_index for f in failures if f.check_id == chk.CheckId.SCOPE_RULE}
    for f in failures:
        detail = {
            "claim_index": f.claim_index,
            "claim_kind": f.claim_kind,
            "round": 0,
            "result": "DROPPED" if f.claim_index in scope_dropped else "FIX_REQUESTED",
        }
        check_seqs.append(lp.diag.step("CHECK", f.check_id, detail, parent_seq=answer_seq))
    final_claims = out
    rnd = 0
    if failures:
        rnd = 1
        fixable = [
            f
            for f in failures
            if f.claim_index is not None
            and model_flags[f.claim_index]
            and f.claim_index not in scope_dropped
        ]
        asked = {f.claim_index for f in fixable}
        fixed_texts = (
            lp.fix_call(
                ans["messages"], [{"text": c.text} for c in out], fixable, fix_for=check_seqs
            )
            if fixable
            else None
        )
        fixed: list[Claim] | None = None
        origin: list[
            int
        ] = []  # 고친 목록의 각 문장이 원래 몇 번째 문장이었는지(진단 기록을 원래 번호로 남기려고)
        if fixed_texts is not None:
            fixed = []
            for i, c in enumerate(out):
                if (
                    i not in fixed_texts or i not in asked
                ):  # 고치라고 하지 않은 문장(코드 문장 포함)은 고치기 결과로 바꾸거나 지우지 않는다
                    fixed.append(c)
                    origin.append(i)
                    continue
                t = fixed_texts[i]
                if t is None:
                    continue
                head = first.get(c.assessment_id or "")
                if c.kind == "ASSESSMENT" and head and not t.startswith(head):
                    t = f"{head} {t}"  # 판단 첫 문장(템플릿)은 고치는 단계도 바꾸지 못한다
                nc = compose._claim(
                    c.kind,
                    t,
                    assessment_id=c.assessment_id,
                    evidence_ids=list(c.evidence_ids),
                    source_ids=list(c.source_ids),
                    action_id=c.action_id,
                )
                if nc is not None:
                    fixed.append(nc)
                    origin.append(i)
        outcome = chk.apply_fix_round(out, fixed, failures, recheck=recheck)
        final_claims = list(outcome.claims)
        kept = {c.assessment_id for c in final_claims if c.kind == "ASSESSMENT"}
        restored: list[Claim] = []
        for c in out:
            head = heads.get(c.assessment_id or "")
            if c.kind != "ASSESSMENT" or c.assessment_id in kept or not head:
                continue
            hc = compose._claim(
                "ASSESSMENT",
                head,
                assessment_id=c.assessment_id,
                evidence_ids=list(c.evidence_ids),
                source_ids=list(c.source_ids),
            )
            if hc is not None and not recheck([hc]):
                restored.append(
                    hc
                )  # 모델 설명만 걸렸으면 코드가 확정한 판단 문장은 남긴다(6-3 · L1-38)
        final_claims = restored + final_claims
        fix_seq = lp._last_model_seq if fixed is not None else answer_seq
        for f in outcome.remaining_failures:
            # 다시 검사한 번호는 고친 목록 기준이라 원래 번호로 바꿔 처음 검사와 맞춘다
            oi = (
                origin[f.claim_index]
                if f.claim_index is not None and f.claim_index < len(origin)
                else f.claim_index
            )
            if oi in scope_dropped:
                continue  # 이미 처음 검사에서 DROPPED 로 남겼다
            detail = {
                "claim_index": oi,
                "claim_kind": f.claim_kind,
                "round": 1,
                "result": "DROPPED",
            }
            lp.diag.step("CHECK", f.check_id, detail, parent_seq=fix_seq)
    lp.diag.step(
        "CHECK",
        "SUMMARY",
        chk.summary_counts(failures, rnd, checked=len(out), dropped=len(out) - len(final_claims)),
    )

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
            original_message=lp.original,
            seq=lp.st.action_seq,
            budget=lp.budget,
        )
        if card:
            lp.st.cards.append(card)
        if action is not None:
            actions.append(action)
    history_summary = (
        draft.get("history_summary") if isinstance(draft.get("history_summary"), str) else None
    )
    if history_summary and (
        len(history_summary) > 600
        or chk.check_display_strings(
            history_summary=history_summary,
            registry=ctx.registry,
            display_evidence=None,
            cfg=cfg,
            assessments=amap,
        )
    ):
        history_summary = None

    # 5. 상태 정하기
    if not final_claims and actions and not out and not no_guide:
        # "바로 발표할래"처럼 연습 이동만 요청해 허용된 답: 문장이 없어도 이동이 답이다(6-2 START_PRACTICE).
        # 모델이 쓴 문장이 검사로 모두 빠졌거나 자료를 찾지 못한 답은 그대로 PARTIAL · 거절이다
        final_claims = [Claim(kind="SYSTEM", text=templates.SystemText.PRACTICE_BUTTON.value)]
    if not final_claims:
        notes = _action_notes(lp)
        if notes:
            # 행동 결과 안내만 있는 답(예: 다른 탭에서 목표가 바뀌어 저장하지 못함): 안내를 그대로 낸다
            return finish(
                status="PARTIAL",
                reason="OTHER",
                claims=[Claim(kind="SYSTEM", text=n) for n in notes],
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
    for note in reversed(_action_notes(lp)):
        if all(c.text != note for c in final_claims):
            final_claims.insert(0, Claim(kind="SYSTEM", text=note))
    if lp.record_changed:
        final_claims.insert(
            0, Claim(kind="SYSTEM", text=templates.SystemText.RECORD_CHANGED_DURING.value)
        )
    # 섞인 요청의 범위 밖 부분: 이유별 고정 문장을 답 문장 뒤에 덧붙인다(1-3 · 7-10)
    for reason in _scope_notes(draft):
        note = templates.scope_text(reason).value
        if all(c.text != note for c in final_claims):
            final_claims.append(Claim(kind="SYSTEM", text=note))
    stopped = _RESEARCH_STOP.get(stop_reason or "")
    if stopped is not None:
        # 조사가 끝까지 가지 못했으면 확인한 데까지만 말한다(8-2 · L1-11 · L1-12 · L1-16)
        return finish(
            status="PARTIAL",
            reason=stopped[0],
            system=[stopped[1]],
            claims=final_claims,
            assessments=all_assessments,
            history_summary=history_summary,
            sources=_sources(final_claims),
        )
    if no_guide:
        final_claims.append(
            Claim(kind="SYSTEM", text=templates.SystemText.KNOWLEDGE_NO_SOURCE.value)
        )
    return finish(
        status="DONE",
        reason="ANSWERED",
        claims=final_claims,
        assessments=all_assessments,
        actions=actions,
        history_summary=history_summary,
        sources=_sources(final_claims),
    )


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


def _referenced(ctx: Any, ids: list[str]) -> list[dict[str, Any]]:
    """답 · 판단 · 카드가 가리킨 근거와 그 입력(계산 근거를 따라감)의 사본(5-5: 입력 근거도 같은 목록에)."""
    out: dict[str, Any] = {}
    stack = list(ids)
    while stack:
        i = stack.pop()
        if i in out:
            continue
        ev = ctx.registry.get(i)
        if ev is None:
            continue
        out[i] = ev.model_dump(mode="json")
        stack.extend(ev.inputs or [])
    return list(out.values())


#: 검증된 결과로 코드가 만든 카드 종류
_CODE_CARDS = ("REASSESS_RESULT", "GOAL_RESULT", "START_PRACTICE")

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
    history_summary: str | None = None,
    goal_result: dict[str, Any] | None = None,
    sources: list[str] | None = None,
    error: str | None = None,
) -> _Result:
    cfg = deps.cfg
    ctx = lp.ctx
    st = lp.st
    lp.budget.sync_elapsed()
    c = lp.counters
    usage = {
        "model_calls": c.model_calls,
        "tool_calls": c.tool_calls,
        "elapsed_ms": c.elapsed_ms,
        "input_tokens": st.usage_in if st.usage_seen_in else None,
        "output_tokens": st.usage_out if st.usage_seen_out else None,
        "measured": not st.usage_missing,
        "missing_reason": "PROVIDER_USAGE_MISSING" if st.usage_missing else None,
    }
    if error is not None:
        d = lp.diag.finish(error, reason, usage=usage, max_bytes=cfg.max_diagnostics_bytes)
        return None, error, d.model_dump(mode="json")
    claim_list: list[Claim] = [Claim(kind="SYSTEM", text=s.value) for s in (system or [])] + list(
        claims or []
    )
    assessments_out = [
        a.model_dump(mode="json") if hasattr(a, "model_dump") else a for a in (assessments or [])
    ]
    if req.run.trigger == "TAKE_REVIEWED":
        assessments_out = []
    amap = {a["assessment_id"]: Assessment.model_validate(a) for a in assessments_out}
    plan = st.plan
    if status == "DONE":
        plan = [
            {**p, "status": "DONE" if p["status"] in ("DOING", "TODO") else p["status"]}
            for p in plan
        ]
    cards = _safe_cards(lp, st.cards, amap) if status != "NEEDS_ACTION" else []
    msg = None
    if claim_list or status != "NEEDS_ACTION":
        if not claim_list:
            claim_list = [Claim(kind="SYSTEM", text=templates.SystemText.PARTIAL_CHECKED.value)]
        text, ordered = chk.assemble_message(claim_list)
        msg = {
            "text": text,
            "claims": [cl.model_dump(mode="json") for cl in ordered],
            "cards": cards,
            "sources": [g.model_dump(mode="json") for g in compose.guide_cards(ctx, sources or [])],
        }
    ev_ids = [e for cl in claim_list for e in cl.evidence_ids]
    ev_ids += [e for a in assessments_out for e in a.get("evidence_ids", [])]
    ev_ids += [e for cd in cards for e in cd.get("evidence_ids") or []]
    ev_ids += [
        e for cd in cards for o in cd.get("options") or [] for e in o.get("evidence_ids") or []
    ]
    act_list = [action] if action is not None else list(actions or [])
    final: dict[str, Any] = {
        "status": status,
        "message": msg,
        "actions": [a.model_dump(mode="json") for a in act_list],
        "state": None,
        "scope_pitch_id": ctx.scope_pitch_id,
        "plan": plan,
        "trace": _safe_trace(lp, st.trace),
        "basis": ctx.basis(sources or []),
        "evidence": _referenced(ctx, ev_ids),
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
        st.registry = ctx.registry.dump()
        st.counters = c.dump()
        st.tool_cache = ctx.tool_cache
        st.read_sources = sorted(ctx.read_sources)
        st.corpus_version = deps.corpus_version
        issued, expires = state.issue_times(deps.clock_ms(), ask=False, cfg=cfg)
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
            final.update(
                status="PARTIAL",
                actions=[],
                state=None,
                scope_pitch_id=payload.scope_pitch_id
                if payload is not None
                else req.run.scope_pitch_id,
                message={
                    "text": sys_claim.text,
                    "claims": [sys_claim.model_dump(mode="json")],
                    "cards": [],
                    "sources": [],
                },
            )
            reason = "LIMIT"
    d = lp.diag.finish(final["status"], reason, usage=usage, max_bytes=cfg.max_diagnostics_bytes)
    final["diagnostics"] = d.model_dump(mode="json")
    try:
        FinalData.model_validate(final)
    except ValidationError as e:
        deps.log(
            {
                "event": "agent_final_invalid",
                "run_id": req.run.run_id,
                "errors": [list(x.get("loc", ())) for x in e.errors()][:10],
            }
        )
        return None, "INTERNAL_ERROR", None
    return final, None, None


__all__ = ["handle_turn", "TurnResponse", "PreStreamError"]
