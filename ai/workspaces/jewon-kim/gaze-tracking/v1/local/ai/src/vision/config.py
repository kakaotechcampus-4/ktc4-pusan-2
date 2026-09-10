"""Typed configuration loaded from ``ai/configs/*.yaml``.

Every tunable number in the pipeline lives here so that an experiment can be
reproduced from a config hash alone (doc 18).  Defaults match the values fixed
in the design document; the YAML files only need to carry overrides.
"""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

#: Repository root = three levels above this file (ai/src/vision/config.py).
REPO_ROOT = Path(__file__).resolve().parents[3]
AI_ROOT = REPO_ROOT / "ai"
CONFIG_DIR = AI_ROOT / "configs"
MODELS_DIR = AI_ROOT / "models"
DATASETS_DIR = AI_ROOT / "datasets"
REPORTS_DIR = AI_ROOT / "reports"


# --------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------


@dataclass
class PreprocessConfig:
    """doc 3-1: input standardisation."""

    analysis_fps: float = 8.0
    face_crop_size: int = 224
    eye_crop_size: List[int] = field(default_factory=lambda: [60, 36])  # (w, h)
    #: Extra margin around the landmark bbox, as a fraction of bbox size.
    face_crop_margin: float = 0.25
    #: Eye crop width as a multiple of the inter-corner eye distance.
    eye_crop_scale: float = 2.2
    #: Below this MediaPipe face score the frame is UNCERTAIN, no inference.
    min_face_confidence: float = 0.50
    #: Face bbox area / frame area below which the face is "too small".
    min_face_area_ratio: float = 0.010
    #: Eye aspect ratio below which we treat the eyes as closed.
    min_eye_openness: float = 0.12
    #: Fraction of the frame border a bbox may touch before OUT_OF_FRAME.
    border_tolerance_px: int = 2
    #: Rotate the face crop so the eye line is horizontal.
    align_face_roll: bool = True
    num_faces: int = 1
    landmarker_model_path: str = "ai/models/face_landmarker.task"
    #: MediaPipe running mode: IMAGE keeps offline runs deterministic.
    running_mode: str = "IMAGE"


@dataclass
class BackboneConfig:
    """doc 3-2 / 3-3: which gaze backbone to run and where its weights live."""

    name: str = "mediapipe_geom"
    checkpoint: Optional[str] = None
    device: str = "cpu"
    #: torch threads; 0 leaves the torch default alone.
    num_threads: int = 0
    batch_size: int = 1
    #: Per-backbone extras (bins, input size, ...).
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CalibrationConfig:
    """doc 5-1 .. 5-4."""

    #: Seconds of CAMERA / BOTTOM staring at take start.
    camera_seconds: float = 2.0
    bottom_seconds: float = 2.0
    #: Hard floor on usable samples per class before we even try to fit.
    min_samples_per_class: int = 10
    #: doc 5-2 pass rule.
    min_loo_accuracy: float = 0.85
    #: Centroid distance floor, in standardised feature units.
    min_centroid_distance: float = 0.60
    #: Fisher-like separability floor (centroid distance / pooled spread).
    min_separability: float = 1.00
    #: Feature set: A = gaze only, B = + head pose, C = + relative-to-centroid.
    feature_set: str = "C"
    #: sklearn LogisticRegression settings (doc 5-3).
    C: float = 1.0
    max_iter: int = 1000
    class_weight: str = "balanced"
    random_seed: int = 42
    #: doc 5-4 UNCERTAIN rule; swept on validation.
    p_max_threshold: float = 0.70
    margin_threshold: float = 0.20
    #: Warn (do not fail) when BOTTOM pitch is not below CAMERA pitch.
    check_pitch_ordering: bool = True


@dataclass
class PlacementConfig:
    """Camera-placement check that runs before calibration.

    Not in the design document: it turns doc 19's ``webcam이 화면 아래/옆에 위치``
    failure bucket into an up-front check, because that geometry silently
    inverts the meaning of CAMERA vs BOTTOM rather than degrading it.
    """

    #: Seconds spent on each of the two cues (look at lens / look at screen centre).
    camera_seconds: float = 2.0
    screen_seconds: float = 2.0
    min_samples_per_target: int = 8
    #: Gaze samples below this backbone confidence are dropped.
    min_sample_confidence: float = 0.30
    #: "learned" fits the same LR family as doc 5-3 and reads the placement off
    #: the decision boundary; "geometric" applies fixed angular thresholds.
    mode: str = "learned"
    #: learned mode: the two cues must be separable this well to be trusted,
    #: mirroring the doc 5-2 calibration gate.
    min_loo_accuracy: float = 0.85
    C: float = 1.0
    max_iter: int = 1000
    random_seed: int = 42
    #: How dominant one axis must be before we call the placement vertical or
    #: horizontal; below this the two axes are treated as ambiguous.
    min_axis_dominance: float = 1.30
    #: geometric mode (and the sanity floor for learned mode).
    min_delta_deg: float = 4.0
    min_separation: float = 1.00


