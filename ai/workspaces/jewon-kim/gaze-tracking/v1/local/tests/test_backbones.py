"""Contracts protected by this file: ``vision.backbones.*``.

The backbone layer is the only place allowed to know a model's native sign
convention (``schemas`` module docstring), so the properties locked in here are

* **registry** -- name resolution is a pure, case/whitespace-insensitive
  function of ``BackboneConfig.name``; the registered key is what lands on
  ``cls.name`` (and therefore in ``AiVersion.gaze_backbone``); a name collision
  is loud; the listing is sorted.
* **lazy torch** -- ``mediapipe_geom`` has to run on a machine where torch was
  never installed, so importing ``l2cs`` / ``gazetr`` (and ``_load_builtins``)
  must not need torch, and must not import it when it happens to be present.
* **checkpoint plumbing** -- an HTML interstitial saved under a ``.pkl`` name is
  reported as corrupt, the three published wrapper shapes plus ``DataParallel``'s
  ``module.`` prefix are unwrapped, and a *missing* required parameter is fatal
  while extra keys are tolerated.
* **geometry / sign** -- on the real ``face.jpg`` fixture the subject looks into
  the lens, so both angles must come out small and finite; and moving the irises
  DOWN in the image must make ``gaze_pitch`` MORE NEGATIVE.  That is the
  BOTTOM-vs-CAMERA invariant every adapter owes the rest of the pipeline.
* **torch adapters** -- the documented GazeHub -> schemas conversion
  (``gaze_yaw = -yaw_model``, ``gaze_pitch = +pitch_model``) is checked through a
  stub model, so it holds with or without the published weights on disk.

There is no recorded dataset in this repo and none is invented here: pixels come
from ``tests/fixtures/face.jpg`` through the shipped preprocess pipeline, and the
hand-built arrays exist only to drive one specific code path (a known offset
through the iris model, a known bin through the L2CS de-binning, a known byte
string through the checkpoint reader).  No accuracy number is asserted anywhere.
"""

from __future__ import annotations

import importlib
import math
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pytest

from vision.backbones import gazetr as gazetr_mod
from vision.backbones import l2cs as l2cs_mod
from vision.backbones import mediapipe_geom as geom_mod
from vision.backbones import registry as registry_mod
from vision.backbones.base import (
    BackboneError,
    CheckpointCorruptError,
    CheckpointMissingError,
    GazeBackbone,
    assert_required_keys_loaded,
    load_checkpoint_state_dict,
)
from vision.backbones.gazetr import GazeTRBackbone
from vision.backbones.l2cs import L2CSBackbone
from vision.backbones.mediapipe_geom import MediaPipeGeomBackbone
from vision.backbones.registry import (
    available_backbones,
    build_backbone,
    get_backbone_class,
    register,
)
from vision.config import BackboneConfig
from vision.preprocess.landmarker import in_bounds_fraction
from vision.schemas import FrameObservation, GazeVector, HeadPose

BUILTIN_BACKBONES = ("gazetr", "l2cs", "mediapipe_geom")


# ==========================================================================
# Helpers
# ==========================================================================


class _StubBackbone(GazeBackbone):
    """Records what the base-class adapters hand to ``predict``."""

    name = "stub"
    version = "9.9.9"

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.closed = 0

    def predict(
        self,
        face_crop,
        left_eye_crop,
        right_eye_crop,
        head_pose,
        *,
        landmarks=None,
        blendshapes=None,
        image_size=None,
    ) -> GazeVector:
        self.calls.append(
            {
                "face_crop": face_crop,
                "left_eye_crop": left_eye_crop,
                "right_eye_crop": right_eye_crop,
                "head_pose": head_pose,
                "landmarks": landmarks,
                "blendshapes": blendshapes,
                "image_size": image_size,
            }
        )
        return GazeVector(gaze_yaw=0.1, gaze_pitch=0.2, confidence=1.0, backbone=self.name)

    def close(self) -> None:
        self.closed += 1


def _geom(**params: Any) -> MediaPipeGeomBackbone:
    return MediaPipeGeomBackbone(BackboneConfig(name="mediapipe_geom", params=dict(params)))


def _predict_landmarks(
    backbone: MediaPipeGeomBackbone,
    landmarks: Optional[np.ndarray],
    *,
    head: Optional[HeadPose] = None,
    blendshapes: Optional[Dict[str, float]] = None,
    image_size: Optional[Tuple[int, int]] = None,
) -> GazeVector:
    """``predict`` with the pixel arguments the geometry backbone ignores."""
    return backbone.predict(
        None, None, None, head, landmarks=landmarks, blendshapes=blendshapes, image_size=image_size
    )


def _run_subprocess(repo_root: Path, script: str) -> "subprocess.CompletedProcess[str]":
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root / "ai" / "src")
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )


@pytest.fixture
def registry_sandbox():
    """Restore the global registry so registration tests cannot leak."""
    registry_mod._load_builtins()
    snapshot = dict(registry_mod._REGISTRY)
    try:
        yield registry_mod
    finally:
        registry_mod._REGISTRY.clear()
        registry_mod._REGISTRY.update(snapshot)


def _import_optional(name: str):
    """Import an optional dependency, or skip.

    ``pytest.importorskip`` only catches ImportError, but torch has a third
    state between present and absent: INSTALLED YET UNLOADABLE. A CUDA wheel on
    a machine without the matching runtime raises OSError from its DLL loader
    ("Error loading shm.dll"), which is not an ImportError, so the whole file
    errored instead of skipping. For a test that only wants "can we exercise the
    torch backends here", a torch that cannot load is the same as no torch --
    the difference belongs in the skip REASON, not in the outcome.
    """
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        pytest.skip(f"{name} is not installed ({exc})")
    except OSError as exc:  # installed but its shared libraries will not load
        pytest.skip(f"{name} is installed but cannot be loaded ({exc})")


@pytest.fixture(scope="session")
def torch_mod():
    return _import_optional("torch")


@pytest.fixture(scope="session")
def l2cs_backbone(torch_mod):
    """The real L2CS backbone, or a skip when the 95 MB checkpoint is absent."""
    _import_optional("torchvision")
    from vision.config import resolve_path

    if not resolve_path(l2cs_mod.DEFAULT_CHECKPOINT).is_file():
        pytest.skip(f"{l2cs_mod.DEFAULT_CHECKPOINT} not installed")
    backbone = L2CSBackbone(BackboneConfig(name="l2cs", num_threads=4, batch_size=2))
    try:
        yield backbone
    finally:
        backbone.close()


# ==========================================================================
# Registry
# ==========================================================================


@pytest.mark.parametrize("name", BUILTIN_BACKBONES)
def test_every_builtin_name_resolves_to_a_backbone_class(name):
    cls = get_backbone_class(name)

    assert issubclass(cls, GazeBackbone)
    assert cls.name == name


