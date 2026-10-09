"""시선 — 최근 창의 라벨별 비율로 '대본만 보고 있음'을 찾는다.

FE 는 최근 창의 요약(ratios · current_label · current_label_ms)이나 1초 기록(records)을 보낸다.
1초 기록이 오면 window_summary 가 같은 요약으로 바꾼다. 기록이 없는 시간과 UNMEASURED 는
UNCERTAIN 과 같이 '측정하지 못함'으로 센다.

라벨 이름은 config.gaze.script_labels 로 묶습니다. 시선 모듈이 방향을 세분화해
라벨이 늘어도 요청 모양은 그대로이고, 여기 설정에 이름만 더하면 됩니다.
SCREEN · OTHER 처럼 대본도 측정 불가도 아닌 라벨은 '보인 시간 중 대본이 아닌 곳'으로 셉니다.
"""

from __future__ import annotations

from ..config import GazeConfig
from ..schemas import GazeInput
from ..tick import Detection, Tick, ramp
from ..vocab import Issue


def window_summary(
    gaze: GazeInput, t_ms: int, cfg: GazeConfig
) -> tuple[dict[str, float], int, str | None, int]:
    """(라벨 → 비율, 창 길이 ms, 지금 라벨, 지금 라벨이 이어진 ms).

    비율은 측정하지 못한 시간까지 합쳐 1 이다. 창은 [max(0, t_ms − window_ms), t_ms) 라
    Take 시작 직후에는 window_ms 보다 짧다.
    records 가 없으면 FE 가 계산한 요약을 그대로 돌려준다. records 가 있으면 기록마다 창과
    겹친 시간을 라벨별로 더하고, 기록이 덮지 못한 시간과 UNMEASURED 는 uncertain_label 로 센다.
    """
    start, end = max(0, t_ms - gaze.window_ms), t_ms
    span = end - start
    if gaze.records is None:
        return gaze.ratios, span, gaze.current_label, gaze.current_label_ms or 0
    if span <= 0:
        return {}, 0, None, 0

    # 창 안으로 자르고 겹침을 뺀 구간 (시작, 끝, 라벨). 창 밖(미래 포함) 기록과 앞 기록에 덮인
    # 시간은 버린다 — 같은 시간을 두 번 세거나 아직 오지 않은 시간을 지금 라벨로 쓰지 않게
    segments: list[tuple[int, int, str]] = []
    covered_until = start
    for r in sorted(gaze.records, key=lambda r: r.t_ms):
        lo, hi = max(r.t_ms, covered_until), min(r.t_ms + r.duration_ms, end)
        if hi <= lo:
            continue
        label = cfg.uncertain_label if r.state == cfg.unmeasured_label else r.state
        segments.append((lo, hi, label))
        covered_until = hi

    by_label: dict[str, float] = {}
    for lo, hi, label in segments:
        by_label[label] = by_label.get(label, 0.0) + (hi - lo)
    missing = span - sum(by_label.values())
    if missing > 0:
        by_label[cfg.uncertain_label] = by_label.get(cfg.uncertain_label, 0.0) + missing
    ratios = {label: ms / span for label, ms in by_label.items()}

    last_end = segments[-1][1] if segments else start
    if not segments or end - last_end > cfg.record_stale_ms:
        # 최근 기록이 끊겼다 — 지금 라벨은 측정하지 못한 것이다
        return ratios, span, cfg.uncertain_label, end - last_end
    run_start, current = segments[-1][0], segments[-1][2]
    for lo, hi, label in reversed(segments[:-1]):
        if label != current or hi != run_start:
            break
        run_start = lo
    return ratios, span, current, last_end - run_start


