"""시선 — FE 시선 모듈이 준 최근 창의 라벨별 비율로 '대본만 보고 있음'을 찾는다.

라벨 이름은 config.gaze.script_labels 로 묶습니다. 시선 모듈이 방향을 세분화해
라벨이 늘어도 요청 모양은 그대로이고, 여기 설정에 이름만 더하면 됩니다.
"""

from __future__ import annotations

from ..vocab import Issue
from .base import Detection, Tick, ramp


def evaluate(tick: Tick) -> None:
    gaze = tick.req.current.gaze
    if gaze is None:
        return
    cfg = tick.cfg.gaze

    # ratios 는 UNCERTAIN 까지 합쳐 1 이다. script_ratio 는 '보이던 시간 중 대본을 본 비율'로 잰다 —
    # 전체 대비로 재면 얼굴이 자주 안 잡힐수록 대본 응시가 낮게 나와 문제를 놓친다
    script_share = sum(gaze.ratios.get(label, 0.0) for label in cfg.script_labels)
    uncertain = gaze.ratios.get(cfg.uncertain_label, 0.0)
    valid = 1.0 - uncertain
    if valid <= 0.0:
        return
    script_ratio = min(1.0, script_share / valid)
    continuous = (gaze.current_label_ms or 0) if gaze.current_label in cfg.script_labels else 0
    recent = [
        s.gaze_uncertain
        for s in tick.history_since(tick.t - cfg.sensor_smoothing_ms)
        if s.gaze_uncertain is not None
    ]
    # 나빠질 때는 바로, 좋아질 때는 천천히 — 지금 값과 최근 평균 중 나쁜 쪽을 쓴다.
    # 평균만 쓰면 센서가 나빠지기 시작할 때 이전의 좋은 값에 끌려 몇 초를 더 믿는다
    smoothed = max(uncertain, (sum(recent) + uncertain) / (len(recent) + 1))
    sensor_ok = smoothed <= cfg.max_uncertain_ratio

    # 센서를 믿을 수 없으면 지표를 비운다 — 개입 효과를 '측정 불가'로 남기기 위해서다.
    # 틀린 숫자로 '효과 없음'을 판정하면 멀쩡한 전략을 포기하게 된다
    tick.metrics["script_ratio"] = round(script_ratio, 4) if sensor_ok else None
    tick.metrics["gaze_uncertain_ratio"] = round(uncertain, 4)
    tick.metrics["gaze_uncertain_smoothed"] = round(smoothed, 4)
    tick.metrics["continuous_script_gaze_ms"] = continuous

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
            issue=Issue.GAZE_SCRIPT,
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