@pytest.mark.parametrize(
    "spelling", ["mediapipe_geom", "MediaPipe_Geom", "  MEDIAPIPE_GEOM  ", "\tmediapipe_geom\n"]
)
def test_name_lookup_ignores_case_and_surrounding_whitespace(spelling):
    assert get_backbone_class(spelling) is MediaPipeGeomBackbone


def test_backbone_records_the_normalised_key_not_the_configured_spelling():
    # AiVersion.gaze_backbone is filled from .name, so it must not vary with
    # however the YAML happened to spell the selection.
    backbone = build_backbone(BackboneConfig(name="  MediaPipe_Geom "))

    assert isinstance(backbone, MediaPipeGeomBackbone)
    assert backbone.name == "mediapipe_geom"


def test_unknown_backbone_name_error_lists_every_available_name():
    with pytest.raises(ValueError) as excinfo:
        get_backbone_class("l2cs_v2")

    message = str(excinfo.value)
    assert "l2cs_v2" in message
    for name in BUILTIN_BACKBONES:
        assert name in message


def test_available_backbones_is_sorted_and_holds_exactly_the_shipped_set():
    names = available_backbones()

    assert names == sorted(names)
    assert set(names) == set(BUILTIN_BACKBONES)


def test_registering_a_different_class_under_a_taken_name_raises(registry_sandbox):
    with pytest.raises(ValueError) as excinfo:

        @register("mediapipe_geom")
        class _Impostor(GazeBackbone):
            def predict(self, *a, **k):  # pragma: no cover - never reached
                raise AssertionError

    message = str(excinfo.value)
    assert "mediapipe_geom" in message
    # The message names the current owner so the collision is diagnosable.
    assert "MediaPipeGeomBackbone" in message
    assert registry_sandbox._REGISTRY["mediapipe_geom"] is MediaPipeGeomBackbone


def test_re_registering_the_same_class_is_idempotent(registry_sandbox):
    # Re-importing a module (reload, or two import spellings) must not explode.
    returned = register("mediapipe_geom")(MediaPipeGeomBackbone)

    assert returned is MediaPipeGeomBackbone
    assert registry_sandbox._REGISTRY["mediapipe_geom"] is MediaPipeGeomBackbone


def test_register_normalises_the_key_and_stamps_it_on_the_class(registry_sandbox):
    @register("  Fancy_Backbone  ")
    class _Fancy(GazeBackbone):
        def predict(self, *a, **k):  # pragma: no cover - never called
            raise AssertionError

    assert _Fancy.name == "fancy_backbone"
    assert get_backbone_class("FANCY_BACKBONE") is _Fancy
    assert "fancy_backbone" in available_backbones()


def test_builtin_module_list_and_registry_stay_in_step():
    # _load_builtins imports exactly these modules and every one of them must
    # register something, otherwise a silent import-order bug hides a backbone.
    assert len(registry_mod._BUILTIN_MODULES) == len(BUILTIN_BACKBONES)
    # mediapipe_geom first: a broken optional dependency in a torch backbone
    # must not be able to break the default path.
    assert registry_mod._BUILTIN_MODULES[0].endswith("mediapipe_geom")


# ==========================================================================
# Lazy torch (torch may only be imported inside the predict path)
# ==========================================================================


_BLOCK_TORCH_SCRIPT = """
import sys

class Blocker:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in ("torch", "torchvision"):
            raise ImportError("torch is blocked for this test")
        return None

sys.meta_path.insert(0, Blocker())

import vision.backbones.l2cs
import vision.backbones.gazetr
from vision.backbones.registry import available_backbones, get_backbone_class

names = available_backbones()
assert names == ["gazetr", "l2cs", "mediapipe_geom"], names
assert get_backbone_class("l2cs") is vision.backbones.l2cs.L2CSBackbone
assert "torch" not in sys.modules
print("OK")
"""

_PARTIAL_REGISTRY_SCRIPT = """
import sys


class Breaker:
    healed = False

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "vision.backbones.gazetr" and not self.healed:
            raise ImportError("simulated broken optional dependency")
        return None


breaker = Breaker()
sys.meta_path.insert(0, breaker)

from vision.backbones.registry import available_backbones

try:
    available_backbones()
except ImportError:
    pass
else:
    raise SystemExit("a broken builtin import was not reported at all")

breaker.healed = True
names = available_backbones()
assert names == ["gazetr", "l2cs", "mediapipe_geom"], names
print("OK")
"""

_LAZY_IMPORT_SCRIPT = """
import sys
import vision.backbones.l2cs
import vision.backbones.gazetr
from vision.backbones.registry import available_backbones

available_backbones()
leaked = sorted(m for m in sys.modules if m.split(".")[0] in ("torch", "torchvision"))
assert not leaked, leaked
print("OK")
"""


def test_torch_backbones_import_on_a_machine_without_torch(repo_root):
    result = _run_subprocess(repo_root, _BLOCK_TORCH_SCRIPT)

    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_a_failed_builtin_import_is_retried_instead_of_latched(repo_root):
    # Needs a fresh interpreter: once a builtin module is in sys.modules its
    # registration cannot be replayed, so the latch is invisible in-process.
    result = _run_subprocess(repo_root, _PARTIAL_REGISTRY_SCRIPT)

    assert result.returncode == 0, result.stdout + result.stderr


def test_importing_the_torch_backbones_does_not_pull_torch_in(repo_root):
    # Stronger than the blocked-import check: catches a module-level
    # ``try: import torch`` that would silently make the default path expensive.
    result = _run_subprocess(repo_root, _LAZY_IMPORT_SCRIPT)

    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


# ==========================================================================
# base.load_checkpoint_state_dict
# ==========================================================================


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(b"<!DOCTYPE html><html><title>Google Drive</title></html>", id="html_page"),
        pytest.param(b"", id="empty_file"),
        pytest.param(b"not a checkpoint at all", id="plain_text"),
    ],
)
def test_a_file_that_is_not_a_torch_archive_is_reported_as_corrupt(tmp_path, torch_mod, payload):
    path = tmp_path / "L2CSNet_gaze360.pkl"
    path.write_bytes(payload)

    with pytest.raises(CheckpointCorruptError) as excinfo:
        load_checkpoint_state_dict(path, "l2cs")

    message = str(excinfo.value)
    assert str(path) in message
    assert "not a torch checkpoint" in message
    # The remedy differs from a missing file, so the message has to say it.
    assert "Delete the file" in message
    assert isinstance(excinfo.value, BackboneError)


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(bytes([0x80]) + b"\x02 truncated pickle", id="pickle_magic"),
        pytest.param(b"PK\x03\x04" + b"truncated zip" * 4, id="zip_magic"),
    ],
)
def test_a_truncated_torch_archive_keeps_the_underlying_reason(tmp_path, torch_mod, payload):
    # Right magic bytes: this is a broken download, not an HTML page, and the
    # message must not claim otherwise.
    path = tmp_path / "weights.pt"
    path.write_bytes(payload)

    with pytest.raises(CheckpointCorruptError) as excinfo:
        load_checkpoint_state_dict(path, "gazetr")

    message = str(excinfo.value)
    assert "cannot read" in message
    assert "not a torch checkpoint" not in message
    assert excinfo.value.__cause__ is not None


