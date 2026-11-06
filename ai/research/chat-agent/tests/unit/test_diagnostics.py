"""진단 기록 시험 — 닫힌 detail · code 허용 목록 · 크기 줄이기 순서 · 요청 id (기능 문서 11-5)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from chat_agent.diagnostics import (
    DiagRecorder,
    code_enum,
    finish_code,
    reduce_to_fit,
    safe_request_id,
)


def recorder(log: list | None = None) -> DiagRecorder:
    t = [100.0]
    sink = log if log is not None else []
    return DiagRecorder(
        request_id="req-1",
        run_id="run-1",
        turn_call=1,
        versions={
            "feature_version": "f",
            "prompt_version": "p",
            "tool_schema": "t",
            "corpus_version": "c",
        },
        monotonic=lambda: t[0],
        log=sink.append,
    )


def test_steps_have_seq_parent_and_log_lines() -> None:
    log: list = []
    d = recorder(log)
    m = d.step(
        "MODEL",
        "RESEARCH",
        {
            "model": "m",
            "success": True,
            "input_tokens": 10,
            "output_tokens": 2,
            "finish_reason": "TOOL_CALLS",
            "tool_call_count": 1,
        },
    )
    t = d.step("TOOL", "compare_takes", {"outcome": "OK", "result_bytes": 120}, parent_seq=m)
    assert (m, t) == (1, 2)
    out = d.finish("DONE", "ANSWERED")
    assert out.steps[1].parent_seq == 1
    assert [x["event"] for x in log] == ["agent_step", "agent_step", "agent_end"]


@pytest.mark.parametrize(
    ("kind", "code", "detail"),
    [
        ("TOOL", "delete_everything", {}),  # 도구 이름 허용 목록 밖
        ("MODEL", "RESEARCH", {"prompt": "사용자 글"}),  # 닫힌 detail: 모르는 필드
        ("CHECK", "NOT_A_CHECK", {}),
    ],
)
def test_closed_detail_and_code_allowlist(kind: str, code: str, detail: dict) -> None:
    with pytest.raises(ValidationError):
        recorder().step(kind, code, detail)


def test_enum_mapping_of_provider_values() -> None:
    assert finish_code("tool_calls") == "TOOL_CALLS" and finish_code("weird") == "OTHER"
    assert code_enum("LLM_TIMEOUT") == "LLM_TIMEOUT" and code_enum("Timeout: 사용자 글") == "OTHER"


def test_request_id_is_kept_only_when_safe() -> None:
    assert safe_request_id("abc-123", run_id="r", turn_call=1) == "abc-123"
    gen = safe_request_id("bad id/../", run_id="r", turn_call=2)
    assert gen.startswith("gen-") and gen == safe_request_id(None, run_id="r", turn_call=2)


def test_reduce_to_fit_follows_order_and_marks_truncated() -> None:
    d = recorder()
    for _ in range(8):
        d.step(
            "MODEL",
            "RESEARCH",
            {
                "model": "m",
                "success": True,
                "input_tokens": 10,
                "output_tokens": 2,
                "finish_reason": "STOP",
                "tool_call_count": 0,
            },
        )
    for i in range(400):
        d.step("TOOL", "list_takes", {"outcome": "OK", "result_bytes": i, "target_ids": [f"t-{i}"]})
        d.step(
            "CHECK",
            "NUMBER_MISMATCH",
            {"claim_index": 0, "claim_kind": "RECORD", "round": 0, "result": "FIX_REQUESTED"},
        )
    d.step(
        "CHECK",
        "SUMMARY",
        {"round": 1, "checked": 3, "failed": 1, "dropped": 1, "actions_dropped": 0},
    )
    full = d.finish("DONE", "ANSWERED", max_bytes=10**9)
    assert not full.truncated
    small = reduce_to_fit(full, 65_536)
    assert len(small.model_dump_json().encode()) <= 65_536
    assert small.truncated
    kinds = {s.kind for s in small.steps}
    assert "MODEL" in kinds
    # ② 이후면 CHECK 는 SUMMARY 만 남는다
    assert all(s.code == "SUMMARY" for s in small.steps if s.kind == "CHECK")
    assert small.end.omitted_steps > 0
    # 아주 작은 한도면 ③ MODEL 과 end 만
    tiny = reduce_to_fit(full, 3_000)
    assert {s.kind for s in tiny.steps} == {"MODEL"}
    assert tiny.end.omitted_steps == len(full.steps) - 8
