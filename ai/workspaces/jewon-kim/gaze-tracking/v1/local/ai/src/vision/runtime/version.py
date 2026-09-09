"""Version stamp for one take (doc 15, vision slice; doc 18 reproducibility).

Every artefact a run produces -- a feature table, an evaluation report, an
experiment record, a stored take -- carries an :class:`~vision.schemas.AiVersion`
so that a number can be traced back to the code and the settings that made it.
Two of its fields are computed rather than declared:

``config_hash``
    ``VisionConfig.hash()`` over the *whole* merged config, not just the section
    a module reads.  A threshold sweep that only touches ``calibration.yaml``
    still changes the hash, which is what makes "same hash => same numbers" a
    usable claim in doc 18's experiment log.

``code_commit``
    Best effort.  This tree is not always a git checkout (the user's local rig
    is not), git may be absent from PATH, and a packaged run has neither, so the
    value degrades to ``"unknown"`` instead of raising -- a missing commit must
    never stop a take from being recorded.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from vision.calibration.features import normalise_feature_set
from vision.config import REPO_ROOT, VisionConfig
from vision.schemas import AiVersion
from vision.temporal.smoother import TEMPORAL_RULE_VERSION

#: Wire value of ``GazeStateEvent.model_version`` (doc 6-2).  It names the whole
#: vision slice, not one component: the per-take detail (backbone, config hash,
#: commit) travels in ``AiVersion``, which is stored once per take, so the event
#: stream stays the fixed-shape contract downstream consumers parse.
MODEL_VERSION = "gaze_v1.0.0"

#: Identifies the doc 5-3 estimator family in ``AiVersion.gaze_classifier``.
#: Distinct from ``classifier.SCHEMA_VERSION``, which versions the *file format*
#: of a persisted model: the two move for different reasons.
CLASSIFIER_VERSION = "per_user_lr_v1"

#: Overrides the git lookup where there is no repository to ask -- a container
#: or a wheel built from a checkout that is no longer around.  Set it in the
#: build, not by hand, or the stamp starts lying.
COMMIT_ENV_VAR = "GAZE_TRACKING_GIT_COMMIT"

#: A hung git (a network-backed filesystem, a stale index lock) must not stall a
#: take; the stamp is worth at most this many seconds.
_GIT_TIMEOUT_S = 2.0

_UNKNOWN_COMMIT = "unknown"

#: Resolved repo path -> commit string.  Spawning git once per recorded frame
#: would cost more than the inference it is stamping.
_COMMIT_CACHE: Dict[Path, str] = {}


def _run_git(args: List[str], cwd: Path) -> Optional[str]:
    """Stripped stdout of a git command, or ``None`` for any failure at all.

    Every failure mode collapses to ``None`` on purpose: no git binary, not a
    repository, a timeout and a non-zero exit all mean the same thing to the
    caller -- there is no commit to record.
    """
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def git_commit(repo_root: Optional[Path] = None, *, refresh: bool = False) -> str:
    """Short commit of the working tree, ``"unknown"`` when unavailable (doc 18).

    A tree with uncommitted *tracked* changes is reported as ``<sha>-dirty``:
    the commit alone would claim a reproducibility this run does not have.
    Untracked files are excluded (``--untracked-files=no``) because the
    directories a run writes into -- ``ai/reports``, ``ai/datasets`` -- would
    otherwise mark every long session dirty for reasons unrelated to the code.
    """
    override = os.environ.get(COMMIT_ENV_VAR, "").strip()
    if override:
        return override

    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    key = root.resolve()
    if not refresh and key in _COMMIT_CACHE:
        return _COMMIT_CACHE[key]

    sha = _run_git(["rev-parse", "--short=12", "HEAD"], key) if key.exists() else None
    if not sha:
        commit = _UNKNOWN_COMMIT
    else:
        status = _run_git(["status", "--porcelain", "--untracked-files=no"], key)
        commit = f"{sha}-dirty" if status else sha
    _COMMIT_CACHE[key] = commit
    return commit


def current_ai_version(
    cfg: VisionConfig,
    backbone_name: Optional[str] = None,
    *,
    repo_root: Optional[Path] = None,
) -> AiVersion:
    """Version snapshot for a take run under ``cfg`` (doc 15).

    ``backbone_name`` is the registry key of the backbone that will actually
    run.  It is an argument rather than a read of ``cfg.backbone.name`` because
    the two can differ: doc 23's Experiment 1 builds several backbones against
    one config, and a stamp naming the config's default would silently
    mislabel every row but one.
    """
    feature_set = normalise_feature_set(cfg.calibration.feature_set)
    return AiVersion(
        gaze_backbone=str(backbone_name or cfg.backbone.name).strip().lower(),
        gaze_classifier=CLASSIFIER_VERSION,
        temporal_rule=TEMPORAL_RULE_VERSION,
        feature_set=feature_set,
        config_hash=cfg.hash(),
        code_commit=git_commit(repo_root),
    )
