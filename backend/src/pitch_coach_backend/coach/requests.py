"""코치 요청을 만든다. DB 도 네트워크도 모른다 (보내는 것은 coach/client.py).

부르는 쪽(Take 시작 · 발표 중 · 종료 · 코칭 계획)이 DB 와 WS 에서 꺼낸 값을 넘기면 요청 dict 가
나온다. 필드 이름 · 단위는 `docs/ai/features/coach-agent.md` 5-2 와 같고, 시각은 모두 Take 시작
기준 ms 다.

창 · 지금 장 · replay 입력은 `windows.py`, 코칭 계획 행 · 고르기는 `coaching_plan.py` 다.

BE 가 값을 고치거나 빼는 곳은 아래뿐이다. 나머지(미션 · 반복 문제 · 1초 기록)는 받은 그대로 싣는다.
  - `mode` · `script_mode`: 계약에 없는 값이면 `COACHING` · null 로 둔다. takes CHECK 가 막지만,
    계약에 없는 값을 그대로 보내면 AI 가 그 Take 의 요청을 전부 422 로 거절해서 한 번 더 막는다
  - `plan.min_ms`: 0 이하면 null (5-2)
  - `plan.slides[].target_ms`: 대본 파싱의 `expected_ms` 를 지금 목표 시간에 맞춰 비율로 늘이거나
    줄인다 (script-parser.md 12-2 — 제한 시간만 바뀌면 다시 파싱하지 않는다)
  - `calibration.base_level_db`: 유한한 수가 아니면 null
  - 코칭 계획 요청: 입력 크기 상한(5-2 plan)을 넘으면 AI 가 422 를 준다. 부르지 않는다. 미션 설명만
    길면 그 설명만 뺀다
"""

import logging
import math
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from typing import Any

from pitch_coach_backend.coach.windows import (
    FinalWord,
    replay_input,
    slide_at,
    slide_changes,
    window_inputs,
)
from pitch_coach_backend.module.take.dto import (
    MetricSecond,
    MissionInput,
    SlideEvent,
    SlideTiming,
    TranscriptSegment,
)

logger = logging.getLogger(__name__)


# /coach/plan 입력 크기 상한 (coach-agent.md 5-2 plan). 넘으면 422 이고 계획 없이 코칭한다
PLAN_MAX_SLIDES = 100

PLAN_MAX_MISSIONS = 20

PLAN_MAX_RECURRING_ISSUES = 20


# 코드 포인트 수 (Python len)
PLAN_MAX_MISSION_DESCRIPTION = 500

_MODES = frozenset({"COACHING", "EXAM"})

_SCRIPT_MODES = frozenset({"HIGHLIGHT", "KEYWORD", "OFF"})


# ── 시간 계획 ─────────────────────────────────────────────────────────
def build_plan(
    target_sec: int,
    *,
    time_limit_sec: int,
    lower_deviation_sec: int,
    upper_deviation_sec: int,
    slides: Iterable[SlideTiming] = (),
    parsed_target_ms: int | None = None,
) -> dict[str, Any]:
    """시간 계획 `plan` (5-2).

    - `target_sec`: evaluate · finalize 는 Take 목표 시간(`takes.goal_time_sec`), 코칭 계획은 Take
      전이라 발표 제한 시간이다.
    - `min_ms` · `max_ms` 는 늘 **발표 제한 시간** ± 허용 범위다. 허용 미달 0 은 "여유 없음"이다
      (FE 가 그렇게 보여 준다) — 그래서 null 로 바꾸지 않고 제한 시간 그대로 싣는다.
    - 장별 계획은 **모든 장에 예상 시간이 있을 때만** 싣는다. 시간 판정은 장 목표의 합으로 남은
      시간을 재므로 일부만 있으면 진행도가 틀어진다. 없으면 빈 목록이고 Take 전체로만 판정한다.
    - `parsed_target_ms` 는 대본을 파싱할 때 요청한 목표(`script_versions.parse_meta.target_ms`)다.
      있으면 장 목표를 `target_sec` 에 맞춰 비율로 바꾼다. 파싱처럼 누적으로 반올림해 장 목표의 합이
      목표와 같게 둔다 — 장마다 반올림하면 합이 몇 ms 씩 어긋나 마지막 장의 남은 시간이 틀어진다.
    """
    target_ms = target_sec * 1000
    min_ms = (time_limit_sec - lower_deviation_sec) * 1000
    ordered = sorted(slides, key=lambda s: s.slide_number)
    planned = bool(ordered) and all(s.expected_ms is not None for s in ordered)
    targets = [s.expected_ms or 0 for s in ordered]
    if planned and parsed_target_ms is not None and parsed_target_ms > 0:
        targets = _scaled(targets, target_ms, parsed_target_ms)
    return {
        "target_ms": target_ms,
        "min_ms": min_ms if min_ms > 0 else None,
        "max_ms": (time_limit_sec + upper_deviation_sec) * 1000,
        "slides": [
            {
                "slide_number": s.slide_number,
                "target_ms": slide_target,
                # 없으면 0 — 그 장의 진행도는 머문 시간으로 잰다 (6-2)
                "script_chars": s.script_chars or 0,
            }
            for s, slide_target in zip(ordered, targets, strict=True)
        ]
        if planned
        else [],
    }