def test_a_checkpoint_that_vanished_is_reported_as_a_typed_backbone_error(tmp_path, torch_mod):
    # torch.load fails, and so does the magic-byte read that follows it.  That
    # second read used to raise a bare FileNotFoundError, which escaped over
    # torch's own reason and past every caller that catches BackboneError.
    missing = tmp_path / "gone.pt"

    with pytest.raises(BackboneError) as excinfo:
        load_checkpoint_state_dict(missing, "l2cs")

    assert str(missing) in str(excinfo.value)
    # torch's reason is what says what was actually wrong; it must be kept.
    assert excinfo.value.__cause__ is not None


def test_the_magic_bytes_are_read_without_slurping_the_whole_checkpoint(
    tmp_path, torch_mod, monkeypatch
):
    # A published checkpoint is ~95 MB, and reading all of it to look at four
    # bytes is exactly the wrong move on a machine that has just failed to load
    # it.  Path.read_bytes is banned for the duration to prove it is not used.
    path = tmp_path / "L2CSNet_gaze360.pkl"
    path.write_bytes(b"<!DOCTYPE html><html>Google Drive</html>" + b"x" * 8192)

    def _refuse(self, *args, **kwargs):
        raise AssertionError(f"read the whole of {self} to look at 4 bytes")

    monkeypatch.setattr(Path, "read_bytes", _refuse)

    with pytest.raises(CheckpointCorruptError) as excinfo:
        load_checkpoint_state_dict(path, "l2cs")

    assert "not a torch checkpoint" in str(excinfo.value)


@pytest.mark.parametrize("wrapper", [None, "model_state_dict", "state_dict", "model"])
@pytest.mark.parametrize("prefix", ["", "module."])
def test_published_checkpoint_shapes_flatten_to_one_param_dict(
    tmp_path, torch_mod, wrapper, prefix
):
    inner = {
        f"{prefix}conv1.weight": torch_mod.zeros(2),
        f"{prefix}fc_yaw_gaze.bias": torch_mod.ones(3),
    }
    blob: Any = inner if wrapper is None else {wrapper: inner, "epoch": 7}
    path = tmp_path / "cp.pt"
    torch_mod.save(blob, path)

    state = load_checkpoint_state_dict(path, "l2cs")

    assert sorted(state) == ["conv1.weight", "fc_yaw_gaze.bias"]
    assert tuple(state["fc_yaw_gaze.bias"].shape) == (3,)


def test_a_checkpoint_holding_something_other_than_a_dict_is_corrupt(tmp_path, torch_mod):
    path = tmp_path / "cp.pt"
    torch_mod.save([torch_mod.zeros(1)], path)

    with pytest.raises(CheckpointCorruptError) as excinfo:
        load_checkpoint_state_dict(path, "l2cs")

    assert "holds list" in str(excinfo.value)


# ==========================================================================
# base.assert_required_keys_loaded
# ==========================================================================


def _load_result(missing: List[str], unexpected: List[str]) -> SimpleNamespace:
    """Stand-in for torch's ``_IncompatibleKeys``."""
    return SimpleNamespace(missing_keys=missing, unexpected_keys=unexpected)


def test_a_missing_forward_pass_parameter_is_fatal(tmp_path):
    result = _load_result(["conv1.weight", "layer1.0.bn1.bias"], [])

    with pytest.raises(CheckpointCorruptError) as excinfo:
        assert_required_keys_loaded(result, ("conv1", "layer"), "l2cs", tmp_path / "cp.pkl")

    message = str(excinfo.value)
    assert "missing 2 parameter" in message
    assert "conv1.weight" in message
    assert str(tmp_path / "cp.pkl") in message


def test_extra_keys_and_unrelated_gaps_are_tolerated(tmp_path):
    # L2CS ships a vestigial fc_finetune; that must not fail a load.
    missing = ["fc_finetune.weight"]
    unexpected = ["fc_finetune.bias", "num_batches_tracked"]

    reported_missing, reported_unexpected = assert_required_keys_loaded(
        _load_result(missing, unexpected),
        ("conv1", "bn1", "layer", "fc_yaw_gaze", "fc_pitch_gaze"),
        "l2cs",
        tmp_path / "cp.pkl",
    )

    assert reported_missing == missing
    assert reported_unexpected == unexpected
    # Copies, so a caller storing them cannot mutate torch's own lists.
    assert reported_missing is not missing
    assert reported_unexpected is not unexpected


def test_an_empty_required_prefix_list_checks_nothing(tmp_path):
    # ``str.startswith(())`` is False for every key, so an empty prefix tuple
    # silently disables the guard instead of demanding everything.  Locked in
    # here so a refactor cannot flip it without a failing test.
    reported_missing, _ = assert_required_keys_loaded(
        _load_result(["conv1.weight"], []), (), "l2cs", tmp_path / "cp.pkl"
    )

    assert reported_missing == ["conv1.weight"]


def test_missing_checkpoint_error_carries_path_and_download_instructions(tmp_path):
    path = tmp_path / "gaze" / "GazeTR-H-ETH.pt"

    error = CheckpointMissingError("gazetr", path, "\n  run the downloader  \n")

    assert error.backbone == "gazetr"
    assert error.path == path
    assert error.instructions == "run the downloader"
    rendered = str(error)
    assert str(path) in rendered
    assert rendered.endswith("run the downloader")
    assert isinstance(error, BackboneError) and isinstance(error, RuntimeError)


# ==========================================================================
# base.GazeBackbone plumbing
# ==========================================================================


def test_predict_observation_forwards_every_field_of_the_observation():
    backbone = _StubBackbone()
    obs = FrameObservation(
        frame_id=3,
        t_ms=125,
        face_confidence=0.9,
        face_valid=True,
        head_pose=HeadPose(yaw=0.1),
        face_crop=np.zeros((4, 4, 3), np.uint8),
        left_eye_crop=np.ones((2, 2, 3), np.uint8),
        right_eye_crop=np.full((2, 2, 3), 7, np.uint8),
        landmarks=np.zeros((478, 3)),
        blendshapes={"eyeLookUpLeft": 0.5},
        image_size=(640, 480),
    )

    backbone.predict_observation(obs)

    (call,) = backbone.calls
    assert call["face_crop"] is obs.face_crop
    assert call["left_eye_crop"] is obs.left_eye_crop
    assert call["right_eye_crop"] is obs.right_eye_crop
    assert call["head_pose"] is obs.head_pose
    assert call["landmarks"] is obs.landmarks
    assert call["blendshapes"] is obs.blendshapes
    assert call["image_size"] == (640, 480)


