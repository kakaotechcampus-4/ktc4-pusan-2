"""Backbone registry (doc 3-2 / 3-3).

Experiment 1 (doc 23) swaps backbones while holding everything else fixed, so
the selection has to be a pure function of ``BackboneConfig.name``.  Registration
is a decorator on the class; the built-in modules are imported lazily by
:func:`_load_builtins` so that importing this module never drags in torch.
"""

from __future__ import annotations

import importlib
from typing import Callable, Dict, List, Type

from vision.config import BackboneConfig
from vision.backbones.base import GazeBackbone

_REGISTRY: Dict[str, Type[GazeBackbone]] = {}

#: Modules that self-register on import.  ``mediapipe_geom`` first so that a
#: broken optional dependency in a torch backbone cannot break the default path.
_BUILTIN_MODULES = (
    "vision.backbones.mediapipe_geom",
    "vision.backbones.l2cs",
    "vision.backbones.gazetr",
)

_builtins_loaded = False


def register(name: str) -> Callable[[Type[GazeBackbone]], Type[GazeBackbone]]:
    """Class decorator that binds ``name`` to a backbone implementation."""

    def decorator(cls: Type[GazeBackbone]) -> Type[GazeBackbone]:
        key = name.strip().lower()
        existing = _REGISTRY.get(key)
        if existing is not None and existing is not cls:
            raise ValueError(
                f"backbone name {key!r} already registered by {existing.__module__}.{existing.__name__}"
            )
        cls.name = key
        _REGISTRY[key] = cls
        return cls

    return decorator


def _load_builtins() -> None:
    """Import the shipped backbones once.

    Import errors are fatal rather than skipped: every backbone module is
    required to be importable without torch installed (torch is imported inside
    the predict path), so an ImportError here is a real bug, not a missing
    optional dependency.

    The latch is set *after* the loop, so a failed import is retried on the next
    call and keeps raising the real :class:`ImportError`.  Setting it first --
    which is what this used to do -- reported the broken dependency exactly once
    and then swallowed it forever: every later call returned the partial
    registry, and :func:`get_backbone_class` blamed the *name*, sending the
    reader hunting for a typo in ``gaze_backbone.yaml`` instead of showing the
    traceback of the module that would not import.  Repeating the loop is free:
    ``importlib.import_module`` is a dict lookup for anything already in
    ``sys.modules``, and a module cannot register twice because its body only
    runs once.
    """
    global _builtins_loaded
    if _builtins_loaded:
        return
    for module in _BUILTIN_MODULES:
        importlib.import_module(module)
    _builtins_loaded = True


def available_backbones() -> List[str]:
    """Registered names, sorted so CLI help and reports are deterministic."""
    _load_builtins()
    return sorted(_REGISTRY)


def get_backbone_class(name: str) -> Type[GazeBackbone]:
    """Look up a backbone class by registry name."""
    _load_builtins()
    key = str(name).strip().lower()
    try:
        return _REGISTRY[key]
    except KeyError:
        raise ValueError(
            f"unknown backbone {name!r}; available: {', '.join(sorted(_REGISTRY))}"
        ) from None


def build_backbone(cfg: BackboneConfig) -> GazeBackbone:
    """Instantiate the backbone named by ``cfg`` (doc 3-3).

    Raises :class:`~vision.backbones.base.CheckpointMissingError` from the
    constructor when a pretrained backbone has no weights -- deliberately at
    build time, so a collection or evaluation run fails before it records a
    single frame under the wrong model version.
    """
    return get_backbone_class(cfg.name)(cfg)