def _scaled(values: list[int], target: int, base: int) -> list[int]:
    """values 를 base 에 대한 비율로 보고 target 에 맞춘다. 누적합을 .5 올림으로 반올림해 나눈다."""
    scaled: list[int] = []
    running = 0
    previous = 0
    for value in values:
        running += value
        # running × target ÷ base 를 .5 올림 (모두 0 이상의 정수라 정수 연산으로 끝난다)
        cumulative = (2 * running * target + base) // (2 * base)
        scaled.append(cumulative - previous)
        previous = cumulative
    return scaled


# ── Take 동안 바뀌지 않는 값 ──────────────────────────────────────────
def missions_payload(missions: Iterable[MissionInput]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for mission in missions:
        entry: dict[str, Any] = {
            "mission_id": mission.mission_id,
            "area": mission.area,
            "slide_number": mission.slide_number,
            "target": dict(mission.target) if mission.target is not None else None,
        }
        if mission.priority is not None:
            entry["priority"] = mission.priority
        if mission.description is not None:
            entry["description"] = mission.description
        payload.append(entry)
    return payload


@dataclass(frozen=True)
class TakeConstants:
    """Take 를 시작할 때 한 번 읽어 Take 동안 들고 있는 값. evaluate · finalize 에 매번 싣는다 —
    `coach_state` 가 깨져 새로 시작해도 계획 · 미션 · 반복 문제는 이어지게 하려는 것이다(5-2)."""

    # Take UUID. 문자열로 바꿔 싣는다
    take_id: str | uuid.UUID
    mode: str
    plan: dict[str, Any]
    missions: list[dict[str, Any]] = field(default_factory=list)
    # 직전 리뷰의 recurring_issues 그대로 (BE 는 항목을 고치지 않는다)
    recurring_issues: list[dict[str, Any]] = field(default_factory=list)
    # Take 를 시작할 때 고른 코칭 계획 (12-2 계획 고르기, `why` 는 뺀다). 없으면 None
    coaching_plan: dict[str, Any] | None = None
    script_used: bool | None = None
    base_level_db: float | None = None
    script_mode: str | None = None

    def common(self) -> dict[str, Any]:
        """evaluate · finalize 가 같이 싣는 칸. Take 동안 바뀌지 않아 한 번만 만든다."""
        return self._common

    @cached_property
    def _common(self) -> dict[str, Any]:
        mode = self.mode.upper()
        base_level_db = self.base_level_db
        if base_level_db is not None and not math.isfinite(base_level_db):
            base_level_db = None
        return {
            "take_id": str(self.take_id),
            # 모르는 값이면 코치의 기본값(COACHING)으로 둔다. EXAM 으로 잘못 보면 말을 전혀 안 건다
            "mode": mode if mode in _MODES else "COACHING",
            "plan": self.plan,
            "missions": self.missions,
            "recurring_issues": self.recurring_issues,
            "coaching_plan": self.coaching_plan,
            "script_used": self.script_used,
            "calibration": {"base_level_db": base_level_db},
        }


# ── 코치 세션 · finalize 가 DB 에서 읽어 오는 묶음 ───────────────────────
@dataclass(frozen=True)
class TakeContext:
    """발표 중 코치가 Take 동안 들고 있는 값. Take 를 시작할 때(첫 WS 연결) 읽는다.

    `slides` 는 이미 저장된 장 변경이다 — 프로세스가 다시 떠 메모리를 잃어도 지금 장을 이어 간다.
    """

    constants: TakeConstants
    slides: tuple[SlideEvent, ...] = ()
    # BE 가 Take 도중 다시 떴을 때 DB 에 이미 있는 초. 재전송이 첫 저장본을 바꾸지 않게 한다.
    seconds: tuple[MetricSecond, ...] = ()


@dataclass(frozen=True)
class FinalizeSource:
    """finalize 를 DB 에서 다시 만들 때의 재료. 프로세스가 다시 떠 코치 세션이 없거나
    (`coach_state` 를 잃음), 409 를 받아 `replay` 가 필요할 때 쓴다."""

    context: TakeContext
    seconds: tuple[MetricSecond, ...]
    segments: tuple[TranscriptSegment, ...]
    utterance_ends: tuple[int, ...]
    # 받은 순서대로 (coach_events.seq). 이벤트 JSON 그대로
    events: tuple[dict[str, Any], ...]
    raw_data_consent: bool

    @property
    def t_end_ms(self) -> int | None:
        """Take 가 끝난 시각 — 마지막 `metrics` 의 바깥 `t_ms`. 1초 원자료가 없으면 None."""
        return max((s.t_ms for s in self.seconds), default=None)

    def replay(self, t_end_ms: int) -> dict[str, Any]:
        return replay_input(
            self.seconds,
            self.segments,
            self.utterance_ends,
            self.context.slides,
            t_end_ms=t_end_ms,
        )

    def last_window(self, replay: Mapping[str, Any], t_end_ms: int) -> dict[str, Any]:
        """마지막 창. `replay`(이 원자료로 만든 것)에서 발표 중과 같은 규칙으로 고른다. STT 상태는
        마지막 1초의 것이다."""
        words = [
            FinalWord(w["word"], w["start_ms"], w["end_ms"], w["final_at_ms"])
            for w in replay["words"]
        ]
        last = max(self.seconds, key=lambda s: s.t_ms, default=None)
        return window_inputs(
            t_end_ms,
            gaze_records=(s.gaze for s in self.seconds if s.gaze is not None),
            voice_records=(s.voice for s in self.seconds if s.voice is not None),
            words=words,
            utterance_ends=self.utterance_ends,
            stt_status=(last.stt_status if last is not None and last.stt_status else "connecting"),
            slide=slide_at(
                t_end_ms, slide_changes(self.context.slides, normalize_start=False)
            ),
        )


# ── 요청 ──────────────────────────────────────────────────────────────
def evaluate_request(
    constants: TakeConstants,
    t_ms: int,
    inputs: dict[str, Any],
    coach_state: dict[str, Any] | None,
) -> dict[str, Any]:
    """`/coach/evaluate`. `t_ms` 는 그 `metrics` 의 바깥 `t_ms`, `coach_state` 는 지난 응답 그대로
    (첫 요청은 None)."""
    return {**constants.common(), "t_ms": t_ms, "inputs": inputs, "coach_state": coach_state}


def finalize_request(
    constants: TakeConstants,
    t_ms: int,
    inputs: dict[str, Any],
    coach_state: dict[str, Any] | None,
    events: Iterable[Mapping[str, Any]],
    replay: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`/coach/finalize`.

    - `t_ms`: 마지막 `metrics` 의 바깥 `t_ms`. 종료에 들어갈 때 고정한다(STT 마무리 시간을 더하지
      않는다).
    - `inputs`: STT 를 닫은 뒤의 마지막 확정 단어까지 넘긴 `window_inputs`. `stt_status` 는 STT 를
      닫기 **직전**의 상태다 — `closed` 를 실으면 마지막 단어를 세지 못한다.
    - `events`: 이 Take 에서 받은 이벤트 전부, 받은 순서대로.
    - `replay`: 409 를 받았거나 `coach_state` 를 잃었을 때만 (`replay_input`).
    """
    script_mode = (constants.script_mode or "").upper()
    request = {
        **constants.common(),
        "t_ms": t_ms,
        # 모르는 값은 싣지 않는다. AI 가 finalize 를 422 로 거절해 Take 결과를 잃는다
        "script_mode": script_mode if script_mode in _SCRIPT_MODES else None,
        "coach_state": coach_state,
        "events": [dict(e) for e in events],
        "inputs": inputs,
    }
    if replay is not None:
        request["replay"] = replay
    return request


def plan_request(
    *,
    pitch_id: str | uuid.UUID,
    source_take_id: str | uuid.UUID | None,
    script_version_id: str | uuid.UUID | None,
    plan: dict[str, Any],
    scripts: Iterable[tuple[int, str]],
    missions: Iterable[MissionInput] = (),
    recurring_issues: Sequence[Mapping[str, Any]] = (),
    previous_take_result: dict[str, Any] | None = None,
    previous_review: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """`/coach/plan`. 입력 크기 상한을 넘으면 None — 부르지 않고 계획 없이 코칭한다.

    - `plan`: `build_plan(time_limit_sec, time_limit_sec=time_limit_sec, ...)`. Take 전이라
      `target_ms` 가 발표 제한 시간이다.
    - `source_take_id`: 그 발표에서 가장 늦게 끝난 `COMPLETED` Take (리뷰가 끝내 실패한 Take 포함,
      architecture.md 1-3 'Take 상태'). 첫 Take 면 None.
    - 미션 설명이 상한보다 길면 그 설명만 빼고 보낸다. 설명은 선택이고 없으면 코치가 영역 · 대상 ·
      목표로 계획을 세운다 — 설명 하나 때문에 계획 전체를 잃을 이유가 없다.
    """
    mission_list = list(missions)
    script_list = sorted(scripts)
    over = (
        len(plan["slides"]) > PLAN_MAX_SLIDES
        or len(script_list) > PLAN_MAX_SLIDES
        or len(mission_list) > PLAN_MAX_MISSIONS
        or len(recurring_issues) > PLAN_MAX_RECURRING_ISSUES
    )
    if over:
        logger.info(
            "코칭 계획 입력이 상한을 넘어 부르지 않는다 pitch=%s slides=%d scripts=%d "
            "missions=%d recurring=%d",
            pitch_id,
            len(plan["slides"]),
            len(script_list),
            len(mission_list),
            len(recurring_issues),
        )
        return None

    trimmed = 0
    for i, mission in enumerate(mission_list):
        description = mission.description
        if description is not None and len(description) > PLAN_MAX_MISSION_DESCRIPTION:
            mission_list[i] = MissionInput(
                mission_id=mission.mission_id,
                area=mission.area,
                slide_number=mission.slide_number,
                target=mission.target,
                priority=mission.priority,
            )
            trimmed += 1
    if trimmed:
        logger.info("코칭 계획 요청에서 긴 미션 설명을 뺐다 pitch=%s count=%d", pitch_id, trimmed)

    return {
        "pitch_id": str(pitch_id),
        "source_take_id": as_id(source_take_id),
        "script_version_id": as_id(script_version_id),
        "plan": plan,
        "scripts": [{"slide_number": n, "script": text} for n, text in script_list],
        "missions": missions_payload(mission_list),
        "recurring_issues": [dict(issue) for issue in recurring_issues],
        "previous_take_result": previous_take_result,
        "previous_review": previous_review,
    }


def as_id(value: str | uuid.UUID | None) -> str | None:
    # DB 의 Uuid 칼럼은 uuid.UUID, JSONB 에 저장된 값은 문자열이다. 그대로 비교하면 늘 다르다
    return str(value) if value is not None else None