def test_default_predict_batch_preserves_order_and_length():
    backbone = _StubBackbone()
    observations = [
        FrameObservation(frame_id=i, t_ms=i * 125, face_confidence=1.0, face_valid=True)
        for i in range(4)
    ]

    vectors = backbone.predict_batch(observations)

    assert len(vectors) == 4
    assert [c["head_pose"] for c in backbone.calls] == [o.head_pose for o in observations]


@pytest.mark.parametrize("iterations,expected", [(-3, 0), (0, 0), (1, 1), (3, 3)])
def test_warmup_runs_exactly_the_requested_number_of_frames(iterations, expected):
    backbone = _StubBackbone()

    backbone.warmup(iterations=iterations)

    assert len(backbone.calls) == expected


def test_warmup_frame_is_deterministic_and_flagged_as_synthetic():
    backbone = _StubBackbone()

    first = backbone._warmup_observation()
    second = backbone._warmup_observation()

    # Seeded, so a warmup can never perturb a seeded experiment.
    assert np.array_equal(first.face_crop, second.face_crop)
    assert first.face_crop.shape == (224, 224, 3) and first.face_crop.dtype == np.uint8
    assert first.left_eye_crop.shape == (36, 60, 3)
    # A negative frame_id keeps warmup frames out of any real frame index.
    assert first.frame_id == -1
    assert first.left_eye_crop is not first.right_eye_crop


def test_close_is_idempotent_and_runs_on_context_exit():
    backbone = _StubBackbone()

    with backbone as entered:
        assert entered is backbone
    backbone.close()

    assert backbone.closed == 2


def test_a_subclass_without_predict_cannot_be_instantiated():
    class _Incomplete(GazeBackbone):
        pass

    with pytest.raises(TypeError):
        _Incomplete()  # type: ignore[abstract]


def test_a_weightless_backbone_reports_zero_model_size():
    assert MediaPipeGeomBackbone().model_size_bytes() == 0


def test_repr_names_the_registry_key_and_version():
    text = repr(MediaPipeGeomBackbone())

    assert "mediapipe_geom" in text
    assert "1.0.0" in text


# ==========================================================================
# mediapipe_geom -- real fixture
# ==========================================================================


def test_real_frontal_face_yields_small_finite_angles(face_observation):
    backbone = MediaPipeGeomBackbone()

    gaze = backbone.predict_observation(face_observation)

    assert math.isfinite(gaze.gaze_yaw) and math.isfinite(gaze.gaze_pitch)
    # The fixture subject looks into the lens; truth is about (0, 0) deg.  A
    # generous bound, because this is a sanity check, not an accuracy claim.
    assert abs(gaze.gaze_yaw_deg) < 10.0
    assert abs(gaze.gaze_pitch_deg) < 10.0
    assert 0.0 <= gaze.confidence <= 1.0
    assert gaze.confidence > 0.0
    assert gaze.backbone == "mediapipe_geom"
    assert gaze.inference_ms >= 0.0


def test_predict_without_landmarks_or_blendshapes_raises(face_observation):
    backbone = MediaPipeGeomBackbone()

    with pytest.raises(ValueError) as excinfo:
        _predict_landmarks(backbone, None, image_size=face_observation.image_size)

    assert "landmarks" in str(excinfo.value) and "blendshapes" in str(excinfo.value)


def test_landmarks_without_iris_refinement_fall_back_to_the_blendshape_branch(face_observation):
    # 468 points = the mesh without the two iris contours; the sphere model
    # cannot run, so the result must equal the blendshape-only estimate rather
    # than half-using a truncated array.
    backbone = MediaPipeGeomBackbone()
    truncated = face_observation.landmarks[:468]

    with_truncated = _predict_landmarks(
        backbone,
        truncated,
        head=face_observation.head_pose,
        blendshapes=face_observation.blendshapes,
        image_size=face_observation.image_size,
    )
    blendshapes_only = _predict_landmarks(
        backbone,
        None,
        head=face_observation.head_pose,
        blendshapes=face_observation.blendshapes,
        image_size=face_observation.image_size,
    )

    assert with_truncated.gaze_yaw == blendshapes_only.gaze_yaw
    assert with_truncated.gaze_pitch == blendshapes_only.gaze_pitch


def test_truncated_landmarks_without_blendshapes_still_raise(face_observation):
    backbone = MediaPipeGeomBackbone()

    with pytest.raises(ValueError):
        _predict_landmarks(
            backbone,
            face_observation.landmarks[:468],
            head=face_observation.head_pose,
            image_size=face_observation.image_size,
        )


@pytest.mark.parametrize(
    "use_blendshapes", [False, True], ids=["iris_only", "iris_plus_blendshape"]
)
@pytest.mark.parametrize(
    "shift_px,direction", [(3.0, "down"), (-3.0, "up")], ids=["irises_down", "irises_up"]
)
def test_irises_moved_down_the_image_make_the_pitch_more_negative(
    face_observation, use_blendshapes, shift_px, direction
):
    """The BOTTOM-vs-CAMERA invariant, straight from the schemas docstring.

    Reading a script below the screen drops the irises within the eye opening;
    that has to come out as a MORE NEGATIVE ``gaze_pitch``.  An adapter that
    leaks its native convention fails exactly here.
    """
    backbone = MediaPipeGeomBackbone()
    height = float(face_observation.image_size[1])
    base_landmarks = face_observation.landmarks
    shifted = base_landmarks.copy()
    # +y is DOWN in normalised MediaPipe coordinates.  The whole iris block
    # (centres 468/473 and both rings) moves together, so the measured iris
    # radius -- and therefore the scale of the estimate -- is untouched.
    shifted[468:478, 1] += shift_px / height

    blendshapes = face_observation.blendshapes if use_blendshapes else None
    kwargs = dict(
        head=face_observation.head_pose,
        blendshapes=blendshapes,
        image_size=face_observation.image_size,
    )
    base = _predict_landmarks(backbone, base_landmarks, **kwargs)
    moved = _predict_landmarks(backbone, shifted, **kwargs)

    delta = moved.gaze_pitch - base.gaze_pitch
    if direction == "down":
        assert delta < -math.radians(1.0), f"pitch moved {math.degrees(delta):+.2f} deg"
    else:
        assert delta > math.radians(1.0), f"pitch moved {math.degrees(delta):+.2f} deg"
    # A purely vertical iris displacement must not masquerade as a yaw change.
    assert abs(moved.gaze_yaw - base.gaze_yaw) < math.radians(2.0)


