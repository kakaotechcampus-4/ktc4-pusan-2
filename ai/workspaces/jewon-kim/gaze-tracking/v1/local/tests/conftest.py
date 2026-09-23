"""Shared test fixtures.

Two jobs.

1. Put the repo root on ``sys.path``.  ``pyproject.toml`` only adds ``ai/src``,
   so ``import ai.tools...`` resolves under ``python -m pytest`` (which puts the
   cwd on ``sys.path[0]``) but not under a bare ``pytest`` console script.  Doing
   it here makes both spellings work.

2. Hand out the two real fixtures in ``tests/fixtures`` and the expensive
   objects built from them.  There is no recorded dataset in this repo, so every
   test that needs pixels uses ``face.jpg`` / ``static_face_30fps.mp4``.  Tests
   must NOT fabricate a stand-in dataset and read accuracy off it -- a number
   measured on invented gaze angles says nothing about the model.  Hand-built
   inputs are fine where the point is a specific code path (a known-angle vector
   through a conversion, a decision sequence through the smoother's state
   machine); they are not fine as evidence that the pipeline "works".

The MediaPipe graph costs ~1 s to build and the landmarker result is
deterministic in IMAGE mode, so both are session-scoped.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"

for _entry in (REPO_ROOT / "ai" / "src", REPO_ROOT):
    if str(_entry) not in sys.path:
        sys.path.insert(0, str(_entry))


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def face_jpg() -> Path:
    """A single real face looking into the lens (truth ~(0, 0) degrees).

    Load-bearing: the PnP model points, the 63 deg focal length, the iris bias
    constants and the cross-backbone sign check were all derived against this
    image. Replacing it invalidates those constants.
    """
    path = FIXTURES / "face.jpg"
    if not path.is_file():
        pytest.skip(f"missing fixture {path}")
    return path


@pytest.fixture(scope="session")
def face_video() -> Path:
    """640x480 30 fps, 420 frames (14 s) of one static face."""
    path = FIXTURES / "static_face_30fps.mp4"
    if not path.is_file():
        pytest.skip(f"missing fixture {path}")
    return path


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def cfg():
    """The shipped ai/configs, loaded once."""
    from vision.config import load_config

    return load_config()


@pytest.fixture
def fresh_cfg():
    """A per-test config, safe to mutate."""
    from vision.config import load_config

    return load_config()


# --------------------------------------------------------------------------
# Pixels and landmarks
# --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def face_rgb(face_jpg: Path) -> np.ndarray:
    """``face.jpg`` as a contiguous uint8 RGB array."""
    cv2 = pytest.importorskip("cv2")
    bgr = cv2.imread(str(face_jpg), cv2.IMREAD_COLOR)
    if bgr is None:
        pytest.skip(f"cv2 could not decode {face_jpg}")
    return np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))


@pytest.fixture(scope="session")
def face_image_size(face_rgb: np.ndarray):
    """(width, height) of the face fixture, in pixels."""
    return (int(face_rgb.shape[1]), int(face_rgb.shape[0]))


def _require_landmarker_model() -> Optional[Path]:
    from vision.config import load_config, resolve_path

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
        pytest.skip("ai/models/face_landmarker.task not installed")

    from vision.preprocess.landmarker import FaceLandmarkerWrapper

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
        pytest.skip("ai/models/face_landmarker.task not installed")

    from vision.preprocess.pipeline import PreprocessPipeline

    with PreprocessPipeline(cfg) as pipeline:
        obs = pipeline.process_rgb(face_rgb, frame_id=0, t_ms=0)
    return obs
