"""E2E 용 AI 서비스 — 진짜 앱(`pitch_coach_ai.main` 의 `create_app`)에 가짜 모델만 끼워 띄운다.

- 부르는 곳: `stack.py`. 연구 모듈의 venv(`ai/research/chat-agent/.venv`)로 돈다.
- 어떻게: `runtime_for(app)` 이 `app.state.chat_agent_runtime` 이 있으면 그대로 쓰는 점을 이용해(서비스 파일은
  고치지 않는다) `AgentRuntime(llm=ScriptedLlm)` 을 미리 넣는다. 서명 · 스트림 · 검사 · 도구 실행은 전부 진짜다.
- 가짜 모델(`ScriptedLlm`): 호출 종류를 보고 정해 둔 응답을 낸다 —
  계획(구조화) → 도구 한 번(`get_take_report`, 3장) → 조사 끝 → 답(RECORD 한 문장, 근거 id 는 도구 결과에서 찾는다).
  서비스 시험의 `FakeLlm` · 도우미를 `chat_agent_lab.service_tests` 로 가져다 쓴다(읽기만).
- 시드와 맞춘 값: Take 1 의 3장 청중 시선 비율 28% (`seed.py`).
- 환경변수: AGENT_ENABLED=true, AGENT_STATE_SECRET(32바이트 이상) 는 `stack.py` 가 넣는다.
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any

import uvicorn
from chat_agent_lab import service_tests as st

from pitch_coach_ai.api.chat_agent import AgentRuntime
from pitch_coach_ai.main import create_app
from pitch_coach_ai.settings import settings

F = st.fakes


def _take_id(req: Any) -> str:
    text = " ".join(str(m.get("content") or "") for m in req.messages)
    found = re.search(r'"take_id":\s*"([0-9a-f-]{36})"', text)
    assert found, "요청에서 take_id 를 찾지 못했다"
    return found.group(1)


def respond(req: Any) -> Any:
    if req.purpose == "RESEARCH" and req.response_schema is not None:
        return F.plan(steps=["기록 확인", "답 쓰기"])
    if req.purpose == "RESEARCH":
        if F.tool_results(req):
            return F.done()
        args = {"take_id": _take_id(req), "section": "slides", "slide_number": 3}
        return F.tools(("get_take_report", args))
    if req.purpose == "ANSWER":
        eid = F.find_evidence(req, lambda o: o.get("metric") == "audience_ratio")
        text = "Take 1의 3장 청중 시선 비율은 28%예요."
        return F.answer([F.claim("RECORD", text, evidence_ids=[eid])])
    raise AssertionError(f"대본에 없는 호출: {req.purpose}")


class _Script(list):
    """FakeLlm 이 `pop(0)` 할 때마다 같은 응답 함수를 준다(끝없이)."""

    def __bool__(self) -> bool:
        return True

    def pop(self, index: int = -1) -> Any:
        return respond


class ScriptedLlm(F.FakeLlm):
    def __init__(self) -> None:
        super().__init__([])
        self.script = _Script()


def build_app() -> Any:
    app = create_app(settings)
    secret = settings.agent_state_secret.get_secret_value().encode()
    app.state.chat_agent_runtime = AgentRuntime(
        llm=ScriptedLlm(),
        corpus=None,
        secret=secret,
        previous_secret=None,
        max_concurrency=settings.agent_max_concurrency,
    )
    return app


if __name__ == "__main__":
    port = int(os.environ.get("AI_PORT", "18001"))
    uvicorn.run(build_app(), host="127.0.0.1", port=port, log_level="info")
    sys.exit(0)
