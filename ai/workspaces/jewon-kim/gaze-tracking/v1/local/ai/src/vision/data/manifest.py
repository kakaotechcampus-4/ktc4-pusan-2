"""Dataset manifest read/write (doc 17).

One JSONL row per analysed frame, carrying the identifiers and the metadata
doc 19 needs to slice failure buckets.  JSONL rather than JSON so a long take
streams line by line and a truncated write costs one row instead of the file.

Privacy (doc 20): the manifest stores a *path* and pseudonymous ids, never
pixels and never a real name.  ``frame_path`` may legitimately be empty when a
run keeps only features and no extracted frames.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator, List, Sequence, Union

from vision.schemas import GazeLabel, ManifestRecord

#: Zero padding on the frame index inside a sample id ("P07_S03_000194").
#: Six digits hold 10 hours at 30 fps, more than any take in doc 4-1.
SAMPLE_ID_FRAME_DIGITS = 6

PathLike = Union[str, Path]


def make_sample_id(participant_id: str, session_id: str, frame_id: int) -> str:
    """Stable per-frame key, e.g. ``P07_S03_000194`` (doc 17).

    The id is the join key between the manifest, the feature table and any
    dumped frame, so it must be reproducible from the three inputs alone --
    no counters, no timestamps.
    """
    pid = str(participant_id).strip()
    sid = str(session_id).strip()
    if not pid or not sid:
        raise ValueError(f"participant_id and session_id must be non-empty, got {pid!r}, {sid!r}")
    if any(ch.isspace() for ch in pid + sid):
        raise ValueError(f"ids must not contain whitespace, got {pid!r}, {sid!r}")
    index = int(frame_id)
    if index < 0:
        raise ValueError(f"frame_id must be >= 0, got {index}")
    return f"{pid}_{sid}_{index:0{SAMPLE_ID_FRAME_DIGITS}d}"


def write_manifest(path: PathLike, records: Sequence[ManifestRecord]) -> Path:
    """Write the manifest as JSONL, one ``ManifestRecord`` per line (doc 17).

    Duplicate ``sample_id`` values are rejected rather than written: a duplicate
    means two rows claim the same frame, which would silently double-count that
    frame in every metric downstream.

    ``gaze_label`` lands on disk in the form ``GazeLabel.coerce`` returns, not in
    the form the caller passed.  Both passes below coerce, and both are needed:
    the first so a typo is refused *before* the file is opened and cannot
    truncate a good manifest, the second so the stored value is the canonical
    one.  The records the caller handed in are not mutated.
    """
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()
    for record in records:
        if record.sample_id in seen:
            raise ValueError(f"duplicate sample_id in manifest: {record.sample_id}")
        seen.add(record.sample_id)
        GazeLabel.coerce(record.gaze_label)  # reject a typo before it reaches evaluation

    with out.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            payload = record.to_dict()
            # Store what coerce returned.  The old code called coerce for its
            # exception only and wrote record.gaze_label verbatim, so a
            # lower-case "camera" passed validation and was stored as "camera";
            # splits.py then compares labels against "CAMERA" and every frame
            # written that way dropped out of the split without a word.
            payload["gaze_label"] = GazeLabel.coerce(record.gaze_label).value
            handle.write(json.dumps(payload, ensure_ascii=False))
            handle.write("\n")
    return out


def read_manifest(path: PathLike) -> List[ManifestRecord]:
    """Read a JSONL manifest; blank lines are skipped, bad lines are located."""
    return list(iter_manifest(path))


def iter_manifest(path: PathLike) -> Iterator[ManifestRecord]:
    """Stream a manifest so a multi-hour dataset never lands in memory at once."""
    src = Path(path)
    with src.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{src}:{line_no}: invalid JSON ({exc.msg})") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"{src}:{line_no}: expected a JSON object, got {type(payload).__name__}")
            yield ManifestRecord.from_dict(payload)


def append_manifest(path: PathLike, records: Sequence[ManifestRecord]) -> Path:
    """Append rows to an existing manifest (a second session for one participant).

    Unlike :func:`write_manifest` this cannot check for duplicates without
    re-reading the file, so it re-reads it; manifests are small enough that
    correctness beats the I/O.
    """
    out = Path(path)
    existing = {record.sample_id for record in iter_manifest(out)} if out.exists() else set()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8", newline="\n") as handle:
        for record in records:
            if record.sample_id in existing:
                raise ValueError(f"duplicate sample_id in manifest: {record.sample_id}")
            existing.add(record.sample_id)
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False))
            handle.write("\n")
    return out
