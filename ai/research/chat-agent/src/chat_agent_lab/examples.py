"""`/agent/turn` 입출력 예시 JSON 만들기 (기능 문서 5-10, 연구 전용).

- 무엇을: 코어 `chat_agent/examples/`의 요청(처음 · 이어 부르기 · 자동 목표 확인) · 스트림(행동 요청 · 답 완료 ·
  부분 답 · 거절 · 오류 줄) · 스트림 전 오류 · 행동 결과 예시를 만든다. 계약 시험(tests/unit/test_contract_examples.py)이
  읽어 5절 모양과 맞는지 보고, BE는 서비스 탑재 뒤 같은 파일로 계약 시험을 한다.
- 실행: 연구 폴더에서 `.\\.venv\\Scripts\\python.exe -m chat_agent_lab.examples` (파일을 다시 쓴다).
- 값: 가상 기록(fixtures.sample_pitch: '졸업 발표' Take 4 · 5의 3장 시선 31% · 28%)으로 만든다.
  `state`는 예시 전용 키(`EXAMPLE_STATE_KEY`, 비밀 아님)로 실제 서명한다. 대기 행동 · 누적량이 이어 부르기 예시와 맞아,
  같은 키로 서명 검사 · 행동 결과 짝 검사를 통과한다(test_contract_examples). 판단 키 · 지문은 형식만 맞춘 자리 값이다.
- 프롬프트 · 설정을 바꾸면 판 값이 바뀌므로 이 파일로 다시 만든다(예시 파일이 만들기 결과와 같은지 시험이 본다).
- 누적량: `budget_used`의 입력 · 출력 토큰은 예약 뒤 공급자 실측으로 정산한 값이라 같은 예시의 `usage` 실측과 같다(5-3 · 8-3).
- 고칠 때: 5절 모양을 바꾸면 이 파일로 예시를 다시 만들고 같은 PR에 넣는다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from chat_agent import state as st
from chat_agent.actions import action_args_hash
from chat_agent.config import DEFAULT_CONFIG
from chat_agent.prompts import PROMPT_VERSION
from chat_agent.templates import SystemText
from chat_agent.version import FEATURE_VERSION, SCHEMA_VERSION, TOOL_SCHEMA_VERSION

from . import fixtures as fx

EXAMPLES = Path(__file__).resolve().parents[1] / "chat_agent" / "examples"

RUN = {"run_id": "run-81", "conversation_id": "c-12", "user_id": "u-3", "scope_pitch_id": "p-1"}
PAGE = {"route": "/takes/t-5", "pitch_id": "p-1", "take_id": "t-5", "screen": None}
USER_MESSAGE = "3장에서 왜 자꾸 시선이 떨어져? 그걸 목표로 잡고 다음 연습 준비해 줘"
GAZE3 = "take_result.areas.GAZE.slides[slide_number=3].metrics.audience_ratio"
META = {
    "schema_version": SCHEMA_VERSION,
    "feature_version": FEATURE_VERSION,
    "model": "openai/gpt-6-luna",
    "prompt_version": PROMPT_VERSION,
    "corpus_version": "guides-4",
    "criteria_versions": {"chat_agent": DEFAULT_CONFIG.criteria_version()},
}
#: 예시 전용 서명 키(비밀 아님). 실제 키는 AGENT_STATE_SECRET 이다
EXAMPLE_STATE_KEY = b"pitchcoach-example-state-key-not-secret"
#: 예시 상태의 발급 시각(ms, 2026-11-04 10:04:00+09:00). 시험은 이 시각으로 검증한다
EXAMPLE_ISSUED_MS = 1_793_754_240_000
#: 행동 요청 응답 때의 누적량: 조사 3번 + 승인 대기 앞 답 쓰기 1번(6-1 4번)
NEEDS_ACTION_BUDGET = {
    "model_calls": 4,
    "tool_calls": 4,
    "elapsed_ms": 10500,
    "input_tokens": 12400,
    "output_tokens": 960,
}
FP = "5f2a9c0d1e3b4a67"


def _context(
    pitch: fx.PitchRecord, *, focus: str = "t-5", goal: dict | None = None, goal_version: int = 0
) -> dict:
    return {
        "pitches": [fx.pitch_summary(pitch)],
        "pitches_total": 1,
        "pitch": fx.pitch_context(pitch, focus_take_id=focus),
        "conversation_goal": goal,
        "goal_version": goal_version,
        "loaded_takes": [],
        "removed_sources": [],
        "selected_proposal": None,
    }


def _evidence(eid: str, take: int, value: float) -> dict:
    return {
        "evidence_id": eid,
        "origin": "RECORD",
        "pitch_id": "p-1",
        "take_id": f"t-{take}",
        "take_number": take,
        "kind": "TAKE_RESULT",
        "revision": 1,
        "path": GAZE3,
        "metric": "audience_ratio",
        "slide_number": 3,
        "value": value,
        "unit": "ratio",
        "rule_version": fx.CRITERIA_VERSIONS["GAZE"],
        "saved": None,
        "screen_snapshot_id": None,
        "inputs": None,
        "calc": None,
    }


def _goal_draft() -> dict:
    return {
        "pitch_id": "p-1",
        "metric": "audience_ratio",
        "slide_number": 3,
        "operator": "gt_base",
        "script_version_id": "sv-1",
        "standard_version": 1,
        "goal_rule_version": DEFAULT_CONFIG.goal_rule_version(),
        "base": {
            "take_id": "t-5",
            "take_number": 5,
            "kind": "TAKE_RESULT",
            "revision": 1,
            "value": 0.28,
            "metric_rule_version": fx.CRITERIA_VERSIONS["GAZE"],
            "range": None,
        },
        "min_delta": 0.05,
    }


def _goal_saved() -> dict:
    return {
        **_goal_draft(),
        "version": 1,
        "created_at": "2026-11-04T10:05:00+09:00",
        "status": "OPEN",
    }


def _basis() -> dict:
    return {
        "records": [
            {
                "pitch_id": "p-1",
                "script_version_id": "sv-1",
                "presentation_version_id": "pv-1",
                "standard_version": 1,
                "takes": [
                    {
                        "take_id": "t-4",
                        "results": {"TAKE_RESULT": 1},
                        "rule_versions": {"GAZE": fx.CRITERIA_VERSIONS["GAZE"]},
                    },
                    {
                        "take_id": "t-5",
                        "results": {"TAKE_RESULT": 1},
                        "rule_versions": {"GAZE": fx.CRITERIA_VERSIONS["GAZE"]},
                    },
                ],
            }
        ],
        "sources": [],
        "screens": [],
        "fingerprint": FP,
    }


def _assessments() -> list[dict]:
    return [
        {
            "assessment_id": "as-1",
            "quote": "3장에서 왜 자꾸 시선이 떨어져",
            "type": "MEASURED",
            "speaker": "USER",
            "speaker_label": None,
            "claim_op": "ISSUE",
            "subject": {
                "dimension": "audience_gaze",
                "metric": "audience_ratio",
                "slide_number": 3,
                "take_ids": ["t-4", "t-5"],
                "relation": "ALL",
            },
            "value": None,
            "value_unit": None,
            "polarity": "NEGATIVE",
            "verdict": "SUPPORTED",
            "verdict_source": "CODE",
            "evidence_ids": ["e1", "e2"],
            "source_ids": [],
            "reason": "Take 4 · 5 모두 3장 시선 비율이 다른 장보다 낮다",
            "claim_key": "ck-7d1e0a",
            "claim_link_key": "cl-3f9b21",
            "evidence_basis": _basis(),
            "supersedes": [],
        },
        {
            "assessment_id": "as-2",
            "quote": "그걸 목표로 잡고 다음 연습 준비해 줘",
            "type": "REQUEST",
            "speaker": "USER",
            "speaker_label": None,
            "claim_op": None,
            "subject": {
                "dimension": "audience_gaze",
                "metric": "audience_ratio",
                "slide_number": 3,
                "take_ids": ["t-5"],
                "relation": None,
            },
            "value": None,
            "value_unit": None,
            "polarity": "NEUTRAL",
            "verdict": "SUPPORTED",
            "verdict_source": "MODEL",
            "evidence_ids": ["e2"],
            "source_ids": [],
            "reason": "3장 시선은 우선 개선 차원이라 목표로 맞다",
            "claim_key": "ck-90aa12",
            "claim_link_key": "cl-90aa12",
            "evidence_basis": _basis(),
            "supersedes": [],
        },
    ]


def _plan(done: bool) -> list[dict]:
    st = "DONE" if done else "TODO"
    return [
        {"step": "최근 Take 비교", "status": "DONE" if done else "DOING", "note": None},
        {"step": "3장 문제 구간 확인", "status": st, "note": None},
        {"step": "발표법 자료 찾기", "status": st, "note": None},
        {"step": "대화 목표 저장", "status": st, "note": None},
    ]


TRACE = {
    "seq": 1,
    "tool": "compare_takes",
    "args_summary": "Take 4 · 5 · audience_ratio · 3장",
    "result_summary": "3장 audience_ratio 둘 다 낮음",
    "ms": 4,
    "outcome": "OK",
}


def _diag(
    turn_call: int, status: str, reason: str, steps: list[dict], run_id: str = "run-81"
) -> dict:
    return {
        "v": 1,
        "request_id": f"req-{run_id[4:]}-{turn_call}",
        "run_id": run_id,
        "turn_call": turn_call,
        "versions": {
            "feature_version": FEATURE_VERSION,
            "prompt_version": META["prompt_version"],
            "tool_schema": TOOL_SCHEMA_VERSION,
            "corpus_version": "guides-4",
        },
        "steps": steps,
        "end": {"status": status, "reason": reason, "omitted_steps": 0},
        "truncated": False,
    }


def _model_step(
    seq: int, code: str, start: int, tool_calls: int, finish: str = "TOOL_CALLS"
) -> dict:
    return {
        "seq": seq,
        "kind": "MODEL",
        "code": code,
        "start_ms": start,
        "ms": 2100,
        "parent_seq": None,
        "detail": {
            "model": "openai/gpt-6-luna",
            "success": True,
            "input_tokens": 3100,
            "cached_input_tokens": 0,
            "output_tokens": 240,
            "finish_reason": finish,
            "tool_call_count": tool_calls,
            "error_code": None,
            "fix_for": None,
        },
    }


def _save_goal_action() -> dict:
    args = {"expected_goal_version": 0, "goal": _goal_draft()}
    return {
        "action_id": "run-81:a1",
        "type": "SAVE_GOAL",
        "args": args,
        "autonomy": "ASK",
        "args_hash": action_args_hash("SAVE_GOAL", args),
        "idempotency_key": "run-81:1",
        "reason": "3장 시선 비율을 Take 5보다 높이는 목표",
        "authorized_by": "그걸 목표로 잡고",
        "preapproved": None,
    }


def _start_practice_action() -> dict:
    return {
        "action_id": "run-81:a2",
        "type": "START_PRACTICE",
        "args": {"pitch_id": "p-1"},
        "autonomy": "BUTTON",
        "args_hash": None,
        "idempotency_key": "run-81:2",
        "reason": "연습 준비 화면으로 이동",
        "authorized_by": "다음 연습 준비해 줘",
        "preapproved": None,
    }


def _claim_assessment() -> dict:
    return {
        "kind": "ASSESSMENT",
        "assessment_id": "as-1",
        "text": "말씀하신 '3장에서 왜 자꾸 시선이 떨어져'는 기록과 맞아요: Take 4 · 5 모두 3장 시선 비율이 31% · 28%로 낮아요.",
        "evidence_ids": ["e1", "e2"],
        "source_ids": [],
        "action_id": None,
    }


def _claim_record() -> dict:
    return {
        "kind": "RECORD",
        "assessment_id": None,
        "text": "Take 4 · 5 모두 3장 시선 비율이 31% · 28%였어요",
        "evidence_ids": ["e1", "e2"],
        "source_ids": [],
        "action_id": None,
    }


def _ask_confirm_card() -> dict:
    """확인 카드: 대기 행동의 args 로 코드가 그린다(모델 문장 없음, 5-2 · 6-2)."""
    action = _save_goal_action()
    expires = datetime.fromtimestamp((EXAMPLE_ISSUED_MS + 1_800_000) / 1000, tz=UTC)
    return {
        "type": "ASK_CONFIRM",
        "action_id": action["action_id"],
        "action_type": "SAVE_GOAL",
        "title": "대화 목표 저장",
        "summary": "Take 5의 3장 청중 시선(28%)보다 높이는 목표예요.",
        "before": None,
        "after": _goal_draft(),
        "notes": ["저장한 뒤에도 되돌릴 수 있어요."],
        "expires_at": expires.isoformat(timespec="seconds"),
        "args_hash": action["args_hash"],
    }


def _signed_state() -> dict:
    """행동 요청 예시의 서명 상태. 대기 행동은 위 SAVE_GOAL, 누적량은 NEEDS_ACTION_BUDGET 이다."""
    pending = {
        **_save_goal_action(),
        "_call_id": "call-3",
        "_tool_name": "save_goal",
        "_tool_args": None,
    }
    payload = st.StatePayload(
        run_id=RUN["run_id"],
        conversation_id=RUN["conversation_id"],
        user_id=RUN["user_id"],
        state_revision=0,
        scope_pitch_id=RUN["scope_pitch_id"],
        trigger="USER_MESSAGE",
        original_user_message=USER_MESSAGE,
        versions=st.RunVersions(
            feature=FEATURE_VERSION,
            prompt=PROMPT_VERSION,
            tool_schema=TOOL_SCHEMA_VERSION,
            corpus="guides-4",
        ),
        issued_at=EXAMPLE_ISSUED_MS,
        # 승인 대기로 멈춘 상태는 발급 + 35분이다(5-6)
        expires_at=EXAMPLE_ISSUED_MS + DEFAULT_CONFIG.state_ttl_ask_seconds * 1000,
        turn_count=1,
        loop={
            "pending_action": pending,
            "counters": {**NEEDS_ACTION_BUDGET, "turn_count": 1, "actions": 1, "saves": 1},
            "action_seq": 1,
            # 멈추기 전에 낸 문장 · 판단: 이어 부른 뒤 최종 답의 앞부분에 그대로 싣는다(5-5)
            "emitted": [_claim_assessment(), _claim_record()],
            "emitted_sources": [],
            "exported": _assessments(),
        },
    )
    return st.sign(payload, EXAMPLE_STATE_KEY)


def _final_needs_action() -> dict:
    claims = [_claim_assessment(), _claim_record()]
    return {
        "status": "NEEDS_ACTION",
        # ASK 행동: 그때까지 확인한 것으로 낸 문장과 확인 카드를 함께 싣는다(5-5)
        "message": {
            "text": "\n".join(c["text"] for c in claims),
            "claims": claims,
            "cards": [_ask_confirm_card()],
            "sources": [],
        },
        "actions": [_save_goal_action()],
        "state": _signed_state(),
        "scope_pitch_id": "p-1",
        "plan": _plan(False),
        "trace": [TRACE],
        "basis": {
            "pitches": [],
            "conversation_goal_version": 0,
            "corpus_version": "guides-4",
            "sources": [],
        },
        "evidence": [_evidence("e1", 4, 0.31), _evidence("e2", 5, 0.28)],
        "history_summary": None,
        "budget_used": dict(NEEDS_ACTION_BUDGET),
        "usage": {
            "model_calls": 4,
            "tool_calls": 4,
            "elapsed_ms": 10500,
            "input_tokens": 12400,
            "output_tokens": 960,
            "measured": True,
            "missing_reason": None,
        },
        "assessments": _assessments(),
        "goal_result": None,
        "meta": META,
        "diagnostics": _diag(
            1,
            "NEEDS_ACTION",
            "NEEDS_ACTION",
            [
                _model_step(1, "RESEARCH", 5, 1),
                {
                    "seq": 2,
                    "kind": "TOOL",
                    "code": "compare_takes",
                    "start_ms": 2110,
                    "ms": 4,
                    "parent_seq": 1,
                    "detail": {
                        "target_ids": ["t-4", "t-5"],
                        "revisions": {"t-4": 1, "t-5": 1},
                        "outcome": "OK",
                        "result_bytes": 610,
                        "query_chars": None,
                        "error_code": None,
                    },
                },
                {
                    "seq": 3,
                    "kind": "ACTION",
                    "code": "SAVE_GOAL",
                    "start_ms": 8390,
                    "ms": None,
                    "parent_seq": 1,
                    "detail": {
                        "action_id": "run-81:a1",
                        "autonomy": "ASK",
                        "authorization": "PASS",
                        "result": "PROPOSED",
                        "reason_code": None,
                    },
                },
                _model_step(4, "ANSWER", 8400, 0, "STOP"),
            ],
        ),
    }


def _final_done() -> dict:
    # 최종 답의 앞부분은 승인 대기로 멈추기 전에 낸 문장 그대로이고, 그 뒤에 행동 결과 문장 · 나머지 답이 이어진다(5-5)
    claims = [
        _claim_assessment(),
        _claim_record(),
        {
            "kind": "ACTION_RESULT",
            "assessment_id": None,
            "text": "목표를 저장했어요.",
            "evidence_ids": [],
            "source_ids": [],
            "action_id": "run-81:a1",
        },
        {
            "kind": "GUIDE",
            "assessment_id": None,
            "text": "핵심 단어만 적은 메모로 연습하면 대본 의존을 줄일 수 있어요",
            "evidence_ids": [],
            "source_ids": ["g-02"],
            "action_id": None,
        },
        # 자료가 모자란 부분의 일반 조언(6-10): 코드 고정 안내 바로 뒤, 근거 · 자료 칩 없이 일반 조언 라벨로 그린다(L3-42)
        {
            "kind": "SYSTEM",
            "assessment_id": None,
            "text": SystemText.GENERAL_INTRO.value,
            "evidence_ids": [],
            "source_ids": [],
            "action_id": None,
        },
        {
            "kind": "GENERAL",
            "assessment_id": None,
            "text": "청중 몇 사람과 차례로 눈을 맞추며 말하면 좋아요.",
            "evidence_ids": [],
            "source_ids": [],
            "action_id": None,
        },
    ]
    return {
        "status": "DONE",
        "message": {
            "text": "\n".join(c["text"] for c in claims),
            "claims": claims,
            "cards": [
                {
                    "type": "GOAL_SAVED",
                    "action_id": "run-81:a1",
                    "goal": _goal_saved(),
                    "reason": "3장 시선 비율을 Take 5보다 높이는 목표",
                    "evidence_ids": ["e2"],
                    "undo": True,
                },
                {"type": "START_PRACTICE", "pitch_id": "p-1"},
            ],
            "sources": [
                {
                    "source_id": "g-02",
                    "title": "대본 의존 줄이기",
                    "quote": "핵심 단어만 적은 메모로 연습하면 대본 의존을 줄일 수 있어요",
                    "source": "팀 작성",
                    "license": "team",
                }
            ],
        },
        "actions": [_start_practice_action()],
        "state": None,
        "scope_pitch_id": "p-1",
        "plan": _plan(True),
        "trace": [TRACE],
        "basis": {
            "pitches": [
                {
                    "pitch_id": "p-1",
                    "script_version_id": "sv-1",
                    "presentation_version_id": "pv-1",
                    "standard_version": 1,
                    "takes": [
                        {
                            "take_id": "t-5",
                            "take_number": 5,
                            "script_version_id": "sv-1",
                            "presentation_version_id": "pv-1",
                            "standard_version": 1,
                            "results": {"REVIEW": 1, "TAKE_RESULT": 1},
                            "rule_versions": {"GAZE": fx.CRITERIA_VERSIONS["GAZE"]},
                        },
                        {
                            "take_id": "t-4",
                            "take_number": 4,
                            "script_version_id": "sv-1",
                            "presentation_version_id": "pv-1",
                            "standard_version": 1,
                            "results": {"TAKE_RESULT": 1},
                            "rule_versions": {"GAZE": fx.CRITERIA_VERSIONS["GAZE"]},
                        },
                    ],
                }
            ],
            "conversation_goal_version": 1,
            "corpus_version": "guides-4",
            "sources": ["g-02"],
        },
        "evidence": [_evidence("e1", 4, 0.31), _evidence("e2", 5, 0.28)],
        "history_summary": None,
        "budget_used": {
            "model_calls": 6,
            "tool_calls": 4,
            "elapsed_ms": 18000,
            "input_tokens": 19500,
            "output_tokens": 1480,
        },
        "usage": {
            "model_calls": 6,
            "tool_calls": 4,
            "elapsed_ms": 18000,
            "input_tokens": 19500,
            "output_tokens": 1480,
            "measured": True,
            "missing_reason": None,
        },
        "assessments": _assessments(),
        "goal_result": None,
        "meta": META,
        "diagnostics": _diag(2, "DONE", "ANSWERED", [_model_step(1, "ANSWER", 10, 0, "STOP")]),
    }


def _final_simple(status: str, text: str, reason: str) -> dict:
    claims = [
        {
            "kind": "SYSTEM",
            "assessment_id": None,
            "text": text,
            "evidence_ids": [],
            "source_ids": [],
            "action_id": None,
        }
    ]
    return {
        "status": status,
        "message": {"text": text, "claims": claims, "cards": [], "sources": []},
        "actions": [],
        "state": None,
        "scope_pitch_id": "p-1",
        "plan": [],
        "trace": [],
        "basis": {
            "pitches": [],
            "conversation_goal_version": 0,
            "corpus_version": "guides-4",
            "sources": [],
        },
        "evidence": [],
        "history_summary": None,
        "budget_used": {
            "model_calls": 1,
            "tool_calls": 0,
            "elapsed_ms": 2300,
            "input_tokens": 2900,
            "output_tokens": 80,
        },
        "usage": {
            "model_calls": 1,
            "tool_calls": 0,
            "elapsed_ms": 2300,
            "input_tokens": 2900,
            "output_tokens": 80,
            "measured": True,
            "missing_reason": None,
        },
        "assessments": [],
        "goal_result": None,
        "meta": META,
        "diagnostics": _diag(1, status, reason, [_model_step(1, "ANSWER", 5, 0, "STOP")]),
    }


def _line(event: str, seq: int, data: dict) -> str:
    return json.dumps(
        {"event": event, "run_id": "run-81", "seq": seq, "data": data},
        ensure_ascii=False,
        allow_nan=False,
    )


def _referenced(ev: dict[str, dict], ids: list[str]) -> list[dict]:
    """근거 id 와 그 입력(계산 근거)을 따라간 사본(코어 compose.referenced 와 같은 순서)."""
    out: dict[str, dict] = {}
    stack = list(ids)
    while stack:
        i = stack.pop()
        if i in out or i not in ev:
            continue
        out[i] = ev[i]
        stack.extend(ev[i].get("inputs") or [])
    return list(out.values())


def _claim_lines(final: dict, seq: int, *, before: dict | None = None) -> list[str]:
    """끝 줄 message 의 문장 중 아직 줄로 내지 않은 문장마다 문장 줄(5-4). `before`는 같은 실행의 앞 응답 끝 줄이다:
    그 문장 수부터 번호를 잇고, 그 문장 · 판단이 쓴 근거 · 자료 · 판단은 이미 보낸 것으로 센다(answer.py `ClaimLines`)."""
    msg = final["message"]
    ev = {e["evidence_id"]: e for e in final["evidence"]}
    src = {g["source_id"]: g for g in msg["sources"]}
    base = 0
    sent_ev: set[str] = set()
    sent_src: set[str] = set()
    sent_as: set[str] = set()
    if before is not None:
        prev_claims = before["message"]["claims"]
        base = len(prev_claims)
        prev_ev = {e["evidence_id"]: e for e in before["evidence"]}
        ids = [e for c in prev_claims for e in c["evidence_ids"]]
        ids += [e for a in before["assessments"] for e in a.get("evidence_ids") or []]
        sent_ev = {e["evidence_id"] for e in _referenced(prev_ev, ids)}
        sent_src = {s for c in prev_claims for s in c["source_ids"]}
        sent_as = {a["assessment_id"] for a in before["assessments"]}
    out = []
    for k, c in enumerate(msg["claims"][base:]):
        new_as = [a for a in final["assessments"] if a["assessment_id"] not in sent_as]
        ids = [*c["evidence_ids"], *(e for a in new_as for e in a.get("evidence_ids") or [])]
        evs = [e for e in _referenced(ev, ids) if e["evidence_id"] not in sent_ev]
        sids = [*c["source_ids"], *(s for a in new_as for s in a.get("source_ids") or [])]
        sids = [s for s in dict.fromkeys(sids) if s not in sent_src and s in src]
        data = {
            "index": base + k,
            "claim": c,
            "evidence": evs,
            "sources": [src[s] for s in sids],
            "assessments": new_as,
        }
        out.append(_line("claim", seq + k, data))
        sent_ev.update(e["evidence_id"] for e in evs)
        sent_src.update(sids)
        sent_as.update(a["assessment_id"] for a in new_as)
    return out


def _stream(progress: list[tuple[str, dict]], final: dict, *, before: dict | None = None) -> str:
    """진행 줄 → 문장 줄 → 끝 줄(5-4)."""
    lines = [_line(ev, i, data) for i, (ev, data) in enumerate(progress, start=1)]
    lines += _claim_lines(final, len(lines) + 1, before=before)
    lines.append(_line("final", len(lines) + 1, final))
    return "\n".join(lines)


def build() -> dict[str, Any]:
    """파일 이름 → 내용(JSON 객체 또는 NDJSON 문자열)."""
    pitch = fx.sample_pitch()
    budget0 = {
        "model_calls": 0,
        "tool_calls": 0,
        "elapsed_ms": 0,
        "input_tokens": 0,
        "output_tokens": 0,
    }
    req_user = {
        "run": {**RUN, "trigger": "USER_MESSAGE", "state_revision": 0},
        "user_message": USER_MESSAGE,
        "choice": None,
        "review_event": None,
        "page": PAGE,
        "history": {"summary": None, "messages": [], "prior_assessments": [], "overflow": None},
        "context": _context(pitch),
        "resume": None,
        "budget_used": budget0,
        # 코칭 프로필 기억(5-3 · 6-14): 켜짐, 저장한 항목 없음
        "memory": {"enabled": True, "memory_version": 0, "profile": []},
    }
    saved_goal = _goal_saved()
    req_resume = {
        **req_user,
        "run": {**RUN, "trigger": "ACTION_RESULT", "state_revision": 0},
        "user_message": None,
        "resume": {
            "state": _signed_state(),
            "action_results": [
                {
                    "action_id": "run-81:a1",
                    "type": "SAVE_GOAL",
                    "status": "SUCCEEDED",
                    "data": {"goal": saved_goal},
                    "error": None,
                    # 승인 뒤 BE 가 실행한 args 의 해시: 대기 행동의 args_hash 와 같아야 한다(5-8)
                    "executed_args_hash": action_args_hash(
                        "SAVE_GOAL", _save_goal_action()["args"]
                    ),
                }
            ],
        },
        "context": _context(pitch, goal=saved_goal, goal_version=1),
        "budget_used": {
            "model_calls": 4,
            "tool_calls": 4,
            "elapsed_ms": 11200,
            "input_tokens": 12400,
            "output_tokens": 960,
        },
    }
    # 자동 목표 확인: Take 6(3장 시선 41%)의 리뷰가 저장된 뒤
    t6 = fx.TakeRecord(
        take_id="t-6",
        take_number=6,
        # 목표(2026-11-04 10:05 저장) 뒤에 시작한 Take 만 자동 목표 확인 대상이다(5-1 · 6-4)
        started_at="2026-11-04T11:00:00+09:00",
        completed_at="2026-11-04T11:10:00+09:00",
        take_result=fx.take_result(
            take_id="t-6", audience=[0.6, 0.6, 0.41, 0.6], script_ratio=[0.15, 0.2, 0.3, 0.18]
        ),
        review=fx.review(
            take_id="t-6", dimensions={"audience_gaze": "IMPROVED", "fluency": "STRENGTH"}, score=79
        ),
    )
    pitch6 = fx.sample_pitch()
    pitch6.takes.append(t6)
    req_reviewed = {
        "run": {**RUN, "trigger": "TAKE_REVIEWED", "state_revision": 0, "run_id": "run-90"},
        "user_message": None,
        "choice": None,
        "review_event": {"take_id": "t-6", "review_revision": 1, "goal_version": 1},
        "page": {"route": "", "pitch_id": "p-1", "take_id": None, "screen": None},
        "history": {"summary": None, "messages": [], "prior_assessments": [], "overflow": None},
        "context": _context(pitch6, focus="t-6", goal=saved_goal, goal_version=1),
        "resume": None,
        "budget_used": budget0,
        # 코칭 프로필 기억(5-3 · 6-14): 켜짐, 저장한 항목 없음
        "memory": {"enabled": True, "memory_version": 0, "profile": []},
    }
    paused = _final_needs_action()
    needs_action = _stream(
        [("plan", {"plan": _plan(False)}), ("tool_done", {"trace": TRACE})], paused
    )
    # 승인 뒤 이어 부른 응답: 문장 줄 번호는 멈추기 전 문장 수부터 잇는다
    done = _stream([], _final_done(), before=paused)
    partial = _stream(
        [],
        _final_simple(
            "PARTIAL", "시간이나 처리 한도에 닿아 확인한 내용까지만 알려드릴게요.", "TIME"
        ),
    )
    refused = _stream(
        [], _final_simple("REFUSED", "발표 연습과 관계없는 질문이에요.", "OUT_OF_SCOPE")
    )
    error = "\n".join(
        [
            _line("plan", 1, {"plan": _plan(False)}),
            _line(
                "error",
                2,
                {
                    "error": {
                        "code": "AGENT_OUTPUT_TOO_LARGE",
                        "message": "출력이 줄 · 누적 크기 한도를 넘었다",
                    }
                },
            ),
        ]
    )
    goal_result = _final_simple("DONE", "말씀하신 목표 결과를 확인했어요.", "ANSWERED")
    goal_result["message"]["claims"] = [
        {
            "kind": "RECORD",
            "assessment_id": None,
            "text": "Take 6의 3장 시선 비율은 41%로 Take 5의 28%보다 13%p 높아 목표를 이뤘어요",
            "evidence_ids": ["e5", "e6", "c1"],
            "source_ids": [],
            "action_id": None,
        }
    ]
    goal_result["message"]["text"] = goal_result["message"]["claims"][0]["text"]
    goal_result["message"]["cards"] = [
        {
            "type": "GOAL_RESULT",
            "goal_version": 1,
            "take_id": "t-6",
            "status": "ACHIEVED",
            "observed": 0.41,
            "reason": "3장 시선 비율 0.41 ≥ 기준 0.28 + 0.05",
        }
    ]
    goal_result["goal_result"] = {
        "goal_version": 1,
        "take_id": "t-6",
        "review_revision": 1,
        "status": "ACHIEVED",
        "observed": 0.41,
        "reason": "3장 시선 비율 0.41 ≥ 기준 0.28 + 0.05",
        "terminal": True,
    }
    goal_result["evidence"] = [
        _evidence("e5", 5, 0.28),
        {**_evidence("e6", 6, 0.41), "take_id": "t-6"},
        {
            "evidence_id": "c1",
            "origin": "COMPUTED",
            "pitch_id": "p-1",
            "take_id": None,
            "take_number": None,
            "kind": None,
            "revision": None,
            "path": "goal.audience_ratio.slide3.diff",
            "metric": "audience_ratio",
            "slide_number": 3,
            "value": 0.13,
            "unit": "ratio",
            "rule_version": DEFAULT_CONFIG.goal_rule_version(),
            "saved": None,
            "screen_snapshot_id": None,
            "inputs": ["e5", "e6"],
            "calc": "GOAL_DIFF",
        },
    ]
    goal_result["scope_pitch_id"] = "p-1"
    goal_result["diagnostics"] = _diag(
        1, "DONE", "ANSWERED", [_model_step(1, "ANSWER", 5, 0, "STOP")], run_id="run-90"
    )
    gv = fx.CRITERIA_VERSIONS["GAZE"]
    goal_result["basis"] = {
        "pitches": [
            {
                "pitch_id": "p-1",
                "script_version_id": "sv-1",
                "presentation_version_id": "pv-1",
                "standard_version": 1,
                "takes": [
                    {
                        "take_id": t,
                        "take_number": n,
                        "script_version_id": "sv-1",
                        "presentation_version_id": "pv-1",
                        "standard_version": 1,
                        "results": {"TAKE_RESULT": 1, "REVIEW": 1},
                        "rule_versions": {"GAZE": gv},
                    }
                    for t, n in (("t-6", 6), ("t-5", 5))
                ],
            }
        ],
        "conversation_goal_version": 1,
        "corpus_version": "guides-4",
        "sources": [],
    }
    return {
        "request_user_message.json": req_user,
        "request_action_result.json": req_resume,
        "request_take_reviewed.json": req_reviewed,
        "stream_needs_action.ndjson": needs_action,
        "stream_done.ndjson": done,
        "stream_partial.ndjson": partial,
        "stream_refused.ndjson": refused,
        "stream_error.ndjson": error,
        "final_take_reviewed.json": goal_result,
        "error_state_invalid.json": {
            "error": {"code": "AGENT_STATE_INVALID", "message": "실행 상태를 확인할 수 없다"}
        },
        "action_result_fetch_pitch_ambiguous.json": {
            "action_id": "run-82:a1",
            "type": "FETCH_PITCH",
            "status": "AMBIGUOUS",
            "data": {
                "candidates": [
                    fx.pitch_summary(pitch),
                    {**fx.pitch_summary(pitch), "pitch_id": "p-9", "title": "졸업 발표(리허설)"},
                ]
            },
            "error": None,
        },
    }


def write() -> list[Path]:
    EXAMPLES.mkdir(parents=True, exist_ok=True)
    written = []
    for name, content in build().items():
        path = EXAMPLES / name
        text = (
            content
            if isinstance(content, str)
            else json.dumps(content, ensure_ascii=False, indent=2, allow_nan=False)
        )
        path.write_text(text + "\n", encoding="utf-8", newline="\n")
        written.append(path)
    return written


if __name__ == "__main__":
    for p in write():
        print(p.name)
