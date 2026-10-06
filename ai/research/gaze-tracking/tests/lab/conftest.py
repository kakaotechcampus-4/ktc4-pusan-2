"""Shared test fixtures.

Two jobs.

1. Hand out the two real fixtures in ``tests/fixtures/local`` and the expensive
   objects built from them.  There is no recorded dataset in this repo, so every
   test that needs pixels uses ``face.png`` / ``static_face_30fps.mp4``.  Both are
   a synthetic face (SFHQ, CC0) that ``tools/fetch_assets.py`` downloads and frames
   like a laptop webcam; neither is committed (``tests/fixtures/README.md``).  Tests
   must NOT fabricate a stand-in dataset and read accuracy off it -- a number
   measured on invented gaze angles says nothing about the model.  Hand-built
   inputs are fine where the point is a specific code path (a known-angle vector
   through a conversion, a decision sequence through the smoother's state
   machine); they are not fine as evidence that the pipeline "works".

The MediaPipe graph costs ~1 s to build and the landmarker result is
deterministic in IMAGE mode, so both are session-scoped.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "local"


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def face_photo() -> Path:
    """A single synthetic face looking into the lens (truth ~(0, 0) degrees).

    The PnP model points, the 63 deg focal length, the iris bias constants and
    the cross-backbone sign check were derived on the archive's earlier photo;
    the tests that read this one check that they still hold on a different face.
    """
    path = FIXTURES / "face.png"
    if not path.is_file():
        pytest.skip(f"missing fixture {path} (run tools/fetch_assets.py)")
    return path


@pytest.fixture(scope="session")
def face_video() -> Path:
    """640x480 30 fps, 420 frames (14 s) of one static face."""
    path = FIXTURES / "static_face_30fps.mp4"
    if not path.is_file():
        pytest.skip(f"missing fixture {path} (run tools/fetch_assets.py)")
    return path


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def cfg():
    """The shipped configs, loaded once."""
    from gaze_lab.config import load_config

    return load_config()


@pytest.fixture
def fresh_cfg():
    """A per-test config, safe to mutate."""
    from gaze_lab.config import load_config

    return load_config()


# --------------------------------------------------------------------------
# Pixels and landmarks
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def face_rgb(face_photo: Path) -> np.ndarray:
    """``face.png`` as a contiguous uint8 RGB array."""
    cv2 = pytest.importorskip("cv2")
    bgr = cv2.imread(str(face_photo), cv2.IMREAD_COLOR)
    if bgr is None:
        pytest.skip(f"cv2 could not decode {face_photo}")
    return np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))


@pytest.fixture(scope="session")
def face_image_size(face_rgb: np.ndarray):
    """(width, height) of the face fixture, in pixels."""
    return (int(face_rgb.shape[1]), int(face_rgb.shape[0]))


def _require_landmarker_model() -> Optional[Path]:
    from gaze_lab.config import load_config, resolve_path

    path = resolve_path(load_config().preprocess.landmarker_model_path)
    return path if path.is_file() else None


@pytest.fixture(scope="session")
def landmark_result(face_rgb: np.ndarray, cfg):
    """MediaPipe's raw output for the face fixture (478 landmarks + blendshapes).

    Skips rather than fails when mediapipe or the .task bundle is absent: both
    are gitignored downloads (doc 16), so a checkout without them is a normal
    state, not a broken one.
    """
    pytest.importorskip("mediapipe")
    if _require_landmarker_model() is None:
        pytest.skip("artifacts/face_landmarker.task not installed (run tools/fetch_assets.py)")

    from gaze_lab.preprocess.landmarker import FaceLandmarkerWrapper

    with FaceLandmarkerWrapper(cfg.preprocess) as wrapper:
        result = wrapper.detect(face_rgb)
    if result is None:
        pytest.skip("no face detected in the fixture")
    return result


@pytest.fixture(scope="session")
def face_landmarks(landmark_result) -> np.ndarray:
    """(478, 3) normalised landmarks for the face fixture."""
    return landmark_result.landmarks


@pytest.fixture(scope="session")
def face_observation(face_rgb: np.ndarray, cfg):
    """A real ``FrameObservation`` straight from the shipped preprocess pipeline."""
    pytest.importorskip("mediapipe")
    if _require_landmarker_model() is None:
        pytest.skip("artifacts/face_landmarker.task not installed (run tools/fetch_assets.py)")

    from gaze_lab.preprocess.pipeline import PreprocessPipeline

    with PreprocessPipeline(cfg) as pipeline:
        obs = pipeline.process_rgb(face_rgb, frame_id=0, t_ms=0)
    return obs
