"""Whether a take may move on: the one place the strict/advisory choice lives.

``VisionSession`` *reports* -- a placement verdict, a calibration grade, a
set-up check -- and never refuses on its own, because research tools need the
numbers from a bad set-up too.  A product UI asks these gates instead.

* **strict** (product default, ``preconditions.strict``): a verdict that says
  the model's assumptions do not hold blocks the next step; the UI shows the
  hint and offers a retry (an operator may still force past it).
* **advisory**: everything that *can* run is allowed, and the verdict is only
  shown.  Only a hard technical gap (no fitted model at all) ever blocks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from gaze_lab.runtime.placement import PlacementCheckResult
from gaze_lab.schemas import CalibrationQuality


@dataclass(frozen=True)
class GateDecision:
    #: May the take move on to the next step?
    allowed: bool
    #: Machine-readable cause when not allowed (or when allowed with a warning).
    reason: Optional[str] = None
    #: What to tell the user.
    hint: Optional[str] = None


def gate_placement(
    result: Optional[PlacementCheckResult], strict: bool, *, block_inconclusive: bool = True
) -> GateDecision:
    """A camera that is not at the top centre inverts or blurs every label.

    ``block_inconclusive=False`` lets a strict take continue when the position
    simply could not be read (still reported as the reason): with the head-pose
    backbone a user may look from the lens to the screen centre without moving
    their head, which says nothing about where the camera is.  A position that
    *was* read as unsupported (below, beside) still blocks.
    """
    if result is None:
        return GateDecision(not strict or not block_inconclusive, "NO_PLACEMENT",
                            "The camera position could not be measured.")
    if result.supported:
        return GateDecision(True)
    if result.placement == "INCONCLUSIVE":
        return GateDecision(not strict or not block_inconclusive, result.reason, result.hint)
    return GateDecision(not strict, result.placement, result.hint)


def gate_calibration(
    quality: Optional[CalibrationQuality], is_calibrated: bool, strict: bool
) -> GateDecision:
    """A model must exist; in strict mode it must also have passed its grade."""
    if quality is None or not is_calibrated:
        reason = None if quality is None else quality.reason
        hint = None if quality is None else quality.hint
        return GateDecision(False, reason or "NOT_CALIBRATED", hint)
    if quality.ok:
        return GateDecision(True, None, quality.hint)
    return GateDecision(not strict, quality.reason, quality.hint)
