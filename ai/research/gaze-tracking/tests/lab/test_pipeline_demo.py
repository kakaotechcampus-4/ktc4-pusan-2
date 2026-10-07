from types import SimpleNamespace

import numpy as np

from pipeline_demo import FaceOverlay, PipelineDemo, Stage, _hint_text


def _stage_demo(stage, session, *, strict=False, flow="full"):
    demo = object.__new__(PipelineDemo)
    demo.stage = stage
    demo.session = session
    demo.strict = strict
    demo.flow = flow
    demo._cue_failed = False
    demo.log = []
    demo._forced = False
    demo._enter = lambda next_stage, _t_ms: setattr(demo, "stage", next_stage)
    return demo


def _unsupported_placement():
    return SimpleNamespace(supported=False, placement="BOTTOM", reason="OK", hint="move it")


def _low_quality():
    return SimpleNamespace(ok=False, reason="LOW_LOO_ACCURACY", hint="retry")


def test_advisory_placement_estimate_never_blocks_calibration():
    demo = _stage_demo(Stage.PLACE_RESULT, SimpleNamespace(placement_result=_unsupported_placement()))

    demo._advance(100)

    assert demo.stage is Stage.CALIB_BOTTOM


def test_strict_mode_blocks_an_unsupported_camera_until_forced():
    demo = _stage_demo(
        Stage.PLACE_RESULT, SimpleNamespace(placement_result=_unsupported_placement()), strict=True
    )

    demo._advance(100)
    assert demo.stage is Stage.PLACE_RESULT

    demo._force(200)
    assert demo.stage is Stage.CALIB_BOTTOM
    assert demo._forced


def test_the_placement_flow_stops_at_its_verdict():
    demo = _stage_demo(
        Stage.PLACE_RESULT,
        SimpleNamespace(placement_result=SimpleNamespace(supported=True, placement="TOP", reason="OK", hint=None)),
        flow="placement",
    )

    demo._advance(100)

    assert demo.stage is Stage.PLACE_RESULT


def test_low_quality_calibration_is_advisory_when_model_exists():
    demo = _stage_demo(
        Stage.CALIB_RESULT,
        SimpleNamespace(calibration_quality=_low_quality(), is_calibrated=True),
    )

    demo._advance(100)

    assert demo.stage is Stage.LIVE


def test_strict_mode_holds_a_low_quality_calibration():
    demo = _stage_demo(
        Stage.CALIB_RESULT,
        SimpleNamespace(calibration_quality=_low_quality(), is_calibrated=True),
        strict=True,
    )

    demo._advance(100)
    assert demo.stage is Stage.CALIB_RESULT
    demo._force(200)
    assert demo.stage is Stage.LIVE


def test_calibration_without_a_model_still_blocks_live_mode():
    demo = _stage_demo(
        Stage.CALIB_RESULT,
        SimpleNamespace(calibration_quality=_low_quality(), is_calibrated=False),
    )

    demo._advance(100)
    demo._force(200)

    assert demo.stage is Stage.CALIB_RESULT


def test_headless_stops_on_a_strict_set_up_rejection():
    report = SimpleNamespace(status="REJECT", reason="MULTIPLE_FACES")
    strict = _stage_demo(Stage.CHECK, SimpleNamespace(precondition_report=report), strict=True)
    advisory = _stage_demo(Stage.CHECK, SimpleNamespace(precondition_report=report))

    assert strict.auto_advance(100) is False
    assert advisory.auto_advance(100) is True
    assert advisory.stage is Stage.SWEEP


def _sweep_status(**kw):
    from gaze_lab.runtime.sweep import SweepStatus

    return SweepStatus(**{"state": "SWEEPING", "ticks": (False,) * 32, **kw})


def test_the_head_circle_follows_the_check_only_in_the_full_flow():
    full = _stage_demo(Stage.CHECK, SimpleNamespace())
    placement = _stage_demo(Stage.CHECK, SimpleNamespace(), flow="placement")

    full._after_check(0)
    placement._after_check(0)

    assert full.stage is Stage.SWEEP
    # collect's camera-position measurement goes straight to the looks.
    assert placement.stage is Stage.CALIB_CAMERA


def test_headless_skips_the_head_circle_nobody_is_there_to_turn():
    demo = _stage_demo(Stage.SWEEP, SimpleNamespace(head_sweep=_sweep_status()), strict=True)

    assert demo.auto_advance(100) is True
    assert demo.stage is Stage.CALIB_CAMERA
    assert "skipped: headless" in demo.log[-1]


def test_space_skips_the_head_circle_and_r_restarts_it():
    demo = _stage_demo(Stage.SWEEP, SimpleNamespace(head_sweep=_sweep_status()), strict=True)

    demo._retry(100)
    assert demo.stage is Stage.SWEEP
    demo._advance(200)
    assert demo.stage is Stage.CALIB_CAMERA
    assert "SWEEPING 0/32" in demo.log[-1] and "(skipped)" in demo.log[-1]


def test_the_head_circle_view_draws_the_ring_around_the_face():
    from gaze_lab.config import load_config

    ticks = tuple(i < 12 for i in range(32))
    for status in (
        _sweep_status(ticks=ticks, pointer_deg=200.0, reach=0.8, hint="DOWN", last_reason="TOO_FAST"),
        _sweep_status(state="DONE", ticks=(True,) * 32),
        _sweep_status(state="CENTERING", ticks=(False,) * 32),
    ):
        session = SimpleNamespace(
            head_sweep=status,
            last_observation=SimpleNamespace(face_bbox=(250, 150, 140, 180)),
            latency_percentile=lambda _p: 31.0,
            events=[],
            placement_result=None,
        )
        demo = PipelineDemo(load_config(), session, korean=True, strict=True)
        demo.show_overlay = False
        demo.stage = Stage.SWEEP
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        out = demo._render(frame, 0)
        assert out.shape == frame.shape and out.any()


