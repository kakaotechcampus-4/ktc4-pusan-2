"""기록 · 재생: 같은 모델 응답이면 코어 · 서비스 HTTP 경로가 같은 스트림 줄을 내는지 본다 (기능 문서 10-2).

- 무엇: 실제 모델로 시나리오를 한 번 돌려 모델 호출마다 (요청 지문, 결과 또는 오류, 응답 조각 순서)를 기록하고(`record`),
  기록한 응답을 차례로 넣어 다시 돌린다(`replay`). 재생은 평가 시계(`evals.OffsetClock`)를 고정하므로 시간이 든 값
  (경과 · 발급 시각 · 만료)까지 같은 바이트가 나온다.
- 어디로: `http=False` 면 연구 앱 대역(`app.AgentApp` → 코어 `handle_turn`), `http=True` 면 서비스 `/agent/turn`
  (`create_app()` 전체, 요청 로그 미들웨어 포함)에 같은 모델 대역 · 자료 묶음 · 서명 키 · 시계로 보낸다.
- 돌려주는 값: 가짜 BE 가 받은 턴마다 (상태, 스트림 전 오류 본문, NDJSON 줄 그대로)와 모델 요청 지문 목록.
- 쓰는 곳: 고정 시험 `tests/lab/test_replay.py`(`tests/lab/data/replay/<시나리오>.json` 의 기록 + 기대 줄). 코어를 옮기거나
  크게 고칠 때 전 · 후가 같은지 보는 데도 쓴다.
- 실행(연구 폴더에서)
  - 기록(실제 모델을 부른다, 요금이 듦): `.venv/bin/python -m chat_agent_lab.replay record ana-compare-4-5`
    → `tests/lab/data/replay/ana-compare-4-5.json` 을 새로 쓴다(기대 줄도 함께 쓴다)
  - 기대 줄 다시 쓰기(모델을 부르지 않는다. 줄이 바뀌는 것이 의도한 변경일 때만): `... replay update ana-compare-4-5`
- 고칠 때: 요청 지문은 모델에 가는 내용(목적 · 메시지 · 도구 · 응답 모양 · 시도 수 · 출력 상한)만 본다. 프롬프트 ·
  도구 설명을 바꾸면 지문이 바뀌어 고정 시험이 실패하므로, 그때는 다시 기록한다(`record`).
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pitch_coach_ai.features.chat_agent import llm_types as lt

from . import app as appmod
from . import evals as ev
from . import scenario as scn

DATA = Path(__file__).resolve().parents[2] / "tests" / "lab" / "data" / "replay"
#: 기록 · 재생에 쓰는 서명 키(시험 전용, 비밀 아님)
SECRET = b"replay-secret-" + b"k" * 18
#: 재생 때 고정하는 시계(평가 시계의 기준값). 흘린 시간(`offset`)은 그대로 더한다
FROZEN_MONOTONIC = 10_000.0
FROZEN_WALL = 1_793_000_000.0


def fingerprint(req: lt.LlmRequest) -> str:
    """모델에 가는 내용의 지문(마감 · 받는 쪽처럼 실행마다 다른 값은 뺀다)."""
    body = {
        "purpose": req.purpose,
        "messages": req.messages,
        "tools": req.tools,
        "schema": req.response_schema,
        "max_attempts": req.max_attempts,
        "max_output_tokens": req.max_output_tokens,
    }
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class Recorder:
    """실제 모델을 감싸 호출마다 결과 · 오류와 응답 조각 순서를 기록한다."""

    def __init__(self, real: Any) -> None:
        self.real = real
        self.calls: list[dict[str, Any]] = []

    @property
    def model_name(self) -> Any:
        return getattr(self.real, "model_name", None)

    def saturated(self) -> bool:
        return False

    def invoke(self, req: lt.LlmRequest, before_attempt: Any) -> lt.LlmResult:
        events: list[list[Any]] = []
        sink = req.text_sink
        if sink is not None:

            class Tee:
                def start(self, n: int) -> None:
                    events.append(["start", n])
                    sink.start(n)

                def feed(self, n: int, text: str) -> None:
                    events.append(["feed", n, text])
                    sink.feed(n, text)

                def committed(self) -> bool:
                    return sink.committed()

            req = dataclasses.replace(req, text_sink=Tee())
        fp = fingerprint(req)
        try:
            r = self.real.invoke(req, before_attempt)
        except lt.LlmError as e:
            err = {
                "cls": type(e).__name__,
                "attempts": e.attempts,
                "usage": [dataclasses.asdict(u) for u in e.usage],
                "cause": e.cause,
                "attempt_errors": list(getattr(e, "attempt_errors", []) or []),
            }
            self.calls.append({"fp": fp, "events": events, "err": err})
            raise
        self.calls.append({"fp": fp, "events": events, "ok": dataclasses.asdict(r)})
        return r


class Replayer:
    """기록한 호출을 차례로 돌려준다. 받은 요청의 지문을 모은다(기록과 같아야 같은 실행이다)."""

    model_name = "replay-model"

    def __init__(self, calls: list[dict[str, Any]]) -> None:
        self.calls = list(calls)
        self.fps: list[str] = []

    def saturated(self) -> bool:
        return False

    def invoke(self, req: lt.LlmRequest, before_attempt: Any) -> lt.LlmResult:
        self.fps.append(fingerprint(req))
        if not self.calls:
            if not before_attempt(1):
                raise lt.LlmBudgetStop(attempts=0, usage=[], cause="budget")
            raise lt.LlmCallFailed(attempts=1, usage=[lt.Usage()], cause="replay_exhausted")
        c = self.calls.pop(0)
        n = c["ok"]["attempts"] if "ok" in c else max(1, c["err"]["attempts"])
        for i in range(1, n + 1):
            if not before_attempt(i):
                raise lt.LlmBudgetStop(attempts=i - 1, usage=[], cause="budget")
        sink = req.text_sink
        for e in c["events"] if sink is not None else []:
            if e[0] == "start":
                sink.start(e[1])
            else:
                sink.feed(e[1], e[2])
        if "ok" in c:
            d = dict(c["ok"])
            d["tool_calls"] = [lt.ToolCall(**t) for t in d["tool_calls"]]
            d["usage"] = lt.Usage(**d["usage"])
            d["attempt_usages"] = [lt.Usage(**u) for u in d["attempt_usages"]]
            return lt.LlmResult(**d)
        e = c["err"]
        kw: dict[str, Any] = {
            "attempts": e["attempts"],
            "usage": [lt.Usage(**u) for u in e["usage"]],
            "cause": e["cause"],
        }
        if e["cls"] == "LlmBudgetStop":
            kw["attempt_errors"] = e["attempt_errors"]
        raise getattr(lt, e["cls"])(**kw)


# ── 턴 기록 · 시계 · 서비스 HTTP 경로 ────────────────────────────────────────────


@contextlib.contextmanager
def _patched(obj: Any, name: str, value: Any) -> Iterator[None]:
    old = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old)


def _http_turn(self: appmod.AgentApp, body: bytes, *, request_id: str | None = None) -> Any:
    """연구 앱 대역 대신 서비스 `/agent/turn`(앱 전체)으로 보낸다. 같은 모델 대역 · 자료 · 키 · 시계를 넣는다."""
    from fastapi.testclient import TestClient
    from pitch_coach_ai.api import chat_agent as svc_api
    from pitch_coach_ai.main import create_app
    from pitch_coach_ai.settings import Settings
    from pydantic import SecretStr

    assert self.secret is not None
    s = Settings(
        _env_file=None, agent_enabled=True, agent_state_secret=SecretStr(self.secret.decode())
    )
    app = create_app(s)
    app.state.chat_agent_runtime = svc_api.AgentRuntime(
        llm=self.llm,
        corpus=self.corpus,
        secret=self.secret,
        previous_secret=self.previous_secret,
        max_concurrency=self.max_concurrency,
        cfg=self.cfg,
        log=self.log,
        clock_ms=self.clock_ms,
        monotonic=self.monotonic,
    )
    headers = {"content-type": "application/json"}
    if request_id:
        headers["X-Request-ID"] = request_id
    with TestClient(app) as c, c.stream("POST", "/agent/turn", content=body, headers=headers) as r:
        raw = b"".join(r.iter_bytes())
        if r.status_code != 200:
            return appmod.TurnOutcome(status=r.status_code, error=json.loads(raw))
    lines = raw.decode("utf-8").splitlines(keepends=True)
    return appmod.TurnOutcome(status=200, lines=iter(lines))


def replay(rec: list[dict[str, Any]], scenario_id: str, *, http: bool = False) -> dict[str, Any]:
    """기록한 응답으로 시나리오를 한 번 돌린다. 돌려주는 값: {"turns": [...], "fps": [...], "left": 남은 기록 수}."""
    sc = next(s for s in scn.load_scenarios() if s.id == scenario_id)
    turns: list[dict[str, Any]] = []
    inner_turn = _http_turn if http else appmod.AgentApp.turn

    def capture(self: appmod.AgentApp, body: bytes, *, request_id: str | None = None) -> Any:
        out = inner_turn(self, body, request_id=request_id)
        t: dict[str, Any] = {"status": out.status, "error": out.error, "lines": []}
        turns.append(t)
        if out.lines is not None:
            src = out.lines

            def tee() -> Iterator[str]:
                try:
                    for x in src:
                        t["lines"].append(x)
                        yield x
                finally:
                    close = getattr(src, "close", None)
                    if close is not None:
                        close()

            out.lines = tee()
        return out

    def mono(self: ev.OffsetClock) -> float:
        return FROZEN_MONOTONIC + self.offset

    def wall_ms(self: ev.OffsetClock) -> int:
        return int((FROZEN_WALL + self.offset) * 1000)

    rp = Replayer(rec)
    with (
        _patched(appmod.AgentApp, "turn", capture),
        _patched(ev.OffsetClock, "monotonic", mono),
        _patched(ev.OffsetClock, "clock_ms", wall_ms),
    ):
        ev.run_one(sc, 0, rp, SECRET, ev.RunnerConfig(repeat=1, workers=1))
    return {"turns": turns, "fps": rp.fps, "left": len(rp.calls)}


def record(scenario_id: str) -> list[dict[str, Any]]:
    """실제 모델(`ai/.env` 의 기본 묶음)로 한 번 돌려 호출을 기록한다. 요금이 든다."""
    sc = next(s for s in scn.load_scenarios() if s.id == scenario_id)
    real = appmod.from_env().llm
    if real is None:
        raise SystemExit("모델 설정(OPENAI_*)이 없다")
    r = Recorder(real)
    ev.run_one(sc, 0, r, SECRET, ev.RunnerConfig(repeat=1, workers=1))
    return r.calls


def path_for(scenario_id: str) -> Path:
    return DATA / f"{scenario_id}.json"


def write_golden(scenario_id: str, calls: list[dict[str, Any]]) -> Path:
    """기록과 그 재생 결과(기대 줄 · 지문)를 한 파일로 쓴다."""
    got = replay(calls, scenario_id)
    data = {
        "scenario": scenario_id,
        "calls": calls,
        "expected": {"turns": got["turns"], "fps": got["fps"]},
    }
    p = path_for(scenario_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    p.write_text(text, encoding="utf-8", newline="\n")
    return p


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=["record", "update"])
    ap.add_argument("scenarios", nargs="+")
    a = ap.parse_args(argv)
    for sid in a.scenarios:
        if a.action == "record":
            calls = record(sid)
        else:
            calls = json.loads(path_for(sid).read_text(encoding="utf-8"))["calls"]
        p = write_golden(sid, calls)
        print(f"{sid}: 모델 호출 {len(calls)}번 → {p.name}", file=sys.stderr)


if __name__ == "__main__":
    main()
