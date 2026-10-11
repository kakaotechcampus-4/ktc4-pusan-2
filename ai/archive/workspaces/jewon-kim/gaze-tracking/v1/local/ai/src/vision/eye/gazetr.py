"""GazeTR-Hybrid gaze backbone (doc 3-2, candidate for the doc 3-3 comparison).

A faithful rebuild of the released model (Cheng & Lu, "Gaze Estimation using
Transformer", ICPR 2022 -- github.com/yihuacheng/GazeTR), parameter names
included, so ``GazeTR-H-ETH.pt`` loads into it unchanged:

* ``base_model``: a ResNet-18 (``BasicBlock``, ``[2, 2, 2, 2]``) whose classifier
  head is replaced by ``conv = Sequential(Conv2d(512, 32, 1), BatchNorm2d(32),
  ReLU)``.  There is no ``avgpool``/``fc``: the 224x224 input leaves a 7x7 map of
  32 channels, i.e. 49 tokens.
* ``encoder``: 6 layers of a DETR-style encoder with ``d_model = 32``, 8 heads,
  feed-forward 512, dropout 0.1, plus a final ``LayerNorm``.  The positional
  embedding is *added to q and k only*, never to the value -- that is what makes
  it DETR-style rather than stock ``nn.TransformerEncoder``, and it changes the
  numbers, so it is reimplemented here instead of borrowed.
* A learned ``cls_token`` is prepended to the 49 tokens and
  ``pos_embedding = nn.Embedding(50, 32)`` indexes all 50 positions.
* ``feed = Linear(32, 2)`` reads the class token and emits **(pitch, yaw) in
  radians** directly -- no bins, no degree conversion.

Preprocessing follows the released reader: ``cv2.imread`` (so **BGR**) followed
by ``ToTensor`` alone, i.e. plain [0, 1] scaling with **no ImageNet mean/std**.
Both facts are unusual enough to be config params (``channel_order``,
``normalize``) rather than silent constants; getting either wrong costs several
degrees without any visible error.

Sign conversion (doc: ``schemas`` module docstring)
---------------------------------------------------
GazeTR is trained on GazeHub-normalised labels, where the 3-D vector is
``(-cos(p)sin(y), -sin(p), -cos(p)cos(y))`` in an OpenCV camera frame.  A
positive model yaw therefore aims the gaze at the image **left** and a positive
model pitch aims **up**, so ``gaze_yaw = -yaw_model`` and
``gaze_pitch = +pitch_model``.  Same conversion as L2CS, same source convention.

torch is imported lazily inside this module.
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
DEFAULT_CHECKPOINT = "ai/models/gaze/backbone/GazeTR-H-ETH.pt"

DOWNLOAD_INSTRUCTIONS = f"""\
GazeTR needs the published GazeTR-H-ETH.pt (ETH-XGaze pre-trained, ~50 MB).

The authors publish it only through Google Drive / Baidu, so there is no stable
direct URL to automate; download it by hand:
  https://drive.google.com/file/d/1WEiKZ8Ga0foNmxM7xFabI4D5ajThWAWj/view
  (or Baidu pan.baidu.com/s/1GEbjbNgXvVkisVWGtTJm7g, code 1234)

Save it as {DEFAULT_CHECKPOINT}, then register it:
  ./.venv/Scripts/python.exe ai/tools/download_checkpoints.py gazetr --from-file <downloaded.pt>

