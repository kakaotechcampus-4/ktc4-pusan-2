"""Protocol-derived ground-truth segment labels (doc 4-2).

The collector never labels frames one by one.  It records the *cue timeline* it
showed the participant, and this module is the single place that turns that
timeline into ``SegmentLabel`` intervals.  Keeping the conversion here means the
transition guard band cannot drift apart between the collector, the label
reviewer and the offline evaluation.

doc 4-2 requires that frames recorded while the eyes are still travelling
between the two targets carry neither label: a cue change is an instruction, not
an instantaneous eye movement.  Those frames become ``IGNORE`` and are dropped
from every metric.
"""

from __future__ import annotations

import json
from dataclasses import fields
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple, Union

from vision.schemas import GazeLabel, ParticipantMeta, SegmentLabel

#: Version stamped into a saved label file.  ``load_segments`` accepts older
#: files as long as the per-segment fields still parse; a bump here is a signal
#: to re-run the collector, not a hard gate.
SEGMENT_FILE_SCHEMA = "gaze_segments_v1"

#: Cue values that terminate the timeline instead of starting a block.
_END_MARKERS = frozenset({"", "END", "STOP", "EOF", "FINISH"})

#: Everything except the interval bounds must match before two segments merge.
_MERGE_KEYS: Tuple[str, ...] = tuple(
    f.name for f in fields(SegmentLabel) if f.name not in {"start_ms", "end_ms"}
)

PathLike = Union[str, Path]


# --------------------------------------------------------------------------
# Cue timeline -> segments
# --------------------------------------------------------------------------


class _Block:
    """One cue held over ``[start_ms, end_ms)`` before guard bands are cut out."""

    __slots__ = ("start_ms", "end_ms", "label", "condition", "lighting")

    def __init__(self, start_ms: int, end_ms: int, label: str, condition: str, lighting: str) -> None:
        self.start_ms = start_ms
        self.end_ms = end_ms
        self.label = label
        self.condition = condition
        self.lighting = lighting


def _is_end_marker(entry: Mapping[str, Any]) -> bool:
    if bool(entry.get("end", False)):
        return True
    cue = entry.get("cue", None)
    if cue is None:
        return True
    return str(cue).strip().upper() in _END_MARKERS


def _cue_blocks(cues: Sequence[Mapping[str, Any]], default_lighting: str) -> List[_Block]:
    """Normalise the raw cue dicts into ordered, non-empty blocks."""
    if len(cues) < 2:
        raise ValueError(
            "cue timeline needs at least one cue plus a final end marker, "
            f"got {len(cues)} entries"
        )

    ordered = sorted(enumerate(cues), key=lambda pair: (int(pair[1]["t_ms"]), pair[0]))
    entries = [entry for _, entry in ordered]

    for position, entry in enumerate(entries[:-1]):
        if _is_end_marker(entry):
            raise ValueError(
                f"end marker at t_ms={entry.get('t_ms')} is entry {position} of "
                f"{len(entries)}, not the last one; a paused recording must be "
                "written as two separate timelines"
            )
    if not _is_end_marker(entries[-1]):
        raise ValueError(
            "cue timeline must end with an end marker, e.g. "
            "{'t_ms': <recording end>, 'cue': 'END'}; the recording length cannot "
            "be inferred from the last cue alone"
        )

    end_ms = int(entries[-1]["t_ms"])
    blocks: List[_Block] = []
    for entry, nxt in zip(entries[:-1], entries[1:]):
        start = int(entry["t_ms"])
        stop = int(nxt["t_ms"])
        if stop <= start:
            continue  # a cue immediately overwritten by the next one holds no frames
        blocks.append(
            _Block(
                start_ms=start,
                end_ms=stop,
                label=GazeLabel.coerce(entry["cue"]).value,
                condition=str(entry.get("condition", "unknown")),
                lighting=str(entry.get("lighting", default_lighting)),
            )
        )
    if not blocks:
        raise ValueError(f"cue timeline covers no time (ends at t_ms={end_ms})")
    return blocks


def _guard_intervals(
    blocks: Sequence[_Block],
    guard_ms: int,
    guard_at_start: bool,
) -> List[Tuple[int, int]]:
    """Merged ``IGNORE`` windows of +/- ``guard_ms`` around each cue change.

    Only a change of *label* is guarded: a block boundary that merely switches
    condition (``static_camera`` -> ``alternating``, both showing CAMERA) asks
    the participant for no eye movement, so those frames stay labelled.
    """
    if guard_ms <= 0:
        return []

    timeline_start = blocks[0].start_ms
    timeline_end = blocks[-1].end_ms

    change_times: List[int] = []
    for index, block in enumerate(blocks):
        if index == 0:
            if guard_at_start:
                change_times.append(block.start_ms)
        elif block.label != blocks[index - 1].label:
            change_times.append(block.start_ms)

    raw = [
        (max(timeline_start, t - guard_ms), min(timeline_end, t + guard_ms))
        for t in change_times
    ]
    raw = [(a, b) for a, b in raw if b > a]
    raw.sort()

    merged: List[Tuple[int, int]] = []
    for start, stop in raw:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], stop))
        else:
            merged.append((start, stop))
    return merged


