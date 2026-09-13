"""Gaze backbone interface (doc 3-2).

A backbone turns one preprocessed frame into a :class:`~vision.schemas.GazeVector`
expressed in the camera convention fixed at the top of ``schemas.py``.  Every
concrete backbone owns the conversion from whatever its native convention is;
nothing downstream is allowed to flip a sign.

This module deliberately imports nothing heavier than numpy.  ``mediapipe_geom``
is the default backbone and must run on a machine where torch was never
installed, so torch may only be imported lazily inside ``l2cs`` / ``gazetr``.
"""

from __future__ import annotations

import abc
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from vision.schemas import FrameObservation, GazeVector, HeadPose

#: torch.save writes a zip archive (torch >= 1.6) or, for older releases, a
#: raw pickle.  Anything else under a .pkl/.pt name is an error page.
_PICKLE_MAGIC = bytes([0x80])
_ZIP_MAGIC = b"PK" + bytes([0x03, 0x04])


class BackboneError(RuntimeError):
    """Base class for every recoverable backbone failure."""


class CheckpointMissingError(BackboneError):
    """Weights for a pretrained backbone are absent.

    doc 3-2 forbids a silent fallback to another backbone: a run whose model
    quietly changed is unreproducible.  The exception therefore carries the
    resolved path and a copy-pasteable download command so a CLI can print
    ``str(exc)`` verbatim instead of a stack trace.
    """

    def __init__(self, backbone: str, path: Path, instructions: str) -> None:
        self.backbone = backbone
        self.path = Path(path)
        self.instructions = instructions.strip()
        super().__init__(
            f"{backbone}: gaze checkpoint not found at\n  {self.path}\n\n{self.instructions}"
        )


class CheckpointCorruptError(BackboneError):
    """The checkpoint file exists but is not a usable state dict.

    Split out from :class:`CheckpointMissingError` because the remedy differs:
    a corrupt file must be deleted before re-downloading, and the usual cause
    is a Google-Drive HTML interstitial saved under a ``.pkl`` name.
    """


def load_checkpoint_state_dict(path: Path, backbone: str) -> Dict[str, Any]:
    """Read a published checkpoint into a flat ``{param_name: tensor}`` dict.

    Handles the three shapes these releases ship in: a bare state dict, a
    ``{"model_state_dict"/"state_dict": ...}`` wrapper, and ``DataParallel``'s
    ``module.`` prefix.  A file that is not a torch archive at all -- almost
    always a Google-Drive HTML interstitial saved under a ``.pkl`` name -- is
    reported as corrupt rather than as a mysterious unpickling error.  Every
    failure leaves here as a :class:`BackboneError`, including a file that torch
    could not open at all, so a caller never has to also catch ``OSError``.

    torch is imported here, inside the call, so the module stays importable
    without torch.
    """
    import torch

    try:
        # weights_only refuses arbitrary pickled globals; these releases are
        # plain tensor dicts, so it should always succeed.
        raw = torch.load(str(path), map_location="cpu", weights_only=True)
    except Exception as exc:  # noqa: BLE001 - re-raised as a typed error below
        try:
            # Through a handle, not ``read_bytes()``: the whole point is to look
            # at four bytes, and slurping first pulled a 95 MB checkpoint into
            # memory to do it -- on a machine that has just failed to load it.
            with path.open("rb") as handle:
                head = handle.read(4)
        except OSError as read_exc:
            # The file is gone or unreadable (a checkpoint deleted mid-run, a
            # dropped network share).  Previously this raised a bare
            # FileNotFoundError straight out of ``read_bytes`` -- untyped, so no
            # caller catching BackboneError saw it, and it buried torch's own
            # reason, which is the one that says what was actually wrong.
            raise CheckpointCorruptError(
                f"{backbone}: cannot read {path}: {read_exc}"
            ) from exc
        if head[:1] != _PICKLE_MAGIC and head != _ZIP_MAGIC:
            raise CheckpointCorruptError(
                f"{backbone}: {path} is not a torch checkpoint (first bytes {head!r}). "
                "A Google-Drive download that returned an HTML page looks exactly "
                "like this. Delete the file and re-run ai/tools/download_checkpoints.py."
            ) from exc
        raise CheckpointCorruptError(f"{backbone}: cannot read {path}: {exc}") from exc

    if isinstance(raw, dict):
        for key in ("model_state_dict", "state_dict", "model"):
            inner = raw.get(key)
            if isinstance(inner, dict):
                raw = inner
                break
    if not isinstance(raw, dict):
        raise CheckpointCorruptError(
            f"{backbone}: {path} holds {type(raw).__name__}, not a state dict"
        )
    return {k[len("module.") :] if k.startswith("module.") else k: v for k, v in raw.items()}


