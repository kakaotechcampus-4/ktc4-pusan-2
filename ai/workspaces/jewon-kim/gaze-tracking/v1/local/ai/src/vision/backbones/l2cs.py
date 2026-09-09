"""L2CS-Net gaze backbone (doc 3-2, candidate for the doc 3-3 comparison).

Architecture and pre/post-processing follow the released implementation
(Abdelrahman et al., "L2CS-Net: Fine-Grained Gaze Estimation in Unconstrained
Environments", 2022 -- github.com/Ahmednull/L2CS-Net) closely enough that the
published Gaze360 weights load and mean what they meant there:

* ResNet-50 trunk (``torchvision`` ``Bottleneck``, layers ``[3, 4, 6, 3]``) with
  the stock ``conv1/bn1/layer1..4`` parameter names, then two independent linear
  heads ``fc_yaw_gaze`` / ``fc_pitch_gaze`` of ``num_bins`` logits each.
* ``fc_finetune`` exists in the released module and therefore in the released
  state dict, but is never called by ``forward``.  It is reconstructed only so
  the checkpoint loads without spurious "unexpected key" noise.
* Prediction = softmax expectation over bin centres.  Training binned the angles
  with ``numpy.digitize(..., arange(-180, 180, 4))``, so bin ``i`` maps to
  ``4*i - 180`` degrees; 90 bins cover +/-180 deg.  This is exactly the
  arithmetic in the released ``pipeline.py``.
* Input is the face crop resized to 448x448, ImageNet-normalised RGB.  That is
  the released *inference* transform (``ToPILImage -> Resize(448) -> ToTensor ->
  Normalize``); ``AdaptiveAvgPool2d`` makes the 14x14 feature map work.  Training
  instead used ``Resize(448) + CenterCrop(224)``, i.e. a 2x centre zoom into the
  face.  Both are supported and both behave sensibly, but a *plain* 224 resize
  does not -- measured on ``tests/fixtures/face.jpg``, whose subject looks into
  the lens so the truth is about (0, 0) deg::

      input_size 448, center_crop_ratio 1.0   yaw  +2.20  pitch  -0.23  conf 0.98  565 ms
      input_size 224, center_crop_ratio 0.5   yaw  +2.75  pitch  +3.63  conf 1.00  228 ms
      input_size 224, center_crop_ratio 1.0   yaw -15.68  pitch -22.80  conf 0.18  195 ms

  The default is the released inference path.  The 224/0.5 pair is the faithful
  training recipe and is 2.5x cheaper; the third row is what happens when the
  face is not zoomed and is why ``center_crop_ratio`` exists at all.  Timings are
  one CPU with ``num_threads=4``, and all three blow the doc-7 125 ms budget.

Sign conversion (doc: ``schemas`` module docstring)
---------------------------------------------------
L2CS emits the GazeHub/MPII 2-D convention, whose drawing code is
``dx = -sin(yaw)*cos(pitch)``, ``dy = -sin(pitch)`` in image pixels.  So a
positive model yaw points the gaze toward the image **left** and a positive
model pitch points **up**.  Our convention has yaw > 0 toward the image right
and pitch > 0 up, hence ``gaze_yaw = -yaw_model`` and ``gaze_pitch = +pitch_model``.

torch is imported lazily inside this module: ``mediapipe_geom`` must keep
working on a machine with no torch installed.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from vision.backbones.base import (
    CheckpointMissingError,
    GazeBackbone,
    assert_required_keys_loaded,
    load_checkpoint_state_dict,
)
from vision.backbones.registry import register
from vision.config import BackboneConfig, resolve_path
from vision.schemas import FrameObservation, GazeVector, HeadPose

#: Where ``ai/tools/download_checkpoints.py`` puts the weights.
DEFAULT_CHECKPOINT = "ai/models/gaze/backbone/L2CSNet_gaze360.pkl"

DOWNLOAD_INSTRUCTIONS = f"""\
L2CS-Net needs the published Gaze360 weights (L2CSNet_gaze360.pkl, 95849977 bytes).

  ./.venv/Scripts/python.exe ai/tools/download_checkpoints.py l2cs

