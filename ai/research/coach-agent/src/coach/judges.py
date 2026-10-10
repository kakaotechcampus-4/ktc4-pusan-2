"""판정 모듈 묶음(Judges)과 한 번의 판정 라운드(run).

코치는 판정 모듈을 import 하지 않는다. 부르는 쪽(나중에는 API 라우터, research 에서는
coach_lab)이 모듈 함수를 묶어 넘긴다. 모듈 사이에는 JSON 모양(dict)만 오가고, 코치는 받은
결과를 자기 schemas 의 JudgmentResult 로 검증한다.

run 은 요청 하나에 대해 filler → pace → gaze → volume → timing 순서로 판정하고 tally 를
누적한다. 모듈 하나가 예외를 내면 그 영역만 '잴 수 없음'으로 보고 나머지는 계속한다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from . import tally
from .config import CoachConfig
from .schemas import CoachInputs, CoachRequest, IssueCriteria, JudgmentResult, SlideNow, TallyItem
from .state import CoachState, Cursor
from .timing import criteria as timing_criteria
from .timing import judge as timing_judge
from .timing import summarize as timing_summarize
from .vocab import FeedbackType

log = logging.getLogger(__name__)

# BE 와 맞춘 통신 규약 값: 요청에 실리는 입력 창 길이
GAZE_VOICE_WINDOW_MS = 30_000  # 시선 · 음량 1초 기록
WORDS_WINDOW_MS = 60_000  # STT 확정 단어 · 문장 끝 신호

#: 모듈 이름 → 그 모듈이 내는 영역 (결과 순서)
_AREAS: dict[str, tuple[FeedbackType, ...]] = {
    "gaze": (FeedbackType.GAZE,),
    "pace": (FeedbackType.SPEED,),
    "volume": (FeedbackType.VOLUME, FeedbackType.PAUSE),
    "filler": (FeedbackType.FILLER,),
    "timing": (FeedbackType.TIME,),
}


#: 영역 → 그 영역 합계를 지표로 바꾸는 모듈 (summarize 가 있는 쪽)
_SUMMARY_MODULE: dict[FeedbackType, str] = {
    FeedbackType.GAZE: "gaze",
    FeedbackType.SPEED: "pace",
    FeedbackType.VOLUME: "volume",
    FeedbackType.PAUSE: "volume",
    FeedbackType.FILLER: "filler",
}


#: 영역 합계(영역 → 이름 → 합) → 그 영역 모듈이 계산한 지표. 못 읽으면 None
Summarizer = Callable[[FeedbackType, dict[str, dict[str, float]]], dict[str, Any] | None]


class Judge(Protocol):
    """판정 모듈 하나. 파이썬 모듈 객체(judge · summarize · criteria 함수)도 이 모양이다."""

    def judge(self, inputs: dict[str, Any], t_ms: int) -> list[Any]:
        """JudgmentResult 목록(모델 또는 dict)."""
        ...

    def summarize(self, tally: dict[str, dict[str, float]]) -> dict[str, Any]: ...

    def criteria(self) -> dict[str, Any]:
        """issue_type → IssueCriteria."""
        ...


@dataclass(frozen=True)
class Judges:
    """부르는 쪽이 판정 모듈을 묶어 넘긴다. 코치는 판정 모듈을 import 하지 않는다."""

    gaze: Judge
    pace: Judge
    volume: Judge
    filler: Judge
    #: volume 의 기준 음량 잡기: 말한 1초의 level_db 표본 → 값(모자라면 None)
    baseline: Callable[[list[float]], float | None]


@dataclass
class JudgeRun:
    """한 라운드의 결과."""

    results: dict[FeedbackType, JudgmentResult] = field(default_factory=dict)
    #: 예외를 낸 모듈 이름
    failed: set[str] = field(default_factory=set)
    #: STT 를 믿을 수 있는가 (상태가 ok 이고 오디오가 살아 있음)
    stt_ok: bool = True
    audio_dead: bool = False
    #: 모듈 이름 → issue_type → 판정 기준
    criteria: dict[str, dict[str, IssueCriteria]] = field(default_factory=dict)
    #: filler 결과의 words (단어별 판정)
    filler_words: list[dict[str, Any]] = field(default_factory=list)
    #: 영역 합계(영역 → 이름 → 합) → 그 영역의 지표. 읽을 수 없으면 None (개입 규칙의 미션 값)
    summarize: Summarizer | None = None


def run(req: CoachRequest, state: CoachState, judges: Judges, cfg: CoachConfig) -> JudgeRun:
    """요청 시각 t 의 판정 라운드. state 에 커서 · 합계 · STT 상태를 반영한다."""
    inputs = req.inputs
    t = req.t_ms
    out = JudgeRun()
    out.summarize = lambda area, totals: summarize(judges, area, totals, cfg)
    prev_ms = _round_start(state)
    slide = _note_slide(req, state)
    _track_stt(state, inputs, t, out)

    words = [w.model_dump() for w in inputs.words]
    max_end = max((w["end_ms"] for w in words), default=None)
    by_module: dict[str, list[JudgmentResult]] = {}

    def call(name: str, fn: Callable[[dict[str, Any], int], list[Any]], mod_inputs: dict) -> None:
        results = _safe_judge(name, fn, mod_inputs, t)
        if results is None:
            out.failed.add(name)
            results = _fallback(name, state, t, max_end)
        by_module[name] = results

    # 1. filler → 2. pace (filler 의 단어 판정을 받는다)
    stt_inputs = {
        "t_ms": t,
        "stt_status": inputs.stt_status,
        # STT 가 끊긴 적이 없으면 Take 시작(0)부터 믿는다
        "stt_ok_since_ms": state.stt_ok_since_ms or 0,
        "utterance_ends": inputs.utterance_ends,
        "words": words,
    }

    def stt_part(name: str) -> dict[str, Any]:
        cur = _cursor(state, name)
        return {**stt_inputs, "since_ms": cur.since_ms, "words_since_ms": cur.words_since_ms}

    call("filler", judges.filler.judge, stt_part("filler"))
    fillers = None
    if "filler" not in out.failed:
        try:
            fillers = _fillers(by_module["filler"][0].words or [])
        except (KeyError, TypeError, ValueError, OverflowError):
            # 단어별 판정이 깨졌으면 군더더기 판정이 실패한 것과 같다
            log.warning("filler words malformed t_ms=%s", t, exc_info=True)
            out.failed.add("filler")
            by_module["filler"] = _fallback("filler", state, t, max_end)
    out.filler_words = by_module["filler"][0].words or []
    call("pace", judges.pace.judge, {**stt_part("pace"), "fillers": fillers})

    # 3. gaze
    voiced = [{"t_ms": r.t_ms, "voiced_ms": r.voiced_ms} for r in inputs.voice_records]
    gaze_in = {
        "t_ms": t,
        "since_ms": _cursor(state, "gaze").since_ms,
        "records": [r.model_dump() for r in inputs.gaze_records],
        "voiced": voiced,
    }
    call("gaze", judges.gaze.judge, gaze_in)

    # 4. volume
    voice_records = [r.model_dump() for r in inputs.voice_records]
    base = _base_level(req, state, judges, inputs)
    volume_in = {
        "t_ms": t,
        "since_ms": _cursor(state, "volume").since_ms,
        "records": voice_records,
        "base_level_db": base,
    }
    call("volume", judges.volume.judge, volume_in)

    # 5. timing: 장별 합계를 넘긴다. 말한 글자 수는 이번 라운드에 말 속도가 센 몫까지 더한다 —
    #    말 속도를 시간 판정보다 먼저 부르는 이유다(진행도가 한 박자 늦지 않게). 머문 시간 · STT 를
    #    믿은 시간은 이번 몫을 시간 판정이 직접 더하므로 판정 전 합계를 넘긴다
    pace_result = by_module["pace"][0]
    out.criteria = criteria_of(judges, cfg)
    fast = out.criteria["pace"].get("PACE_FAST")
    slide_chars = _per_slide(state, "SPEED", "chars")
    for piece in pace_result.tally:
        chars = int(piece.values.get("chars", 0))
        spoken_in = tally.slide_at(state, piece.t_ms)
        if chars and spoken_in is not None:
            slide_chars[str(spoken_in)] = slide_chars.get(str(spoken_in), 0) + chars
    timing_in = {
        "t_ms": t,
        "since_ms": _cursor(state, "timing").since_ms,
        "plan": req.plan.model_dump(mode="json"),
        "slide": slide.model_dump() if slide else None,
        "slide_chars": slide_chars,
        "slide_dwell_ms": _per_slide(state, "TIME", "elapsed_ms"),
        "slide_stt_ok_ms": dict(state.slide_stt_ok_ms),
        "pace": {
            "cpm": pace_result.metrics.get("cpm"),
            # 오디오가 멈췄거나 군더더기 · 말 속도 판정이 실패하면 속도를 모르는 것으로 넘긴다
            "measurable": pace_result.measurable
            and out.stt_ok
            and not {"filler", "pace"} & out.failed,
            "fast_threshold": fast.threshold if fast else None,
        },
    }
    call("timing", lambda i, t_ms: timing_judge(i, t_ms, cfg.timing), timing_in)

    # 누적 → 커서 · 기준 버전. 결과는 영역 순서(GAZE … TIME)로 담는다
    trusted = out.stt_ok and not {"filler", "pace"} & out.failed
    all_results = _commit(state, by_module, prev_ms, t, trusted, out.failed)
    out.results = {r.area: r for r in all_results}
    return out


def skip(req: CoachRequest, state: CoachState) -> None:
    """코치 안의 예외 뒤에 이번 시간을 잴 수 없던 것으로 넘긴다.

    모든 모듈이 예외를 낸 것과 같다: 잴 수 없음으로 두고, 커서는 이번 창 끝까지 넘기고, 그 시간은
    영역 합계의 total_ms(timing 은 elapsed_ms)에만 센다. 같은 요청이 같은 예외를 되풀이해도
    빠져나오게 하려는 것이다.
    """
    t = req.t_ms
    prev_ms = _round_start(state)
    _note_slide(req, state)
    max_end = max((w.end_ms for w in req.inputs.words), default=None)
    by_module = {name: _fallback(name, state, t, max_end) for name in _AREAS}
    _commit(state, by_module, prev_ms, t, trusted=False, failed=set(_AREAS))


def _round_start(state: CoachState) -> int:
    """이번 라운드가 시작하는 시각: 시각을 세는 모듈 커서 중 가장 이른 것."""
    return min(_cursor(state, n).since_ms for n in ("gaze", "volume", "timing"))


def _note_slide(req: CoachRequest, state: CoachState) -> SlideNow | None:
    """장 전환을 기록하고 지금 장을 돌려준다.

    장 정보가 이번 요청에 없으면 마지막으로 알던 장으로 본다
    (시간 판정 · 장 귀속 · 개입 규칙이 같은 장을 쓰게).
    """
    slide = req.inputs.slide
    if slide is None and state.slide_log:
        number, started = state.slide_log[-1]
        slide = SlideNow(number=number, started_ms=started)
    tally.note_slide(state, slide)
    return slide


def _commit(
    state: CoachState,
    by_module: dict[str, list[JudgmentResult]],
    prev_ms: int,
    t: int,
    trusted: bool,
    failed: set[str],
) -> list[JudgmentResult]:
    """모듈별 결과를 합계에 더하고 센 구간을 표시하고 커서를 넘긴다. 영역 순서의 결과를 돌려준다.

    실패한 모듈의 결과는 기준 버전을 기록하지 않는다 — 모듈이 낸 버전이 아니다.
    """
    all_results = [r for name in _AREAS for r in by_module[name]]
    tally.accumulate(state, all_results, stt_trusted=trusted)
    tally.mark_covered(state, prev_ms, t, GAZE_VOICE_WINDOW_MS)
    tally.extend_slide_span(state, t)
    for name, results in by_module.items():
        _advance(state, name, results, record_version=name not in failed)
    return all_results


def _cursor(state: CoachState, name: str) -> Cursor:
    return state.cursors.get(name) or Cursor()


def _fillers(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """말 속도에 넘길 군더더기 판정: 군더더기(true)와 보류(null) 단어만."""
    out = []
    for w in words:
        is_filler = w.get("is_filler")
        if is_filler is not None and not isinstance(is_filler, bool):
            raise ValueError(f"is_filler 가 bool · null 이 아니다: {is_filler!r}")
        if is_filler is False:
            continue
        out.append(
            {"start_ms": int(w["start_ms"]), "end_ms": int(w["end_ms"]), "is_filler": is_filler}
        )
    return out


def _per_slide(state: CoachState, area: str, name: str) -> dict[str, int]:
    """장 번호(문자열) → 그 장 합계 중 area 의 name."""
    return {k: int(v[area].get(name, 0)) for k, v in state.slide_totals.items() if area in v}


def _track_stt(state: CoachState, inputs: CoachInputs, t: int, out: JudgeRun) -> None:
    """STT 를 믿을 수 있는지 정하고 stt_gap · stt_ok_since_ms 를 갱신한다.

    오디오가 멈추면 STT 도 못 듣는다 — 상태가 ok 여도 그동안의 단어는 믿지 않는다. 모듈에는
    BE 의 stt_status 를 그대로 넘기고, 오디오 정지는 코치의 slide_stt_ok_ms 계산에만 쓴다.
    """
    latest = max(inputs.voice_records, key=lambda r: r.t_ms, default=None)
    out.audio_dead = latest is not None and not latest.audio_live
    out.stt_ok = inputs.stt_status == "ok" and not out.audio_dead
    if not out.stt_ok:
        state.stt_gap = True
    elif state.stt_gap:
        state.stt_gap = False
        state.stt_ok_since_ms = t


def _base_level(
    req: CoachRequest, state: CoachState, judges: Judges, inputs: CoachInputs
) -> float | None:
    """volume 에 넘길 기준 음량. 보정 값이 있으면 그것, 없으면 Take 첫 발화로 잡은 값."""
    calibrated = req.calibration.base_level_db
    if calibrated is not None:
        state.base_level_db = calibrated
        state.base_level_source = "CALIBRATION"
        return calibrated
    if state.base_level_db is None:
        since = _cursor(state, "volume").since_ms
        # 같은 1초가 두 번 오면 먼저 온 기록만 쓴다(소리 크기 모듈과 같은 규칙).
        # 말하지 않은 1초(level_db 없음)는 표본에서 뺀다
        first: dict[int, float | None] = {}
        for r in inputs.voice_records:
            if r.t_ms >= since:
                first.setdefault(r.t_ms, r.level_db)
        state.baseline_samples.extend(v for _, v in sorted(first.items()) if v is not None)
        try:
            value = judges.baseline(list(state.baseline_samples))
        except Exception:  # noqa: BLE001 — 기준을 못 잡아도 다음 요청에서 다시 시도한다
            log.warning("baseline failed", exc_info=True)
            value = None
        if value is not None:
            state.base_level_db = value
            state.base_level_source = "TAKE"
            state.baseline_samples = []
    return state.base_level_db


def _safe_judge(
    name: str, fn: Callable[[dict[str, Any], int], list[Any]], inputs: dict[str, Any], t: int
) -> list[JudgmentResult] | None:
    """모듈을 부르고 결과를 JudgmentResult 로 검증한다. 예외가 나면 None."""
    try:
        raw = fn(inputs, t)
        results = [
            JudgmentResult.model_validate(r if isinstance(r, dict) else r.model_dump(mode="json"))
            for r in raw
        ]
        areas = [r.area for r in results]
        if sorted(areas) != sorted(_AREAS[name]) or any(r.evaluator != name for r in results):
            raise ValueError(f"{name}: 결과 영역 {areas} (기대 {list(_AREAS[name])})")
        return sorted(results, key=lambda r: _AREAS[name].index(r.area))
    except Exception:  # noqa: BLE001 — 모듈 하나의 예외가 다른 영역을 막으면 안 된다
        log.warning("judge module failed: %s t_ms=%s", name, t, exc_info=True)
        return None


def _fallback(
    name: str, state: CoachState, t: int, max_word_end: int | None
) -> list[JudgmentResult]:
    """모듈이 예외를 냈을 때의 결과: 잴 수 없음, 커서는 이번 창 끝까지, 시간만 합계에 센다.

    순수 코드의 예외는 같은 입력에서 되풀이되므로 커서를 넘겨 그 시간을 건너뛴다.
    """
    cur = _cursor(state, name)
    key = "elapsed_ms" if name == "timing" else "total_ms"
    pieces = [
        TallyItem(t_ms=lo, values={key: hi - lo})
        for lo, hi in tally.time_pieces(state, cur.since_ms, t)
    ]
    words_until = None
    if name in ("filler", "pace"):
        words_until = (
            cur.words_since_ms if max_word_end is None else max(cur.words_since_ms, max_word_end)
        )
    # 모듈이 낸 결과가 아니라 버전은 마지막으로 알던 것(모르면 unknown)을 싣기만 한다
    version = state.latest_criteria_versions.get(name, f"{name}-unknown")
    return [
        JudgmentResult(
            evaluator=name,
            area=area,
            t_ms=t,
            counted_until_ms=max(cur.since_ms, t),
            words_counted_until_ms=words_until,
            criteria_version=version,
            measurable=False,
            state="UNMEASURABLE" if name == "gaze" else "UNKNOWN",
            tally=pieces,
        )
        for area in _AREAS[name]
    ]


def _advance(
    state: CoachState, name: str, results: list[JudgmentResult], record_version: bool = True
) -> None:
    """커서를 결과의 counted_until 로 옮기고 criteria_version 을 기록한다."""
    cur = state.cursors.setdefault(name, Cursor())
    for r in results:
        cur.since_ms = max(cur.since_ms, r.counted_until_ms)
        if r.words_counted_until_ms is not None:
            cur.words_since_ms = max(cur.words_since_ms, r.words_counted_until_ms)
    if not record_version:
        return
    version = results[0].criteria_version
    state.criteria_versions.setdefault(name, version)
    state.latest_criteria_versions[name] = version


def summarize(
    judges: Judges, area: FeedbackType, totals: dict[str, dict[str, float]], cfg: CoachConfig
) -> dict[str, Any] | None:
    """합계를 그 영역 모듈의 summarize 로 지표로 바꾼다. 못 읽었으면 None."""
    try:
        if area == FeedbackType.TIME:
            return timing_summarize(totals, cfg.timing)
        return dict(getattr(judges, _SUMMARY_MODULE[area]).summarize(totals))
    except Exception:  # noqa: BLE001 — 지표를 못 읽어도 미션 가중만 빠진다
        log.warning("summarize failed: %s", area, exc_info=True)
        return None


def criteria_of(judges: Judges, cfg: CoachConfig) -> dict[str, dict[str, IssueCriteria]]:
    """모듈별 판정 기준. 기준을 못 읽은 모듈은 빈 dict."""
    sources: dict[str, Callable[[], dict[str, Any]]] = {
        "gaze": judges.gaze.criteria,
        "pace": judges.pace.criteria,
        "volume": judges.volume.criteria,
        "filler": judges.filler.criteria,
    }
    out: dict[str, dict[str, IssueCriteria]] = {}
    for name, fn in sources.items():
        try:
            raw = fn()
            out[name] = {
                str(k): IssueCriteria.model_validate(v if isinstance(v, dict) else v.model_dump())
                for k, v in raw.items()
            }
        except Exception:  # noqa: BLE001
            log.warning("criteria failed: %s", name, exc_info=True)
            out[name] = {}
    out["timing"] = timing_criteria(cfg.timing)
    return out
