"""입력 · 출력 계약과 기준값 (gaze-tracking.md 5 · 9절, architecture.md 6-2 · 7절)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from gaze import config
from gaze.config import DEFAULT, GazeConfig
from gaze.schemas import (
    GazeInput,
    GazeRecord,
    JudgmentIssue,
    JudgmentResult,
    TallyItem,
)
from gaze.version import FEATURE_VERSION

REQUIRED = {"t_ms": 1000, "since_ms": 0}
#: 소리 크기 판정 모듈의 기준값 파일. 시선 모듈 폴더에서 찾는다:
#: 서비스는 같은 features/ 아래 volume/, 연구 폴더는 ai/research/volume-analysis/src/volume/
_GAZE_DIR = Path(config.__file__).parent
_VOLUME_CONFIGS = (
    _GAZE_DIR.parent / "volume" / "config.py",
    _GAZE_DIR.parents[2] / "volume-analysis" / "src" / "volume" / "config.py",
)
VOLUME_CONFIG = next((p for p in _VOLUME_CONFIGS if p.exists()), _VOLUME_CONFIGS[0])

# ── 입력 ───────────────────────────────────────────────────────────────────────


def test_unknown_input_fields_are_ignored():
    inp = GazeInput.model_validate(
        {**REQUIRED, "note": "x", "voiced": [{"t_ms": 0, "voiced_ms": 1, "y": 2}]}
    )
    assert inp.voiced[0].voiced_ms == 1


@pytest.mark.parametrize("field", ["t_ms", "since_ms"])
def test_time_and_cursor_are_required(field):
    raw = dict(REQUIRED)
    del raw[field]
    with pytest.raises(ValidationError):
        GazeInput.model_validate(raw)


def test_records_and_voiced_default_to_empty():
    inp = GazeInput.model_validate(REQUIRED)
    assert (inp.records, inp.voiced) == ([], [])


def test_record_keeps_direction_only_for_other():
    other = GazeRecord.model_validate(
        {"t_ms": 0, "duration_ms": 1000, "state": "OTHER", "direction": "LEFT"}
    )
    camera = GazeRecord.model_validate(
        {"t_ms": 0, "duration_ms": 1000, "state": "CAMERA", "direction": "LEFT"}
    )
    assert (other.direction, camera.direction) == ("LEFT", None)


def test_record_drops_only_an_unknown_direction():
    r = GazeRecord.model_validate(
        {"t_ms": 0, "duration_ms": 1000, "state": "OTHER", "direction": "BEHIND"}
    )
    assert (r.state, r.direction) == ("OTHER", None)


def test_record_reads_null_issues_as_empty_and_ignores_non_strings():
    base = {"t_ms": 0, "duration_ms": 1000, "state": "CAMERA"}
    assert GazeRecord.model_validate({**base, "issues": None}).issues == ()
    assert GazeRecord.model_validate({**base, "issues": ["TOO_FAR", 3]}).issues == ("TOO_FAR",)


def test_record_missing_optional_fields_default_to_zero():
    r = GazeRecord.model_validate({"t_ms": 0, "duration_ms": 1000, "state": "BOTTOM"})
    assert (r.confidence, r.reliability, r.frames) == (0.0, 0.0, 0)


# ── 출력 ───────────────────────────────────────────────────────────────────────


def _result() -> dict:
    return {
        "t_ms": 1000, "counted_until_ms": 1000, "criteria_version": "gaze-1.3+000000000000",
        "measurable": True, "state": "AUDIENCE",
        "metrics": {
            "audience_ratio": 1.0, "screen_ratio": 0.0, "script_ratio": 0.0, "away_ratio": 0.0,
            "reading_ratio": None, "audience_ratio_short": 1.0, "script_ratio_short": 0.0,
            "away_ratio_short": 0.0, "measured_ratio": 1.0, "mean_reliability": 0.9,
            "script_run_ms": 0, "screen_run_ms": 0, "away_run_ms": 0,
        },
        "tally": [], "issues": [],
    }  # fmt: skip


def test_result_has_fixed_evaluator_and_area():
    r = JudgmentResult.model_validate(_result())
    assert (r.evaluator, r.area) == ("gaze", "GAZE")


def test_output_models_reject_unknown_fields():
    with pytest.raises(ValidationError):
        JudgmentResult.model_validate({**_result(), "extra": 1})
    with pytest.raises(ValidationError):
        JudgmentResult.model_validate({**_result(), "metrics": {**_result()["metrics"], "x": 1}})
    with pytest.raises(ValidationError):
        TallyItem.model_validate({"t_ms": 0, "values": {}, "x": 1})


def test_output_rejects_a_state_outside_the_gaze_states():
    with pytest.raises(ValidationError):
        JudgmentResult.model_validate({**_result(), "state": "UNKNOWN"})


@pytest.mark.parametrize("value", [-0.1, 1.1])
def test_issue_severity_must_be_between_0_and_1(value):
    with pytest.raises(ValidationError):
        JudgmentIssue(
            issue_type="GAZE_AWAY", severity=value, confidence=1, persistence_sec=1,
            threshold=1, bad=2, actionable=True,
        )  # fmt: skip


def test_feature_version_is_gaze_1_2():
    assert FEATURE_VERSION == "gaze-1.3"


# ── 기준값 ─────────────────────────────────────────────────────────────────────


def test_windows_fit_in_the_30_second_input_window():
    assert 0 < DEFAULT.short_window_ms <= DEFAULT.recent_window_ms <= 30_000


@pytest.mark.parametrize(
    "change",
    [
        {"recent_window_ms": 31_000},
        {"short_window_ms": 10_000, "recent_window_ms": 5_000},
        {"script_run_bad_ms": 3_000},
        {"low_eye_contact_bad": 0.5},
        {"unmeasurable_bad": 0.6},
        {"script_short_ratio": 0.0},
        {"take_start_quiet_ms": -1},
    ],
)
def test_config_rejects_values_that_break_the_rules(change):
    with pytest.raises(ValidationError):
        GazeConfig(**change)


def test_config_is_frozen():
    with pytest.raises(ValidationError):
        DEFAULT.script_run_ms = 1  # type: ignore[misc]


def _volume_voiced_min_ms() -> int | None:
    """소리 크기 config.py 의 voiced_min_ms 기본값 (파일이 없으면 None). import 하지 않고 읽는다."""
    if not VOLUME_CONFIG.exists():
        return None
    for node in ast.walk(ast.parse(VOLUME_CONFIG.read_text(encoding="utf-8"))):
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "voiced_min_ms"
            and isinstance(node.value, ast.Constant)
        ):
            return int(node.value.value)
    raise AssertionError(f"voiced_min_ms 를 찾지 못했다: {VOLUME_CONFIG}")


def test_speaking_second_threshold_matches_volume():
    """'말한 1초'의 기준은 시선 · 소리 크기가 같은 값이다(architecture.md 6-2)."""
    volume = _volume_voiced_min_ms()
    if volume is None:
        pytest.skip(f"소리 크기 모듈이 아직 없다: {VOLUME_CONFIG}")
    assert DEFAULT.speaking_min_voiced_ms == volume
