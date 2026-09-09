"""Contract tests for the dataset layer (``vision.data``).

The four modules under test are the only place the offline evaluation learns
*what a frame is*, so the contracts protected here are the ones whose silent
breakage would show up as a plausible-looking but wrong number in a report:

``manifest``
    the join key ``P07_S03_000194`` is reproducible from its three inputs
    alone, and a duplicate ``sample_id`` (which would double-count a frame in
    every metric) is rejected -- by :func:`write_manifest` *before* the output
    file is opened, and by :func:`append_manifest` while it writes.  Bad rows
    are located as ``path:line``.

``labels``
    the doc 4-2 cue timeline -> ``SegmentLabel`` conversion: a final end marker
    is mandatory, intervals are half-open ``[start, end)``, and the transition
    guard band is ``+/- guard_ms`` around a *label* change only -- a block
    boundary that merely changes ``condition`` asks for no eye movement and so
    must not cost any frames.  That asymmetry is the subtle part.

``splits``
    doc 4-3: participants are split, never frames; the split is a function of
    ``(set of ids, ratios, seed)`` and not of input order; and within a
    participant the evaluation starts after the calibration *block* ends, not
    after the last calibration *frame*.  Any frame reachable from both halves
    is leakage and must raise.

``features_table``
    :data:`FEATURE_COLUMNS` and ``observations_to_rows`` are one unit -- a
    column added to either alone raises; a missing value stays ``None`` and is
    never filled with a sentinel number; and the table round-trips through both
    the parquet and the gzipped-CSV backend.

No dataset is invented here.  Every hand-built input exercises one specific
code path (a cue timeline through the guard logic, a frame table through the
partition rules); nothing in this file measures a model.
"""

from __future__ import annotations

import json
import math
import warnings
from types import SimpleNamespace

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from vision.config import CalibrationConfig, VisionConfig
from vision.data import features_table as ft
from vision.data import labels as lb
from vision.data import manifest as mf
from vision.data import splits as sp
from vision.schemas import (
    FrameObservation,
    FrameQuality,
    GazeDecision,
    GazeLabel,
    GazeVector,
    HeadPose,
    ManifestRecord,
    ParticipantMeta,
    SegmentLabel,
)

