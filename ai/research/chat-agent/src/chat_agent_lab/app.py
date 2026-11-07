"""연구 모듈의 서비스 계층 대역 — 서비스(api/chat_agent.py)가 할 일을 흉내 내 `/agent/turn` 한 번을 처리한다 (기능 문서 6-11 · 11-1 · 11-2).

- 하는 일: `ai/.env`에서 모델 · 서명 키를 읽고, 발표법 · 사용법 자료 묶음(`chat_agent/guides/`)을 검사해 읽고,
  동시 실행 자리(`AGENT_MAX_CONCURRENCY`, 기본 4)를 잡은 뒤 코어 `handle_turn`을 부른다. 자리가 없으면 스트림 전 429 `AGENT_BUSY`,
  꺼져 있으면 503 `AGENT_DISABLED`. 스트림이 끝나거나 닫히면 자리를 돌려준다.
- 쓰는 곳: 평가(evals) · live 시험 · 손으로 해 보기.
  `.\\.venv\\Scripts\\python.exe -m chat_agent_lab.app --message "Take 4보다 Take 5에서 3장 시선이 좋아졌어?" --loaded t-4`
- 서명 키: `AGENT_STATE_SECRET`이 없으면 프로세스마다 임의 키를 만든다(연구용. 서비스는 키가 없으면 대화 경로를 끈다, 11-1).
- 서비스의 같은 자리: 환경변수 읽기는 settings, 자리 · 실행 풀 · HTTP 스트림은 api/chat_agent.py, 모델은 common/llm.py 의
  `tool_llm`, 자료 묶음은 `allow_unreviewed=False`(검수 전이면 자료 없이 시작)로 읽는다. 여기서는 모델을 서비스의
  `ToolLlmClient` 로 만들고 자료 묶음은 검수 전 카드까지 읽는다(평가용).
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pitch_coach_ai.features import chat_agent as _core
from pitch_coach_ai.features.chat_agent import templates
from pitch_coach_ai.features.chat_agent.config import DEFAULT_CONFIG, ChatAgentConfig
from pitch_coach_ai.features.chat_agent.corpus import Corpus, build_corpus
from pitch_coach_ai.features.chat_agent.llm_types import ToolLlm
from pitch_coach_ai.features.chat_agent.loop import TurnDeps
from pitch_coach_ai.features.chat_agent.turn import handle_turn

from .service_tests import fixtures as fx

GUIDES = Path(_core.__file__).parent / "guides"  # 서비스 코어의 자료 묶음
ENV_FILE = Path(__file__).resolve().parents[4] / ".env"  # ai/.env


def load_corpus(cfg: ChatAgentConfig = DEFAULT_CONFIG, *, allow_unreviewed: bool = True) -> Corpus:
    """자료 묶음을 읽어 검사한다(목록 · 해시 · 라이선스 · 검수, 6-10). 어긋나면 `CorpusInvalid`."""
    manifest = json.loads((GUIDES / "manifest.json").read_text(encoding="utf-8"))
    files = {
        name: json.loads((GUIDES / name).read_text(encoding="utf-8"))
        for name in manifest.get("files", [])
    }
    return build_corpus(manifest, files, cfg=cfg, allow_unreviewed=allow_unreviewed)


@dataclass
class TurnOutcome:
    """한 호출의 결과: 스트림 전 오류면 `status`와 `error`, 아니면 200과 줄 반복자."""

    status: int
    lines: Iterator[str] | None = None
    error: dict[str, Any] | None = None


@dataclass
class AgentApp:
    llm: ToolLlm | None
    secret: bytes | None
    corpus: Corpus | None
    previous_secret: bytes | None = None
    cfg: ChatAgentConfig = DEFAULT_CONFIG
    enabled: bool = True
    max_concurrency: int = 4
    log: Callable[[dict[str, Any]], None] = lambda _l: None
    clock_ms: Callable[[], int] = field(default=lambda: int(time.time() * 1000))
    monotonic: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        self._slots = threading.BoundedSemaphore(self.max_concurrency)

    @property
    def corpus_version(self) -> str:
        return self.corpus.version if self.corpus is not None else "none"

    def deps(self) -> TurnDeps:
        return TurnDeps(
            llm=self.llm,
            corpus=self.corpus,
            corpus_version=self.corpus_version,
            clock_ms=self.clock_ms,
            monotonic=self.monotonic,
            cfg=self.cfg,
            log=self.log,
        )

    def turn(self, body: bytes, *, request_id: str | None = None) -> TurnOutcome:
        if not self.enabled:
            return _error(503, "AGENT_DISABLED")
        saturated = getattr(self.llm, "saturated", None)
        if callable(saturated) and saturated():
            # 끝나지 않은 모델 호출이 실행 풀을 채웠으면 새 턴을 받지 않는다(L1-14)
            return _error(429, "AGENT_BUSY")
        if not self._slots.acquire(blocking=False):
            return _error(429, "AGENT_BUSY")
        try:
            resp = handle_turn(
                body,
                self.deps(),
                secret=self.secret,
                previous_secret=self.previous_secret,
                request_id=request_id,
            )
        except BaseException:
            self._slots.release()
            raise
        if resp.error is not None:
            self._slots.release()
            return TurnOutcome(status=resp.error.status, error=resp.error.body())
        assert resp.lines is not None
        return TurnOutcome(status=200, lines=SlotLines(resp.lines, self._slots.release))

    def run(self, req: dict[str, Any]) -> tuple[int, list[dict[str, Any]]]:
        """요청 dict 하나를 끝까지 돌려 (상태, 줄 목록)을 돌려준다(평가 · 시험용)."""
        out = self.turn(json.dumps(req, ensure_ascii=False).encode("utf-8"))
        if out.lines is None:
            return out.status, [{"event": "error", "data": out.error}]
        return out.status, [json.loads(x) for x in out.lines]


class SlotLines:
    """줄 반복자 + 동시 실행 자리. 끝까지 읽거나, 중간에 닫거나, 한 줄도 읽기 전에 닫아도 자리를 한 번 돌려준다(5-4 · L1-08)."""

    def __init__(self, lines: Iterator[str], release: Callable[[], None]) -> None:
        self._lines = lines
        self._release = release
        self._closed = False

    def __iter__(self) -> SlotLines:
        return self

    def __next__(self) -> str:
        try:
            return next(self._lines)
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            close = getattr(self._lines, "close", None)
            if close is not None:
                close()
        finally:
            self._release()


def _error(status: int, code: str) -> TurnOutcome:
    return TurnOutcome(
        status=status,
        error={"error": {"code": code, "message": templates.ERROR_MESSAGES.get(code, code)}},
    )


def from_env(
    *,
    env_file: Path | None = ENV_FILE,
    bundle: str = "OPENAI",
    log: Callable[[dict[str, Any]], None] | None = None,
    role: Any = None,
) -> AgentApp:
    """`ai/.env`에서 모델 묶음(`OPENAI_*`, 비교 시험은 `OPENAI_SOL_*`)과 서명 키를 읽어 앱을 만든다. 키 값은 출력하지 않는다."""
    from dotenv import load_dotenv
    from pitch_coach_ai.common.llm import ToolLlmClient, tool_role

    if env_file is not None and env_file.exists():
        load_dotenv(env_file)
    base_url = os.environ.get(f"{bundle}_BASE_URL")
    api_key = os.environ.get(f"{bundle}_API_KEY")
    model = os.environ.get(f"{bundle}_MODEL")
    llm = (
        ToolLlmClient(
            base_url=base_url,
            api_key=api_key,
            model=model,
            role=role if role is not None else tool_role(),
            log=log or (lambda _l: None),
        )
        if base_url and api_key and model
        else None
    )
    raw = os.environ.get("AGENT_STATE_SECRET")
    secret = raw.encode("utf-8") if raw else secrets.token_bytes(32)
    prev = os.environ.get("AGENT_STATE_SECRET_PREVIOUS")
    return AgentApp(
        llm=llm,
        secret=secret,
        previous_secret=prev.encode("utf-8") if prev else None,
        corpus=load_corpus(),
        enabled=os.environ.get("AGENT_ENABLED", "true").lower() != "false",
        max_concurrency=int(os.environ.get("AGENT_MAX_CONCURRENCY", "4")),
        log=log or (lambda _l: None),
    )


def build_request(
    message: str,
    *,
    run_id: str = "run-lab",
    page_take_id: str | None = "t-5",
    loaded: tuple[str, ...] = (),
    goal: dict[str, Any] | None = None,
    goal_version: int = 0,
    history: dict[str, Any] | None = None,
    pitch: fx.PitchRecord | None = None,
    screen: dict[str, Any] | None = None,
    memory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """가상 기록(fixtures.sample_pitch)으로 처음 `/agent/turn` 요청을 만든다(평가 · 손으로 해 보기).

    `memory`를 주지 않으면 기억 꺼짐(`enabled:false`)으로 보낸다(5-3)."""
    p = pitch or fx.sample_pitch()
    focus = page_take_id if page_take_id and p.take(page_take_id) else None
    return {
        "run": {
            "run_id": run_id,
            "conversation_id": "c-lab",
            "user_id": p.user_id,
            "scope_pitch_id": p.pitch_id,
            "trigger": "USER_MESSAGE",
            "state_revision": 0,
        },
        "user_message": message,
        "choice": None,
        "review_event": None,
        "page": {
            "route": f"/takes/{page_take_id}" if page_take_id else "/",
            "pitch_id": p.pitch_id,
            "take_id": page_take_id,
            "screen": screen,
        },
        "history": history or {"summary": None, "messages": [], "prior_assessments": []},
        "context": {
            "pitches": [fx.pitch_summary(p)],
            "pitches_total": 1,
            "pitch": fx.pitch_context(p, focus_take_id=focus),
            "conversation_goal": goal,
            "goal_version": goal_version,
            "loaded_takes": [
                fx.take_snapshot(t) for t in (p.take(i) for i in loaded) if t is not None
            ],
            "removed_sources": [],
            "selected_proposal": None,
        },
        "resume": None,
        "budget_used": {
            "model_calls": 0,
            "tool_calls": 0,
            "elapsed_ms": 0,
            "input_tokens": 0,
            "output_tokens": 0,
        },
        "memory": memory or {"enabled": False, "memory_version": 0, "profile": None},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="대화 에이전트 한 턴을 실제 모델로 돌려 본다(가상 기록)"
    )
    ap.add_argument("--message", required=True)
    ap.add_argument("--loaded", nargs="*", default=[], help="BE가 미리 읽어 둔 Take id (예: t-4)")
    ap.add_argument("--bundle", default="OPENAI", help="모델 묶음(OPENAI 또는 OPENAI_SOL)")
    args = ap.parse_args(argv)
    app = from_env(bundle=args.bundle)
    if app.llm is None:
        print("모델 설정(ai/.env)이 없습니다", file=sys.stderr)
        return 2
    status, lines = app.run(build_request(args.message, loaded=tuple(args.loaded)))
    for ln in lines:
        print(json.dumps(ln, ensure_ascii=False))
    return 0 if status == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