or fetch it by hand and save it as {DEFAULT_CHECKPOINT}:
  official (Google Drive folder, manual click-through)
    https://drive.google.com/drive/folders/17p6ORr-JQJcw-eYtG2WGNiuS_qVKwdWd
  community mirror (direct, sha256-pinned by the downloader)
    https://huggingface.co/dorni/SpeakerVid-5M-data-curation-models/resolve/5c6e04d7fa3321e6228e79162f8ec98466bf308a/L2CSNet_gaze360.pkl

Then point ai/configs/gaze_backbone.yaml at it, or leave `checkpoint: null` to
use the default path above.  Until then run with `name: mediapipe_geom`."""

#: Cached nn.Module subclass; built on first use so torch stays out of import.
_MODULE_CLASS: Optional[type] = None


def _l2cs_module_class() -> type:
    """Define (once) the released L2CS module on top of torchvision's Bottleneck."""
    global _MODULE_CLASS
    if _MODULE_CLASS is not None:
        return _MODULE_CLASS

    import torch.nn as nn
    from torchvision.models.resnet import Bottleneck

    class L2CS(nn.Module):
        def __init__(self, block: type, layers: Sequence[int], num_bins: int) -> None:
            super().__init__()
            self.inplanes = 64
            self.conv1 = nn.Conv2d(3, 64, kernel_size=7, stride=2, padding=3, bias=False)
            self.bn1 = nn.BatchNorm2d(64)
            self.relu = nn.ReLU(inplace=True)
            self.maxpool = nn.MaxPool2d(kernel_size=3, stride=2, padding=1)
            self.layer1 = self._make_layer(block, 64, layers[0])
            self.layer2 = self._make_layer(block, 128, layers[1], stride=2)
            self.layer3 = self._make_layer(block, 256, layers[2], stride=2)
            self.layer4 = self._make_layer(block, 512, layers[3], stride=2)
            self.avgpool = nn.AdaptiveAvgPool2d((1, 1))
            self.fc_yaw_gaze = nn.Linear(512 * block.expansion, num_bins)
            self.fc_pitch_gaze = nn.Linear(512 * block.expansion, num_bins)
            #: Unused by forward; present in the released checkpoint.
            self.fc_finetune = nn.Linear(512 * block.expansion + 3, 3)

        def _make_layer(self, block: type, planes: int, blocks: int, stride: int = 1) -> Any:
            downsample = None
            if stride != 1 or self.inplanes != planes * block.expansion:
                downsample = nn.Sequential(
                    nn.Conv2d(
                        self.inplanes, planes * block.expansion, kernel_size=1, stride=stride, bias=False
                    ),
                    nn.BatchNorm2d(planes * block.expansion),
                )
            layers = [block(self.inplanes, planes, stride, downsample)]
            self.inplanes = planes * block.expansion
            layers.extend(block(self.inplanes, planes) for _ in range(1, blocks))
            return nn.Sequential(*layers)

        def forward(self, x: Any) -> Tuple[Any, Any]:
            x = self.maxpool(self.relu(self.bn1(self.conv1(x))))
            x = self.layer4(self.layer3(self.layer2(self.layer1(x))))
            x = self.avgpool(x).flatten(1)
            return self.fc_yaw_gaze(x), self.fc_pitch_gaze(x)

    L2CS.BLOCK = Bottleneck  # type: ignore[attr-defined]
    _MODULE_CLASS = L2CS
    return L2CS