def evaluate(tick: Tick) -> None:
    gaze = tick.req.current.gaze
    if gaze is None:
        return
    cfg = tick.cfg.gaze
    ratios, span_ms, current_label, current_label_ms = window_summary(gaze, tick.t, cfg)
    if gaze.records is not None and span_ms <= 0:
        # Take 시작 순간(t=0) — 기록으로 잴 시간이 아직 없다
        tick.metrics["gaze_window_short"] = True
        return

    # ratios 는 측정하지 못한 시간까지 합쳐 1 이다. script_ratio 는 '보이던 시간 중 대본을 본
    # 비율'로 잰다 — 전체 대비로 재면 얼굴이 자주 안 잡힐수록 대본 응시가 낮게 나와 문제를 놓친다
    script_share = sum(ratios.get(label, 0.0) for label in cfg.script_labels)
    uncertain = min(
        1.0, ratios.get(cfg.uncertain_label, 0.0) + ratios.get(cfg.unmeasured_label, 0.0)
    )
    valid = 1.0 - uncertain
    recent = [
        s.gaze_uncertain
        for s in tick.history_since(tick.t - cfg.sensor_smoothing_ms)
        if s.gaze_uncertain is not None
    ]
    # 나빠질 때는 바로, 좋아질 때는 천천히 — 지금 값과 최근 평균 중 나쁜 쪽을 쓴다.
    # 평균만 쓰면 센서가 나빠지기 시작할 때 이전의 좋은 값에 끌려 몇 초를 더 믿는다
    smoothed = max(uncertain, (sum(recent) + uncertain) / (len(recent) + 1))
    sensor_ok = smoothed <= cfg.max_uncertain_ratio
    tick.metrics["gaze_uncertain_ratio"] = round(uncertain, 4)
    tick.metrics["gaze_uncertain_smoothed"] = round(smoothed, 4)
    if valid <= 0.0:
        # 창 전체를 측정하지 못했다 — 지적할 근거는 없지만, 센서 판단과 상태 표시에는 남긴다
        tick.metrics["script_ratio"] = None
        tick.metrics["continuous_script_gaze_ms"] = 0
        return
    script_ratio = min(1.0, script_share / valid)
    continuous = current_label_ms if current_label in cfg.script_labels else 0

    # 센서를 믿을 수 없으면 지표를 비운다 — 개입 효과를 '측정 불가'로 남기기 위해서다.
    # 틀린 숫자로 '효과 없음'을 판정하면 멀쩡한 전략을 포기하게 된다
    tick.metrics["script_ratio"] = round(script_ratio, 4) if sensor_ok else None
    tick.metrics["continuous_script_gaze_ms"] = continuous
    if tick.t < cfg.min_window_ms:
        # Take 시작 직후 — 몇 초의 표본으로 낸 비율은 한두 번의 판정에 크게 흔들려 지적하지 않는다.
        # 장별 누적에는 그대로 쓴다. 창 길이가 아니라 Take 경과 시간으로 본다 — FE 가 짧은 창을
        # 보내도 지적이 영영 막히지 않게
        tick.metrics["gaze_window_short"] = True
        return

    by_ratio = script_ratio >= cfg.script_ratio
    by_streak = continuous >= cfg.continuous_ms and script_ratio >= cfg.streak_min_ratio
    if not (by_ratio or by_streak):
        return

    severity = max(
        ramp(script_ratio, cfg.script_ratio, cfg.script_ratio_bad) if by_ratio else 0.0,
        ramp(continuous, cfg.continuous_ms, cfg.continuous_bad_ms) if by_streak else 0.0,
    )
    tick.detections.append(
        Detection(
            issue_type=Issue.GAZE_ON_SCRIPT,
            severity=severity,
            confidence=max(0.0, 1.0 - smoothed),
            sensor_ok=sensor_ok,
            slide_number=tick.slide_number,
            metric="script_ratio",
            evidence={
                "script_ratio": round(script_ratio, 4),
                "continuous_script_gaze_ms": continuous,
                "gaze_uncertain_ratio": round(smoothed, 4),
                "window_ms": gaze.window_ms,
            },
        )
    )