That command only moves and checksums the file -- it never scrapes Drive, because
a Drive scrape silently yields an HTML page that looks like a checkpoint.
Until then run with `name: mediapipe_geom`."""

#: Cached nn.Module subclass; built on first use so torch stays out of import.
_MODULE_CLASS: Optional[type] = None


def _gazetr_module_class() -> type:
    """Define (once) the released GazeTR modules, matching its parameter names."""
    global _MODULE_CLASS
    if _MODULE_CLASS is not None:
        return _MODULE_CLASS

    import copy

    import torch
    import torch.nn as nn
    from torchvision.models.resnet import BasicBlock

    class ResNetMaps(nn.Module):
        """Released ``resnet.py``: a ResNet-18 trunk ending in a 1x1 projection."""

        def __init__(self, block: type, layers: Sequence[int], maps: int = 32) -> None:
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
            self.conv = nn.Sequential(
                nn.Conv2d(512, maps, 1), nn.BatchNorm2d(maps), nn.ReLU(inplace=True)
            )

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

        def forward(self, x: Any) -> Any:
            x = self.maxpool(self.relu(self.bn1(self.conv1(x))))
            x = self.layer4(self.layer3(self.layer2(self.layer1(x))))
            return self.conv(x)

    class TransformerEncoderLayer(nn.Module):
        """Post-norm encoder layer; the position code is added to q and k only."""

        def __init__(self, d_model: int, nhead: int, dim_feedforward: int, dropout: float) -> None:
            super().__init__()
            self.self_attn = nn.MultiheadAttention(d_model, nhead, dropout=dropout)
            self.linear1 = nn.Linear(d_model, dim_feedforward)
            self.dropout = nn.Dropout(dropout)
            self.linear2 = nn.Linear(dim_feedforward, d_model)
            self.norm1 = nn.LayerNorm(d_model)
            self.norm2 = nn.LayerNorm(d_model)
            self.dropout1 = nn.Dropout(dropout)
            self.dropout2 = nn.Dropout(dropout)
            self.activation = nn.ReLU(inplace=True)

        def forward(self, src: Any, pos: Any) -> Any:
            q = k = src + pos.unsqueeze(1).repeat(1, src.size(1), 1)
            src = self.norm1(src + self.dropout1(self.self_attn(q, k, value=src)[0]))
            fed = self.linear2(self.dropout(self.activation(self.linear1(src))))
            return self.norm2(src + self.dropout2(fed))

    class TransformerEncoder(nn.Module):
        def __init__(self, layer: nn.Module, num_layers: int, norm: Optional[nn.Module]) -> None:
            super().__init__()
            self.layers = nn.ModuleList(copy.deepcopy(layer) for _ in range(num_layers))
            self.num_layers = num_layers
            self.norm = norm

        def forward(self, src: Any, pos: Any) -> Any:
            out = src
            for layer in self.layers:
                out = layer(out, pos)
            return self.norm(out) if self.norm is not None else out

    class GazeTR(nn.Module):
        def __init__(
            self,
            maps: int = 32,
            nhead: int = 8,
            dim_feature: int = 49,
            dim_feedforward: int = 512,
            dropout: float = 0.1,
            num_layers: int = 6,
        ) -> None:
            super().__init__()
            self.base_model = ResNetMaps(BasicBlock, [2, 2, 2, 2], maps=maps)
            layer = TransformerEncoderLayer(maps, nhead, dim_feedforward, dropout)
            self.encoder = TransformerEncoder(layer, num_layers, nn.LayerNorm(maps))
            self.cls_token = nn.Parameter(torch.randn(1, 1, maps))
            self.pos_embedding = nn.Embedding(dim_feature + 1, maps)
            self.feed = nn.Linear(maps, 2)

        def forward(self, face: Any) -> Any:
            feature = self.base_model(face)
            batch = feature.size(0)
            # [B, C, H, W] -> [HW, B, C]: nn.MultiheadAttention here is
            # batch_first=False, exactly as in the released model.
            feature = feature.flatten(2).permute(2, 0, 1)
            feature = torch.cat([self.cls_token.repeat(1, batch, 1), feature], dim=0)
            positions = torch.arange(feature.size(0), device=feature.device)
            feature = self.encoder(feature, self.pos_embedding(positions))
            return self.feed(feature[0])

    _MODULE_CLASS = GazeTR
    return GazeTR


@register("gazetr")
class GazeTRBackbone(GazeBackbone):
    """GazeTR-Hybrid with the published ETH-XGaze weights (doc 3-2)."""

    version = "1.0.0"

    #: Config knobs, all overridable through ``BackboneConfig.params``.
    DEFAULTS: Dict[str, Any] = {
        "maps": 32,
        "nhead": 8,
        "num_layers": 6,
        "dim_feedforward": 512,
        "dropout": 0.1,
        "input_size": 224,
        #: Released reader uses cv2.imread, i.e. BGR channel order.
        "channel_order": "bgr",
        #: Released reader applies ToTensor only -- no ImageNet statistics.
        "normalize": "none",
        #: A plain regressor exposes no uncertainty; see ``predict``.
        "fixed_confidence": 1.0,
    }

    def __init__(self, cfg: Optional[BackboneConfig] = None) -> None:
        self.cfg = cfg if cfg is not None else BackboneConfig(name="gazetr")
        params = dict(self.DEFAULTS)
        unknown = sorted(set(self.cfg.params or {}) - set(params))
        if unknown:
            raise ValueError(f"gazetr: unknown backbone params {unknown}; valid: {sorted(params)}")
        params.update(self.cfg.params or {})
        self.params = params

        self.checkpoint_path = resolve_path(self.cfg.checkpoint or DEFAULT_CHECKPOINT)
        if not self.checkpoint_path.is_file():
            raise CheckpointMissingError("gazetr", self.checkpoint_path, DOWNLOAD_INSTRUCTIONS)

        import torch

        if int(self.cfg.num_threads) > 0:
            torch.set_num_threads(int(self.cfg.num_threads))

        self.device = torch.device(self.cfg.device or "cpu")
        size = int(params["input_size"])
        # The positional embedding has one row per token, so the token count --
        # and therefore the input size -- is fixed by the checkpoint.
        tokens = (size // 32) ** 2
        cls = _gazetr_module_class()
        self.model = cls(
            maps=int(params["maps"]),
            nhead=int(params["nhead"]),
            dim_feature=tokens,
            dim_feedforward=int(params["dim_feedforward"]),
            dropout=float(params["dropout"]),
            num_layers=int(params["num_layers"]),
        )
        state = load_checkpoint_state_dict(self.checkpoint_path, "gazetr")
        result = self.model.load_state_dict(state, strict=False)
        self.missing_keys, self.unexpected_keys = assert_required_keys_loaded(
            result,
            ("base_model", "encoder", "cls_token", "pos_embedding", "feed"),
            "gazetr",
            self.checkpoint_path,
        )
        # eval() matters more here than for a pure CNN: six dropout layers plus
        # attention dropout would otherwise randomise every prediction.
        self.model.eval().to(self.device)
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
        """Gaze from the face crop alone; head pose and landmarks are unused.

        ``confidence`` is the constant ``params["fixed_confidence"]``: GazeTR
        regresses two numbers with no uncertainty head, and inventing a
        pseudo-confidence from the magnitude of the output would be worse than
        admitting there is none.  Callers that gate on confidence must not treat
        this backbone's value as informative.
        """
        if face_crop is None:
            raise ValueError("gazetr needs a face crop; the frame has none (CROP_FAILED)")
        return self._run([np.asarray(face_crop)])[0]

    def predict_batch(self, observations: Sequence[FrameObservation]) -> List[GazeVector]:
        """Real batching, in ``cfg.batch_size`` chunks (doc 3-3 latency comparison)."""
        crops = []
        for obs in observations:
            if obs.face_crop is None:
                raise ValueError(f"gazetr: frame {obs.frame_id} has no face crop (CROP_FAILED)")
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
            str(self.params["channel_order"]),
            self._mean,
            self._std,
            self.device,
        )
        # Context manager rather than a decorator: a decorator would need torch
        # at module import time, which the doc-level rules forbid.
        with torch.inference_mode():
            out = self.model(batch)
        per_frame = (time.perf_counter() - t0) * 1000.0 / max(1, len(crops))
        confidence = float(np.clip(float(self.params["fixed_confidence"]), 0.0, 1.0))
        return [
            GazeVector(
                # Native order is (pitch, yaw); GazeHub yaw is positive toward
                # the image left, ours toward the image right.
                gaze_yaw=float(-out[i, 1].item()),
                gaze_pitch=float(out[i, 0].item()),
                confidence=confidence,
                backbone=self.name,
                inference_ms=per_frame,
            )
            for i in range(len(crops))
        ]


def _normalisation(torch: Any, mode: str, device: Any) -> Tuple[Any, Any]:
    """ImageNet statistics, or a no-op for models trained on raw [0, 1] pixels."""
    if mode == "imagenet":
        mean, std = [0.485, 0.456, 0.406], [0.229, 0.224, 0.225]
    elif mode == "none":
        mean, std = [0.0, 0.0, 0.0], [1.0, 1.0, 1.0]
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
    channel_order: str,
    mean: Any,
    std: Any,
    device: Any,
) -> Any:
    """Stack uint8 RGB crops into the normalised NCHW batch the model expects.

    ``FrameObservation`` crops are RGB by contract, so a model trained on
    ``cv2.imread`` output needs the channels reversed here.
    """
    stacked = np.stack([np.ascontiguousarray(c[:, :, :3]) for c in crops])
    if channel_order == "bgr":
        stacked = stacked[:, :, :, ::-1]
    elif channel_order != "rgb":
        raise ValueError(f"unknown channel_order {channel_order!r}; expected 'rgb' or 'bgr'")
    tensor = (
        torch.from_numpy(np.ascontiguousarray(stacked))
        .to(device)
        .permute(0, 3, 1, 2)
        .float()
        .div_(255.0)
    )
    if tensor.shape[-2:] != (size, size):
        # antialias=True matches PIL/cv2 area behaviour on a downscale; without
        # it a 448 -> 224 crop aliases and shifts the angle by a degree or so.
        tensor = torch.nn.functional.interpolate(
            tensor, size=(size, size), mode="bilinear", align_corners=False, antialias=True
        )
    return (tensor - mean) / std