@dataclass
class TemporalConfig:
    """doc 6: EMA + weighted voting + hysteresis."""

    window_frames: int = 8
    #: EMA smoothing factor on p_bottom; higher = more reactive.
    ema_alpha: float = 0.35
    #: Newest frame weight relative to oldest in the voting window.
    vote_recency_weight: float = 2.0
    #: Probability a smoothed state must exceed to arm a transition.
    enter_bottom_threshold: float = 0.60
    enter_camera_threshold: float = 0.60
    #: Dwell time before the armed transition is committed (doc 6-1).
    to_bottom_dwell_ms: int = 600
    to_camera_dwell_ms: int = 400
    #: Consecutive invalid-face time before the state falls back to UNCERTAIN.
    uncertain_dwell_ms: int = 800
    #: Emit a heartbeat event every N ms even without a transition.
    heartbeat_ms: int = 1000
    #: Invalid frames decay the EMA toward 0.5 instead of freezing it.
    decay_on_invalid: bool = True


@dataclass
class ReleaseGateConfig:
    """doc 7: PoC pass line."""

    min_macro_f1: float = 0.85
    min_bottom_recall: float = 0.90
    min_per_user_f1: float = 0.75
    max_uncertain_ratio: float = 0.20
    max_calibration_failure_rate: float = 0.10
    #: 8 FPS leaves a 125 ms budget per frame.
    max_p95_latency_ms: float = 125.0


@dataclass
class ProtocolCondition:
    """One recorded block in the collection protocol (doc 4-1)."""

    key: str
    seconds: float
    #: Fixed cue for a static block, or empty when the cue alternates.
    cue: str = ""
    #: For alternating blocks: seconds each cue is held.
    alternate_period_s: float = 0.0
    speaking: bool = False
    head_motion: bool = False
    prompt: str = ""


@dataclass
class CollectionConfig:
    """doc 4-1: the guided recording protocol."""

    record_fps: int = 30
    frame_width: int = 1280
    frame_height: int = 720
    #: Frames within this window of a cue change are labelled IGNORE.
    transition_guard_ms: int = 500
    countdown_s: float = 3.0
    conditions: List[ProtocolCondition] = field(default_factory=list)


@dataclass
class VisionConfig:
    """The whole vision configuration, as one hashable object."""

    preprocess: PreprocessConfig = field(default_factory=PreprocessConfig)
    backbone: BackboneConfig = field(default_factory=BackboneConfig)
    placement: PlacementConfig = field(default_factory=PlacementConfig)
    calibration: CalibrationConfig = field(default_factory=CalibrationConfig)
    temporal: TemporalConfig = field(default_factory=TemporalConfig)
    release_gate: ReleaseGateConfig = field(default_factory=ReleaseGateConfig)
    collection: CollectionConfig = field(default_factory=CollectionConfig)

    # -- serialisation ----------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def hash(self) -> str:
        """Stable short hash over the full config (doc 18 reproducibility)."""
        blob = json.dumps(self.to_dict(), sort_keys=True, default=str).encode("utf-8")
        return hashlib.sha256(blob).hexdigest()[:12]

    def frame_interval_ms(self) -> float:
        return 1000.0 / float(self.preprocess.analysis_fps)


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------


def _require_mapping(section: str, data: Any) -> Dict[str, Any]:
    """Return one raw config section as a mapping, or raise naming the section.

    A missing/empty section is legitimate (it means "all defaults"), so ``None``
    becomes ``{}``.  Anything else that is not a mapping is a malformed file --
    a stray ``- `` turning ``preprocess.yaml`` into a list, a scalar where a
    block was meant.  Previously such a section was passed through unchanged and
    REPLACED the dataclass with the str/list, so the config object looked fine
    until something far away did ``cfg.preprocess.analysis_fps`` and raised an
    AttributeError that named neither the file nor the section.  Failing here
    costs the caller a traceback at load time and names both.
    """
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise TypeError(
            f"config section {section!r} must be a mapping, got "
            f"{type(data).__name__}"
        )
    return data