@register("l2cs")
class L2CSBackbone(GazeBackbone):
    """ResNet-50 L2CS-Net with the published Gaze360 weights (doc 3-2)."""

    version = "1.0.0"

    #: Config knobs, all overridable through ``BackboneConfig.params``.
    DEFAULTS: Dict[str, Any] = {
        "num_bins": 90,
        "bin_width_deg": 4.0,
        "bin_offset_deg": -180.0,
        "input_size": 448,
        #: Central fraction of the crop kept before the resize.  1.0 = the
        #: released inference pipeline; 0.5 with input_size 224 reproduces the
        #: training-time ``Resize(448) + CenterCrop(224)`` at 2.5x the speed.
        #: See the module docstring for the measured difference.
        "center_crop_ratio": 1.0,
        "normalize": "imagenet",
        #: Half-width, in bins, of the window whose softmax mass becomes the
        #: reported confidence.  3 bins = +/-12 deg around the expectation.
        "confidence_window_bins": 3,
    }

    def __init__(self, cfg: Optional[BackboneConfig] = None) -> None:
        self.cfg = cfg if cfg is not None else BackboneConfig(name="l2cs")
        params = dict(self.DEFAULTS)
        unknown = sorted(set(self.cfg.params or {}) - set(params))
        if unknown:
            raise ValueError(f"l2cs: unknown backbone params {unknown}; valid: {sorted(params)}")
        params.update(self.cfg.params or {})
        self.params = params

        self.checkpoint_path = resolve_path(self.cfg.checkpoint or DEFAULT_CHECKPOINT)
        if not self.checkpoint_path.is_file():
            raise CheckpointMissingError("l2cs", self.checkpoint_path, DOWNLOAD_INSTRUCTIONS)

        import torch

        if int(self.cfg.num_threads) > 0:
            # doc 7 budgets 125 ms/frame on CPU; thread count is the single
            # biggest lever and has to be reproducible from the config.
            torch.set_num_threads(int(self.cfg.num_threads))

        self.device = torch.device(self.cfg.device or "cpu")
        cls = _l2cs_module_class()
        self.model = cls(cls.BLOCK, [3, 4, 6, 3], int(params["num_bins"]))
        state = load_checkpoint_state_dict(self.checkpoint_path, "l2cs")
        result = self.model.load_state_dict(state, strict=False)
        self.missing_keys, self.unexpected_keys = assert_required_keys_loaded(
            result,
            ("conv1", "bn1", "layer", "fc_yaw_gaze", "fc_pitch_gaze"),
            "l2cs",
            self.checkpoint_path,
        )
        self.model.eval().to(self.device)

        # Bin centres in radians, in the model's own sign convention.
        idx = np.arange(int(params["num_bins"]), dtype=np.float32)
        centres = idx * float(params["bin_width_deg"]) + float(params["bin_offset_deg"])
        self._bins = torch.from_numpy(np.radians(centres)).to(self.device)
        self._mean, self._std = _normalisation(torch, str(params["normalize"]), self.device)

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
        """Gaze from the face crop alone; head pose and landmarks are unused."""
        if face_crop is None:
            raise ValueError("l2cs needs a face crop; the frame has none (CROP_FAILED)")
        return self._run([np.asarray(face_crop)])[0]

    def predict_batch(self, observations: Sequence[FrameObservation]) -> List[GazeVector]:
        """Real batching, in ``cfg.batch_size`` chunks (doc 3-3 latency comparison)."""
        crops = []
        for obs in observations:
            if obs.face_crop is None:
                raise ValueError(f"l2cs: frame {obs.frame_id} has no face crop (CROP_FAILED)")
            crops.append(np.asarray(obs.face_crop))
        out: List[GazeVector] = []
        step = max(1, int(self.cfg.batch_size))
        for start in range(0, len(crops), step):
            out.extend(self._run(crops[start : start + step]))
        return out

    def model_size_bytes(self) -> int:
        return self.checkpoint_path.stat().st_size

    def close(self) -> None:
        self.model = None

    # -- internals --------------------------------------------------------

    def _run(self, crops: Sequence[np.ndarray]) -> List[GazeVector]:
        import torch

        t0 = time.perf_counter()
        batch = _to_batch(
            torch,
            crops,
            int(self.params["input_size"]),
            float(self.params["center_crop_ratio"]),
            self._mean,
            self._std,
            self.device,
        )
        # inference_mode as a context manager, not a decorator: a decorator would
        # need torch at module import time, which doc "non-negotiables" forbids.
        with torch.inference_mode():
            yaw_logits, pitch_logits = self.model(batch)
            yaw_p = torch.softmax(yaw_logits, dim=1)
            pitch_p = torch.softmax(pitch_logits, dim=1)
            yaw = (yaw_p * self._bins).sum(dim=1)
            pitch = (pitch_p * self._bins).sum(dim=1)
            conf = torch.sqrt(
                _peak_mass(torch, yaw_p, int(self.params["confidence_window_bins"]))
                * _peak_mass(torch, pitch_p, int(self.params["confidence_window_bins"]))
            )
        per_frame = (time.perf_counter() - t0) * 1000.0 / max(1, len(crops))
        return [
            GazeVector(
                # L2CS yaw is positive toward the image left; ours is positive right.
                gaze_yaw=float(-yaw[i].item()),
                gaze_pitch=float(pitch[i].item()),
                confidence=float(np.clip(conf[i].item(), 0.0, 1.0)),
                backbone=self.name,
                inference_ms=per_frame,
            )
            for i in range(len(crops))
        ]


