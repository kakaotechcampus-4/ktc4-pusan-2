"""Parity fixtures: the Python engine's answers for the TypeScript tests.

Every case here runs the real Python code (``vision.*``) and records inputs and
outputs as JSON; ``web/test/parity.test.ts`` feeds the same inputs to the
TypeScript port and compares.  Nothing is hand-computed.

Run from ``v1/local`` after changing either implementation::

    ./.venv/Scripts/python.exe web/scripts/make_fixtures.py
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "ai" / "src"))

from scipy.special import log_ndtr  # noqa: E402

from vision.calibration.gauge import CalibrationGauge  # noqa: E402
from vision.calibration.references import ReferenceAnchorClassifier, soft_box_log_density  # noqa: E402
from vision.config import load_config  # noqa: E402
from vision.evidence.gaze import (  # noqa: E402
    GazeFrame,
    GazeSlicer,
    GazeTimeline,
    compare_summaries,
    evaluate_gaze,
    intervention_outcome,
    take_summary,
)
from vision.preprocess.crops import (  # noqa: E402
    eye_aspect_ratio,
    face_bbox_from_landmarks,
    iris_diameter_px,
)
from vision.preprocess.headpose import focal_length_px, head_pose_from_matrix  # noqa: E402
from vision.preprocess.landmarker import in_bounds_fraction  # noqa: E402
from vision.preprocess.pipeline import second_face_ratio, select_main_face  # noqa: E402
from vision.runtime.condition import ConditionMonitor, SceneBaselineAccumulator  # noqa: E402
from vision.runtime.placement import placement_from_anchors  # noqa: E402
from vision.runtime.preconditions import IRIS_DIAMETER_CM, PreconditionChecker, head_distance_cm  # noqa: E402
from vision.runtime.sweep import HeadSweep  # noqa: E402
from vision.calibration.references import robust_sigma  # noqa: E402
from vision.schemas import (  # noqa: E402
    CalibrationSample,
    FaceScene,
    FrameObservation,
    FrameQuality,
    GazeVector,
    HeadPose,
)

OUT = HERE.parent / "test" / "fixtures" / "parity.json"
CFG = load_config()
RNG = np.random.default_rng(20261003)


def clean(value: Any) -> Any:
    """JSON-safe: tuples to lists, numpy to float, inf/nan to strings."""
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, (np.floating, float)):
        f = float(value)
        if math.isnan(f):
            return "NaN"
        if math.isinf(f):
            return "Infinity" if f > 0 else "-Infinity"
        return f
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, np.bool_):
        return bool(value)
    return value


# --------------------------------------------------------------------------
# math / densities / head pose / geometry
# --------------------------------------------------------------------------


def math_cases() -> Dict[str, Any]:
    xs = [-80.0, -45.0, -25.0, -20.5, -20.0, -19.99, -12.0, -6.5, -3.0, -1.0, -0.5, 0.0, 0.3, 1.0, 1.9,
          2.0, 2.1, 4.0, 5.9, 6.0, 6.1, 8.0, 12.0, 30.0]
    boxes = []
    for x, lo, hi, s in [
        (0.0, -1.0, 1.0, 1.5), (5.0, -1.0, 1.0, 1.5), (-60.0, -1.0, 1.0, 1.5), (300.0, -3.0, 2.0, 1.2),
        (1.0, 1.0, 1.0, 1.5), (0.7, 2.0, -2.0, 2.0), (0.0, 0.0, 1e-5, 1.5), (-10.0, 0.0, 30.0, 1.5),
        (25.0, -12.0, 12.0, 1.875), (-4.0, -20.0, -14.0, 1.5),
    ]:
        boxes.append({"x": x, "lo": lo, "hi": hi, "s": s, "out": soft_box_log_density(x, lo, hi, s)})
    return {"log_ndtr": [{"x": x, "out": float(log_ndtr(x))} for x in xs], "soft_box": boxes}


def rotation(yaw: float, pitch: float, roll: float) -> np.ndarray:
    a, b, c = math.radians(pitch), math.radians(yaw), math.radians(roll)
    rx = np.array([[1, 0, 0], [0, math.cos(a), -math.sin(a)], [0, math.sin(a), math.cos(a)]])
    ry = np.array([[math.cos(b), 0, math.sin(b)], [0, 1, 0], [-math.sin(b), 0, math.cos(b)]])
    rz = np.array([[math.cos(c), -math.sin(c), 0], [math.sin(c), math.cos(c), 0], [0, 0, 1]])
    return rz @ ry @ rx


def headpose_cases() -> List[Dict[str, Any]]:
    cases = []
    for yaw, pitch, roll in [(0, 0, 0), (12, -7, 3), (-30, 20, -10), (45, -35, 15), (5, 60, 0), (-80, 10, 5)]:
        m = np.eye(4)
        m[:3, :3] = rotation(yaw, pitch, roll)
        m[:3, 3] = [-1.5, 21.0, -62.0]
        pose = head_pose_from_matrix(m, (640, 480))
        cases.append({
            "col_major": m.T.flatten().tolist(),
            "row_major": m.flatten().tolist(),
            "yaw": pose.yaw, "pitch": pose.pitch, "roll": pose.roll,
            "depth_cm": head_distance_cm(pose, (640, 480)),
        })
    return cases


def synthetic_landmarks(cx: float, cy: float, scale: float) -> np.ndarray:
    pts = RNG.normal(0, 0.02, size=(478, 3))
    pts[:, 0] = cx + pts[:, 0] * scale
    pts[:, 1] = cy + pts[:, 1] * scale
    # make the eye and iris points plausible so EAR / iris are meaningful
    for outer, inner, up1, up2, lo1, lo2, sign in ((263, 362, 387, 385, 380, 373, 1), (33, 133, 160, 158, 153, 144, -1)):
        ex = cx + sign * 0.06 * scale
        pts[outer, :2] = (ex + sign * 0.025 * scale, cy - 0.02 * scale)
        pts[inner, :2] = (ex - sign * 0.025 * scale, cy - 0.02 * scale)
        pts[up1, :2] = (ex + sign * 0.01 * scale, cy - 0.028 * scale)
        pts[up2, :2] = (ex - sign * 0.01 * scale, cy - 0.029 * scale)
        pts[lo2, :2] = (ex + sign * 0.01 * scale, cy - 0.012 * scale)
        pts[lo1, :2] = (ex - sign * 0.01 * scale, cy - 0.011 * scale)
    for ring, sign in (((474, 475, 476, 477), 1), ((469, 470, 471, 472), -1)):
        ex = cx + sign * 0.06 * scale
        for k, (dx, dy) in zip(ring, ((0.008, 0), (0, -0.008), (-0.008, 0), (0, 0.008))):
            pts[k, :2] = (ex + dx * scale, cy - 0.02 * scale + dy * scale)
    return pts


def geometry_cases() -> Dict[str, Any]:
    size = (640, 480)
    faces = []
    for cx, cy, scale in [(0.5, 0.45, 1.0), (0.3, 0.6, 0.6), (0.96, 0.5, 1.2)]:
        lm = synthetic_landmarks(cx, cy, scale)
        faces.append({
            "landmarks": lm[:, :3].tolist(),
            "bbox": list(face_bbox_from_landmarks(lm, size)),
            "ear_left": eye_aspect_ratio(lm, "left", size),
            "ear_right": eye_aspect_ratio(lm, "right", size),
            "iris": iris_diameter_px(lm, size),
            "visibility": in_bounds_fraction(lm),
        })
    bboxes = [tuple(f["bbox"]) for f in faces]
    select = []
    for hint in [None, (0.3, 0.6), (0.9, 0.5), (0.5, 0.45)]:
        select.append({"hint": hint, "out": select_main_face(bboxes, size, hint)})
    main = (260, 160, 120, 160)
    second = []
    for others in ([], [(270, 170, 100, 140)], [(200, 100, 300, 360)], [(20, 20, 40, 50)],
                   [(480, 140, 120, 160)], [(20, 20, 40, 50), (480, 160, 90, 120)], [(380, 160, 120, 160)],
                   [(0, 0, 0, 0)]):
        second.append({"main": main, "others": others, "out": second_face_ratio(main, others, size, 0.010)})
    return {"size": size, "faces": faces, "select": select, "second": second}


# --------------------------------------------------------------------------
# reference classifier
# --------------------------------------------------------------------------


def cloud(cue: str, centre, n=16, sd=0.6, head=(0.0, 0.0)) -> List[Dict[str, float]]:
    out = []
    for _ in range(n):
        out.append({"cue": cue, "yaw": centre[0] + RNG.normal(0, sd), "pitch": centre[1] + RNG.normal(0, sd),
                    "head_yaw": head[0] + RNG.normal(0, 0.3), "head_pitch": head[1] + RNG.normal(0, 0.3)})
    return out


def to_samples(rows) -> List[CalibrationSample]:
    return [
        CalibrationSample(
            label=r["cue"],
            gaze=GazeVector(math.radians(r["yaw"]), math.radians(r["pitch"]), 0.9),
            head_pose=HeadPose(yaw=math.radians(r["head_yaw"]), pitch=math.radians(r["head_pitch"])),
        )
        for r in rows
    ]


def reference_cases() -> List[Dict[str, Any]]:
    C, S, B = (0.5, 18.0), (0.8, 11.0), (0.6, 4.0)
    scenarios = {
        "three_cues": (cloud("CAMERA", C) + cloud("SCREEN", S) + cloud("BOTTOM", B), {}),
        "two_cues": (cloud("CAMERA", C) + cloud("BOTTOM", B), {}),
        "screen_on_lens_merged": (cloud("CAMERA", C) + cloud("SCREEN", (0.6, 17.6)) + cloud("BOTTOM", B), {}),
        "screen_on_lens_no_merge": (cloud("CAMERA", C) + cloud("SCREEN", (0.6, 17.6)) + cloud("BOTTOM", B),
                                    {"merge_inseparable_screen": False}),
        "lens_equals_script": (cloud("CAMERA", C) + cloud("BOTTOM", (0.5, 17.4)), {}),
        "partial_screen": (cloud("CAMERA", C) + cloud("SCREEN", S, n=5) + cloud("BOTTOM", B), {}),
        "inverted": (cloud("CAMERA", B) + cloud("SCREEN", S) + cloud("BOTTOM", C), {}),
        "diluted_script": (cloud("CAMERA", C) + cloud("SCREEN", S) + cloud("BOTTOM", B),
                           {"script_width_fraction": 1.0, "script_height_fraction": 1.0, "prior_bottom": 0.05,
                            "merge_inseparable_screen": False}),
        "noisy_overlap": (cloud("CAMERA", C, sd=6.0) + cloud("BOTTOM", (0.5, 9.0), sd=6.0),
                          {"min_anchor_separation": 0.0}),
        "degenerate": ([{"cue": c, "yaw": 1.0, "pitch": 2.0, "head_yaw": 0.0, "head_pitch": 0.0}
                        for c in ("CAMERA",) * 12 + ("BOTTOM",) * 12], {}),
        "head_moved_calibration": (cloud("CAMERA", C, head=C) + cloud("SCREEN", S, head=S) + cloud("BOTTOM", B, head=B), {}),
    }
    queries = [(0.5, 18.0, 0.0, 0.0), (0.8, 11.0, 0.0, 0.0), (0.6, 4.0, 0.0, 0.0), (14.0, 11.0, 0.0, 0.0),
               (0.5, 14.5, 0.0, 0.0), (30.0, -20.0, 0.0, 0.0), (0.5, 18.0, 40.0, 0.0), (0.5, 7.5, 0.0, 0.0),
               (-25.0, 11.0, 0.0, 0.0), (0.6, 40.0, 0.0, 0.0), (0.6, -25.0, 0.0, 0.0), (-20.0, 35.0, 0.0, 0.0)]
    cases = []
    for name, (rows, overrides) in scenarios.items():
        cfg = replace(CFG.calibration, **overrides)
        clf, quality = ReferenceAnchorClassifier.fit(to_samples(rows), cfg)
        model = clf.model
        case = {"name": name, "overrides": overrides, "samples": rows, "quality": quality.to_dict(), "model": None,
                "decisions": []}
        if model is not None:
            case["model"] = {
                "anchors": model.anchors, "counts": model.counts, "sigma": model.sigma,
                "boxes": {k: [b.yaw_lo, b.yaw_hi, b.pitch_lo, b.pitch_hi] for k, b in model.boxes.items()},
                "log_priors": model.log_priors, "log_other_density": model.log_other_density,
                "head_baseline": model.head_baseline, "classes": list(model.classes), "spreads": model.spreads,
            }
            for yaw, pitch, hy, hp in queries:
                obs = FrameObservation(frame_id=1, t_ms=125, face_confidence=1.0, face_valid=True,
                                       head_pose=HeadPose(yaw=math.radians(hy), pitch=math.radians(hp)))
                d = clf.decide(obs, GazeVector(math.radians(yaw), math.radians(pitch), 0.9))
                case["decisions"].append({"query": [yaw, pitch, hy, hp], "label": d.label,
                                          "probs": d.probs, "reason": d.uncertain_reason,
                                          "direction": d.direction, "offset_deg": d.offset_deg,
                                          "aim_deg": d.aim_deg})
        cases.append(case)
    return cases


def placement_cases() -> List[Dict[str, Any]]:
    out = []
    for name, cam, scr, n_c, n_s in [
        ("top", (0.5, 18.0), (0.7, 10.0), 16, 16),
        ("bottom", (0.5, 4.0), (0.7, 12.0), 16, 16),
        ("side_right", (0.5, 10.0), (9.0, 10.5), 16, 16),
        ("side_left", (0.5, 10.0), (-9.0, 10.5), 16, 16),
        ("corner", (0.5, 18.0), (5.0, 11.0), 16, 16),
        ("small", (0.5, 18.0), (0.6, 15.0), 16, 16),
        ("same", (0.5, 18.0), (0.55, 17.9), 16, 16),
        ("few", (0.5, 18.0), (0.7, 10.0), 16, 5),
    ]:
        rc = [(cam[0] + RNG.normal(0, 0.5), cam[1] + RNG.normal(0, 0.5)) for _ in range(n_c)]
        rs = [(scr[0] + RNG.normal(0, 0.5), scr[1] + RNG.normal(0, 0.5)) for _ in range(n_s)]
        c = np.median(np.asarray(rc), axis=0)
        s = np.median(np.asarray(rs), axis=0)
        res = np.vstack([np.asarray(rc) - c, np.asarray(rs) - s])
        sigma = (robust_sigma(res[:, 0], CFG.calibration), robust_sigma(res[:, 1], CFG.calibration))
        result = placement_from_anchors(tuple(c), tuple(s), n_c, n_s, sigma, CFG.placement)
        out.append({"name": name, "camera": rc, "screen": rs, "out": result.to_dict()})
    return out


# --------------------------------------------------------------------------
# sequences (preconditions, condition, gauge)
# --------------------------------------------------------------------------


def make_obs(d: Dict[str, Any]) -> FrameObservation:
    has_face = d.get("reason") != "NO_FACE"
    q = FrameQuality(
        left_eye_openness=d.get("ear", 0.3), right_eye_openness=d.get("ear", 0.3),
        face_brightness=d.get("brightness", 120.0), background_brightness=d.get("background", 100.0),
    )
    return FrameObservation(
        frame_id=d["t"] // 125, t_ms=d["t"], face_confidence=1.0 if has_face else 0.0,
        face_valid=d.get("valid", True) and d.get("reason") is None, invalid_reason=d.get("reason"),
        head_pose=HeadPose(yaw=math.radians(d.get("yaw", 0.0)), pitch=math.radians(d.get("pitch", 15.0)),
                           reprojection_error=math.inf if d.get("unmeasured") else 0.0,
                           depth_proxy=d.get("depth", 0.0) / focal_length_px((640, 480))),
        quality=q, face_bbox=tuple(d.get("bbox", (250, 150, 140, 180))) if has_face else None,
        image_size=(640, 480),
        scene=FaceScene(n_faces=1 if has_face else 0, second_face_area_ratio=d.get("second", 0.0),
                        iris_diameter_px=d.get("iris", 11.0) if has_face else 0.0),
    )


def precondition_sequence() -> Dict[str, Any]:
    seq = []
    t = 0
    for i in range(10):  # settle then pass
        seq.append({"t": t}); t += 125
    for i in range(6):  # a blink in the middle of a hold
        seq.append({"t": t, "reason": "EYES_CLOSED", "valid": False} if i == 2 else {"t": t}); t += 125
    for i in range(30):  # off centre for long -> reject
        seq.append({"t": t, "bbox": (20, 150, 140, 180)}); t += 125
    for i in range(6):
        seq.append({"t": t, "reason": "NO_FACE", "valid": False}); t += 125
    for i in range(2):  # a false find for two frames: not a second person
        seq.append({"t": t, "second": 0.6}); t += 125
    for i in range(8):  # a second person held long enough
        seq.append({"t": t, "second": 0.6}); t += 125
    for i in range(4):
        seq.append({"t": t, "pitch": 40.0}); t += 125
    for i in range(3):  # the distance shown comes from the face mesh when there is one
        seq.append({"t": t, "depth": 58.0, "iris": 6.0}); t += 125
    for i in range(4):
        seq.append({"t": t, "brightness": 40.0}); t += 125
    for i in range(4):
        seq.append({"t": t, "brightness": 80.0, "background": 200.0}); t += 125
    for i in range(4):
        seq.append({"t": t, "iris": 5.0}); t += 125
    for i in range(4):
        seq.append({"t": t, "iris": 30.0}); t += 125
    for i in range(4):
        seq.append({"t": t, "reason": "OUT_OF_FRAME", "valid": False}); t += 125
    checker = PreconditionChecker(CFG.preconditions)
    out = []
    for d in seq:
        r = checker.update(make_obs(d))
        out.append({"status": r.status, "reason": r.reason, "held_ms": r.held_ms, "failing_ms": r.failing_ms,
                    "blocking": r.blocking, "measurements": r.measurements,
                    "checks": [[c.name, c.ok] for c in r.checks]})
    return {"frames": seq, "reports": out}


def condition_sequence() -> Dict[str, Any]:
    calib = []
    for cue, pitch in (("CAMERA", 18.0), ("SCREEN", 11.0), ("BOTTOM", 4.0)):
        for i in range(8):
            calib.append({"cue": cue, "frame": {"t": i * 125, "pitch": pitch + RNG.normal(0, 0.3)}})
    acc = SceneBaselineAccumulator()
    for c in calib:
        acc.add(make_obs(c["frame"]), cue=c["cue"])
    base = acc.build()
    live = []
    t = 10_000
    for d in ([{"pitch": 18.0}] * 4 + [{"pitch": 4.0}] * 3 + [{"yaw": 25.0, "pitch": 10.0}] * 2 + [{"iris": 7.5}] * 3
              + [{"iris": 15.0}] * 2 + [{"bbox": (330, 150, 140, 180)}] * 2 + [{"second": 0.6}] * 2
              + [{"pitch": 18.0}] + [{"second": 0.6}] * 10
              + [{"pitch": 18.0, "bbox": (250 + 70 - 70 * k, 150 + 90 - 90 * k, 140 * k, 180 * k), "iris": 11.0 * k}
                 for k in [1.0 - 0.45 * i / 15 for i in range(16)]]
              + [{"brightness": 60.0}] * 2 + [{"reason": "EYES_CLOSED", "valid": False}] * 6
              + [{"reason": "NO_FACE", "valid": False}] * 16 + [{"pitch": 18.0}] * 3
              + [{"bbox": (20, 20, 300, 400)}] * 8 + [{"pitch": 18.0}] * 30):
        f = dict(d)
        f["t"] = t
        live.append(f)
        t += 125
    def run(head_is_gaze: bool) -> List[Dict[str, Any]]:
        monitor = ConditionMonitor(CFG.condition, base, head_is_gaze=head_is_gaze)
        states = []
        for f in live:
            s = monitor.update(make_obs(f))
            states.append({"reliability": s.reliability, "issues": s.issues, "severe": s.severe,
                           "components": s.components, "drift": s.drift, "emit": monitor.should_emit(s),
                           "notices": s.notices, "jitter_deg": s.jitter_deg})
        return states

    return {"calibration": calib, "baseline": {
        "centre": base.centre, "face_area": base.face_area, "iris_px": base.iris_px, "brightness": base.brightness,
        "head": base.head, "head_poses": base.head_poses, "depth_cm": base.depth_cm}, "live": live,
        "states": run(False), "states_head_is_gaze": run(True)}


def condition_jitter_sequence() -> Dict[str, Any]:
    """Head-direction noise: steady, a smooth turn (not noise), shaky, a gap, back to steady."""
    calib = []
    for cue, pitch in (("CAMERA", 18.0), ("SCREEN", 11.0), ("BOTTOM", 4.0)):
        for i in range(8):
            calib.append({"cue": cue, "frame": {"t": i * 125, "pitch": pitch + RNG.normal(0, 0.3)}})
    acc = SceneBaselineAccumulator()
    for c in calib:
        acc.add(make_obs(c["frame"]), cue=c["cue"])
    base = acc.build()
    live = []
    t = 10_000
    frames = ([{"pitch": 11.0 + RNG.normal(0, 0.2)} for _ in range(12)]
              + [{"pitch": 11.0, "yaw": 1.5 * i} for i in range(10)]
              + [{"pitch": 11.0 + (2.5 if i % 2 else -2.5)} for i in range(20)]
              + [{"reason": "NO_FACE", "valid": False}] * 4
              + [{"pitch": 11.0 + RNG.normal(0, 0.2)} for _ in range(24)])
    for d in frames:
        f = dict(d)
        f["t"] = t
        live.append(f)
        t += 125
    monitor = ConditionMonitor(CFG.condition, base, head_is_gaze=True)
    states = []
    for f in live:
        s = monitor.update(make_obs(f))
        states.append({"reliability": s.reliability, "issues": s.issues, "components": s.components,
                       "jitter_deg": s.jitter_deg})
    return {"calibration": calib, "live": live, "states": states}


def condition_depth_sequence() -> Dict[str, Any]:
    """Drift from the face-mesh distance: hidden eyes, moves, and the iris fallback."""
    depth = focal_length_px((640, 480)) * IRIS_DIAMETER_CM / 11.0
    calib = []
    for cue, pitch in (("CAMERA", 18.0), ("SCREEN", 11.0), ("BOTTOM", 4.0)):
        for i in range(8):
            calib.append({"cue": cue, "frame": {"t": i * 125, "pitch": pitch + RNG.normal(0, 0.3),
                                                "depth": depth + RNG.normal(0, 0.2)}})
    acc = SceneBaselineAccumulator()
    for c in calib:
        acc.add(make_obs(c["frame"]), cue=c["cue"])
    base = acc.build()
    live = []
    t = 10_000
    for d in ([{"pitch": 11.0, "depth": depth}] * 3 + [{"pitch": 4.0, "depth": depth, "iris": 6.0}] * 3
              + [{"pitch": 11.0, "depth": depth * 1.5}] * 3 + [{"pitch": 11.0, "depth": depth * 0.6}] * 3
              + [{"pitch": 11.0, "depth": depth, "bbox": (300, 150, 140, 180)}] * 3
              + [{"pitch": 11.0, "iris": 9.0}] * 3 + [{"pitch": 11.0, "depth": depth}] * 3):
        f = dict(d)
        f["t"] = t
        live.append(f)
        t += 125
    monitor = ConditionMonitor(CFG.condition, base, head_is_gaze=True)
    states = []
    for f in live:
        s = monitor.update(make_obs(f))
        states.append({"reliability": s.reliability, "issues": s.issues, "severe": s.severe, "drift": s.drift})
    return {"calibration": calib, "depth_cm": base.depth_cm, "live": live, "states": states}


def gauge_sequences() -> List[Dict[str, Any]]:
    cfg = replace(CFG.calibration, target_good_frames=16, min_samples_per_class=5, cue_timeout_ms=4000)
    out = []
    for eye_based in (False, True):
        frames = []
        t = 0
        for i in range(36):
            d: Dict[str, Any] = {"t": t, "pitch": 18.0 + RNG.normal(0, 0.2), "conf": 0.9}
            if i == 10:
                d["ear"] = 0.13  # blink (eye backbones only)
            if i == 11:
                d["pitch"] = 30.0  # glance away: OUTLIER, or HEAD_MOVED for eye backbones
            if i == 12:
                d["conf"] = 0.1
            if i == 13:
                d["reason"] = "EYES_CLOSED"; d["valid"] = False
            if i == 14:
                d["pitch"] = 25.0
            frames.append(d)
            t += 125
        gauge = CalibrationGauge(cfg, blink_ratio=0.7, eye_based=eye_based)
        gauge.start("CAMERA", 0)
        results = []
        for d in frames:
            obs = make_obs(d)
            gaze = GazeVector(obs.head_pose.yaw, obs.head_pose.pitch, d["conf"])
            ok, reason = gauge.offer(obs, gaze, d["t"])
            results.append([ok, reason])
        st = gauge.status
        out.append({"eye_based": eye_based, "frames": frames, "results": results,
                    "status": {"state": st.state, "good": st.good, "dominant": st.dominant_reason,
                               "rejected": st.rejected}})
    return out


def gauge_extend_sequence() -> Dict[str, Any]:
    """A short target (the screen-centre confirmation), then extended to a full look."""
    cfg = replace(CFG.calibration, target_good_frames=12, min_samples_per_class=5, cue_timeout_ms=4000)
    gauge = CalibrationGauge(cfg, blink_ratio=0.7, eye_based=False)
    gauge.start("SCREEN", 0, target=6)
    steps = []
    t = 0
    extended = False
    for _ in range(24):
        t += 125
        obs = make_obs({"t": t, "pitch": 11.0 + RNG.normal(0, 0.2)})
        ok, reason = gauge.offer(obs, GazeVector(obs.head_pose.yaw, obs.head_pose.pitch, 0.9), t)
        st = gauge.status
        if st.state == "DONE" and not extended:
            gauge.extend(12)
            extended = True
        st = gauge.status
        steps.append({"t": t, "pitch": obs.head_pose.pitch, "ok": ok, "reason": reason,
                      "state": st.state, "good": st.good, "target": st.target})
    return {"steps": steps}


# --------------------------------------------------------------------------
# agent evidence (slices, windows, coach issues, review summary, outcome)
# --------------------------------------------------------------------------


def evidence_frames(plan, t0: int = 0) -> List[Dict[str, Any]]:
    """Frames at ~8 fps from ``(state, seconds, extra)`` blocks, with jitter, flips and drops."""
    frames: List[Dict[str, Any]] = []
    t = t0
    others = ("CAMERA", "SCREEN", "BOTTOM", "OTHER")
    for state, seconds, extra in plan:
        end = t + int(seconds * 1000)
        if state == "GAP":
            t = end
            continue
        while t < end:
            if RNG.random() >= extra.get("drop", 0.05):
                st = state
                if state != "UNMEASURED" and RNG.random() < extra.get("flip", 0.1):
                    st = str(others[int(RNG.integers(0, 4))])
                d: Dict[str, Any] = {"t_ms": t, "state": st,
                                     "reliability": float(extra.get("rel", 1.0) - RNG.random() * 0.1)}
                if st == "OTHER":
                    dirs = extra.get("dirs", ("LEFT",))
                    d["direction"] = str(dirs[int(RNG.integers(0, len(dirs)))])
                if extra.get("issues") and RNG.random() < 0.8:
                    d["issues"] = list(extra["issues"])
                frames.append(d)
            t += 125 + int(RNG.integers(-12, 13))
    return frames


def to_frame(d: Dict[str, Any]) -> GazeFrame:
    return GazeFrame(t_ms=d["t_ms"], state=d["state"], direction=d.get("direction"),
                     reliability=d["reliability"], issues=tuple(d.get("issues", ())))


def slice_frames(frames: List[Dict[str, Any]], cfg, t_end: int) -> List[Any]:
    slicer = GazeSlicer(cfg)
    samples = []
    for d in frames:
        samples.extend(slicer.push(to_frame(d)))
    samples.extend(slicer.flush(t_end))
    return samples


def evidence_cases() -> Dict[str, Any]:
    cfg = CFG.evidence
    plan = [
        ("CAMERA", 6, {}),
        ("BOTTOM", 6, {}),
        # (an evenly split second and a thin second are inserted here)
        ("OTHER", 3, {"dirs": ("LEFT", "LEFT", "UP_LEFT")}),
        ("GAP", 2, {}),
        ("UNMEASURED", 2, {"rel": 0.3, "issues": ("TOO_FAR",)}),
        ("SCREEN", 6, {}),
        ("BOTTOM", 9, {"flip": 0.25}),
        ("OTHER", 4, {"dirs": ("DOWN_RIGHT", "RIGHT")}),
        ("BOTTOM", 12, {"flip": 0.2}),
        ("CAMERA", 10, {}),
        ("CAMERA", 4, {"rel": 0.45, "issues": ("LIGHTING_CHANGED",)}),
    ]
    frames = evidence_frames(plan[:2])
    t = frames[-1]["t_ms"] + 125
    for i in range(8):  # one evenly split second: an UNCERTAIN slice
        frames.append({"t_ms": t, "state": "CAMERA" if i % 2 else "BOTTOM", "reliability": 1.0})
        t += 125
    for i in range(3):  # a thin second: UNMEASURED by too few frames
        frames.append({"t_ms": t, "state": "CAMERA", "reliability": 0.9})
        t += 333
    frames += evidence_frames(plan[2:], t0=t)
    t_end = frames[-1]["t_ms"] + 125
    samples = slice_frames(frames, cfg, t_end)
    tl = GazeTimeline(samples)

    end = tl.end_ms
    probes = sorted({5000, 9000, 12500, 14800, 17000, 21000, 26000, 31000, 38000, 45000, 52000, 60000,
                     66000, end - 1500, end})
    stats = [{"t": p, "window": w, "out": tl.stats(p, w)} for p in probes for w in (cfg.short_window_ms,
                                                                                   cfg.long_window_ms)]
    issues = [{"t": p, "out": evaluate_gaze(tl, p, cfg)} for p in probes]
    summary1 = take_summary(tl, cfg)

    take2_plan = [("CAMERA", 12, {"flip": 0.05}), ("BOTTOM", 4, {}), ("CAMERA", 14, {}),
                  ("OTHER", 2.5, {"dirs": ("UP",)}), ("CAMERA", 8, {})]
    frames2 = evidence_frames(take2_plan)
    samples2 = slice_frames(frames2, cfg, frames2[-1]["t_ms"] + 125)
    summary2 = take_summary(GazeTimeline(samples2), cfg)

    outcomes = []
    for p, issue in [(12000, "GAZE_ON_SCRIPT"), (16500, "GAZE_AWAY"), (26500, "GAZE_ON_SCREEN"),
                     (52000, "GAZE_ON_SCRIPT"), (60000, "GAZE_LOW_EYE_CONTACT"), (end - 3000, "GAZE_ON_SCRIPT"),
                     (4000, "GAZE_LOW_EYE_CONTACT")]:
        outcomes.append({"t": p, "issue": issue, "out": intervention_outcome(tl, p, issue, cfg)})
    return {
        "frames": frames, "t_end": t_end, "samples": [s.to_dict() for s in samples],
        "runs": [r.to_dict() for r in tl.runs()], "stats": stats, "issues": issues,
        "summary": summary1,
        "take2": {"frames": frames2, "samples": [s.to_dict() for s in samples2], "summary": summary2},
        "compare": compare_summaries(summary2, summary1),
        "compare_empty": compare_summaries(take_summary(GazeTimeline(), cfg), summary1),
        "outcomes": outcomes,
    }


# --------------------------------------------------------------------------
# head circle check
# --------------------------------------------------------------------------


def sweep_sequences() -> List[Dict[str, Any]]:
    """Frames for the ring: centre, a circle with a jerk, a lost face, a stall, the rest."""
    cfg = CFG.sweep
    centre = (1.0, 14.0)

    def turned(t: int, angle: float, scale: float) -> Dict[str, Any]:
        a = math.radians(angle)
        right = scale * cfg.reach_yaw_deg * math.cos(a) + RNG.normal(0, 0.3)
        up = scale * cfg.reach_pitch_deg * math.sin(a) + RNG.normal(0, 0.3)
        return {"t": t, "yaw": centre[0] - right, "pitch": centre[1] + up}

    def at_centre(t: int) -> Dict[str, Any]:
        return {"t": t, "yaw": centre[0] + RNG.normal(0, 0.4), "pitch": centre[1] + RNG.normal(0, 0.4)}

    full: List[Dict[str, Any]] = []
    t = 0

    def step() -> int:
        nonlocal t
        t += 125 + int(RNG.integers(-12, 13))
        return t

    for _ in range(5):
        full.append(at_centre(step()))
    for k in range(9):  # up from the right, slowly
        full.append(turned(step(), k * 20.0, 1.25))
    full.append(turned(step(), 340.0, 1.3))  # a jerk across the circle: TOO_FAST
    full.append({"t": step(), "reason": "NO_FACE", "valid": False})
    for k in range(10, 13):
        full.append(turned(step(), k * 20.0, 1.25))
    for _ in range(3):  # turned too far left: the face is lost (charged to LEFT once)
        full.append({"t": step(), "reason": "NO_FACE", "valid": False})
    full.append({**turned(step(), 250.0, 1.2), "unmeasured": True})
    for _ in range(30):  # a stall at the centre: the hint appears
        full.append(at_centre(step()))
    for k in range(10, 20):  # the rest of the circle
        full.append(turned(step(), k * 20.0, 1.3))
    for k in range(0, 4):
        full.append(turned(step(), 340.0 + k * 10.0, 1.3))
    for k in range(1, 11):  # round once more: the stretch the jerk and the lost face skipped
        full.append(turned(step(), k * 20.0, 1.3))
    out = []
    for name, frames, overrides in [("full", full, {}),
                                    ("timeout", full[:40], {"timeout_ms": 4000})]:
        sweep = HeadSweep(replace(cfg, **overrides))
        sweep.start(frames[0]["t"] - 125)
        statuses = [sweep.offer(make_obs(f), f["t"]).to_dict() for f in frames]
        out.append({"name": name, "overrides": overrides, "frames": frames, "statuses": statuses})
    return out


def gate_sequences() -> List[Dict[str, Any]]:
    """Calibration cues read against a reference pose (head-pose engine): wrong way, right way, sideways."""
    cfg = replace(CFG.calibration, cue_timeout_ms=5000, min_samples_per_class=5)
    ref = (1.0, 14.0)
    out = []
    plans = {
        # (yaw, pitch) per frame after the settle time
        "CAMERA": [(1.0, 14.0)] * 4 + [(1.0, 16.5)] * 3 + [(13.5, 17.0)] * 2 + [(0.5, 17.0)] * 12,
        "BOTTOM": [(1.0, 12.5)] * 4 + [(1.0, 9.0)] * 3 + [(-10.0, 9.0)] * 2 + [(1.5, 8.5)] * 12,
        "SCREEN": [(1.0, 22.5)] * 3 + [(9.0, 14.0)] * 2 + [(1.2, 13.6)] * 14,
    }
    for reference in (ref, None):
        for cue, plan in plans.items():
            frames = [{"t": t, "yaw": 1.0, "pitch": 14.0, "conf": 0.9} for t in (0, 125, 250, 375)]
            t = 500
            for yaw, pitch in plan:
                frames.append({"t": t, "yaw": yaw + RNG.normal(0, 0.15), "pitch": pitch + RNG.normal(0, 0.15),
                               "conf": 0.9})
                t += 125
            gauge = CalibrationGauge(cfg, blink_ratio=0.7, eye_based=False)
            gauge.set_direction_reference(reference)
            gauge.start(cue, 0)
            results = []
            for d in frames:
                obs = make_obs(d)
                gaze = GazeVector(obs.head_pose.yaw, obs.head_pose.pitch, d["conf"])
                ok, reason = gauge.offer(obs, gaze, d["t"])
                results.append([ok, reason])
            st = gauge.status
            out.append({"cue": cue, "reference": reference, "frames": frames, "results": results,
                        "status": {"state": st.state, "good": st.good, "dominant": st.dominant_reason,
                                   "rejected": st.rejected}})
    return out


def build() -> Dict[str, Any]:
    """Every fixture, JSON-safe.

    Deterministic only from a fresh RNG: the generator is seeded once at import
    and every case draws from it in order, so call this once per import.
    """
    return clean({
        "_generated_by": "web/scripts/make_fixtures.py -- do not edit by hand",
        "config_hash": CFG.hash(),
        "math": math_cases(),
        "headpose": headpose_cases(),
        "geometry": geometry_cases(),
        "reference": reference_cases(),
        "placement": placement_cases(),
        "preconditions": precondition_sequence(),
        "condition": condition_sequence(),
        "condition_depth": condition_depth_sequence(),
        "condition_jitter": condition_jitter_sequence(),
        "gauge_extend": gauge_extend_sequence(),
        "gauge": gauge_sequences(),
        "evidence": evidence_cases(),
        "sweep": sweep_sequences(),
        "gate": gate_sequences(),
    })


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build(), ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
