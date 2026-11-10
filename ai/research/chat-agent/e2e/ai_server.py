"""E2E 용 AI 서비스 — 진짜 앱(`pitch_coach_ai.main` 의 `create_app`)에 가짜 모델만 끼워 띄운다.

- 부르는 곳: `stack.py`. 연구 모듈의 venv(`ai/research/chat-agent/.venv`)로 돈다.
- 대화 모델: `runtime_for(app)` 이 `app.state.chat_agent_runtime` 이 있으면 그대로 쓰는 점을 이용해(서비스 파일은
  고치지 않는다) `AgentRuntime(llm=ScriptedLlm)` 을 미리 넣는다. 서명 · 스트림 · 검사 · 도구 실행은 전부 진짜다.
- 시나리오별 대본: 테스트가 시작할 때 시나리오 이름을 통제 파일(`.stack/scenario`, 환경변수 `E2E_SCENARIO_FILE`)에
  쓴다. `ScriptedLlm` 은 모델을 부를 때마다 파일을 보고 바뀌었으면 `scripts/<이름>.json` 을 다시 읽어 응답 줄을 새로
  채운다. 대본은 서비스 시험의 `FakeLlm` 과 같다 — 호출 순서대로 하나씩 꺼내 쓴다(조사 · 계획 줄 `script`,
  답 쓰기 줄 `answers`, 고치기 줄 `fixes`). 승인 대기 뒤 재개 · 이어서 확인 같은 같은 실행의 다음 호출도 같은 줄에서 이어 꺼낸다.
- 대본 파일 모양(`scripts/*.json`):
    user_message  테스트가 입력칸에 치는 말
    script[]      {"plan": {steps, assessments, route...}} | {"tools": [[이름, 인자], ...], "prefix": "x"} |
                  {"done": true} | {"error": "LlmTimeout"|"LlmCallFailed"|"LlmRefused"}
                  계획 단계 steps 는 글자 또는 {"step","status","note", ...}(나중에 생길 `tool` 같은 칸은 그대로 실린다)
    answers[]     {"claims": [{kind, text, evidence_metric?, ...}], 그 밖 F.answer 인자} | {"error": ...}
    fixes[]       {"fix": {"0": "고친 문장"}} | {"error": ...}
    expect        테스트가 화면 · API · DB 에서 확인할 문구(계획 문구는 여기 `plan_steps` 에 둔다)
  인자 문자열 `$take_id` 는 요청에서 찾은 Take id 로 바꾼다. `evidence_metric` 은 도구 결과에서 그 지표의 근거 id 를 찾는다.
- 리뷰 엔드포인트(`/takes/review`, REASSESS 와 시드가 부른다): 모델 없이 돈다. 의존성 `get_llm_factory` 를
  `LlmUnavailable` 을 던지게 바꿔 코어가 기본 결과(`fallback_reason: LLM_UNAVAILABLE`)를 내게 한다. 게다가 모델 설정은
  닿지 않는 주소(127.0.0.1:9)라 어디서든 모델을 부르려 하면 연결 실패다.
- 환경변수: AGENT_ENABLED=true, AGENT_STATE_SECRET(32바이트 이상) 는 `stack.py` 가 넣는다.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import uvicorn
from chat_agent_lab import service_tests as st

from pitch_coach_ai.api.chat_agent import AgentRuntime
from pitch_coach_ai.api.review import get_llm_factory
from pitch_coach_ai.common.errors import LlmUnavailable
from pitch_coach_ai.features.chat_agent import llm_types as lt
from pitch_coach_ai.main import create_app
from pitch_coach_ai.settings import settings

F = st.fakes
HERE = Path(__file__).resolve().parent
SCRIPTS = HERE / "scripts"
CONTROL = Path(os.environ.get("E2E_SCENARIO_FILE", HERE / ".stack" / "scenario"))
ERRORS = {"LlmTimeout": lt.LlmTimeout, "LlmCallFailed": lt.LlmCallFailed, "LlmRefused": lt.LlmRefused}


def _take_id(req: Any) -> str:
    text = " ".join(str(m.get("content") or "") for m in req.messages)
    found = re.search(r'"take_id":\s*"([0-9a-f-]{36})"', text)
    assert found, "요청에서 take_id 를 찾지 못했다"
    return found.group(1)


def _fill(value: Any, req: Any) -> Any:
    """인자 속 `$take_id` 를 요청의 Take id 로 바꾼다."""
    if isinstance(value, str):
        return _take_id(req) if value == "$take_id" else value
    if isinstance(value, list):
        return [_fill(v, req) for v in value]
    if isinstance(value, dict):
        return {k: _fill(v, req) for k, v in value.items()}
    return value


def _plan(spec: dict[str, Any]) -> Any:
    steps = [{"step": s} if isinstance(s, str) else dict(s) for s in spec["steps"]]
    for i, step in enumerate(steps):
        step.setdefault("status", "DOING" if i == 0 else "TODO")
        step.setdefault("note", None)
    drafts = [F.draft(**d["draft"]) if "draft" in d else d for d in spec.get("assessments", [])]
    return F.structured(
        {
            "route": spec.get("route", "RESEARCH"),
            "talk_kind": spec.get("talk_kind"),
            "scope_reason": spec.get("scope_reason"),
            "plan": steps,
            "guide_queries": spec.get("guide_queries", []),
            "assessments": drafts,
        }
    )


def _script_item(item: dict[str, Any]) -> Any:
    if "error" in item:
        return ERRORS[item["error"]]()
    if "plan" in item:
        return lambda req, spec=item["plan"]: _plan(spec)
    if item.get("done"):
        return lambda req: F.done()
    if "tools" in item:
        calls = item["tools"]
        prefix = item.get("prefix", "call")
        return lambda req: F.tools(*[(n, _fill(a, req)) for n, a in calls], prefix=prefix)
    raise AssertionError(f"모르는 대본 항목: {item}")


def _answer_item(item: dict[str, Any]) -> Any:
    if "error" in item:
        return ERRORS[item["error"]]()

    def make(req: Any) -> Any:
        claims = []
        for c in item["claims"]:
            c = dict(c)
            metric = c.pop("evidence_metric", None)
            if metric:
                c["evidence_ids"] = [F.find_evidence(req, lambda o, m=metric: o.get("metric") == m)]
            claims.append(F.claim(**c))
        extra = {k: v for k, v in item.items() if k != "claims"}
        return F.answer(claims, **extra)

    return make


def _fix_item(item: dict[str, Any]) -> Any:
    if "error" in item:
        return ERRORS[item["error"]]()
    return F.fix({int(k): v for k, v in item["fix"].items()})


class ScriptedLlm(F.FakeLlm):
    """통제 파일이 가리키는 시나리오 대본으로 답한다. 파일이 바뀌면 줄을 새로 채운다."""

    def __init__(self) -> None:
        super().__init__([], answers=[], fixes=[])
        self._loaded: tuple[str, int] | None = None

    def _reload(self) -> None:
        stat = CONTROL.stat()
        name = CONTROL.read_text().strip()
        if self._loaded == (name, stat.st_mtime_ns):
            return
        data = json.loads((SCRIPTS / f"{name}.json").read_text(encoding="utf-8"))
        self.script = [_script_item(i) for i in data["script"]]
        self.queues["ANSWER"] = [_answer_item(i) for i in data.get("answers", [])]
        self.queues["FIX"] = [_fix_item(i) for i in data.get("fixes", [])]
        self._loaded = (name, stat.st_mtime_ns)

    def invoke(self, req: Any, before_attempt: Any) -> Any:
        self._reload()
        return super().invoke(req, before_attempt)


def _no_llm(*_args: Any, **_kw: Any) -> Any:
    raise LlmUnavailable()


def build_app() -> Any:
    if os.environ.get("E2E_REAL_MODEL") == "1":
        # 실제 모델 시연(README): 서비스 앱 그대로(대화 · 리뷰 모두 `ai/.env`의 실제 모델 묶음, 요금이 듦)
        return create_app(settings)
    app = create_app(settings)
    secret = settings.agent_state_secret.get_secret_value().encode()
    app.state.chat_agent_runtime = AgentRuntime(
        llm=ScriptedLlm(),
        corpus=None,
        secret=secret,
        previous_secret=None,
        max_concurrency=settings.agent_max_concurrency,
    )
    # 리뷰는 모델 없이: 코어가 기본 결과(fallback_reason=LLM_UNAVAILABLE)를 낸다
    app.dependency_overrides[get_llm_factory] = lambda: _no_llm
    return app


if __name__ == "__main__":
    port = int(os.environ.get("AI_PORT", "18001"))
    uvicorn.run(build_app(), host="127.0.0.1", port=port, log_level="info")
    sys.exit(0)
