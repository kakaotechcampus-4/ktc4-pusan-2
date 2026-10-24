import re

from script_coverage import config
from script_coverage.script_analysis import config as script_config
from script_coverage.stt_evaluation import config as stt_config


def test_criteria_version_format():
    assert re.fullmatch(r"script_coverage-1\.0\+[0-9a-f]{12}", config.CRITERIA_VERSION)
    assert f"script_coverage-1.0+{config.CRITERIA_HASH}" == config.CRITERIA_VERSION


def test_criteria_hash_is_deterministic():
    assert config.criteria_hash() == config.CRITERIA_HASH


def test_changing_a_criterion_changes_the_hash(monkeypatch):
    before = config.criteria_hash()
    monkeypatch.setattr(stt_config, "LEXICAL_PRESENT", 0.61)
    changed_sub = config.criteria_hash()
    monkeypatch.undo()
    monkeypatch.setattr(config, "SCORE_WEIGHT", {"critical": 4, "high": 2, "normal": 1})
    changed_weight = config.criteria_hash()
    assert len({before, changed_sub, changed_weight}) == 3


def test_script_analysis_constant_and_role_importance_change_the_hash(monkeypatch):
    before = config.criteria_hash()
    monkeypatch.setattr(script_config, "QUOTE_MATCH", 0.7)
    changed_script = config.criteria_hash()
    monkeypatch.undo()
    monkeypatch.setattr(
        config,
        "ROLE_IMPORTANCE",
        {"claim": "critical", "evidence": "normal", "detail": "normal", "skip": "normal"},
    )
    changed_role = config.criteria_hash()
    assert len({before, changed_script, changed_role}) == 3


def test_max_workers_is_not_hashed(monkeypatch):
    before = config.criteria_hash()
    monkeypatch.setattr(config, "MAX_WORKERS", 16)
    assert config.criteria_hash() == before