def _build(cls, data: Any, section: str = ""):
    """Construct a flat dataclass from a plain dict, ignoring unknown keys.

    Unknown keys are dropped rather than raising: a YAML file left over from an
    older config revision should degrade to defaults, not break every tool.  A
    section that is not a mapping at all is a different story and raises -- see
    :func:`_require_mapping`.  All sections here are flat; nested structures
    (``VisionConfig``, ``CollectionConfig.conditions``) are assembled explicitly
    in ``load_config``.

    ``section`` only names the offending section in that error; it defaults to
    the dataclass name so an ad-hoc call still says something useful.
    """
    known = {f.name for f in fields(cls)}
    raw = _require_mapping(section or cls.__name__, data)
    return cls(**{k: v for k, v in raw.items() if k in known})


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Merge ``override`` into ``base``, recursing only where BOTH sides are mappings.

    Everything else is assigned as-is, so the result can still alias values owned
    by the caller -- ``load_config`` copies the merged tree rather than making
    this function copy every leaf it touches.
    """
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _read_yaml(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_config(
    config_dir: Optional[Path] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> VisionConfig:
    """Load every ``ai/configs/*.yaml`` section and merge optional overrides.

    File-to-section mapping::

        preprocess.yaml     -> preprocess
        gaze_backbone.yaml  -> backbone          (the one name that differs)
        placement.yaml      -> placement
        calibration.yaml    -> calibration
        temporal.yaml       -> temporal
        release_gate.yaml   -> release_gate
        collection.yaml     -> collection

    An ``overrides`` dict is keyed by the SECTION name, not the file name, so
    the backbone section must be given as ``{"backbone": {...}}``; a key that is
    not one of the seven above is merged into the raw mapping and then silently
    ignored.  A missing file yields ``{}`` and ``_build`` drops unknown keys, so
    a wrong ``config_dir`` or a typo'd key produces an all-defaults config with
    a valid-looking hash rather than an error.  A file that parses to something
    other than a mapping is the one case that is NOT degraded that way: it
    raises ``TypeError`` naming the section, because it used to replace the
    whole dataclass (see :func:`_require_mapping`).

    The returned config owns its values: the merged mapping is deep-copied
    before the dataclasses are built, so nothing in it aliases ``overrides`` and
    two configs loaded from the same ``overrides`` cannot move each other's
    :meth:`VisionConfig.hash`.
    """
    directory = Path(config_dir) if config_dir else CONFIG_DIR
    raw: Dict[str, Any] = {
        "preprocess": _read_yaml(directory / "preprocess.yaml"),
        "backbone": _read_yaml(directory / "gaze_backbone.yaml"),
        "placement": _read_yaml(directory / "placement.yaml"),
        "calibration": _read_yaml(directory / "calibration.yaml"),
        "temporal": _read_yaml(directory / "temporal.yaml"),
        "release_gate": _read_yaml(directory / "release_gate.yaml"),
        "collection": _read_yaml(directory / "collection.yaml"),
    }
    if overrides:
        raw = _deep_merge(raw, overrides)

    # _deep_merge recurses only when BOTH sides are mappings, so a list -- or a
    # dict the YAML has no counterpart for -- came out of the merge as the very
    # object the caller passed in.  Two configs loaded from one ``overrides``
    # then shared it, and appending to one config's ``eye_crop_size`` silently
    # moved the OTHER config's ``hash()``, the doc 18 provenance key, without
    # that config ever being touched.  One deep copy of the merged tree here
    # gives every config its own values; ``raw`` is plain YAML data.
    raw = copy.deepcopy(raw)

    collection_raw = _require_mapping("collection", raw.get("collection"))
    conditions = [
        _build(ProtocolCondition, c, f"collection.conditions[{i}]")
        for i, c in enumerate(collection_raw.get("conditions") or [])
    ]
    collection = _build(
        CollectionConfig,
        {k: v for k, v in collection_raw.items() if k != "conditions"},
        "collection",
    )
    collection.conditions = conditions

    return VisionConfig(
        preprocess=_build(PreprocessConfig, raw.get("preprocess"), "preprocess"),
        backbone=_build(BackboneConfig, raw.get("backbone"), "backbone"),
        placement=_build(PlacementConfig, raw.get("placement"), "placement"),
        calibration=_build(CalibrationConfig, raw.get("calibration"), "calibration"),
        temporal=_build(TemporalConfig, raw.get("temporal"), "temporal"),
        release_gate=_build(ReleaseGateConfig, raw.get("release_gate"), "release_gate"),
        collection=collection,
    )


def resolve_path(path_like: str) -> Path:
    """Resolve a config path that may be repo-relative or absolute."""
    p = Path(path_like)
    return p if p.is_absolute() else (REPO_ROOT / p)