def segments_from_cue_timeline(
    cues: Sequence[Mapping[str, Any]],
    guard_ms: int,
    meta: ParticipantMeta,
    session_id: str,
    *,
    lighting: str = "normal",
    label_version: str = "gaze_label_v1",
    source: str = "protocol",
    guard_at_start: bool = True,
) -> List[SegmentLabel]:
    """Convert a recorded cue timeline into ground-truth segments (doc 4-2).

    ``cues`` is the list the collector wrote, ordered or not::

        [{"t_ms": 0,     "cue": "CAMERA", "condition": "static_camera"},
         {"t_ms": 20000, "cue": "BOTTOM", "condition": "static_bottom"},
         {"t_ms": 40000, "cue": "END"}]

    Each entry holds until the next one; the final entry is an end marker
    (``cue`` of ``END``/empty/``None``, or ``end: true``) and only supplies the
    recording end.  ``condition`` and an optional per-entry ``lighting`` ride
    along into the segments so doc 19 can bucket by recording block.

    Every cue *change* becomes an ``IGNORE`` window of ``+/- guard_ms``
    (``collection.transition_guard_ms``).  ``guard_at_start`` also guards the
    very first cue, whose onset is an eye movement like any other -- only the
    half that falls before the recording exists is clipped away.  Guard windows
    are merged before they are subtracted, so a guard wider than the block it
    lands in swallows that block instead of producing inverted intervals.
    """
    if guard_ms < 0:
        raise ValueError(f"guard_ms must be >= 0, got {guard_ms}")

    blocks = _cue_blocks(cues, default_lighting=lighting)
    guards = _guard_intervals(blocks, int(guard_ms), guard_at_start)

    def _make(start: int, stop: int, label: str, block: _Block) -> SegmentLabel:
        return SegmentLabel(
            participant_id=meta.participant_id,
            session_id=session_id,
            start_ms=int(start),
            end_ms=int(stop),
            label=label,
            condition=block.condition,
            glasses=bool(meta.glasses),
            lighting=block.lighting,
            device_group=meta.device_group,
            label_version=label_version,
            source=source,
        )

    pieces: List[SegmentLabel] = []
    for block in blocks:
        cursor = block.start_ms
        for guard_start, guard_stop in guards:
            if guard_stop <= block.start_ms or guard_start >= block.end_ms:
                continue
            lo = max(guard_start, block.start_ms)
            hi = min(guard_stop, block.end_ms)
            if lo > cursor:
                pieces.append(_make(cursor, lo, block.label, block))
            pieces.append(_make(lo, hi, GazeLabel.IGNORE.value, block))
            cursor = hi
        if cursor < block.end_ms:
            pieces.append(_make(cursor, block.end_ms, block.label, block))

    return merge_adjacent(pieces)


def merge_adjacent(segments: Sequence[SegmentLabel]) -> List[SegmentLabel]:
    """Join touching or overlapping segments that agree on every other field.

    Guard-band subtraction leaves an ``IGNORE`` piece on each side of a cue
    change; when both sides share a condition they are one interval and the
    label file should say so.  Returns copies, so the caller's segments are
    never mutated.
    """
    ordered = sorted(segments, key=lambda s: (s.start_ms, s.end_ms))
    out: List[SegmentLabel] = []
    for segment in ordered:
        if out:
            last = out[-1]
            mergeable = all(getattr(last, key) == getattr(segment, key) for key in _MERGE_KEYS)
            if mergeable and segment.start_ms <= last.end_ms:
                last.end_ms = max(last.end_ms, segment.end_ms)
                continue
        out.append(SegmentLabel(**segment.to_dict()))
    return out


def label_at(segments: Sequence[SegmentLabel], t_ms: int) -> str:
    """Ground-truth label at ``t_ms``; ``IGNORE`` when no segment covers it.

    Intervals are half-open ``[start_ms, end_ms)`` (``SegmentLabel.contains``),
    so a cue change at ``t`` belongs to the block that starts there.  Time
    outside the recorded timeline is unlabelled, and doc 4-2 treats unlabelled
    as ``IGNORE`` rather than guessing.

    Linear in ``len(segments)``: a take carries tens of segments, so scanning
    thousands of frames costs nothing worth indexing away.
    """
    probe = int(t_ms)
    for segment in segments:
        if segment.contains(probe):
            return segment.label
    return GazeLabel.IGNORE.value


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


def save_segments(path: PathLike, segments: Sequence[SegmentLabel]) -> Path:
    """Write segments as one JSON object next to the recording (doc 4-2).

    Returns the path written so callers can log it.  ``source`` lives on each
    segment, which is what lets ``label_review`` emit a ``corrected`` file
    beside the untouched ``protocol`` original.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": SEGMENT_FILE_SCHEMA,
        "n_segments": len(segments),
        "segments": [segment.to_dict() for segment in segments],
    }
    with out.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return out


def load_segments(path: PathLike) -> List[SegmentLabel]:
    """Read a label file written by :func:`save_segments` (or a bare JSON list)."""
    src = Path(path)
    with src.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    raw: Any = payload.get("segments", []) if isinstance(payload, dict) else payload
    if not isinstance(raw, list):
        raise ValueError(f"{src}: expected a list of segments, got {type(raw).__name__}")
    return [SegmentLabel.from_dict(item) for item in raw]


def segment_summary(segments: Sequence[SegmentLabel]) -> Dict[str, Any]:
    """Duration per label plus counts -- what the collector prints after a take."""
    per_label: Dict[str, int] = {}
    for segment in segments:
        per_label[segment.label] = per_label.get(segment.label, 0) + segment.duration_ms
    total = sum(per_label.values())
    return {
        "n_segments": len(segments),
        "total_ms": total,
        "duration_ms_by_label": per_label,
        "ignore_ratio": (per_label.get(GazeLabel.IGNORE.value, 0) / total) if total else 0.0,
        "start_ms": min((s.start_ms for s in segments), default=0),
        "end_ms": max((s.end_ms for s in segments), default=0),
    }
