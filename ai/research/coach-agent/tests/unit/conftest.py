"""테스트 공용 — 요청을 짧게 만드는 도우미와, coach_state 를 들고 다니는 세션.

판정 모듈은 가짜(fakes.py)다. 테스트는 모듈이 낼 판정 결과(문제 · 지표 · 상태)를 적어 주고
코치 규칙이 그것으로 어떻게 판단하는지만 본다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from coach import decide
from coach.candidates import Candidate
from coach.config import CoachConfig, load_config
from coach.judges import Judges
from coach.policy import RULE_POLICY, Selection
from coach.schemas import CoachResponse
from coach.state import CoachState, Cursor, dump_state
from coach.tick import Tick
from coach.vocab import CandidateStatus, Instruction, Issue

from .fakes import MODULE_OF, ScriptedJudge, fake_issue, fake_judges

#: 3분 발표, 4장. 장 목표 30/60/60/30초. 글자 수는 계산하기 쉽게 장 목표 초 × 10
PLAN: dict[str, Any] = {
    "target_ms": 180_000,
    "min_ms": 165_000,
    "max_ms": 195_000,
    "slides": [
        {"slide_number": 1, "target_ms": 30_000, "script_chars": 300},
        {"slide_number": 2, "target_ms": 60_000, "script_chars": 600},
        {
            "slide_number": 3,
            "target_ms": 60_000,
            "script_chars": 600,
            "required_keywords": ["로컬 처리"],
        },
        {"slide_number": 4, "target_ms": 30_000, "script_chars": 300},
    ],
}

#: 말이 끊긴 틈. 이걸 기본으로 써서 '문장 끝 기다리기'가 다른 테스트를 방해하지 않게 한다
IN_PAUSE = {"silence_ms": 400, "audio_live": True}
SPEAKING = {"silence_ms": 0, "audio_live": True, "level_db": -24.0}


def spoken(
    end_ms: int,
    *,
    cpm: float = 300,
    seconds: float = 15,
    chars: int = 3,
    filler_every: int = 0,
) -> list[dict[str, Any]]:
    """end_ms 까지 seconds 초 동안 cpm 속도로 쉬지 않고 말한 확정 단어들(요청 inputs.words)."""
    dur = round(chars * 60_000 / cpm)
    out: list[dict[str, Any]] = []
    t = int(end_ms - seconds * 1000)
    i = 0
    while t + dur <= end_ms:
        i += 1
        if filler_every and i % filler_every == 0:
            out.append({"word": "음", "start_ms": t, "end_ms": t + 300})
            t += 300
            continue
        out.append({"word": "가" * chars, "start_ms": t, "end_ms": t + dur})
        t += dur
    return out


def words(
    end_ms: int,
    *,
    cpm: float = 300,
    seconds: float = 15,
    final: bool = True,
    chars: int = 3,
    filler_every: int = 0,
) -> list[dict[str, Any]]:
    """이전 요청 모양(w · final · filler)의 단어. 말 속도 대역 테스트가 계약 모양으로 바꿔 쓴다."""
    dur = round(chars * 60_000 / cpm)
    out: list[dict[str, Any]] = []
    t = int(end_ms - seconds * 1000)
    i = 0
    while t + dur <= end_ms:
        i += 1
        if filler_every and i % filler_every == 0:
            out.append(
                {"w": "음", "start_ms": t, "end_ms": t + 300, "final": final, "filler": True}
            )
            t += 300
            continue
        out.append({"w": "가" * chars, "start_ms": t, "end_ms": t + dur, "final": final})
        t += dur
    return out


def make_request(
    t_ms: int,
    *,
    state: dict[str, Any] | None = None,
    mode: str = "COACHING",
    plan: dict[str, Any] | None = None,
    slide: int | None = 1,
    slide_started: int = 0,
    voice: dict[str, Any] | None = None,
    words: list[dict[str, Any]] | None = None,
    utterance_ends: list[int] | None = None,
    stt_status: str = "ok",
    base_level_db: float | None = None,
    missions: list[dict[str, Any]] | None = None,
    recurring_issues: list[dict[str, Any]] | None = None,
    coaching_plan: dict[str, Any] | None = None,
    script_used: bool | None = None,
) -> dict[str, Any]:
    """voice 는 지난 1초의 음량 기록 하나(silence_ms · audio_live · level_db …)."""
    record = {"t_ms": max(0, t_ms - 1000), **(IN_PAUSE if voice is None else voice)}
    return {
        "take_id": "test-take",
        "t_ms": t_ms,
        "mode": mode,
        "plan": {} if plan is None else plan,
        "missions": missions or [],
        "recurring_issues": recurring_issues or [],
        "coaching_plan": coaching_plan,
        "script_used": script_used,
        "inputs": {
            "voice_records": [record],
            "words": words or [],
            "utterance_ends": utterance_ends or [],
            "stt_status": stt_status,
            "slide": {"number": slide, "started_ms": slide_started} if slide is not None else None,
        },
        "calibration": {"base_level_db": base_level_db},
        "coach_state": state,
    }


@dataclass
class CandidateView:
    """한 틱의 후보 하나가 어떻게 판단됐는가 (응답에는 실리지 않아 정책을 거쳐 본다)."""

    candidate_id: str
    issue_type: Issue
    instruction: Instruction
    status: CandidateStatus
    #: 고른 후보면 고른 이유, 아니면 걸린 이유
    reasons: list[str]


class RecordingPolicy:
    """규칙 정책을 그대로 쓰면서 그 틱의 후보와 판단 결과를 남긴다."""

    name = "recording"

    def __init__(self) -> None:
        self._seen: list[Candidate] = []

    def select(self, tick: Tick, candidates: list[Candidate]) -> Selection:
        self._seen = candidates
        return RULE_POLICY.select(tick, candidates)

    def take(self) -> list[CandidateView]:
        """마지막 select 의 후보. select 를 거치지 않은 틱(STALE_TICK 등)이면 빈 목록."""
        seen, self._seen = self._seen, []
        views = []
        for c in seen:
            status = c.status or CandidateStatus.IGNORED
            shown = c.reasons_for if status == CandidateStatus.SELECTED else c.reasons_against
            views.append(
                CandidateView(
                    candidate_id=c.candidate_id,
                    issue_type=c.issue_type,
                    instruction=c.instruction,
                    status=status,
                    reasons=[r.value for r in shown],
                )
            )
        return views


def decide_seen(
    request: dict[str, Any], judges: Judges, config: CoachConfig | None = None
) -> tuple[CoachResponse, list[CandidateView]]:
    """decide 한 번과 그 틱의 후보."""
    policy = RecordingPolicy()
    resp = decide(request, judges, config, policy)
    return resp, policy.take()


class Session:
    """BE 처럼 coach_state 를 받아 두었다가 다음 요청에 붙인다.

    step 에 issues(fake_issue 목록) · metrics · unmeasurable(영역 이름)을 주면 그 틱에 판정 모듈이
    그 결과를 낸다. judges 를 직접 주면 그것을 쓴다.
    """

    def __init__(
        self, config: CoachConfig | None = None, judges: Judges | None = None, **defaults: Any
    ) -> None:
        self.config = config or load_config()
        self.defaults = defaults
        self.state: dict[str, Any] | None = None
        self.responses: list[CoachResponse] = []
        self._candidates: list[list[CandidateView]] = []
        self.board: dict[str, Any] = {}
        self.judges = judges or fake_judges(
            **{n: ScriptedJudge(n, self._spec(n)) for n in ("gaze", "pace", "volume", "filler")}
        )

    def _spec(self, name: str):
        def spec(t_ms: int) -> dict[str, Any]:  # noqa: ARG001
            issues = [i for i in self.board.get("issues", []) if MODULE_OF[i["area"]] == name]
            return {
                "issues": issues,
                "metrics": self.board.get("metrics", {}),
                "unmeasurable": set(self.board.get("unmeasurable", ())),
            }

        return spec

    def step(
        self,
        t_ms: int,
        *,
        issues: list[dict[str, Any]] | None = None,
        metrics: dict[str, Any] | None = None,
        unmeasurable: tuple[str, ...] = (),
        **kw: Any,
    ) -> CoachResponse:
        args = {**self.defaults, **kw}
        self.board = {
            "issues": issues or [],
            "metrics": metrics or {},
            "unmeasurable": unmeasurable,
        }
        resp, views = decide_seen(
            make_request(t_ms, state=self.state, **args), self.judges, self.config
        )
        self.state = resp.coach_state
        self.responses.append(resp)
        self._candidates.append(views)
        return resp

    def candidates_of(self, resp: CoachResponse) -> list[CandidateView]:
        """이 세션이 돌려준 응답 하나의 틱에서 후보들이 어떻게 판단됐는가."""
        return next(c for r, c in zip(self.responses, self._candidates, strict=True) if r is resp)

    def cand(self, resp: CoachResponse, issue: str) -> CandidateView | None:
        return next((c for c in self.candidates_of(resp) if c.issue_type.value == issue), None)

    def run(
        self, start_ms: int, end_ms: int, step_ms: int = 1000, **kw: Any
    ) -> list[CoachResponse]:
        return [self.step(t, **kw) for t in range(start_ms, end_ms + 1, step_ms)]

    @property
    def events(self) -> list[Any]:
        return [e for r in self.responses for e in r.events]

    @property
    def interventions(self) -> list[CoachResponse]:
        return [r for r in self.responses if r.feedback is not None]


def _script_metrics(ratio: float | None) -> dict[str, Any]:
    """시선 모듈이 내는 대본 응시 비율 지표: 지금 값과 효과를 재는 짧은 평균."""
    return {"script_ratio": ratio, "script_ratio_short": ratio}


def gaze_on(
    script_ratio: float = 0.9, *, severity: float | None = None, confidence: float = 1.0
) -> dict[str, Any]:
    """Session.step 에 풀어 쓰는 '대본만 보는 중' 판정: 문제 하나와 script_ratio 지표.

    심각도는 기준 0.7 에서 0.5, 0.95 에서 1.0 인 직선으로 정한다(시선 모듈의 규칙).
    """
    sev = severity if severity is not None else min(1.0, 0.5 + 0.5 * (script_ratio - 0.7) / 0.25)
    issue = fake_issue(
        "GAZE",
        "GAZE_ON_SCRIPT",
        sev,
        confidence=confidence,
        evidence={"script_ratio": script_ratio, "window_ms": 10_000},
    )
    return {"issues": [issue], "metrics": _script_metrics(script_ratio)}


def gaze_off(script_ratio: float = 0.1) -> dict[str, Any]:
    """대본을 거의 안 보는 중(문제 없음)."""
    return {"metrics": _script_metrics(script_ratio)}


def gaze_blind() -> dict[str, Any]:
    """얼굴이 안 잡혀 시선을 잴 수 없다: 문제 후보는 나오지만 믿을 수 없고 지표는 비어 있다."""
    issue = gaze_on(0.9)["issues"][0]
    return {"issues": [issue], "metrics": _script_metrics(None), "unmeasurable": ("GAZE",)}


def mid_slide_state(slide: int, started_ms: int, chars: int, now_ms: int) -> dict[str, Any]:
    """slide 번 장을 started_ms 부터 말하는 중이고 chars 자를 말했으며 now_ms 직전까지 센 상태."""
    state = CoachState(
        slide_number=slide,
        slide_log=[(slide, started_ms)],
        slide_totals={
            str(slide): {
                "SPEED": {"chars": chars},
                "TIME": {"elapsed_ms": now_ms - started_ms},
            }
        },
        slide_stt_ok_ms={str(slide): now_ms - started_ms},
        cursors={
            n: Cursor(since_ms=now_ms) for n in ("gaze", "volume", "timing", "pace", "filler")
        },
    )
    return dump_state(state)


@pytest.fixture
def session() -> Session:
    return Session()