def _normalisation(torch: Any, mode: str, device: Any) -> Tuple[Any, Any]:
    """ImageNet statistics, or a no-op for models trained on raw [0, 1] pixels."""
    if mode == "imagenet":
        mean = [0.485, 0.456, 0.406]
        std = [0.229, 0.224, 0.225]
    elif mode == "none":
        mean = [0.0, 0.0, 0.0]
        std = [1.0, 1.0, 1.0]
    else:
        raise ValueError(f"unknown normalize mode {mode!r}; expected 'imagenet' or 'none'")
    shape = (1, 3, 1, 1)
    return (
        torch.tensor(mean, dtype=torch.float32, device=device).reshape(shape),
        torch.tensor(std, dtype=torch.float32, device=device).reshape(shape),
    )


def _to_batch(
    torch: Any,
    crops: Sequence[np.ndarray],
    size: int,
    crop_ratio: float,
    mean: Any,
    std: Any,
    device: Any,
) -> Any:
    """Stack uint8 HWC crops into a normalised NCHW float batch.

    ``antialias=True`` matches PIL's resize, which is what the released
    transforms use; without it an upsample-then-downsample crop shifts the
    predicted angle by a degree or so.
    """
    stacked = np.stack([np.ascontiguousarray(c[:, :, :3]) for c in crops])
    tensor = torch.from_numpy(stacked).to(device).permute(0, 3, 1, 2).float().div_(255.0)
    if 0.0 < crop_ratio < 1.0:
        h, w = tensor.shape[-2:]
        kh, kw = max(1, int(round(h * crop_ratio))), max(1, int(round(w * crop_ratio)))
        top, left = (h - kh) // 2, (w - kw) // 2
        tensor = tensor[:, :, top : top + kh, left : left + kw]
    if tensor.shape[-2:] != (size, size):
        tensor = torch.nn.functional.interpolate(
            tensor, size=(size, size), mode="bilinear", align_corners=False, antialias=True
        )
    return (tensor - mean) / std


def _peak_mass(torch: Any, probs: Any, half_window: int) -> Any:
    """Softmax mass within +/-``half_window`` bins of the expectation.

    A binned regressor has no calibrated uncertainty head, but a bimodal or flat
    bin distribution is a real signal that the crop is ambiguous, and this
    collapses it to one number in [0, 1].
    """
    idx = torch.arange(probs.shape[1], device=probs.device, dtype=probs.dtype)
    centre = (probs * idx).sum(dim=1, keepdim=True)
    window = (idx.unsqueeze(0) - centre).abs() <= float(half_window)
    return (probs * window).sum(dim=1)