META = ParticipantMeta(
    participant_id="P01",
    glasses=True,
    device_group="laptop_internal_cam",
    camera_position="top_center",
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _record(frame_id: int, *, participant: str = "P01", session: str = "S01",
            label: str = "CAMERA") -> ManifestRecord:
    return ManifestRecord(
        sample_id=mf.make_sample_id(participant, session, frame_id),
        participant_id=participant,
        session_id=session,
        timestamp_ms=frame_id * 125,
        frame_path="",
        gaze_label=label,
    )


def _cue(t_ms: int, cue=..., **extra):
    entry = {"t_ms": t_ms}
    if cue is not ...:
        entry["cue"] = cue
    entry.update(extra)
    return entry


def _segments(cues, guard_ms=0, **kwargs):
    return lb.segments_from_cue_timeline(cues, guard_ms, META, "S01", **kwargs)


def _table(rows) -> pd.DataFrame:
    """Frame table from ``(participant, session, [(condition, label, n), ...])``.

    Frames are 500 ms apart and numbered from 0 within each session, which is
    all the partition rules look at.
    """
    out = []
    for participant_id, session_id, spec in rows:
        position = 0
        for condition, label, count in spec:
            for _ in range(count):
                out.append(
                    {
                        "participant_id": participant_id,
                        "session_id": session_id,
                        "t_ms": position * 500,
                        "label": label,
                        "condition": condition,
                        "frame_id": position,
                        "sample_id": mf.make_sample_id(participant_id, session_id, position),
                    }
                )
                position += 1
    return pd.DataFrame(out)


def _observation(frame_id: int = 7, t_ms: int = 875) -> FrameObservation:
    """One analysed frame with every numeric slot filled by a distinct value."""
    return FrameObservation(
        frame_id=frame_id,
        t_ms=t_ms,
        face_confidence=0.93,
        face_valid=True,
        head_pose=HeadPose(yaw=0.11, pitch=-0.22, roll=0.033,
                           reprojection_error=1.25, depth_proxy=4.5),
        quality=FrameQuality(
            face_area_ratio=0.115,
            face_brightness=121.5,
            background_brightness=90.25,
            face_contrast=33.5,
            left_eye_openness=0.31,
            right_eye_openness=0.29,
            landmark_visibility=0.99,
            touches_border=False,
        ),
        preprocess_ms=12.5,
    )


def _gaze() -> GazeVector:
    return GazeVector(gaze_yaw=0.1 + 0.2, gaze_pitch=-0.35, confidence=0.8,
                      backbone="mediapipe_geom", inference_ms=3.25)


def _decision() -> GazeDecision:
    return GazeDecision(t_ms=875, frame_id=7, label="BOTTOM", p_camera=1.0 / 3.0,
                        p_bottom=2.0 / 3.0, face_valid=True, latency_ms=40.5)


def _seg(start, end, label="CAMERA", **kwargs):
    return SegmentLabel(participant_id="P01", session_id="S01", start_ms=start,
                        end_ms=end, label=label, **kwargs)


@pytest.fixture(autouse=True)
def _isolate_parquet_probe():
    """``parquet_available`` is ``lru_cache``d over an env-var read.

    Clearing on both sides keeps a ``GAZE_TRACKING_FORCE_CSV`` monkeypatch from
    leaking a cached ``False`` into the next test (or the next test module).
    """
    ft.parquet_available.cache_clear()
    yield
    ft.parquet_available.cache_clear()


@pytest.fixture
def force_csv(monkeypatch):
    """Take the gzipped-CSV branch without uninstalling an engine."""
    monkeypatch.setenv(ft.FORCE_CSV_ENV, "1")
    ft.parquet_available.cache_clear()
    assert ft.parquet_available() is False
    return None


def _has_parquet_engine() -> bool:
    for engine in ("pyarrow", "fastparquet"):
        try:
            __import__(engine)
            return True
        except ImportError:
            continue
    return False


requires_parquet = pytest.mark.skipif(
    not _has_parquet_engine(),
    reason="no parquet engine (pyarrow / fastparquet) installed",
)


# ==========================================================================
# manifest -- sample id
# ==========================================================================


@pytest.mark.parametrize(
    "frame_id, expected",
    [
        (0, "P07_S03_000000"),
        (194, "P07_S03_000194"),
        (999_999, "P07_S03_999999"),
        # Six digits is a floor, not a truncation: an overlong take must still
        # produce a unique id rather than collide with frame 234567.
        (1_234_567, "P07_S03_1234567"),
    ],
)
def test_sample_id_zero_pads_the_frame_index_to_six_digits(frame_id, expected):
    assert mf.make_sample_id("P07", "S03", frame_id) == expected
    assert mf.SAMPLE_ID_FRAME_DIGITS == 6


def test_sample_id_depends_on_nothing_but_its_three_inputs():
    first = mf.make_sample_id("P07", "S03", 194)

    assert mf.make_sample_id("P07", "S03", 194) == first
    assert mf.make_sample_id("P07", "S03", 195) != first
    assert mf.make_sample_id("P07", "S04", 194) != first
    assert mf.make_sample_id("P08", "S03", 194) != first


def test_sample_id_accepts_any_integer_like_frame_index():
    assert mf.make_sample_id("P1", "S1", True) == "P1_S1_000001"
    assert mf.make_sample_id("P1", "S1", 3.0) == "P1_S1_000003"


@pytest.mark.parametrize(
    "participant_id, session_id, frame_id, fragment",
    [
        ("", "S1", 0, "non-empty"),
        ("   ", "S1", 0, "non-empty"),
        ("P1", "", 0, "non-empty"),
        ("P 1", "S1", 0, "whitespace"),
        ("P1", "S\t1", 0, "whitespace"),
        ("P1", "S1", -1, ">= 0"),
        ("P1", "S1", -999, ">= 0"),
    ],
)
def test_sample_id_rejects_inputs_that_would_break_the_join_key(
    participant_id, session_id, frame_id, fragment
):
    with pytest.raises(ValueError, match=fragment):
        mf.make_sample_id(participant_id, session_id, frame_id)


def test_sample_id_strips_surrounding_whitespace_before_joining():
    assert mf.make_sample_id("  P1 ", "\tS1\n", 5) == "P1_S1_000005"


# ==========================================================================
# manifest -- write / append / read
# ==========================================================================


def test_manifest_round_trip_keeps_one_row_per_frame(tmp_path):
    path = tmp_path / "nested" / "manifest.jsonl"
    records = [_record(i) for i in range(3)]

    written = mf.write_manifest(path, records)

    assert written == path
    assert path.read_bytes().count(b"\n") == 3
    assert [r.to_dict() for r in mf.read_manifest(path)] == [r.to_dict() for r in records]


def test_manifest_round_trip_preserves_non_ascii_frame_paths(tmp_path):
    path = tmp_path / "manifest.jsonl"
    record = _record(0)
    record.frame_path = "frames/한글/0.png"

    mf.write_manifest(path, [record])

    assert "한글" in path.read_text(encoding="utf-8")
    assert mf.read_manifest(path)[0].frame_path == record.frame_path


def test_write_manifest_rejects_duplicates_before_opening_the_file(tmp_path):
    path = tmp_path / "manifest.jsonl"
    mf.write_manifest(path, [_record(0), _record(1)])
    before = path.read_bytes()

    with pytest.raises(ValueError, match="duplicate sample_id"):
        mf.write_manifest(path, [_record(2), _record(3), _record(2)])

    assert path.read_bytes() == before, "the previous manifest must survive a rejected write"


def test_write_manifest_rejects_an_unknown_label_before_opening_the_file(tmp_path):
    path = tmp_path / "manifest.jsonl"
    mf.write_manifest(path, [_record(0)])
    before = path.read_bytes()
    bad = _record(1)
    bad.gaze_label = "SIDEWAYS"

    with pytest.raises(ValueError, match="SIDEWAYS"):
        mf.write_manifest(path, [_record(2), bad])

    assert path.read_bytes() == before


def test_write_manifest_truncates_rather_than_appending(tmp_path):
    path = tmp_path / "manifest.jsonl"
    mf.write_manifest(path, [_record(i) for i in range(5)])

    mf.write_manifest(path, [_record(0)])

    assert len(mf.read_manifest(path)) == 1


def test_append_manifest_detects_a_duplicate_of_a_row_already_on_disk(tmp_path):
    path = tmp_path / "manifest.jsonl"
    mf.write_manifest(path, [_record(0), _record(1)])

    with pytest.raises(ValueError, match="duplicate sample_id"):
        mf.append_manifest(path, [_record(1)])


def test_append_manifest_validates_while_writing_so_earlier_rows_land(tmp_path):
    """The documented asymmetry with ``write_manifest``.

    ``append_manifest`` validates row by row as it writes, so a duplicate late
    in the batch leaves the rows before it on disk.  Locked in so that making
    the append atomic is a deliberate decision rather than an accident.
    """
    path = tmp_path / "manifest.jsonl"
    mf.write_manifest(path, [_record(0), _record(1)])

    with pytest.raises(ValueError, match="duplicate sample_id"):
        mf.append_manifest(path, [_record(2), _record(3), _record(1)])

    assert [r.sample_id for r in mf.read_manifest(path)] == [
        "P01_S01_000000",
        "P01_S01_000001",
        "P01_S01_000002",
        "P01_S01_000003",
    ]


def test_append_manifest_creates_a_missing_file_and_its_parent(tmp_path):
    path = tmp_path / "made" / "up" / "manifest.jsonl"

    written = mf.append_manifest(path, [_record(0)])

    assert written == path
    assert [r.sample_id for r in mf.read_manifest(path)] == ["P01_S01_000000"]


def test_append_manifest_rejects_a_duplicate_inside_the_new_batch(tmp_path):
    path = tmp_path / "manifest.jsonl"

    with pytest.raises(ValueError, match="duplicate sample_id"):
        mf.append_manifest(path, [_record(7), _record(7)])


def test_iter_manifest_skips_blank_lines(tmp_path):
    path = tmp_path / "manifest.jsonl"
    first = json.dumps(_record(0).to_dict())
    second = json.dumps(_record(1).to_dict())
    path.write_text(f"\n{first}\n   \n{second}\n\n", encoding="utf-8")

    assert [r.sample_id for r in mf.read_manifest(path)] == [
        "P01_S01_000000",
        "P01_S01_000001",
    ]


@pytest.mark.parametrize(
    "body, line_no, fragment",
    [
        ("{oops\n", 1, "invalid JSON"),
        ("\n\n{oops\n", 3, "invalid JSON"),
        ("[1, 2]\n", 1, "expected a JSON object, got list"),
        ('"a string"\n', 1, "expected a JSON object, got str"),
    ],
)
def test_iter_manifest_reports_the_path_and_line_of_a_bad_row(tmp_path, body, line_no, fragment):
    path = tmp_path / "manifest.jsonl"
    path.write_text(body, encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        mf.read_manifest(path)

    message = str(excinfo.value)
    assert message.startswith(f"{path}:{line_no}: ")
    assert fragment in message


def test_iter_manifest_streams_so_a_bad_row_does_not_hide_the_good_prefix(tmp_path):
    path = tmp_path / "manifest.jsonl"
    path.write_text(json.dumps(_record(0).to_dict()) + "\n{oops\n", encoding="utf-8")

    stream = mf.iter_manifest(path)

    assert next(stream).sample_id == "P01_S01_000000"
    with pytest.raises(ValueError, match=r"manifest\.jsonl:2:"):
        next(stream)


def test_write_manifest_stores_the_label_it_coerced(tmp_path):
    """Accepting a spelling and storing it verbatim is worse than rejecting it.

    ``splits`` compares labels against ``"CAMERA"``, so a manifest holding
    ``"camera"`` loses those frames from the split silently.
    """
    path = tmp_path / "manifest.jsonl"
    record = _record(0)
    record.gaze_label = "camera"

    mf.write_manifest(path, [record])

    assert mf.read_manifest(path)[0].gaze_label == GazeLabel.CAMERA.value


def test_write_manifest_coerces_the_stored_label_without_touching_the_caller(tmp_path):
    """Normalising the file is fine; editing the caller's records is not.

    ``extract_features`` keeps the same ``ManifestRecord`` objects after
    writing, so a write that mutated them would change what the run reports.
    """
    record = _record(0)
    record.gaze_label = "camera"

    mf.write_manifest(tmp_path / "manifest.jsonl", [record])

    assert record.gaze_label == "camera"


# ==========================================================================
# labels -- cue timeline structure
# ==========================================================================


@pytest.mark.parametrize("cues", [[], [_cue(0, "CAMERA")]])
def test_a_timeline_shorter_than_a_cue_plus_an_end_marker_is_rejected(cues):
    with pytest.raises(ValueError, match="at least one cue plus a final end marker"):
        _segments(cues)


def test_a_timeline_without_a_final_end_marker_is_rejected():
    with pytest.raises(ValueError, match="must end with an end marker"):
        _segments([_cue(0, "CAMERA"), _cue(20_000, "BOTTOM")])


def test_an_end_marker_in_the_middle_is_rejected():
    cues = [_cue(0, "CAMERA"), _cue(500, "END"), _cue(1000, "BOTTOM"), _cue(2000, "END")]

    with pytest.raises(ValueError, match="not the last one"):
        _segments(cues)


@pytest.mark.parametrize(
    "marker",
    [
        _cue(1000, "END"),
        _cue(1000, "end"),
        _cue(1000, "STOP"),
        _cue(1000, "EOF"),
        _cue(1000, "FINISH"),
        _cue(1000, ""),
        _cue(1000, None),
        _cue(1000),  # no cue key at all
        _cue(1000, "CAMERA", end=True),  # the explicit flag beats the cue text
    ],
)
def test_every_end_marker_spelling_only_supplies_the_recording_end(marker):
    segments = _segments([_cue(0, "CAMERA"), marker])

    assert [(s.start_ms, s.end_ms, s.label) for s in segments] == [(0, 1000, "CAMERA")]


def test_cue_entries_are_ordered_by_time_not_by_position_in_the_list():
    shuffled = [_cue(0, "CAMERA"), _cue(2000, "END"), _cue(1000, "BOTTOM")]

    segments = _segments(shuffled)

    assert [(s.start_ms, s.end_ms, s.label) for s in segments] == [
        (0, 1000, "CAMERA"),
        (1000, 2000, "BOTTOM"),
    ]


def test_a_cue_overwritten_at_the_same_instant_holds_no_frames():
    segments = _segments([_cue(0, "CAMERA"), _cue(0, "BOTTOM"), _cue(1000, "END")])

    assert [(s.start_ms, s.end_ms, s.label) for s in segments] == [(0, 1000, "BOTTOM")]


def test_a_timeline_covering_no_time_is_rejected():
    with pytest.raises(ValueError, match="covers no time"):
        _segments([_cue(0, "CAMERA"), _cue(0, "END")])


def test_a_negative_guard_is_rejected():
    with pytest.raises(ValueError, match="guard_ms must be >= 0"):
        _segments([_cue(0, "CAMERA"), _cue(1000, "END")], guard_ms=-1)


def test_a_cue_that_is_not_a_gaze_label_is_rejected():
    with pytest.raises(ValueError, match="LEFT"):
        _segments([_cue(0, "LEFT"), _cue(1000, "END")])


def test_segment_metadata_comes_from_the_participant_and_the_cue_entry():
    segments = _segments(
        [
            _cue(0, "CAMERA", condition="static_camera", lighting="dim"),
            _cue(500, "BOTTOM", condition="static_bottom"),
            _cue(1000, "END"),
        ],
        label_version="gaze_label_v2",
        source="manual",
        lighting="bright",
    )

    first, second = segments
    assert (first.participant_id, first.session_id) == ("P01", "S01")
    assert first.glasses is True
    assert first.device_group == "laptop_internal_cam"
    assert (first.condition, second.condition) == ("static_camera", "static_bottom")
    # a per-entry lighting wins; the keyword is only the default
    assert (first.lighting, second.lighting) == ("dim", "bright")
    assert {s.label_version for s in segments} == {"gaze_label_v2"}
    assert {s.source for s in segments} == {"manual"}


# ==========================================================================
# labels -- guard bands
# ==========================================================================


def test_the_guard_band_is_symmetric_around_a_label_change():
    segments = _segments(
        [
            _cue(0, "CAMERA", condition="c"),
            _cue(20_000, "BOTTOM", condition="c"),
            _cue(40_000, "END"),
        ],
        guard_ms=500,
        guard_at_start=False,
    )

    assert [(s.start_ms, s.end_ms, s.label) for s in segments] == [
        (0, 19_500, "CAMERA"),
        (19_500, 20_500, "IGNORE"),
        (20_500, 40_000, "BOTTOM"),
    ]


@pytest.mark.parametrize("guard_at_start", [False, True])
def test_a_condition_change_holding_the_same_label_costs_no_frames(guard_at_start):
    """The asymmetry doc 4-2 asks for: the guard band is an eye-movement budget.

    Switching ``static_camera`` -> ``alternating`` while the cue stays CAMERA
    instructs the participant to do nothing, so no frame may be thrown away.
    """
    condition_change = _segments(
        [
            _cue(0, "CAMERA", condition="static_camera"),
            _cue(10_000, "CAMERA", condition="alternating"),
            _cue(20_000, "END"),
        ],
        guard_ms=500,
        guard_at_start=guard_at_start,
    )
    label_change = _segments(
        [
            _cue(0, "CAMERA", condition="static_camera"),
            _cue(10_000, "BOTTOM", condition="alternating"),
            _cue(20_000, "END"),
        ],
        guard_ms=500,
        guard_at_start=guard_at_start,
    )

    for probe in (9_501, 9_999, 10_000, 10_499):
        assert lb.label_at(condition_change, probe) == "CAMERA", probe
        # the same boundary with the label flipping is guarded on both sides
        assert lb.label_at(label_change, probe) == "IGNORE", probe


def test_guard_at_start_ignores_the_onset_of_the_very_first_cue():
    guarded = _segments([_cue(0, "CAMERA"), _cue(5000, "END")], guard_ms=500, guard_at_start=True)
    unguarded = _segments([_cue(0, "CAMERA"), _cue(5000, "END")], guard_ms=500, guard_at_start=False)

    # the half of the window falling before the recording is clipped away,
    # never emitted as a negative start
    assert [(s.start_ms, s.end_ms, s.label) for s in guarded] == [
        (0, 500, "IGNORE"),
        (500, 5000, "CAMERA"),
    ]
    assert [(s.start_ms, s.end_ms, s.label) for s in unguarded] == [(0, 5000, "CAMERA")]


def test_a_guard_wider_than_a_block_swallows_the_whole_block():
    segments = _segments(
        [_cue(0, "CAMERA"), _cue(1000, "BOTTOM"), _cue(2000, "CAMERA"), _cue(3000, "END")],
        guard_ms=800,
        guard_at_start=False,
    )

    assert [(s.start_ms, s.end_ms, s.label) for s in segments] == [
        (0, 200, "CAMERA"),
        (200, 2800, "IGNORE"),
        (2800, 3000, "CAMERA"),
    ]
    assert "BOTTOM" not in {s.label for s in segments}
    assert all(s.end_ms > s.start_ms for s in segments), "no inverted interval"


def test_a_zero_guard_leaves_every_block_labelled():
    segments = _segments(
        [_cue(0, "CAMERA"), _cue(1000, "BOTTOM"), _cue(2000, "END")], guard_ms=0
    )

    assert [(s.start_ms, s.end_ms, s.label) for s in segments] == [
        (0, 1000, "CAMERA"),
        (1000, 2000, "BOTTOM"),
    ]


@pytest.mark.parametrize("guard_ms", [0, 1, 250, 500, 900])
@pytest.mark.parametrize("guard_at_start", [False, True])
def test_segments_tile_the_recording_with_no_gap_and_no_overlap(guard_ms, guard_at_start):
    cues = [
        _cue(0, "CAMERA", condition="c1"),
        _cue(1000, "BOTTOM", condition="c1"),
        _cue(2000, "CAMERA", condition="c2"),
        _cue(3000, "END"),
    ]

    segments = _segments(cues, guard_ms=guard_ms, guard_at_start=guard_at_start)

    assert segments[0].start_ms == 0
    assert segments[-1].end_ms == 3000
    for previous, following in zip(segments, segments[1:]):
        assert previous.end_ms == following.start_ms
    assert sum(s.duration_ms for s in segments) == 3000


# ==========================================================================
# labels -- merge_adjacent / label_at
# ==========================================================================


@pytest.mark.parametrize(
    "differing",
    [
        {"label": "BOTTOM"},
        {"condition": "other"},
        {"lighting": "dim"},
        {"glasses": True},
        {"device_group": "external_webcam"},
        {"label_version": "gaze_label_v2"},
        {"source": "corrected"},
    ],
)
def test_merge_adjacent_refuses_to_join_segments_differing_in_any_field(differing):
    merged = lb.merge_adjacent([_seg(0, 100), _seg(100, 200, **differing)])

    assert len(merged) == 2


def test_merge_adjacent_joins_touching_segments_that_agree_everywhere():
    merged = lb.merge_adjacent([_seg(100, 200), _seg(0, 100), _seg(200, 300)])

    assert [(s.start_ms, s.end_ms) for s in merged] == [(0, 300)]


@pytest.mark.parametrize(
    "second, expected",
    [
        ((100, 200), (0, 200)),   # touching
        ((50, 200), (0, 200)),    # overlapping
        ((25, 75), (0, 100)),     # fully contained -- must not shrink the result
    ],
)
def test_merge_adjacent_absorbs_overlapping_intervals(second, expected):
    merged = lb.merge_adjacent([_seg(0, 100), _seg(*second)])

    assert [(s.start_ms, s.end_ms) for s in merged] == [expected]


def test_merge_adjacent_returns_copies_and_never_mutates_the_caller():
    first, second = _seg(0, 100), _seg(100, 200)
    original = [first.to_dict(), second.to_dict()]

    merged = lb.merge_adjacent([first, second])
    merged[0].end_ms = 999_999
    merged[0].label = "IGNORE"

    assert merged[0] is not first
    assert [first.to_dict(), second.to_dict()] == original


def test_merge_adjacent_on_an_empty_take_returns_an_empty_list():
    assert lb.merge_adjacent([]) == []


@pytest.mark.parametrize(
    "t_ms, expected",
    [
        (-1, "IGNORE"),      # before the recording
        (0, "CAMERA"),       # start_ms is inside
        (99, "CAMERA"),
        (100, "BOTTOM"),     # end_ms belongs to the next block
        (199, "BOTTOM"),
        (200, "IGNORE"),     # end_ms of the last block is outside
        (10 ** 9, "IGNORE"),
    ],
)
def test_label_at_uses_half_open_intervals_and_ignores_the_outside(t_ms, expected):
    segments = [_seg(0, 100, "CAMERA"), _seg(100, 200, "BOTTOM")]

    assert lb.label_at(segments, t_ms) == expected


def test_label_at_on_an_empty_label_file_is_ignore():
    assert lb.label_at([], 0) == GazeLabel.IGNORE.value


def test_segment_summary_of_an_empty_take_reports_zero_not_a_division_error():
    summary = lb.segment_summary([])

    assert summary["n_segments"] == 0
    assert summary["total_ms"] == 0
    assert summary["ignore_ratio"] == 0.0
    assert (summary["start_ms"], summary["end_ms"]) == (0, 0)


def test_segment_summary_accumulates_duration_per_label():
    summary = lb.segment_summary(
        [_seg(0, 100, "CAMERA"), _seg(100, 300, "IGNORE"), _seg(300, 400, "CAMERA")]
    )

    assert summary["duration_ms_by_label"] == {"CAMERA": 200, "IGNORE": 200}
    assert summary["ignore_ratio"] == pytest.approx(0.5)
    assert (summary["start_ms"], summary["end_ms"]) == (0, 400)


# ==========================================================================
# labels -- persistence
# ==========================================================================


def test_segments_round_trip_through_a_label_file(tmp_path):
    path = tmp_path / "nested" / "labels.json"
    segments = _segments(
        [
            _cue(0, "CAMERA", condition="static_camera"),
            _cue(1000, "BOTTOM", condition="static_bottom"),
            _cue(2000, "END"),
        ],
        guard_ms=100,
    )

    written = lb.save_segments(path, segments)

    assert written == path
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema"] == lb.SEGMENT_FILE_SCHEMA
    assert payload["n_segments"] == len(segments)
    assert [s.to_dict() for s in lb.load_segments(path)] == [s.to_dict() for s in segments]


def test_load_segments_accepts_a_bare_json_list(tmp_path):
    path = tmp_path / "bare.json"
    segments = [_seg(0, 100, "CAMERA"), _seg(100, 200, "BOTTOM")]
    path.write_text(json.dumps([s.to_dict() for s in segments]), encoding="utf-8")

    assert [s.to_dict() for s in lb.load_segments(path)] == [s.to_dict() for s in segments]


def test_load_segments_tolerates_a_field_a_later_schema_added(tmp_path):
    path = tmp_path / "future.json"
    payload = _seg(0, 100).to_dict()
    payload["reviewer_note"] = "looks fine"
    path.write_text(json.dumps({"segments": [payload]}), encoding="utf-8")

    assert lb.load_segments(path)[0].label == "CAMERA"


def test_load_segments_of_a_file_without_segments_is_empty_not_an_error(tmp_path):
    path = tmp_path / "header_only.json"
    path.write_text(json.dumps({"schema": lb.SEGMENT_FILE_SCHEMA}), encoding="utf-8")

    assert lb.load_segments(path) == []


def test_load_segments_names_the_file_when_the_payload_is_not_a_list(tmp_path):
    path = tmp_path / "wrong.json"
    path.write_text(json.dumps({"segments": {"start_ms": 0}}), encoding="utf-8")

    with pytest.raises(ValueError, match="expected a list of segments, got dict"):
        lb.load_segments(path)


# ==========================================================================
# splits -- participant_split
# ==========================================================================


def test_participant_split_ignores_input_order_and_duplicates():
    ids = [f"P{i:02d}" for i in range(10)]

    forward = sp.participant_split(ids)
    backward = sp.participant_split(list(reversed(ids)))
    with_duplicates = sp.participant_split(ids + ids)

    assert forward == backward == with_duplicates


def test_participant_split_is_deterministic_per_seed_and_moves_with_it():
    ids = [f"P{i:02d}" for i in range(12)]

    assert sp.participant_split(ids, seed=42) == sp.participant_split(ids, seed=42)
    assert sp.participant_split(ids, seed=42) != sp.participant_split(ids, seed=7)


def test_participant_split_only_depends_on_the_ratio_proportions():
    ids = [f"P{i:02d}" for i in range(10)]

    assert sp.participant_split(ids, (0.6, 0.2, 0.2)) == sp.participant_split(ids, (3, 1, 1))


@pytest.mark.parametrize("n_participants", list(range(1, 13)))
def test_every_participant_lands_in_exactly_one_split(n_participants):
    ids = [f"P{i:02d}" for i in range(n_participants)]

    splits = sp.participant_split(ids)

    assert set(splits) == set(sp.SPLIT_NAMES)
    members = [pid for name in sp.SPLIT_NAMES for pid in splits[name]]
    assert sorted(members) == sorted(ids)
    assert len(members) == len(set(members)), "doc 4-3: no participant on both sides"


@pytest.mark.parametrize(
    "n_participants, expected_empty",
    [(1, ["val", "test"]), (2, ["test"]), (3, [])],
)
def test_a_split_is_left_empty_rather_than_reusing_a_participant(n_participants, expected_empty):
    ids = [f"P{i:02d}" for i in range(n_participants)]

    splits = sp.participant_split(ids)

    assert [name for name in sp.SPLIT_NAMES if not splits[name]] == expected_empty
    assert splits["train"], "train has the highest ratio and is never starved"


def test_a_zero_ratio_split_is_not_repaired_into_existence():
    ids = [f"P{i:02d}" for i in range(10)]

    splits = sp.participant_split(ids, (0.8, 0.2, 0.0))

    assert splits["test"] == []
    assert len(splits["train"]) + len(splits["val"]) == 10


@pytest.mark.parametrize(
    "ratios, fragment",
    [
        ((0.5, 0.5), "expected 3 ratios"),
        ((0.25, 0.25, 0.25, 0.25), "expected 3 ratios"),
        ((0.6, -0.1, 0.5), "ratios must be >= 0"),
        ((0.0, 0.0, 0.0), "must not all be zero"),
    ],
)
def test_participant_split_rejects_ratios_it_cannot_allocate(ratios, fragment):
    with pytest.raises(ValueError, match=fragment):
        sp.participant_split(["P01", "P02", "P03"], ratios)


def test_split_frame_selects_only_that_split_and_hands_back_a_copy():
    df = _table([("P01", "S1", [("c", "CAMERA", 2)]), ("P02", "S1", [("c", "CAMERA", 2)])])
    splits = {"train": ["P01"], "val": ["P02"], "test": []}

    train = sp.split_frame(df, splits, "train")
    train.loc[:, "label"] = "MUTATED"

    assert set(train["participant_id"]) == {"P01"}
    assert sp.split_frame(df, splits, "test").empty
    assert set(df["label"]) == {"CAMERA"}, "split_frame must not alias the source table"


def test_split_frame_names_the_problem_for_a_bad_split_or_a_bad_table():
    df = _table([("P01", "S1", [("c", "CAMERA", 1)])])

    with pytest.raises(KeyError, match="unknown split"):
        sp.split_frame(df, {"train": ["P01"]}, "holdout")
    with pytest.raises(ValueError, match=r"missing required column\(s\): \['participant_id'\]"):
        sp.split_frame(df.drop(columns=["participant_id"]), {"train": ["P01"]}, "train")


# ==========================================================================
# splits -- calibration / evaluation partition
# ==========================================================================


def test_an_explicit_calibration_condition_beats_the_label_run_heuristic():
    df = _table(
        [
            (
                "P01",
                "S1",
                [
                    ("calib_camera", "CAMERA", 3),
                    ("calib_bottom", "BOTTOM", 3),
                    ("free_talk", "CAMERA", 4),
                ],
            )
        ]
    )

    calibration = sp.calibration_frames(df, "P01")

    assert calibration["t_ms"].tolist() == [0, 500, 1000, 1500, 2000, 2500]
    assert set(calibration["condition"]) == {"calib_camera", "calib_bottom"}
    assert sp.evaluation_frames(df, "P01")["t_ms"].tolist() == [3000, 3500, 4000, 4500]


def test_evaluation_starts_after_the_calibration_block_not_the_last_calibration_frame():
    """doc 4-3: the tail of a 20 s static block is the same pose, held still."""
    df = _table(
        [
            (
                "P01",
                "S1",
                [
                    ("static_camera", "CAMERA", 10),   # 0 .. 4500
                    ("static_bottom", "BOTTOM", 10),   # 5000 .. 9500
                    ("alternating", "CAMERA", 4),      # 10000 .. 11500
                ],
            )
        ]
    )

    calibration = sp.calibration_frames(df, "P01")
    evaluation = sp.evaluation_frames(df, "P01")

    # only the first 2 s of each run are calibration ...
    assert calibration["t_ms"].tolist() == [0, 500, 1000, 1500, 5000, 5500, 6000, 6500]
    # ... but the boundary is the end of the *blocks* those frames came from
    assert sp.calibration_boundary_ms(df, "P01") == ("S1", 9500)
    assert evaluation["t_ms"].tolist() == [10_000, 10_500, 11_000, 11_500]
    assert not set(calibration["sample_id"]) & set(evaluation["sample_id"])


def test_without_a_condition_column_the_boundary_falls_back_to_the_last_frame():
    """The documented weaker guarantee; worth fixing in the collector, not here."""
    df = _table(
        [("P01", "S1", [("static_camera", "CAMERA", 10), ("static_bottom", "BOTTOM", 10)])]
    ).drop(columns=["condition"])

    assert sp.calibration_boundary_ms(df, "P01") == ("S1", 6500)
    assert sp.evaluation_frames(df, "P01")["t_ms"].min() == 7000


def test_a_single_condition_session_also_falls_back_to_the_last_frame():
    df = _table([("P01", "S1", [("free_talk", "CAMERA", 6), ("free_talk", "BOTTOM", 6)])])

    session_id, boundary = sp.calibration_boundary_ms(df, "P01")

    assert session_id == "S1"
    assert boundary == int(sp.calibration_frames(df, "P01")["t_ms"].max())


@pytest.mark.parametrize(
    "drop_ignore, expected", [(True, ["CAMERA"]), (False, ["CAMERA", "IGNORE"])]
)
def test_ignore_frames_are_never_calibration_and_leave_evaluation_by_default(drop_ignore, expected):
    df = _table(
        [
            (
                "P01",
                "S1",
                [
                    ("static", "IGNORE", 1),    # 0
                    ("static", "CAMERA", 2),    # 500, 1000
                    ("static", "IGNORE", 1),    # 1500
                    ("static", "BOTTOM", 2),    # 2000, 2500
                    ("free", "IGNORE", 1),      # 3000
                    ("free", "CAMERA", 4),      # 3500 .. 5000
                ],
            )
        ]
    )

    calibration = sp.calibration_frames(df, "P01")
    evaluation = sp.evaluation_frames(df, "P01", drop_ignore=drop_ignore)

    assert calibration["label"].tolist() == ["CAMERA", "CAMERA", "BOTTOM", "BOTTOM"]
    assert "IGNORE" not in set(calibration["label"]), "IGNORE frames carry no ground truth"
    assert sorted(set(evaluation["label"])) == expected


def test_every_row_of_a_later_session_is_evaluation():
    df = pd.concat(
        [
            _table([("P01", "S1", [("static_camera", "CAMERA", 4),
                                   ("static_bottom", "BOTTOM", 4)])]),
            _table([("P01", "S2", [("free_talk", "CAMERA", 3)])]),
        ],
        ignore_index=True,
    )
    df.loc[df["session_id"] == "S2", "t_ms"] += 100_000

    calibration = sp.calibration_frames(df, "P01")
    evaluation = sp.evaluation_frames(df, "P01")

    assert set(calibration["session_id"]) == {"S1"}
    assert int((evaluation["session_id"] == "S2").sum()) == 3


def test_the_calibration_window_length_follows_the_calibration_config():
    df = _table([("P01", "S1", [("static_camera", "CAMERA", 10), ("static_bottom", "BOTTOM", 10)])])

    default = sp.calibration_frames(df, "P01")
    short = sp.calibration_frames(
        df, "P01", CalibrationConfig(camera_seconds=0.6, bottom_seconds=0.6)
    )

    assert len(default) == 8   # 2.0 s -> 4 frames per class at 500 ms spacing
    assert len(short) == 4     # 0.6 s -> 2 frames per class
    assert set(short["sample_id"]) < set(default["sample_id"])


@pytest.mark.parametrize(
    "cfg",
    [None, CalibrationConfig(), VisionConfig(), SimpleNamespace(calibration=CalibrationConfig())],
)
def test_the_cfg_argument_accepts_every_shape_the_callers_have(cfg):
    df = _table([("P01", "S1", [("static_camera", "CAMERA", 4), ("static_bottom", "BOTTOM", 4)])])

    assert not sp.calibration_frames(df, "P01", cfg).empty


@pytest.mark.parametrize("cfg", [123, "calibration", object()])
def test_an_unusable_cfg_is_named_rather_than_silently_defaulted(cfg):
    df = _table([("P01", "S1", [("static_camera", "CAMERA", 4)])])

    with pytest.raises(TypeError, match="expected VisionConfig or CalibrationConfig"):
        sp.calibration_frames(df, "P01", cfg)


def test_an_unknown_participant_yields_empty_frames_and_no_boundary():
    df = _table([("P01", "S1", [("static_camera", "CAMERA", 4)])])

    assert sp.calibration_frames(df, "P99").empty
    assert sp.calibration_boundary_ms(df, "P99") is None
    assert sp.evaluation_frames(df, "P99").empty


@pytest.mark.parametrize("column", ["participant_id", "session_id", "t_ms", "label"])
def test_a_missing_required_column_is_named_not_guessed(column):
    df = _table([("P01", "S1", [("static_camera", "CAMERA", 4)])]).drop(columns=[column])

    with pytest.raises(ValueError, match=rf"missing required column\(s\): \['{column}'\]"):
        sp.per_participant_partition(df)


def test_per_participant_partition_keeps_the_two_halves_disjoint():
    df = pd.concat(
        [
            _table([("P01", "S1", [("static_camera", "CAMERA", 8),
                                   ("static_bottom", "BOTTOM", 8),
                                   ("free", "CAMERA", 4)])]),
            _table([("P02", "S1", [("calib_camera", "CAMERA", 4),
                                   ("calib_bottom", "BOTTOM", 4),
                                   ("free", "BOTTOM", 4)])]),
        ],
        ignore_index=True,
    )

    partition = sp.per_participant_partition(df)

    assert sorted(partition) == ["P01", "P02"]
    for participant, halves in partition.items():
        calibration = set(halves["calibration"]["sample_id"])
        evaluation = set(halves["evaluation"]["sample_id"])
        assert calibration and evaluation, participant
        assert not calibration & evaluation, participant


def test_a_frame_reachable_from_both_halves_raises_rather_than_leaking():
    """doc 4-3 leakage guard, checked on real ``sample_id`` values.

    A collector bug that re-emits a calibration frame's ``sample_id`` later in
    the take is invisible to the interval arithmetic -- the second row sits well
    after the boundary -- so the partition has to catch it by identity.
    """
    df = _table([("P01", "S1", [("static_camera", "CAMERA", 8),
                                ("static_bottom", "BOTTOM", 8),
                                ("free", "CAMERA", 4)])])
    df.loc[df.index[-1], "sample_id"] = df["sample_id"].iloc[0]

    with pytest.raises(RuntimeError, match=r"P01: 1 frame\(s\) are in both calibration"):
        sp.per_participant_partition(df)


def test_the_partition_still_runs_on_a_table_without_sample_ids():
    """``_frame_keys`` falls back to ``(session_id, t_ms)``.

    That fallback keeps the partition working, but it cannot see the duplicated
    frame the ``sample_id`` version catches: the copy sits at the calibration
    frame's own ``t_ms``, so it joins calibration a second time instead of
    reaching evaluation.  See ``bugs_found`` -- the guard is only real when the
    table carries ``sample_id``.
    """
    df = _table([("P01", "S1", [("static_camera", "CAMERA", 8),
                                ("static_bottom", "BOTTOM", 8),
                                ("free", "CAMERA", 4)])]).drop(columns=["sample_id"])
    corrupted = pd.concat([df, df.iloc[[0]]], ignore_index=True)

    with pytest.warns(RuntimeWarning, match="sample_id"):
        partition = sp.per_participant_partition(corrupted)

    calibration = partition["P01"]["calibration"]
    evaluation = partition["P01"]["evaluation"]
    assert (calibration["t_ms"] == 0).sum() == 2, "the duplicate joined calibration twice"
    assert not set(zip(calibration["session_id"], calibration["t_ms"])) & set(
        zip(evaluation["session_id"], evaluation["t_ms"])
    )


def test_the_partition_says_out_loud_that_it_could_not_run_the_real_check():
    """A degraded check must not be reported by silence.

    Without ``sample_id`` the leakage guard re-reads the interval arithmetic it
    exists to verify independently, and the old code did that without a word.
    """
    df = _table([("P01", "S1", [("static_camera", "CAMERA", 4),
                                ("static_bottom", "BOTTOM", 4),
                                ("free", "CAMERA", 4)])]).drop(columns=["sample_id"])

    with pytest.warns(RuntimeWarning, match=r"leakage check degrades"):
        sp.per_participant_partition(df)


def test_the_partition_is_silent_when_it_can_check_the_sample_ids():
    df = _table([("P01", "S1", [("static_camera", "CAMERA", 4),
                                ("static_bottom", "BOTTOM", 4),
                                ("free", "CAMERA", 4)])])

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        sp.per_participant_partition(df)


# ==========================================================================
# features_table -- schema
# ==========================================================================


def test_the_feature_table_has_forty_one_uniquely_named_columns():
    assert len(ft.FEATURE_COLUMNS) == 41
    assert len(set(ft.FEATURE_COLUMNS)) == 41
    assert ft._FEATURE_COLUMN_SET == set(ft.FEATURE_COLUMNS)


def test_the_dtype_groups_partition_the_feature_columns():
    """A new column must be classified, not silently inherit ``float``."""
    groups = {
        "bool": set(ft._BOOL_COLUMNS),
        "int": set(ft._INT_COLUMNS),
        "str": set(ft._STR_COLUMNS),
        "float": set(ft._FLOAT_COLUMNS),
    }

    assert set().union(*groups.values()) == set(ft.FEATURE_COLUMNS)
    assert sum(len(members) for members in groups.values()) == len(ft.FEATURE_COLUMNS)


@pytest.mark.parametrize(
    "mutate, fragment",
    [
        (
            lambda mp: mp.setattr(ft, "FEATURE_COLUMNS", ft.FEATURE_COLUMNS + ["gaze_roll"]),
            r"unclassified=\['gaze_roll'\]",
        ),
        (
            lambda mp: mp.setattr(ft, "_INT_COLUMNS", ft._INT_COLUMNS + ("gaze_yaw",)),
            r"in two groups=\['gaze_yaw'\]",
        ),
        (
            lambda mp: mp.setattr(ft, "_STR_COLUMNS", ft._STR_COLUMNS + ("operator_note",)),
            r"not a feature column=\['operator_note'\]",
        ),
    ],
)
def test_a_column_no_dtype_group_claims_is_a_loud_import_failure(monkeypatch, mutate, fragment):
    """The check that replaces "unclassified means float".

    ``_FLOAT_COLUMNS`` used to be the complement of the other three groups, so a
    new column became a float and ``_coerce_dtypes`` NaN-ed its values away with
    nothing raised.  The guard runs at import; here it is called directly
    because a module already imported cannot be re-imported wrongly.
    """
    mutate(monkeypatch)

    with pytest.raises(RuntimeError, match=fragment):
        ft._assert_dtype_partition()


def test_the_dtype_partition_check_passes_as_shipped():
    ft._assert_dtype_partition()  # the guard that ran at import, run again


def test_a_row_is_exactly_the_feature_columns_in_order():
    row = ft.observations_to_rows(_observation(), _gaze(), _decision(), META, "S03", "BOTTOM")

    assert list(row) == ft.FEATURE_COLUMNS


@pytest.mark.parametrize("direction", ["column added to the list", "column dropped from the set"])
def test_feature_columns_and_the_row_builder_must_move_together(monkeypatch, direction):
    if direction == "column added to the list":
        monkeypatch.setattr(ft, "FEATURE_COLUMNS", ft.FEATURE_COLUMNS + ["gaze_roll"])
    else:
        monkeypatch.setattr(ft, "_FEATURE_COLUMN_SET", ft._FEATURE_COLUMN_SET - {"latency_ms"})

    with pytest.raises(RuntimeError, match="row does not match FEATURE_COLUMNS"):
        ft.observations_to_rows(_observation(), _gaze(), _decision(), META, "S03", "BOTTOM")


def test_missing_values_stay_none_instead_of_a_neutral_looking_number():
    row = ft.observations_to_rows(_observation(), None, None, META, "S03", None)

    empty = {column for column, value in row.items() if value is None}
    assert empty == {
        "label", "backbone", "gaze_yaw", "gaze_pitch", "gaze_confidence",
        "invalid_reason", "pred_label", "p_camera", "p_bottom",
        "uncertain_reason", "inference_ms", "latency_ms",
    }
    # the two a "sensible default" would most plausibly corrupt
    assert row["p_camera"] is None, "an absent decision must not become p=0.5"
    assert row["gaze_pitch"] is None, "an absent gaze must not become 0 rad"


def test_a_backbone_name_survives_a_frame_the_backbone_could_not_process():
    row = ft.observations_to_rows(
        _observation(), None, None, META, "S03", "CAMERA", backbone="l2cs"
    )

    assert row["backbone"] == "l2cs"
    assert row["gaze_yaw"] is None


def test_a_segment_label_supplies_provenance_a_bare_string_cannot():
    segment = SegmentLabel(
        participant_id="P01", session_id="S03", start_ms=0, end_ms=1000, label="BOTTOM",
        condition="static_bottom", glasses=False, lighting="dim",
        device_group="external_webcam", label_version="gaze_label_v2", source="corrected",
    )

    from_segment = ft.observations_to_rows(_observation(), None, None, META, "S03", segment)
    from_string = ft.observations_to_rows(_observation(), None, None, META, "S03", "bottom")

    assert from_segment["label_version"] == "gaze_label_v2"
    assert from_segment["label_source"] == "corrected"
    assert (from_segment["condition"], from_segment["lighting"]) == ("static_bottom", "dim")
    assert from_segment["device_group"] == "external_webcam"
    # a bare string carries no provenance, so the defaults are used -- but the
    # label itself is still normalised through GazeLabel
    assert from_string["label"] == "BOTTOM"
    assert (from_string["label_version"], from_string["label_source"]) == (
        "gaze_label_v1", "protocol",
    )
    assert (from_string["condition"], from_string["lighting"]) == ("unknown", "normal")


@pytest.mark.parametrize("override", [{"condition": "override_c"}, {"lighting": "override_l"}])
def test_an_explicit_condition_or_lighting_overrides_the_segment(override):
    segment = SegmentLabel(participant_id="P01", session_id="S03", start_ms=0, end_ms=1,
                           label="CAMERA", condition="from_segment", lighting="from_segment")

    row = ft.observations_to_rows(_observation(), None, None, META, "S03", segment, **override)

    key, value = next(iter(override.items()))
    assert row[key] == value


def test_the_row_id_is_the_manifest_join_key():
    row = ft.observations_to_rows(_observation(frame_id=194), None, None,
                                  ParticipantMeta(participant_id="P07"), "S03", None)

    assert row["sample_id"] == mf.make_sample_id("P07", "S03", 194)


def test_an_empty_run_still_yields_every_column():
    frame = ft.rows_to_frame([])

    assert list(frame.columns) == ft.FEATURE_COLUMNS
    assert len(frame) == 0


def test_a_row_built_from_the_real_pipeline_matches_the_schema(face_observation):
    """The same flattening, on a FrameObservation the shipped preprocess made."""
    if face_observation is None:
        pytest.skip("preprocess returned no observation for the fixture")

    row = ft.observations_to_rows(face_observation, None, None, META, "S01", "CAMERA")

    assert list(row) == ft.FEATURE_COLUMNS
    assert row["sample_id"] == mf.make_sample_id("P01", "S01", face_observation.frame_id)
    assert isinstance(row["face_valid"], bool)
    assert math.isfinite(row["q_backlight_ratio"])


# ==========================================================================
# features_table -- dtypes
# ==========================================================================


def _sparse_rows():
    """One fully populated frame plus one that produced no gaze and no decision."""
    full = ft.observations_to_rows(_observation(1, 125), _gaze(), _decision(), META, "S03", "BOTTOM")
    empty = ft.observations_to_rows(_observation(2, 250), None, None, META, "S03", None)
    return [full, empty]


def test_a_complete_column_keeps_its_plain_dtype():
    frame = ft.rows_to_frame(_sparse_rows())

    assert frame["frame_id"].dtype == "int64"
    assert frame["glasses"].dtype == "bool"
    assert frame["face_valid"].dtype == "bool"


def test_a_column_with_a_hole_becomes_the_nullable_dtype():
    rows = _sparse_rows()
    rows[1]["frame_id"] = None
    rows[1]["glasses"] = None

    frame = ft.rows_to_frame(rows)

    assert frame["frame_id"].dtype == "Int64"
    assert frame["frame_id"].isna().tolist() == [False, True]
    assert frame["glasses"].dtype == "boolean"
    assert frame["glasses"].isna().tolist() == [False, True]


def test_string_columns_stay_object_so_a_missing_value_still_equals_none():
    frame = ft.rows_to_frame(_sparse_rows())

    for column in ("invalid_reason", "pred_label", "uncertain_reason", "label"):
        assert frame[column].dtype == object, column
    assert frame["invalid_reason"].iloc[0] is None
    assert frame["label"].tolist() == ["BOTTOM", None]


def test_a_float_column_with_a_hole_uses_nan_not_a_sentinel():
    frame = ft.rows_to_frame(_sparse_rows())

    assert frame["p_camera"].dtype == "float64"
    assert frame["p_camera"].iloc[0] == pytest.approx(1.0 / 3.0)
    assert math.isnan(frame["p_camera"].iloc[1])


@pytest.mark.parametrize(
    "token, expected",
    [("true", True), ("TRUE", True), ("t", True), ("yes", True), ("Y", True), ("1", True),
     ("false", False), ("F", False), ("no", False), ("0", False), ("", None), (None, None)],
)
def test_boolean_columns_read_every_spelling_a_csv_can_produce(token, expected):
    assert ft._bool_scalar(token) is expected


def test_a_boolean_column_refuses_a_value_it_cannot_interpret():
    with pytest.raises(ValueError, match="cannot read 'maybe' as a boolean"):
        ft._bool_scalar("maybe")


def test_columns_a_later_stage_added_keep_their_own_dtypes():
    frame = ft.rows_to_frame(_sparse_rows())
    frame["bucket_backlight"] = pd.Series([True, False], dtype="boolean")

    coerced = ft._coerce_dtypes(frame)

    assert coerced["bucket_backlight"].dtype == "boolean"


# ==========================================================================
# features_table -- storage
# ==========================================================================


@pytest.mark.parametrize(
    "requested, with_engine, without_engine",
    [
        ("features", "features.parquet", "features.csv.gz"),
        ("features.parquet", "features.parquet", "features.csv.gz"),
        ("features.pq", "features.pq", "features.csv.gz"),
        ("features.csv", "features.csv", "features.csv"),
        ("features.csv.gz", "features.csv.gz", "features.csv.gz"),
    ],
)
def test_resolve_table_path_rewrites_only_what_the_machine_cannot_write(
    monkeypatch, tmp_path, requested, with_engine, without_engine
):
    monkeypatch.setattr(ft, "parquet_available", lambda: True)
    assert ft.resolve_table_path(tmp_path / requested).name == with_engine

    monkeypatch.setattr(ft, "parquet_available", lambda: False)
    assert ft.resolve_table_path(tmp_path / requested).name == without_engine


def test_parquet_available_reads_the_force_env_only_when_the_cache_is_cleared(monkeypatch):
    monkeypatch.setenv(ft.FORCE_CSV_ENV, "1")
    ft.parquet_available.cache_clear()

    assert ft.parquet_available() is False
    assert ft.table_suffix() == ".csv.gz"

    monkeypatch.delenv(ft.FORCE_CSV_ENV)
    assert ft.parquet_available() is False, "the answer stays cached until cache_clear()"
    ft.parquet_available.cache_clear()
    assert ft.parquet_available() is _has_parquet_engine()


def test_save_table_returns_the_path_it_actually_wrote(tmp_path, force_csv):
    frame = ft.rows_to_frame(_sparse_rows())

    written = ft.save_table(frame, tmp_path / "features.parquet")

    assert written == tmp_path / "features.csv.gz"
    assert written.is_file()
    assert not (tmp_path / "features.parquet").exists()


def test_save_table_creates_the_output_directory(tmp_path, force_csv):
    written = ft.save_table(ft.rows_to_frame(_sparse_rows()), tmp_path / "a" / "b" / "features")

    assert written == tmp_path / "a" / "b" / "features.csv.gz"
    assert written.is_file()


def test_the_csv_backend_round_trips_the_table_unchanged(tmp_path, force_csv):
    frame = ft.rows_to_frame(_sparse_rows())

    written = ft.save_table(frame, tmp_path / "features")
    reloaded = ft.load_table(tmp_path / "features")

    assert written.name == "features.csv.gz"
    assert_frame_equal(frame, reloaded)
    # spelled out, because "round-trips bit-exactly" is the module's own claim
    assert reloaded["gaze_yaw"].iloc[0] == 0.1 + 0.2
    assert reloaded["p_camera"].iloc[0] == 1.0 / 3.0


@requires_parquet
def test_the_parquet_backend_round_trips_the_table_unchanged(tmp_path):
    frame = ft.rows_to_frame(_sparse_rows())

    written = ft.save_table(frame, tmp_path / "features")
    reloaded = ft.load_table(tmp_path / "features")

    assert written.name == "features.parquet"
    assert_frame_equal(frame, reloaded)
    assert reloaded["gaze_yaw"].iloc[0] == 0.1 + 0.2
    assert reloaded["p_camera"].iloc[0] == 1.0 / 3.0


@requires_parquet
def test_load_table_resolves_a_name_written_by_the_other_backend(tmp_path):
    frame = ft.rows_to_frame(_sparse_rows())
    ft.save_table(frame, tmp_path / "features.parquet")

    # a config or report that recorded the CSV name still finds the parquet file
    assert_frame_equal(frame, ft.load_table(tmp_path / "features.csv.gz"))
    assert_frame_equal(frame, ft.load_table(tmp_path / "features"))


def test_load_table_lists_every_name_it_tried(tmp_path):
    with pytest.raises(FileNotFoundError) as excinfo:
        ft.load_table(tmp_path / "missing.parquet")

    message = str(excinfo.value)
    for suffix in (".parquet", ".pq", ".csv.gz", ".csv"):
        assert f"missing{suffix}" in message


def test_save_table_keeps_extra_columns_after_the_canonical_ones(tmp_path, force_csv):
    frame = ft.rows_to_frame(_sparse_rows())
    frame.insert(0, "bucket", ["glasses_dim", "small_face"])

    reloaded = ft.load_table(ft.save_table(frame, tmp_path / "features"))

    assert list(reloaded.columns) == ft.FEATURE_COLUMNS + ["bucket"]
    assert reloaded["bucket"].tolist() == ["glasses_dim", "small_face"]


def test_save_table_writes_the_canonical_columns_it_has_when_some_are_absent(tmp_path, force_csv):
    frame = ft.rows_to_frame(_sparse_rows())[["label", "t_ms", "sample_id"]]

    reloaded = ft.load_table(ft.save_table(frame, tmp_path / "partial"))

    assert list(reloaded.columns) == ["sample_id", "t_ms", "label"]


def _zero_padded_frame() -> pd.DataFrame:
    row = ft.observations_to_rows(
        _observation(), None, None, ParticipantMeta(participant_id="07"), "03", "CAMERA"
    )
    return ft.rows_to_frame([row])


def test_the_csv_backend_preserves_zero_padded_string_ids(tmp_path, force_csv):
    """An identity column is text; an inferring reader made "07" the number 7.

    The id is the join key to the doc 17 manifest, so a table that came back
    through the fallback backend joined nothing.
    """
    frame = _zero_padded_frame()

    reloaded = ft.load_table(ft.save_table(frame, tmp_path / "features"))

    assert reloaded["participant_id"].iloc[0] == "07"
    assert reloaded["session_id"].iloc[0] == "03"
    assert reloaded["sample_id"].iloc[0] == "07_03_000007"


@requires_parquet
def test_the_parquet_backend_preserves_zero_padded_string_ids_too(tmp_path):
    """The backend that was always right stays right -- that is the target."""
    frame = _zero_padded_frame()

    reloaded = ft.load_table(ft.save_table(frame, tmp_path / "features"))

    assert reloaded["participant_id"].iloc[0] == "07"
    assert reloaded["session_id"].iloc[0] == "03"


def test_pinning_the_csv_string_dtypes_does_not_cost_the_missing_values(tmp_path, force_csv):
    """doc 19 filters buckets on ``None``, so an empty field must not become "".

    Reading the string columns as text and only ``""`` as NA has to leave a
    missing value comparing equal to ``None`` exactly as parquet does.
    """
    frame = ft.rows_to_frame(_sparse_rows())

    reloaded = ft.load_table(ft.save_table(frame, tmp_path / "features"))

    assert reloaded["invalid_reason"].iloc[0] is None
    assert reloaded["label"].tolist() == ["BOTTOM", None]
    assert reloaded["pred_label"].iloc[1] is None
    assert reloaded["uncertain_reason"].iloc[1] is None


def test_a_string_column_spelled_like_a_missing_value_survives_the_csv(tmp_path, force_csv):
    """``NA`` is a condition name, not a hole.

    Parquet always kept it; the CSV reader used to turn it into ``None`` from
    the default NA token list, so the two backends disagreed on a doc 19 bucket
    key.  Only the empty field means missing now.
    """
    rows = _sparse_rows()
    rows[0]["condition"] = "NA"
    rows[1]["condition"] = "None"
    frame = ft.rows_to_frame(rows)

    reloaded = ft.load_table(ft.save_table(frame, tmp_path / "features"))

    assert reloaded["condition"].tolist() == ["NA", "None"]