def assert_required_keys_loaded(
    result: Any, required_prefixes: Sequence[str], backbone: str, path: Path
) -> Tuple[List[str], List[str]]:
    """Fail loudly when a checkpoint left a parameter the forward pass uses unset.

    ``strict=False`` is deliberate -- released checkpoints legitimately carry
    extra keys, such as L2CS's vestigial ``fc_finetune`` -- but a *missing* key
    means random weights would be used for real inference, and that must never
    pass silently.
    """
    missing = [k for k in result.missing_keys if k.startswith(tuple(required_prefixes))]
    if missing:
        raise CheckpointCorruptError(
            f"{backbone}: {path} is missing {len(missing)} parameter(s) the forward pass "
            f"needs, e.g. {missing[:5]}. Wrong architecture or a truncated download."
        )
    return list(result.missing_keys), list(result.unexpected_keys)


class GazeBackbone(abc.ABC):
    """One gaze estimator (doc 3-2).

    Subclasses are constructed from a :class:`~vision.config.BackboneConfig` by
    :func:`vision.backbones.registry.build_backbone` and are single-frame
    stateful only in the sense of holding a loaded model; ``predict`` itself is
    pure so that offline evaluation is reproducible.
    """

    #: Registry key; also what lands in ``AiVersion.gaze_backbone``.
    name: str = "unknown"
    #: Bumped whenever the numeric output of this backbone changes.
    version: str = "0.0.0"

    @abc.abstractmethod
    def predict(
        self,
        face_crop: Optional[np.ndarray],
        left_eye_crop: Optional[np.ndarray],
        right_eye_crop: Optional[np.ndarray],
        head_pose: Optional[HeadPose],
        *,
        landmarks: Optional[np.ndarray] = None,
        blendshapes: Optional[Dict[str, float]] = None,
        image_size: Optional[Tuple[int, int]] = None,
    ) -> GazeVector:
        """Estimate gaze for one frame (doc 3-2).

        Crops are ``uint8`` RGB.  ``landmarks`` is the ``(478, 3)`` normalised
        MediaPipe array and ``image_size`` is ``(width, height)`` of the source
        frame -- both are ``None`` for backbones that only look at pixels.
        Implementations raise :class:`ValueError` when the inputs they actually
        need are missing rather than returning a plausible-looking zero.
        """

    def predict_observation(self, obs: FrameObservation) -> GazeVector:
        """Adapter from the preprocess contract to :meth:`predict` (doc 3-1)."""
        return self.predict(
            obs.face_crop,
            obs.left_eye_crop,
            obs.right_eye_crop,
            obs.head_pose,
            landmarks=obs.landmarks,
            blendshapes=obs.blendshapes,
            image_size=obs.image_size,
        )

    def predict_batch(self, observations: Sequence[FrameObservation]) -> List[GazeVector]:
        """Default = a loop.  Torch backbones override this with a real batch."""
        return [self.predict_observation(obs) for obs in observations]

    def warmup(self, iterations: int = 3) -> None:
        """Pay lazy-init costs (thread pools, cuDNN/XNNPACK plans) up front.

        The first inference of a torch model is several times slower than the
        steady state, which would otherwise poison the p95 latency reported by
        doc 7's release gate.  Failures are *not* swallowed: a missing
        checkpoint must surface here rather than mid-session.
        """
        obs = self._warmup_observation()
        for _ in range(max(0, int(iterations))):
            self.predict_observation(obs)

    def model_size_bytes(self) -> int:
        """On-disk weight size, for the doc-23 Experiment 1 comparison table.

        ``0`` means the backbone carries no learned weights of its own.
        """
        return 0

    def close(self) -> None:
        """Release any held resources.  Safe to call more than once."""

    def __enter__(self) -> "GazeBackbone":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r} version={self.version!r}>"

    # -- helpers ----------------------------------------------------------

    def _warmup_observation(self) -> FrameObservation:
        """A synthetic frame that exercises the pixel path only.

        Noise rather than zeros because a constant image can be short-circuited
        by some BLAS paths, which would understate the warmup cost.  The seed is
        fixed so a warmup never perturbs a seeded experiment.  Backbones that
        need landmarks override this.
        """
        rng = np.random.default_rng(0)
        crop = rng.integers(0, 256, size=(224, 224, 3), dtype=np.uint8)
        eye = rng.integers(0, 256, size=(36, 60, 3), dtype=np.uint8)
        return FrameObservation(
            frame_id=-1,
            t_ms=0,
            face_confidence=1.0,
            face_valid=True,
            head_pose=HeadPose(),
            face_crop=crop,
            left_eye_crop=eye,
            right_eye_crop=eye.copy(),
            image_size=(640, 480),
        )

    @staticmethod
    def _elapsed_ms(t0: float) -> float:
        return (time.perf_counter() - t0) * 1000.0
