"""독립 최종 평가 세트 도구(holdout.py): 형식 검사 · 동결 · 대조가 문항 글을 출력하지 않는지(13-5)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from chat_agent_lab import holdout
from chat_agent_lab import scenario as scn

SAMPLE = scn.SCENARIO_DIR / "ana-slide3-gaze.json"


def _copy(tmp: Path, name: str = "ana-slide3-gaze") -> Path:
    d = tmp / "set"
    d.mkdir(exist_ok=True)
    shutil.copy(SAMPLE, d / f"{name}.json")
    return d


def _messages(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [t["message"] for t in data["turns"] if t.get("message")]


def test_check_ok_and_no_content(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    d = _copy(tmp_path)
    assert holdout.check(d) == 0
    out = capsys.readouterr().out
    assert "ana-slide3-gaze.json OK" in out
    for msg in _messages(SAMPLE):
        assert msg not in out


def test_check_fail_prints_location_not_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    d = _copy(tmp_path)
    p = d / "ana-slide3-gaze.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    secret = "이 글은 출력에 나오면 안 된다"
    data["turns"][0]["message"] = secret
    data["expected"]["checks"][0]["compare"] = "아무거나"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert holdout.check(d) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "SCHEMA expected.checks.0.compare" in out
    assert secret not in out


def test_check_semantic_code(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # 파일 이름과 id 가 다름 → 내용 없는 코드
    d = _copy(tmp_path, name="other-name")
    assert holdout.check(d) == 1
    assert "ID_FILENAME" in capsys.readouterr().out


def test_freeze_and_verify(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    d = _copy(tmp_path)
    rules = tmp_path / "rules.md"
    rules.write_text("규칙", encoding="utf-8")
    m = tmp_path / "manifest.json"
    assert holdout.freeze(d, rules, m) == 0
    data = json.loads(m.read_text(encoding="utf-8"))
    assert data["count"] == 1 and data["rules_sha256"] and "grading.py" in data["grader_sha256"]
    # 동결 목록에도 문항 글이 없다
    text = m.read_text(encoding="utf-8")
    for msg in _messages(SAMPLE):
        assert msg not in text
    assert holdout.verify(d, m) == 0
    p = d / "ana-slide3-gaze.json"
    p.write_text(p.read_text(encoding="utf-8").replace("}", "} ", 1), encoding="utf-8")
    assert holdout.verify(d, m) == 1
    assert "문항 다름: ana-slide3-gaze.json" in capsys.readouterr().out


def test_freeze_refuses_invalid(tmp_path: Path) -> None:
    d = _copy(tmp_path, name="other-name")
    assert holdout.freeze(d, None, tmp_path / "m.json") == 1
    assert not (tmp_path / "m.json").exists()
