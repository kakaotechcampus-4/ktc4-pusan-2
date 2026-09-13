from types import SimpleNamespace

import numpy as np

from ai.tools.pipeline_demo import FaceOverlay, PipelineDemo, Stage, _hint_text


def _stage_demo(stage, session):
    demo = object.__new__(PipelineDemo)
    demo.stage = stage
    demo.session = session
    demo._enter = lambda next_stage, _t_ms: setattr(demo, "stage", next_stage)
    return demo


def test_placement_estimate_never_blocks_calibration():
    demo = _stage_demo(
        Stage.PLACE_RESULT,
        SimpleNamespace(placement_result=SimpleNamespace(supported=False)),
    )

    demo._advance(100)

    assert demo.stage is Stage.CALIB_CAMERA


def test_low_quality_calibration_is_advisory_when_model_exists():
    demo = _stage_demo(
        Stage.CALIB_RESULT,
        SimpleNamespace(
            calibration_quality=SimpleNamespace(ok=False),
            is_calibrated=True,
        ),
    )

    demo._advance(100)

    assert demo.stage is Stage.LIVE


def test_calibration_without_a_model_still_blocks_live_mode():
    demo = _stage_demo(
        Stage.CALIB_RESULT,
        SimpleNamespace(
            calibration_quality=SimpleNamespace(ok=False),
            is_calibrated=False,
        ),
    )

    demo._advance(100)

    assert demo.stage is Stage.CALIB_RESULT


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