def test_predict_does_not_mutate_the_caller_landmarks(face_observation):
    backbone = MediaPipeGeomBackbone()
    landmarks = face_observation.landmarks.copy()
    untouched = landmarks.copy()

    _predict_landmarks(
        backbone,
        landmarks,
        head=face_observation.head_pose,
        blendshapes=dict(face_observation.blendshapes or {}),
        image_size=face_observation.image_size,
    )

    assert np.array_equal(landmarks, untouched)


def test_angles_are_invariant_to_a_uniform_image_rescale(face_observation):
    # Both the iris offset and the iris radius scale with the frame, so a square
    # frame of any size has to give the identical angle.
    backbone = MediaPipeGeomBackbone()
    kwargs = dict(head=face_observation.head_pose, blendshapes=None)

    small = _predict_landmarks(
        backbone, face_observation.landmarks, image_size=(640, 640), **kwargs
    )
    large = _predict_landmarks(
        backbone, face_observation.landmarks, image_size=(1280, 1280), **kwargs
    )

    assert small.gaze_yaw == pytest.approx(large.gaze_yaw, abs=1e-9)
    assert small.gaze_pitch == pytest.approx(large.gaze_pitch, abs=1e-9)


def test_omitting_image_size_behaves_like_a_square_frame(face_observation):
    # _landmarks_to_working_frame documents the image_size=None fallback as
    # "exact for a square frame".
    backbone = MediaPipeGeomBackbone()
    kwargs = dict(head=face_observation.head_pose, blendshapes=None)

    square = _predict_landmarks(
        backbone, face_observation.landmarks, image_size=(1024, 1024), **kwargs
    )
    fallback = _predict_landmarks(backbone, face_observation.landmarks, image_size=None, **kwargs)

    assert fallback.gaze_yaw == pytest.approx(square.gaze_yaw, abs=1e-6)
    assert fallback.gaze_pitch == pytest.approx(square.gaze_pitch, abs=1e-6)


def test_degenerate_all_zero_landmarks_give_zero_confidence_not_nan():
    backbone = MediaPipeGeomBackbone()

    gaze = _predict_landmarks(
        backbone, np.zeros((478, 3)), head=HeadPose(), image_size=(640, 480)
    )

    assert math.isfinite(gaze.gaze_yaw) and math.isfinite(gaze.gaze_pitch)
    assert gaze.gaze_yaw == 0.0 and gaze.gaze_pitch == 0.0
    assert gaze.confidence == 0.0


def test_non_finite_landmarks_do_not_become_a_confident_straight_down(face_observation):
    backbone = MediaPipeGeomBackbone()
    landmarks = face_observation.landmarks.copy()
    landmarks[468:478] = np.nan

    gaze = _predict_landmarks(
        backbone,
        landmarks,
        head=face_observation.head_pose,
        image_size=face_observation.image_size,
    )

    assert not math.isfinite(gaze.gaze_pitch) or gaze.confidence == 0.0


def test_extra_landmark_columns_are_accepted(face_observation):
    backbone = MediaPipeGeomBackbone()
    # e.g. an array carrying a visibility column after xyz.
    padded = np.concatenate([face_observation.landmarks, np.ones((478, 1))], axis=1)

    padded_gaze = _predict_landmarks(
        backbone, padded, head=face_observation.head_pose, image_size=face_observation.image_size
    )
    plain_gaze = _predict_landmarks(
        backbone,
        face_observation.landmarks,
        head=face_observation.head_pose,
        image_size=face_observation.image_size,
    )

    assert padded_gaze.gaze_pitch == pytest.approx(plain_gaze.gaze_pitch, abs=1e-12)


def test_closed_eyes_zero_the_confidence_but_still_return_an_angle(face_observation):
    backbone = MediaPipeGeomBackbone()
    landmarks = face_observation.landmarks.copy()
    for eye in geom_mod._EYES:
        _, _, v1a, v1b, v2a, v2b = eye.ear_points
        for upper, lower in ((v1a, v1b), (v2a, v2b)):
            midpoint = 0.5 * (landmarks[upper] + landmarks[lower])
            landmarks[upper] = midpoint
            landmarks[lower] = midpoint

    gaze = _predict_landmarks(
        backbone,
        landmarks,
        head=face_observation.head_pose,
        blendshapes=face_observation.blendshapes,
        image_size=face_observation.image_size,
    )

    assert gaze.confidence == 0.0
    assert math.isfinite(gaze.gaze_pitch)


def _confidence_of(
    backbone: MediaPipeGeomBackbone, landmarks: Optional[np.ndarray], n_sources: int = 2
) -> float:
    """``_confidence`` with two wide-open eyes and a frontal head, varying one term."""
    measurements = [
        geom_mod._EyeMeasurement(eye=eye, ear=backbone.params.ear_open, weight=1.0)
        for eye in geom_mod._EYES
    ]
    return backbone._confidence(
        measurements=measurements,
        head=HeadPose(),
        landmarks=landmarks,
        n_sources=n_sources,
        iris=(0.0, 0.0),
        blend=(0.0, 0.0),
    )


def test_the_visibility_term_is_the_shared_in_bounds_fraction():
    # One definition of "out of frame", shared with FrameQuality.landmark_visibility
    # through preprocess.landmarker, so the backbone and the doc-19 out-of-frame
    # bucket can never drift apart.
    backbone = MediaPipeGeomBackbone()
    inside = np.full((478, 3), 0.5)
    half_out = inside.copy()
    half_out[:239, 0] = 1.5
    outside = np.full((478, 3), 1.5)

    assert in_bounds_fraction(inside) == pytest.approx(1.0)
    assert in_bounds_fraction(half_out) == pytest.approx(0.5)
    assert in_bounds_fraction(outside) == pytest.approx(0.0)

    full, half, none_visible = (_confidence_of(backbone, a) for a in (inside, half_out, outside))

    assert none_visible < half < full
    # Affine in the shared metric: half the landmarks out of frame costs half of
    # what all of them out of frame costs.  Stated without naming the weights, so
    # a re-tune of the modulation stays legal and a swapped metric does not.
    assert (full - half) == pytest.approx(half - none_visible)


