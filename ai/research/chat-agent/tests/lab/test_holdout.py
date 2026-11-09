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
    assert holdout.verify(d, m, rules=rules) == 0
    p = d / "ana-slide3-gaze.json"
    p.write_text(p.read_text(encoding="utf-8").replace("}", "} ", 1), encoding="utf-8")
    assert holdout.verify(d, m, rules=rules) == 1
    assert "문항 다름: ana-slide3-gaze.json" in capsys.readouterr().out


def test_freeze_refuses_invalid(tmp_path: Path) -> None:
    d = _copy(tmp_path, name="other-name")
    assert holdout.freeze(d, None, tmp_path / "m.json") == 1
    assert not (tmp_path / "m.json").exists()


def test_check_masks_user_keys_in_error_locations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """오류 위치에는 스키마 칸 이름 · 목록 번호만 쓴다. 작성자가 정한 사전 열쇠(Take id)와 모르는 칸 이름은 `<key>`로 가린다."""
    d = _copy(tmp_path)
    p = d / "ana-slide3-gaze.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data["숨은 칸 이름"] = 1  # 모르는 칸(extra_forbidden)
    pitches = data.setdefault("fixtures", {}).setdefault("pitches", [{"preset": "sample"}])
    pitches[0]["take_overrides"] = {"비밀-take-이름": "사전이 아님"}
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert holdout.check(d) == 1
    out = capsys.readouterr().out
    assert "숨은 칸 이름" not in out and "비밀-take-이름" not in out
    assert "SCHEMA <key> extra_forbidden" in out
    assert "SCHEMA fixtures.pitches.0.take_overrides.<key> dict_type" in out


def test_verify_compares_the_rules_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """동결 목록에 규칙 해시가 있으면 `--rules`가 필수이고 해시가 같아야 한다(없거나 다르면 1)."""
    d = _copy(tmp_path)
    rules = tmp_path / "rules.md"
    rules.write_text("규칙", encoding="utf-8")
    m = tmp_path / "manifest.json"
    assert holdout.freeze(d, rules, m) == 0
    capsys.readouterr()
    assert holdout.verify(d, m, rules=rules) == 0
    assert holdout.verify(d, m) == 1  # 규칙을 주지 않음
    assert "규칙 대조 못 함" in capsys.readouterr().out
    assert holdout.verify(d, m, rules=tmp_path / "없는 파일.md") == 1
    assert "규칙 대조 못 함" in capsys.readouterr().out
    rules.write_text("바뀐 규칙", encoding="utf-8")
    assert holdout.verify(d, m, rules=rules) == 1
    assert "규칙 다름" in capsys.readouterr().out
    assert (
        holdout.main(["verify", "--dir", str(d), "--manifest", str(m), "--rules", str(rules)]) == 1
    )
    # 규칙 해시가 없는 동결 목록은 규칙을 대조하지 않는다(전과 같음)
    m2 = tmp_path / "m2.json"
    assert holdout.freeze(d, None, m2) == 0
    assert holdout.verify(d, m2) == 0
