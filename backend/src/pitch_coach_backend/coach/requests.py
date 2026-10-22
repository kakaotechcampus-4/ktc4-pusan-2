"""코치 요청을 만든다. DB 도 네트워크도 모른다 (보내는 것은 coach/client.py).

부르는 쪽(Take 시작 · 발표 중 · 종료 · 코칭 계획)이 DB 와 WS 에서 꺼낸 값을 넘기면 요청 dict 가
나온다. 필드 이름 · 단위는 `docs/ai/features/coach-agent.md` 5-2 와 같고, 시각은 모두 Take 시작
기준 ms 다.

**창을 고르는 규칙은 AI 의 replay 와 같다** (`ai/research/coach-agent/src/coach/recovery.py` 의
`inputs_at`). 발표 중 BE 가 보낸 창과 Take 뒤 원자료로 다시 만든 창이 같아야, 응답이 빠져 replay
로 만든 Take 결과가 정상 진행과 같다(coach-agent.md 1-2). 슬라이드 시작 시각도 발표 중과 replay 가
`SlideTracker` 하나로 정한다.

BE 가 값을 고치거나 빼는 곳은 아래뿐이다. 나머지(미션 · 반복 문제 · 1초 기록)는 받은 그대로 싣는다.
  - `mode` · `script_mode`: 계약에 없는 값이면 `COACHING` · null 로 둔다. takes CHECK 가 막지만,
    계약에 없는 값을 그대로 보내면 AI 가 그 Take 의 요청을 전부 422 로 거절해서 한 번 더 막는다
  - `plan.min_ms`: 0 이하면 null (5-2)
  - `calibration.base_level_db`: 유한한 수가 아니면 null
  - 코칭 계획 요청: 입력 크기 상한(5-2 plan)을 넘으면 AI 가 422 를 준다. 부르지 않는다. 미션 설명만
    길면 그 설명만 뺀다
"""

import logging
import math
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pitch_coach_backend.coach.client import PlanReply

logger = logging.getLogger(__name__)

# 발표 중 창 (architecture.md 2절). AI 의 judges.py 와 같은 값이다
GAZE_VOICE_WINDOW_MS = 30_000
WORDS_WINDOW_MS = 60_000

# /coach/plan 입력 크기 상한 (coach-agent.md 5-2 plan). 넘으면 422 이고 계획 없이 코칭한다
PLAN_MAX_SLIDES = 100
PLAN_MAX_MISSIONS = 20
PLAN_MAX_RECURRING_ISSUES = 20
# 코드 포인트 수 (Python len)
PLAN_MAX_MISSION_DESCRIPTION = 500

_MODES = frozenset({"COACHING", "EXAM"})
_SCRIPT_MODES = frozenset({"HIGHLIGHT", "KEYWORD", "OFF"})


# ── 시간 계획 ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SlideTiming:
    """대본 한 장의 시간 계획 (script_slides). 대본 파싱이 값을 주기 전이면 None 이다."""

    slide_number: int
    expected_ms: int | None
    script_chars: int | None


def build_plan(
    target_sec: int,
    *,
    time_limit_sec: int,
    lower_deviation_sec: int,
    upper_deviation_sec: int,
    slides: Iterable[SlideTiming] = (),
) -> dict[str, Any]:
    """시간 계획 `plan` (5-2).

    - `target_sec`: evaluate · finalize 는 Take 목표 시간(`takes.goal_time_sec`), 코칭 계획은 Take
      전이라 발표 제한 시간이다.
    - `min_ms` · `max_ms` 는 늘 **발표 제한 시간** ± 허용 범위다. 허용 미달 0 은 "여유 없음"이다
      (FE 가 그렇게 보여 준다) — 그래서 null 로 바꾸지 않고 제한 시간 그대로 싣는다.
    - 장별 계획은 **모든 장에 예상 시간이 있을 때만** 싣는다. 시간 판정은 장 목표의 합으로 남은
      시간을 재므로 일부만 있으면 진행도가 틀어진다. 없으면 빈 목록이고 Take 전체로만 판정한다.
    """
    min_ms = (time_limit_sec - lower_deviation_sec) * 1000
    ordered = sorted(slides, key=lambda s: s.slide_number)
    planned = bool(ordered) and all(s.expected_ms is not None for s in ordered)
    return {
        "target_ms": target_sec * 1000,
        "min_ms": min_ms if min_ms > 0 else None,
        "max_ms": (time_limit_sec + upper_deviation_sec) * 1000,
        "slides": [
            {
                "slide_number": s.slide_number,
                "target_ms": s.expected_ms,
                # 없으면 0 — 그 장의 진행도는 머문 시간으로 잰다 (6-2)
                "script_chars": s.script_chars or 0,
            }
            for s in ordered
        ]
        if planned
        else [],
    }


