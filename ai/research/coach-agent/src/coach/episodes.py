"""문제 구간 — 같은 문제가 이어지는 동안을 한 구간으로 묶는다.

구간은 세 가지에 쓰입니다.
- 지속시간: 구간이 시작된 뒤 몇 ms 째인가 (NOT_PERSISTENT 판단)
- candidate_id: "문제코드-시작시각". 구간이 이어지는 동안 같은 ID 라서 개입과 문제를 잇는다
- 리뷰 근거: 구간이 끝나면 EPISODE 이벤트로 남는다. 개입하지 못한 구간도 남긴다

1~2초 깜빡임으로 구간이 끊기지 않도록 episode_gap_ms 동안 안 보여야 닫는다.
"""

from __future__ import annotations

from typing import Literal

from .candidates import Candidate
from .config import CoachConfig
from .evaluators.base import Tick
from .events import EventSink
from .schemas import EpisodeEvent
from .state import CoachState, EpisodeState, strategy_key
from .vocab import ISSUE_TYPE, SLIDE_SCOPED, CandidateStatus


def observe(tick: Tick, sink: EventSink) -> None:
    st = tick.state
    gap = tick.cfg.policy.episode_gap_ms
    seen: set[str] = set()

    for det in tick.detections:
        key = strategy_key(det.issue_type, det.slide_number, det.issue_type in SLIDE_SCOPED)
        seen.add(key)
        episode = st.episodes.get(key)
        if episode is not None and tick.t - episode.last_seen_ms > gap:
            close(st, key, tick.t, "RESOLVED", sink, tick.cfg)
            episode = None
        if episode is None:
            episode = EpisodeState(
                candidate_id=f"{det.issue_type.value}-{tick.t}",
                issue_type=det.issue_type,
                area=ISSUE_TYPE[det.issue_type],
                slide_number=det.slide_number,
                start_ms=tick.t,
                last_seen_ms=tick.t,
            )
            st.episodes[key] = episode
        episode.last_seen_ms = tick.t
        episode.severity_ms += det.severity * tick.dt_ms
        if det.sensor_ok and det.confidence >= tick.cfg.policy.min_confidence:
            episode.reliable_ms += tick.dt_ms
            if episode.reliable_since_ms is None:
                episode.reliable_since_ms = tick.t
        else:
            # 센서를 믿을 수 없던 시간은 지속시간에 넣지 않는다 — 잠깐 믿을 만해진 1초에 바로 말하지
            # 않게
            episode.unreliable_ms += tick.dt_ms
            episode.reliable_since_ms = None
        if det.severity > episode.peak_severity:
            episode.peak_severity = round(det.severity, 4)
            episode.peak_evidence = dict(det.evidence)

    for key in list(st.episodes):
        if key not in seen and tick.t - st.episodes[key].last_seen_ms > gap:
            close(st, key, tick.t, "RESOLVED", sink, tick.cfg)


def note_candidates(tick: Tick, candidates: list[Candidate]) -> None:
    """말하지 못한 이유를 구간에 모은다 — 리뷰가 '왜 이 구간엔 코칭이 없었나'를 알 수 있게."""
    for c in candidates:
        if c.praise is not None or c.status not in (
            CandidateStatus.WAITING,
            CandidateStatus.IGNORED,
        ):
            continue
        episode = tick.state.episodes.get(c.strategy_key)
        if episode is None:
            continue
        for reason in c.reasons_against:
            if reason.value not in episode.suppressed_reasons:
                episode.suppressed_reasons.append(reason.value)


def note_intervention(tick: Tick, c: Candidate, intervention_id: str) -> None:
    episode = tick.state.episodes.get(c.strategy_key)
    if episode is not None:
        episode.intervention_ids.append(intervention_id)


def close(
    st: CoachState,
    key: str,
    t_ms: int,
    closed_by: Literal["RESOLVED", "TAKE_END"],
    sink: EventSink,
    cfg: CoachConfig,
) -> None:
    episode = st.episodes.pop(key)
    duration = episode.last_seen_ms - episode.start_ms
    # 짧고 아무 일도 없던 깜빡임은 리뷰에 넘기지 않는다
    if not episode.intervention_ids and duration < cfg.policy.min_episode_ms:
        return
    sink.emit(
        EpisodeEvent,
        t_ms=t_ms,
        candidate_id=episode.candidate_id,
        issue_type=episode.issue_type,
        area=episode.area,
        slide_number=episode.slide_number,
        start_ms=episode.start_ms,
        end_ms=episode.last_seen_ms,
        peak_severity=episode.peak_severity,
        intervention_ids=list(episode.intervention_ids),
        suppressed_reasons=list(episode.suppressed_reasons),
        closed_by=closed_by,
        peak_evidence=dict(episode.peak_evidence),
        reliable_ms=episode.reliable_ms,
        unreliable_ms=episode.unreliable_ms,
        mean_severity=round(episode.severity_ms / seen, 4)
        if (seen := episode.reliable_ms + episode.unreliable_ms)
        else episode.peak_severity,
    )
