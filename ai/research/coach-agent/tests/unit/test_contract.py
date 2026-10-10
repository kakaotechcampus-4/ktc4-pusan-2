"""계약 — BE 가 보내고 받는 모양. 여기가 깨지면 BE · FE · 리뷰가 깨진다."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from coach import decide
from coach.config import load_config
from coach.schemas import CoachRequest, CoachResponse
from coach.state import CoachState, dump_state, load_state
from coach.version import POLICY_VERSION, SCHEMA_VERSION, STATE_VERSION
from coach.vocab import FeedbackType, Instruction

from .conftest import make_request
from .fakes import fake_judges


def test_first_request_without_state_starts_fresh():
    resp = decide(make_request(1000), fake_judges())
    assert resp.schema_version == SCHEMA_VERSION
    assert resp.policy_version == POLICY_VERSION
    assert resp.coach_state["v"] == STATE_VERSION
    assert resp.action.value == "WAIT"
    assert resp.reason_codes == ["NO_CANDIDATE"]
    assert resp.feedback is None


def test_response_is_plain_json_round_trip():
    resp = decide(make_request(1000), fake_judges())
    blob = json.dumps(resp.model_dump(mode="json"), ensure_ascii=False)
    again = CoachResponse.model_validate_json(blob)
    assert again == resp


def test_unknown_request_fields_are_ignored():
    req = make_request(1000)
    req["something_new_from_be"] = {"x": 1}
    req["inputs"]["voice_records"][0]["new_meter"] = 3
    decide(req, fake_judges())  # 깨지지 않는다


def test_missing_areas_skip_only_that_area():
    req = make_request(1000, slide=None)
    req["inputs"] = {}
    resp = decide(req, fake_judges())
    assert resp.action.value == "WAIT"


def test_negative_time_is_rejected():
    with pytest.raises(ValidationError):
        CoachRequest.model_validate(make_request(-1))


def test_state_round_trips_without_loss():
    state, reset = load_state(None)
    assert not reset
    state.history = []
    state.slide_totals = {"3": {"SPEED": {"chars": 150}}}
    raw = dump_state(state)
    again, reset = load_state(json.loads(json.dumps(raw)))
    assert not reset
    assert again == state


def test_state_dump_keeps_version_even_though_it_is_default():
    assert dump_state(CoachState())["v"] == STATE_VERSION


def test_foreign_state_is_reset_and_reported():
    resp = decide(make_request(1000, state={"v": 999, "whatever": 1}), fake_judges())
    assert "STATE_RESET" in resp.reason_codes
    resp = decide(
        make_request(1000, state={"v": STATE_VERSION, "history": "broken"}), fake_judges()
    )
    assert "STATE_RESET" in resp.reason_codes


def test_old_tick_does_not_change_state():
    judges = fake_judges()
    first = decide(make_request(5000), judges)
    again = decide(make_request(5000, state=first.coach_state), judges)
    assert again.reason_codes == ["STALE_TICK"]
    assert again.coach_state == first.coach_state


def test_feedback_type_enum_matches_review_and_mission_types():
    # 리뷰의 ReviewPoint.type · Mission.area 과 같은 7개
    assert {t.value for t in FeedbackType} == {
        "GAZE",
        "SPEED",
        "VOLUME",
        "PAUSE",
        "FILLER",
        "CONTENT",
        "TIME",
    }


def test_every_ladder_step_has_a_template():
    cfg = load_config()
    for issue, rule in cfg.issues.items():
        for step in rule.ladder:
            key = f"{step.instruction.value}.{step.variant}"
            assert key in cfg.templates, f"{issue}: {key} 문구가 없습니다"
    assert {s.instruction for r in cfg.issues.values() for s in r.ladder} == set(Instruction)


def test_config_override_changes_hash():
    base = load_config()
    tuned = load_config(policy={"cooldown_ms": 20_000})
    assert tuned.policy.cooldown_ms == 20_000
    assert tuned.policy.min_gap_ms == base.policy.min_gap_ms  # 나머지는 기본값
    assert tuned.config_hash() != base.config_hash()