# ── Take 동안 바뀌지 않는 값 ──────────────────────────────────────────


@dataclass(frozen=True)
class MissionInput:
    """이번 Take 에 실린 미션 하나 (review-agent.md 12-1 의 missions)."""

    mission_id: str
    area: str
    slide_number: int | None
    # {metric, operator, value}
    target: Mapping[str, Any] | None
    # 없으면 키를 뺀다. 코치가 1 로 본다 (명시적 null 은 계약 위반이다)
    priority: int | None = None
    description: str | None = None


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
    # 오래된 계획 막기를 통과한 계획 (`coaching_plan_for_take`). 없으면 None
    coaching_plan: dict[str, Any] | None = None
    script_used: bool | None = None
    base_level_db: float | None = None
    script_mode: str | None = None

    def common(self) -> dict[str, Any]:
        """evaluate · finalize 가 같이 싣는 칸."""
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


# ── 창 ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class FinalWord:
    """STT 확정 단어. `final_at_ms` 는 그 단어가 확정된 Take 시각(replay 에서 넣는 순서)."""

    word: str
    start_ms: int
    end_ms: int
    final_at_ms: int


class SlideTracker:
    """`metrics.slide_number` 로 지금 장과 그 장의 시작 시각을 정한다.

    - 장이 바뀌면 그 `metrics` 의 시각(바깥 `t_ms`)이 새 장의 시작이다.
    - 처음 알게 된 장은 Take 시작(0)부터로 본다. 발표는 첫 장에서 시작하고, 앞의 `metrics` 가
      장을 몰랐어도(null) 그동안 화면에 있던 장이다.
    - null(잴 수 없음)은 무시하고 마지막으로 알던 장을 둔다.
    """

    def __init__(self) -> None:
        self.current: dict[str, int] | None = None
        # 지금까지의 장 변경. replay 의 `slides`
        self.changes: list[dict[str, int]] = []

    def observe(self, t_ms: int, slide_number: int | None) -> None:
        if slide_number is None:
            return
        if self.current is not None and self.current["number"] == slide_number:
            return
        started_ms = t_ms if self.current is not None else 0
        self.current = {"number": slide_number, "started_ms": started_ms}
        self.changes.append(self.current)


def window_inputs(
    t_ms: int,
    *,
    gaze_records: Iterable[Mapping[str, Any]],
    voice_records: Iterable[Mapping[str, Any]],
    words: Iterable[FinalWord],
    utterance_ends: Iterable[int],
    stt_status: str,
    slide: Mapping[str, int] | None,
) -> dict[str, Any]:
    """`t_ms` 요청의 `inputs` (5-2). 넘긴 것 중 창 안의 것만 고른다.

    - 시선 · 음량: 30초 안에 시작해 `t_ms` 까지 끝난 1초 기록. 같은 1초가 또 있으면 먼저 것
    - 단어: 60초 안에 끝났고 `t_ms` 까지 끝난 확정 단어. 넣을지는 끝난 시각 `end_ms` 로 정한다
    - 문장 끝: 60초 안의 것

    단어는 부르는 쪽이 그때까지 받은 것만 넘긴다(확정 시각으로 거르지 않는다). finalize 의 마지막
    창은 STT 를 닫은 뒤에 확정된 단어까지 넘기면 된다. Take 끝(`t_ms`)보다 늦게 끝난 것으로 찍힌
    단어는 마지막 창에서도 빠진다 — AI 의 replay 마지막 창과 예시 요청이 그렇게 고른다.
    """
    record_lo = t_ms - GAZE_VOICE_WINDOW_MS
    word_lo = t_ms - WORDS_WINDOW_MS
    return {
        "gaze_records": _records_between(gaze_records, record_lo, t_ms),
        "voice_records": _records_between(voice_records, record_lo, t_ms),
        "words": [
            {"word": w.word, "start_ms": w.start_ms, "end_ms": w.end_ms}
            for w in sorted(words, key=lambda w: (w.start_ms, w.end_ms))
            if word_lo < w.end_ms <= t_ms
        ],
        "utterance_ends": sorted({u for u in utterance_ends if word_lo <= u <= t_ms}),
        "stt_status": stt_status,
        "slide": dict(slide) if slide is not None else None,
    }