@pytest.mark.parametrize("landmarks", [None, np.zeros((0, 3)), np.zeros((478, 1))])
def test_an_unmeasurable_landmark_array_is_not_penalised_for_visibility(landmarks):
    # "None means do not penalise" is this backbone's policy and lives at the
    # call site, not in the shared metric: in_bounds_fraction answers 0.0 for an
    # empty array, which is right for a quality field and wrong for a frame that
    # was simply never measured for out-of-frame points (blendshapes only).
    backbone = MediaPipeGeomBackbone()

    assert _confidence_of(backbone, landmarks) == _confidence_of(backbone, np.full((478, 3), 0.5))


def test_a_frame_with_no_measured_source_scores_zero_confidence():
    # Neither branch produced an estimate, so _fuse returns (0, 0) and the
    # reported angle is the head direction.  Wide-open eyes say nothing about a
    # gaze nobody managed to measure, and scoring one anyway is how a frame of
    # NaN irises came back at 0.65 confidence.
    backbone = MediaPipeGeomBackbone()

    assert _confidence_of(backbone, np.full((478, 3), 0.5), n_sources=0) == 0.0


def test_a_turned_head_lowers_confidence(face_observation):
    backbone = MediaPipeGeomBackbone()
    kwargs = dict(blendshapes=None, image_size=face_observation.image_size)

    frontal = _predict_landmarks(backbone, face_observation.landmarks, head=HeadPose(), **kwargs)
    turned = _predict_landmarks(
        backbone, face_observation.landmarks, head=HeadPose(yaw=1.2), **kwargs
    )

    assert turned.confidence < frontal.confidence


# ==========================================================================
# mediapipe_geom -- geometry unit checks
# ==========================================================================


def test_zero_eye_in_head_offset_reproduces_the_head_direction_exactly():
    # The module docstring's degenerate case: irises sitting exactly at the
    # modelled eyeball centre must return the head direction untouched.
    backbone = MediaPipeGeomBackbone()

    gaze = _predict_landmarks(
        backbone, geom_mod._synthetic_landmarks(), head=HeadPose(), image_size=(640, 640)
    )

    assert gaze.gaze_yaw == pytest.approx(0.0, abs=1e-9)
    assert gaze.gaze_pitch == pytest.approx(0.0, abs=1e-9)


@pytest.mark.parametrize(
    "head",
    [
        HeadPose(yaw=0.20),
        HeadPose(pitch=-0.15),
        HeadPose(roll=0.10),
        HeadPose(yaw=0.20, pitch=-0.15, roll=0.10),
    ],
    ids=["yaw", "pitch", "roll", "combined"],
)
def test_zero_eye_in_head_offset_tracks_a_rotated_head(head):
    backbone = MediaPipeGeomBackbone()

    gaze = _predict_landmarks(
        backbone, geom_mod._synthetic_landmarks(), head=head, image_size=(640, 640)
    )

    # Not exact away from the origin -- the bias constants are subtracted in the
    # de-rotated head frame -- but it must stay within a degree.
    assert gaze.gaze_yaw == pytest.approx(head.yaw, abs=math.radians(1.0))
    assert gaze.gaze_pitch == pytest.approx(head.pitch, abs=math.radians(1.0))


@pytest.mark.parametrize(
    "head",
    [
        HeadPose(),
        HeadPose(yaw=0.3),
        HeadPose(pitch=0.3),
        HeadPose(roll=0.3),
        HeadPose(0.2, -0.1, 0.05),
    ],
)
def test_head_to_camera_rotation_is_a_proper_rotation(head):
    rotation = geom_mod._rotation_head_to_camera(head)

    assert np.allclose(rotation @ rotation.T, np.eye(3), atol=1e-12)
    assert np.linalg.det(rotation) == pytest.approx(1.0, abs=1e-12)


def test_head_rotation_signs_match_the_schemas_convention():
    forward = np.array([0.0, 0.0, 1.0])

    # yaw > 0 = face turned toward the image right -> +x in the working frame.
    assert (geom_mod._rotation_head_to_camera(HeadPose(yaw=0.3)) @ forward)[0] > 0.0
    # pitch > 0 = chin up -> +y, which is UP in the working frame (y flipped
    # relative to schemas, see the function docstring).
    assert (geom_mod._rotation_head_to_camera(HeadPose(pitch=0.3)) @ forward)[1] > 0.0
    # roll is about the viewing axis, so it leaves the forward direction alone.
    assert np.allclose(geom_mod._rotation_head_to_camera(HeadPose(roll=0.3)) @ forward, forward)


def test_unknown_geom_params_are_rejected_with_the_valid_list():
    with pytest.raises(ValueError) as excinfo:
        _geom(iris_weigth=0.5)  # typo, one letter

    message = str(excinfo.value)
    assert "iris_weigth" in message
    assert "iris_weight" in message


def test_known_geom_params_override_the_defaults_and_are_coerced_to_float():
    backbone = _geom(iris_weight="0.9", max_eye_angle_deg=30)

    assert backbone.params.iris_weight == pytest.approx(0.9)
    assert isinstance(backbone.params.max_eye_angle_deg, float)
    # Untouched fields keep the documented defaults.
    assert backbone.params.iris_radius_mm == pytest.approx(5.85)


def test_default_construction_needs_no_config():
    backbone = MediaPipeGeomBackbone()

    assert backbone.cfg.name == "mediapipe_geom"
    assert backbone.params == geom_mod._GeomParams()


def test_warmup_exercises_the_geometry_path():
    backbone = MediaPipeGeomBackbone()

    backbone.warmup(iterations=1)  # must not raise on the synthetic landmarks


# ==========================================================================
# l2cs / gazetr -- construction and config, no weights required
# ==========================================================================


@pytest.mark.parametrize("cls", [L2CSBackbone, GazeTRBackbone], ids=["l2cs", "gazetr"])
def test_a_typo_in_backbone_params_is_reported_before_the_weights_are_looked_up(cls, tmp_path):
    cfg = BackboneConfig(
        name=cls.name, checkpoint=str(tmp_path / "absent.pt"), params={"input_sixe": 224}
    )

    with pytest.raises(ValueError) as excinfo:
        cls(cfg)

    message = str(excinfo.value)
    assert "input_sixe" in message
    assert "input_size" in message
    assert not isinstance(excinfo.value, CheckpointMissingError)


@pytest.mark.parametrize(
    "cls,module",
    [(L2CSBackbone, l2cs_mod), (GazeTRBackbone, gazetr_mod)],
    ids=["l2cs", "gazetr"],
)
def test_absent_weights_fail_at_build_time_with_a_copy_pasteable_message(cls, module, tmp_path):
    missing = tmp_path / "weights" / "absent.pt"
    cfg = BackboneConfig(name=cls.name, checkpoint=str(missing))

    with pytest.raises(CheckpointMissingError) as excinfo:
        build_backbone(cfg)

    message = str(excinfo.value)
    assert str(missing) in message
    assert "download_checkpoints.py" in message
    assert "mediapipe_geom" in message  # the documented fallback to run instead
    assert excinfo.value.path == missing
    assert module.DEFAULT_CHECKPOINT in module.DOWNLOAD_INSTRUCTIONS