def test_live_view_renders_every_state_without_a_lookup_error():
    from gaze_lab.config import load_config
    from gaze_lab.evidence.gaze import GazeEvidenceRecorder
    from gaze_lab.schemas import GazeDecision, GazeStateEvent

    cfg = load_config()
    for label in ("CAMERA", "SCREEN", "BOTTOM", "OTHER", "UNCERTAIN"):
        probs = {"CAMERA": 0.1, "SCREEN": 0.2, "BOTTOM": 0.3, "OTHER": 0.4}
        evidence = GazeEvidenceRecorder(cfg.evidence)
        for t in range(0, 4000, 125):  # four seconds of this label: a coach line for BOTTOM / OTHER
            evidence.record(GazeDecision(t_ms=t, frame_id=0, label=label, p_camera=0.1, p_bottom=0.3,
                                         face_valid=True, direction="LEFT" if label == "OTHER" else None))
        session = SimpleNamespace(
            last_event=GazeStateEvent(t_ms=0, label=label, confidence=0.5,
                                      continuous_duration_ms=1200, face_valid=True),
            last_decision=GazeDecision(t_ms=0, frame_id=0, label=label, p_camera=0.1, p_bottom=0.3,
                                       face_valid=True, probs=probs,
                                       direction="UP_LEFT" if label == "OTHER" else None),
            gaze_evidence=evidence,
            last_condition=SimpleNamespace(reliability=0.62, issues=["TOO_FAR", "SECOND_FACE"]),
            reanchor_status=SimpleNamespace(state="DONE", shift_deg=(1.0, -0.5), collected=8,
                                            target=8, reason=None),
            latency_percentile=lambda _p: 31.0,
            events=[],
            last_observation=None,
            placement_result=None,
        )
        demo = PipelineDemo(cfg, session, korean=True, strict=True)
        demo.stage = Stage.LIVE
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        out = demo._render(frame, 0)
        assert out.shape == frame.shape and out.any()


def test_quality_hints_use_video_bottom_and_advisory_wording():
    korean = SimpleNamespace(korean=True, _unicode_ok=True)
    english = SimpleNamespace(korean=False, _unicode_ok=True)

    assert "영상 하단" in _hint_text(korean, "CLASS_NOT_SEPARABLE", None)
    assert "video-bottom" in _hint_text(english, "CLASS_NOT_SEPARABLE", None)


def test_face_overlay_crosshair_reaches_the_face_landmark_anchors():
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    points = np.zeros((478, 2), dtype=np.int32)
    points[10] = (40, 20)
    points[152] = (40, 80)
    points[234] = (20, 50)
    points[454] = (60, 50)

    FaceOverlay._draw_face_crosshair(frame, points, (90, 220, 120))

    # The axes extend almost to both cheeks, the forehead and the chin.
    assert frame[50, 22].any()
    assert frame[50, 58].any()
    assert frame[22, 40].any()
    assert frame[78, 40].any()

    # They stop at the face anchors rather than becoming a screen-wide guide.
    assert not frame[50, 10].any()
    assert not frame[50, 70].any()
    assert not frame[10, 40].any()
    assert not frame[90, 40].any()
    assert not hasattr(FaceOverlay, "_draw_gaze_ray")


def test_face_overlay_crosshair_follows_face_roll():
    frame = np.zeros((100, 100, 3), dtype=np.uint8)
    points = np.zeros((478, 2), dtype=np.int32)
    points[10] = (30, 20)
    points[152] = (50, 80)
    points[234] = (15, 40)
    points[454] = (65, 60)

    FaceOverlay._draw_face_crosshair(frame, points, (90, 220, 120))

    assert frame[35, 35].any()
    assert frame[65, 45].any()
    assert frame[45, 28].any()
    assert frame[55, 52].any()
    assert not frame[20, 40].any()
    assert not frame[50, 20].any()


def test_no_observation_draws_nothing():
    frame = np.zeros((40, 40, 3), dtype=np.uint8)

    FaceOverlay().draw(frame, None, mesh=False)

    assert not frame.any()


def _inconclusive():
    return SimpleNamespace(supported=False, placement="INCONCLUSIVE", reason="TARGETS_NOT_SEPARATED",
                           hint="look at each target", summary=lambda: "INCONCLUSIVE")


def _screen_cue_session(eye_based):
    return SimpleNamespace(eye_based=eye_based, estimate_placement=_inconclusive,
                           placement_result=_inconclusive())


def test_head_mode_does_not_stop_on_an_unreadable_camera_position():
    demo = _stage_demo(Stage.CALIB_SCREEN, _screen_cue_session(eye_based=False), strict=True)

    demo._next_after_cue(100)

    assert demo.stage is Stage.CALIB_BOTTOM


def test_eye_mode_still_stops_on_an_unreadable_camera_position():
    demo = _stage_demo(Stage.CALIB_SCREEN, _screen_cue_session(eye_based=True), strict=True)

    demo._next_after_cue(100)
    assert demo.stage is Stage.PLACE_RESULT
    demo._advance(200)
    assert demo.stage is Stage.PLACE_RESULT


def test_a_folded_screen_is_explained_in_korean():
    korean = SimpleNamespace(korean=True, _unicode_ok=True)
    from gaze_lab.calibration.references import SCREEN_MERGED_WARNING

    assert "정면" in _hint_text(korean, None, SCREEN_MERGED_WARNING)