def _records_between(
    records: Iterable[Mapping[str, Any]], lo_ms: int, hi_ms: int
) -> list[dict[str, Any]]:
    """[lo_ms 에 시작, hi_ms 까지 끝남] 인 1초 기록을 시작 순으로. 같은 시작은 먼저 온 것만."""
    picked: dict[int, dict[str, Any]] = {}
    for record in records:
        start = record["t_ms"]
        if start in picked or start < lo_ms:
            continue
        if start + record.get("duration_ms", 1000) <= hi_ms:
            picked[start] = dict(record)
    return [picked[start] for start in sorted(picked)]


# ── replay ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MetricSecond:
    """1초 원자료 한 행 (take_metric_seconds). `t_ms` 는 그 `metrics` 의 바깥 시각이다."""

    t_ms: int
    slide_number: int | None
    gaze: Mapping[str, Any] | None
    voice: Mapping[str, Any] | None
    # 그 `metrics` 를 받았을 때의 STT 상태. 마이그레이션 전 행은 None
    stt_status: str | None


@dataclass(frozen=True)
class TranscriptSegment:
    """확정 전사 구간 한 행 (take_transcript_segments). words 는 저장된 모양 그대로."""

    end_ms: int
    words: Sequence[Mapping[str, Any]]
    # 그 구간이 확정된 Take 시각. 마이그레이션 전 행은 None
    final_at_ms: int | None


def replay_input(
    seconds: Iterable[MetricSecond],
    segments: Iterable[TranscriptSegment],
    utterance_ends: Iterable[int],
    *,
    t_end_ms: int,
) -> dict[str, Any]:
    """Take 전체 원자료 `replay` (5-2 finalize). 발표 중 보낸 창이 이것에서 다시 나오게 만든다.

    `t_end_ms` 는 finalize 요청의 `t_ms`(Take 끝)다.

    - STT 상태 변경: 각 `metrics` 를 받았을 때의 상태가 바뀐 시각. 발표 중 요청도 그 시각의 상태를
      실었으므로 그 사이의 짧은 끊김은 양쪽 다 모른다. 처음 알게 된 상태는 Take 시작(0)부터로 둔다 —
      AI 는 이력보다 앞을 '연결 중'으로 보고 그동안의 단어를 세지 않는데, 발표 중 첫 요청은 그
      상태로 첫 1초의 단어를 셌다
    - 장 변경: `SlideTracker` (발표 중과 같은 규칙)
    - 단어: 구간의 확정 시각을 구간 안 단어마다 붙인다(12-1). 확정 시각이 없는 옛 구간은 구간 끝을
      대신 쓴다 — 실제보다 일찍 들어가지만 순서는 그대로다. STT 를 닫은 뒤에 확정된 단어는 확정
      시각이 Take 끝보다 늦다. AI 는 마지막 창에 확정 시각이 Take 끝 이하인 단어만 넣으므로 Take
      끝으로 자른다 — 정상 finalize 의 마지막 창에 들어간 단어가 replay 에서 빠지지 않게(AI 예시도
      이렇게 만든다)
    """
    ordered = sorted(seconds, key=lambda s: s.t_ms)
    slides = SlideTracker()
    stt_status_changes: list[dict[str, Any]] = []
    for second in ordered:
        slides.observe(second.t_ms, second.slide_number)
        status = second.stt_status
        if status is None:
            continue
        if not stt_status_changes:
            stt_status_changes.append({"t_ms": 0, "status": status})
        elif stt_status_changes[-1]["status"] != status:
            stt_status_changes.append({"t_ms": second.t_ms, "status": status})

    words: list[dict[str, Any]] = []
    for segment in segments:
        final_at_ms = segment.final_at_ms if segment.final_at_ms is not None else segment.end_ms
        final_at_ms = min(final_at_ms, t_end_ms)
        words.extend(
            {
                "word": w["word"],
                "start_ms": w["start_ms"],
                "end_ms": w["end_ms"],
                "final_at_ms": final_at_ms,
            }
            for w in segment.words
        )
    words.sort(key=lambda w: (w["final_at_ms"], w["start_ms"], w["end_ms"]))

    return {
        "gaze_records": _first_per_start(s.gaze for s in ordered),
        "voice_records": _first_per_start(s.voice for s in ordered),
        "stt_status_changes": stt_status_changes,
        "words": words,
        "utterance_ends": sorted(set(utterance_ends)),
        "slides": slides.changes,
    }


