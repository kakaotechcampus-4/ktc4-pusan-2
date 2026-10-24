"""features/sample: examples 계약(요청 → 결과), 센 기록의 창 · 중복 · 커서, summarize · criteria."""

import json
from pathlib import Path

from pitch_coach_ai.common import meta
from pitch_coach_ai.features.sample import core
from pitch_coach_ai.features.sample.config import DEFAULT, SampleConfig
from pitch_coach_ai.features.sample.schemas import SampleInput, SampleRecord
from pitch_coach_ai.features.sample.version import FEATURE_VERSION

EXAMPLES = Path(core.__file__).parent / "examples"


def _example(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def _judge(since_ms: int, t_ms: int, *starts: int) -> dict:
    inputs = SampleInput(since_ms=since_ms, records=[SampleRecord(t_ms=s) for s in starts])
    [result] = core.judge(inputs, t_ms)
    return result.model_dump(mode="json")


def test_example_request_gives_example_results() -> None:
    req = _example("judge_request.json")
    results = core.judge(SampleInput.model_validate(req), req["t_ms"])
    expected = _example("judge_response.json")["results"]
    assert [r.model_dump(mode="json") for r in results] == expected


def test_example_criteria_version_matches_common_meta_hash() -> None:
    # 기능 config.py 의 해시와 라우터가 meta 에 싣는 common/meta.py 의 해시가 같은 방식인지
    expected = meta.criteria_version(FEATURE_VERSION, DEFAULT)
    resp = _example("judge_response.json")
    assert resp["meta"]["criteria_versions"] == {"sample": expected}
    assert resp["results"][0]["criteria_version"] == expected


def test_feature_version_has_module_prefix() -> None:
    assert FEATURE_VERSION.startswith("sample-")


def test_no_records_is_unmeasurable_but_time_still_counts() -> None:
    result = _judge(0, 3000)
    assert (result["measurable"], result["state"]) == (False, "UNKNOWN")
    assert result["metrics"] == {"measured_ratio": 0.0}
    assert result["tally"] == [{"t_ms": 0, "values": {"measured_ms": 0.0, "total_ms": 3000.0}}]
    assert result["counted_until_ms"] == 3000


def test_counts_only_seconds_that_started_after_cursor_and_finished_by_t_ms() -> None:
    # 1000 은 커서 앞(이미 셈), 4000 은 4000~5000 이라 t_ms=4500 에 아직 끝나지 않음
    result = _judge(2000, 4500, 1000, 2000, 3000, 4000)
    assert result["tally"][0]["values"] == {"measured_ms": 2000.0, "total_ms": 2500.0}


def test_duplicate_second_uses_the_first_record() -> None:
    inputs = SampleInput(
        since_ms=0,
        records=[SampleRecord(t_ms=0, duration_ms=500), SampleRecord(t_ms=0, duration_ms=1000)],
    )
    [result] = core.judge(inputs, 1000)
    assert result.tally[0].values["measured_ms"] == 500


def test_overlapping_records_cap_ratio_at_one() -> None:
    inputs = SampleInput(
        since_ms=0,
        records=[SampleRecord(t_ms=0, duration_ms=1500), SampleRecord(t_ms=500, duration_ms=1500)],
    )
    [result] = core.judge(inputs, 2000)
    assert result.metrics["measured_ratio"] == 1.0
    assert core.summarize({"VOLUME": {"measured_ms": 3000, "total_ms": 2000}}) == {
        "VOLUME": {"measured_ratio": 1.0}
    }


def test_cursor_never_goes_back() -> None:
    result = _judge(5000, 3000)
    assert result["counted_until_ms"] == 5000
    assert result["tally"] == []


def test_changing_config_changes_criteria_version() -> None:
    inputs = SampleInput(since_ms=0, records=[])
    [default] = core.judge(inputs, 1000)
    [strict] = core.judge(inputs, 1000, SampleConfig(min_measured_ratio=0.9))
    assert default.criteria_version != strict.criteria_version
    assert strict.criteria_version.startswith(f"{FEATURE_VERSION}+")


def test_unknown_record_fields_are_ignored() -> None:
    inputs = SampleInput.model_validate(
        {"since_ms": 0, "records": [{"t_ms": 0, "state": "CAMERA", "frames": 30}]}
    )
    assert inputs.records == [SampleRecord(t_ms=0)]


def test_summarize_divides_sums_and_zero_denominator_is_zero() -> None:
    assert core.summarize({"VOLUME": {"measured_ms": 3000, "total_ms": 4000}}) == {
        "VOLUME": {"measured_ratio": 0.75}
    }
    assert core.summarize({}) == {"VOLUME": {"measured_ratio": 0.0}}


def test_criteria_is_empty_because_sample_raises_no_issue() -> None:
    assert core.criteria() == {}