@pytest.mark.parametrize("cls", [L2CSBackbone, GazeTRBackbone], ids=["l2cs", "gazetr"])
def test_construction_never_mutates_the_class_defaults_or_the_config(cls, tmp_path):
    defaults_before = dict(cls.DEFAULTS)
    params = {"input_size": 96}
    cfg = BackboneConfig(name=cls.name, checkpoint=str(tmp_path / "absent.pt"), params=params)

    with pytest.raises(CheckpointMissingError):
        cls(cfg)

    assert cls.DEFAULTS == defaults_before
    assert params == {"input_size": 96}


@pytest.mark.parametrize("cls", [L2CSBackbone, GazeTRBackbone], ids=["l2cs", "gazetr"])
def test_a_frame_without_a_face_crop_is_an_explicit_error(cls):
    # No weights needed: predict rejects the missing crop before any tensor work.
    backbone = object.__new__(cls)

    with pytest.raises(ValueError) as excinfo:
        backbone.predict(None, None, None, HeadPose())

    assert "face crop" in str(excinfo.value)
    assert "CROP_FAILED" in str(excinfo.value)


@pytest.mark.parametrize("cls", [L2CSBackbone, GazeTRBackbone], ids=["l2cs", "gazetr"])
def test_batch_prediction_names_the_frame_that_has_no_crop(cls):
    backbone = object.__new__(cls)
    observations = [
        FrameObservation(
            frame_id=0,
            t_ms=0,
            face_confidence=1.0,
            face_valid=True,
            face_crop=np.zeros((8, 8, 3), np.uint8),
        ),
        FrameObservation(frame_id=17, t_ms=125, face_confidence=0.2, face_valid=False),
    ]

    with pytest.raises(ValueError) as excinfo:
        backbone.predict_batch(observations)

    assert "17" in str(excinfo.value)


# ==========================================================================
# l2cs / gazetr -- tensor helpers and the sign conversion
# ==========================================================================


def _stub_l2cs(torch, yaw_bin: int, pitch_bin: int) -> L2CSBackbone:
    """An L2CS backbone whose trunk is replaced by a fixed one-hot response.

    Only the attributes ``_run`` reads are populated, so this exercises the
    de-binning and the sign conversion without the 95 MB checkpoint.
    """

    class _OneHot(torch.nn.Module):
        def forward(self, x):
            yaw = torch.full((x.shape[0], 90), -60.0)
            pitch = torch.full((x.shape[0], 90), -60.0)
            yaw[:, yaw_bin] = 60.0
            pitch[:, pitch_bin] = 60.0
            return yaw, pitch

    backbone = object.__new__(L2CSBackbone)
    backbone.cfg = BackboneConfig(name="l2cs", batch_size=2)
    backbone.params = dict(L2CSBackbone.DEFAULTS)
    backbone.params["input_size"] = 8
    backbone.device = torch.device("cpu")
    backbone._mean, backbone._std = l2cs_mod._normalisation(torch, "imagenet", backbone.device)
    centres = np.arange(90, dtype=np.float32) * 4.0 - 180.0
    backbone._bins = torch.from_numpy(np.radians(centres)).to(backbone.device)
    backbone.model = _OneHot()
    return backbone


def _stub_gazetr(torch, pitch: float, yaw: float) -> GazeTRBackbone:
    """A GazeTR backbone whose model is a constant (pitch, yaw) regressor."""

    class _Constant(torch.nn.Module):
        def forward(self, x):
            return torch.tensor([[pitch, yaw]]).repeat(x.shape[0], 1)

    backbone = object.__new__(GazeTRBackbone)
    backbone.cfg = BackboneConfig(name="gazetr", batch_size=2)
    backbone.params = dict(GazeTRBackbone.DEFAULTS)
    backbone.params["input_size"] = 8
    backbone.device = torch.device("cpu")
    backbone._mean, backbone._std = gazetr_mod._normalisation(torch, "none", backbone.device)
    backbone.model = _Constant()
    return backbone


@pytest.mark.parametrize(
    "yaw_bin,pitch_bin,expected_yaw_deg,expected_pitch_deg",
    [
        (45, 45, 0.0, 0.0),
        (50, 45, -20.0, 0.0),  # model yaw +20 deg (toward image left) -> ours -20
        (45, 50, 0.0, 20.0),  # model pitch +20 deg (up) -> ours +20, unflipped
        (40, 40, 20.0, -20.0),
    ],
)
def test_l2cs_flips_yaw_and_keeps_pitch_when_leaving_the_gazehub_convention(
    torch_mod, yaw_bin, pitch_bin, expected_yaw_deg, expected_pitch_deg
):
    backbone = _stub_l2cs(torch_mod, yaw_bin, pitch_bin)

    gaze = backbone._run([np.zeros((8, 8, 3), np.uint8)])[0]

    assert gaze.gaze_yaw_deg == pytest.approx(expected_yaw_deg, abs=0.05)
    assert gaze.gaze_pitch_deg == pytest.approx(expected_pitch_deg, abs=0.05)
    assert gaze.backbone == "l2cs"
    assert 0.0 <= gaze.confidence <= 1.0


def test_gazetr_reads_pitch_then_yaw_and_flips_the_yaw(torch_mod):
    backbone = _stub_gazetr(torch_mod, pitch=0.2, yaw=0.3)

    gaze = backbone._run([np.zeros((8, 8, 3), np.uint8)])[0]

    assert gaze.gaze_pitch == pytest.approx(0.2, abs=1e-6)
    assert gaze.gaze_yaw == pytest.approx(-0.3, abs=1e-6)
    assert gaze.confidence == pytest.approx(1.0)
    assert gaze.backbone == "gazetr"


def test_a_batch_reports_the_per_frame_share_of_its_cost(torch_mod):
    backbone = _stub_gazetr(torch_mod, pitch=0.1, yaw=0.0)
    crop = np.zeros((8, 8, 3), np.uint8)

    vectors = backbone._run([crop, crop, crop])

    assert len(vectors) == 3
    assert {v.inference_ms for v in vectors} == {vectors[0].inference_ms}
    assert vectors[0].inference_ms >= 0.0


@pytest.mark.parametrize("cls", [L2CSBackbone, GazeTRBackbone], ids=["l2cs", "gazetr"])
def test_an_empty_batch_produces_no_vectors(torch_mod, cls):
    backbone = object.__new__(cls)
    backbone.cfg = BackboneConfig(name=cls.name, batch_size=2)

    assert backbone.predict_batch([]) == []