def _first_per_start(records: Iterable[Mapping[str, Any] | None]) -> list[dict[str, Any]]:
    picked: dict[int, dict[str, Any]] = {}
    for record in records:
        if record is not None and record["t_ms"] not in picked:
            picked[record["t_ms"]] = dict(record)
    return [picked[start] for start in sorted(picked)]


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
    - `source_take_id`: 그 발표에서 가장 최근에 `COMPLETED` 가 된 Take. 첫 Take 면 None.
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
        "source_take_id": _id(source_take_id),
        "script_version_id": _id(script_version_id),
        "plan": plan,
        "scripts": [{"slide_number": n, "script": text} for n, text in script_list],
        "missions": missions_payload(mission_list),
        "recurring_issues": [dict(issue) for issue in recurring_issues],
        "previous_take_result": previous_take_result,
        "previous_review": previous_review,
    }


# ── 코칭 계획 보관 · 고르기 ───────────────────────────────────────────


def stored_plan(request: Mapping[str, Any], reply: PlanReply) -> dict[str, Any]:
    """`pitches.coaching_plan` 에 둘 모양 (coach-agent.md 12-1).

    두 id 와 제한 시간(`plan` 의 target · min · max)은 BE 가 보낸 **요청** 값으로 둔다. 다음 Take 를
    시작할 때 이 값으로 오래된 계획인지 본다. AI 는 두 id 를 그대로 돌려주게 되어 있지만, 빠뜨리거나
    다르게 돌려줘도 저장할 계획이 영영 오래된 것으로 남지 않게 응답 값은 쓰지 않는다.
    """
    plan = request["plan"]
    for key in ("source_take_id", "script_version_id"):
        echoed = getattr(reply, key)
        if echoed is not None and echoed != request[key]:
            logger.warning(
                "코칭 계획 응답의 %s 가 요청과 다르다 pitch=%s", key, request.get("pitch_id")
            )
    return {
        "coaching_plan": reply.coaching_plan,
        "source_take_id": request["source_take_id"],
        "script_version_id": request["script_version_id"],
        "target_ms": plan["target_ms"],
        "min_ms": plan["min_ms"],
        "max_ms": plan["max_ms"],
        "fallback_reason": reply.fallback_reason,
        "dropped": reply.dropped,
        "planner_hash": reply.planner_hash,
        "meta": reply.meta,
    }


def coaching_plan_for_take(
    stored: Mapping[str, Any] | None,
    *,
    source_take_id: str | uuid.UUID | None,
    script_version_id: str | uuid.UUID | None,
    time_limit_sec: int,
    lower_deviation_sec: int,
    upper_deviation_sec: int,
) -> dict[str, Any] | None:
    """오래된 계획 막기 (12-2). 저장된 계획이 지금 것과 같을 때만 `why` 를 뺀 계획을 돌려준다.

    '지금 것'은 Take 를 시작할 때의 발표 상태다: 가장 최근에 `COMPLETED` 가 된 Take(없으면 None) ·
    지금 대본 버전 · 지금 제한 시간과 허용 범위. 하나라도 다르면 None 이고 계획 없이 시작한다.
    시간 계획은 계획을 요청할 때처럼 제한 시간으로 만들어 비교한다 — Take 목표 시간으로 만든
    계획을 넘기면 목표가 제한 시간과 다를 때마다 오래된 계획이 된다.
    """
    if stored is None:
        return None
    plan = build_plan(
        time_limit_sec,
        time_limit_sec=time_limit_sec,
        lower_deviation_sec=lower_deviation_sec,
        upper_deviation_sec=upper_deviation_sec,
    )
    current = (
        _id(source_take_id),
        _id(script_version_id),
        plan["target_ms"],
        plan["min_ms"],
        plan["max_ms"],
    )
    saved = tuple(
        stored.get(key)
        for key in ("source_take_id", "script_version_id", "target_ms", "min_ms", "max_ms")
    )
    if saved != current:
        return None
    coaching_plan = stored.get("coaching_plan")
    if not isinstance(coaching_plan, Mapping):
        return None
    return {
        **coaching_plan,
        # why 는 와도 쓰지 않는다 (5-2). 사람이 읽는 근거라 1초마다 싣지 않는다
        "focus": [_without_why(item) for item in coaching_plan.get("focus", [])],
        "relax": [_without_why(item) for item in coaching_plan.get("relax", [])],
    }


def _id(value: str | uuid.UUID | None) -> str | None:
    # DB 의 Uuid 칼럼은 uuid.UUID, JSONB 에 저장된 값은 문자열이다. 그대로 비교하면 늘 다르다
    return str(value) if value is not None else None


def _without_why(item: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key != "why"}