def test_peak_mass_separates_a_confident_spike_from_a_split_distribution(torch_mod):
    spike = torch_mod.zeros(1, 90)
    spike[0, 10] = 1.0
    split = torch_mod.zeros(1, 90)
    split[0, 10] = split[0, 80] = 0.5
    flat = torch_mod.full((1, 90), 1.0 / 90.0)

    assert l2cs_mod._peak_mass(torch_mod, spike, 3).item() == pytest.approx(1.0, abs=1e-6)
    # Two far-apart modes put the expectation in the empty middle: no confidence.
    assert l2cs_mod._peak_mass(torch_mod, split, 3).item() == pytest.approx(0.0, abs=1e-6)
    assert l2cs_mod._peak_mass(torch_mod, flat, 3).item() < 0.1


@pytest.mark.parametrize("module", [l2cs_mod, gazetr_mod], ids=["l2cs", "gazetr"])
def test_an_unknown_normalisation_mode_is_rejected(torch_mod, module):
    with pytest.raises(ValueError) as excinfo:
        module._normalisation(torch_mod, "zscore", torch_mod.device("cpu"))

    assert "zscore" in str(excinfo.value)


@pytest.mark.parametrize("mode,expected_mean", [("imagenet", 0.485), ("none", 0.0)])
def test_normalisation_modes_carry_the_documented_statistics(torch_mod, mode, expected_mean):
    mean, std = l2cs_mod._normalisation(torch_mod, mode, torch_mod.device("cpu"))

    assert tuple(mean.shape) == (1, 3, 1, 1) and tuple(std.shape) == (1, 3, 1, 1)
    assert mean.flatten()[0].item() == pytest.approx(expected_mean, abs=1e-6)


def test_gazetr_reverses_the_channel_order_for_its_bgr_trained_weights(torch_mod):
    device = torch_mod.device("cpu")
    mean, std = gazetr_mod._normalisation(torch_mod, "none", device)
    crop = np.zeros((4, 4, 3), np.uint8)
    crop[..., 0], crop[..., 1], crop[..., 2] = 10, 20, 30  # RGB by contract

    as_bgr = gazetr_mod._to_batch(torch_mod, [crop], 4, "bgr", mean, std, device)
    as_rgb = gazetr_mod._to_batch(torch_mod, [crop], 4, "rgb", mean, std, device)

    assert [round(as_bgr[0, c].mean().item() * 255) for c in range(3)] == [30, 20, 10]
    assert [round(as_rgb[0, c].mean().item() * 255) for c in range(3)] == [10, 20, 30]


def test_an_unknown_channel_order_is_rejected(torch_mod):
    device = torch_mod.device("cpu")
    mean, std = gazetr_mod._normalisation(torch_mod, "none", device)

    with pytest.raises(ValueError) as excinfo:
        gazetr_mod._to_batch(
            torch_mod, [np.zeros((4, 4, 3), np.uint8)], 4, "grb", mean, std, device
        )

    assert "grb" in str(excinfo.value)


def test_l2cs_centre_crop_zooms_before_the_resize(torch_mod):
    # center_crop_ratio exists because a plain resize of an un-zoomed face is
    # what produced the -15.7 / -22.8 deg row in the module docstring.
    device = torch_mod.device("cpu")
    mean, std = l2cs_mod._normalisation(torch_mod, "none", device)
    crop = np.zeros((8, 8, 3), np.uint8)
    crop[2:6, 2:6] = 255  # only the central half is bright

    zoomed = l2cs_mod._to_batch(torch_mod, [crop], 4, 0.5, mean, std, device)
    whole = l2cs_mod._to_batch(torch_mod, [crop], 4, 1.0, mean, std, device)

    assert zoomed.mean().item() == pytest.approx(1.0, abs=1e-6)
    assert whole.mean().item() < zoomed.mean().item()


@pytest.mark.parametrize(
    "module,factory",
    [(l2cs_mod, "_l2cs_module_class"), (gazetr_mod, "_gazetr_module_class")],
    ids=["l2cs", "gazetr"],
)
def test_the_torch_module_class_is_defined_once_and_cached(torch_mod, module, factory):
    _import_optional("torchvision")
    build = getattr(module, factory)

    first = build()
    second = build()

    assert first is second
    assert module._MODULE_CLASS is first


# ==========================================================================
# l2cs -- the real published weights (skipped when absent)
# ==========================================================================


def test_l2cs_bin_centres_follow_the_published_binning(l2cs_backbone):
    bins = l2cs_backbone._bins

    assert len(bins) == 90
    assert math.degrees(bins[0].item()) == pytest.approx(-180.0, abs=1e-4)
    assert math.degrees(bins[45].item()) == pytest.approx(0.0, abs=1e-4)
    assert math.degrees(bins[1].item() - bins[0].item()) == pytest.approx(4.0, abs=1e-4)


def test_l2cs_loads_every_parameter_its_forward_pass_needs(l2cs_backbone):
    assert l2cs_backbone.missing_keys == []
    assert l2cs_backbone.model_size_bytes() > 0


def test_l2cs_and_mediapipe_geom_agree_on_the_real_face(l2cs_backbone, face_observation):
    """Cross-backbone check on the one image whose truth we know (~0, 0 deg).

    Two independent estimators with different native conventions; if either
    adapter forgot a sign flip the pitches point opposite ways by far more than
    the disagreement seen here.
    """
    torch_gaze = l2cs_backbone.predict_observation(face_observation)
    geom_gaze = MediaPipeGeomBackbone().predict_observation(face_observation)

    assert abs(torch_gaze.gaze_yaw_deg) < 20.0
    assert abs(torch_gaze.gaze_pitch_deg) < 20.0
    assert 0.0 <= torch_gaze.confidence <= 1.0
    assert torch_gaze.backbone == "l2cs"

    # Same sign, unless both are so close to zero that the sign is noise.
    pitches = (torch_gaze.gaze_pitch, geom_gaze.gaze_pitch)
    assert (
        torch_gaze.gaze_pitch * geom_gaze.gaze_pitch > 0.0
        or max(abs(p) for p in pitches) < math.radians(2.0)
    ), f"pitch signs disagree: {[round(math.degrees(p), 2) for p in pitches]}"
    assert abs(torch_gaze.gaze_pitch - geom_gaze.gaze_pitch) < math.radians(8.0)


def test_l2cs_batching_matches_single_frame_inference(l2cs_backbone, face_observation):
    single = l2cs_backbone.predict_observation(face_observation)

    batched = l2cs_backbone.predict_batch([face_observation, face_observation])

    assert len(batched) == 2
    assert batched[0].gaze_yaw == pytest.approx(single.gaze_yaw, abs=1e-5)
    assert batched[1].gaze_pitch == pytest.approx(single.gaze_pitch, abs=1e-5)
