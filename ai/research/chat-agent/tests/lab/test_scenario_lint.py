"""평가 문항 점검 — 실행기 관례 · 가상 기록과 맞지 않아 어떤 에이전트도 통과할 수 없는 문항을 미리 막는다(Issue #1194).

- 왜: 독립 평가에서 늘 실패한 문항 중 일부는 에이전트가 아니라 문항 쪽 문제였다 — 평가 실행기는 실패한 행동에 실행 사건
  (`exec:<행동>:FAIL`)을 남기지 않는데 그것을 기대함, 평가 기준이 없는 피치에서 다시 평가 확인 카드를 기대함.
- 대상: 고칠 수 있는 문항 폴더(`evals/scenarios` · `evals/path` · `evals/regression-holdout/*`). 동결본(`evals/final/set` ·
  `evals/final-v2/set` · `evals/final-v3/set`)은 기록이라 보지 않는다(동결 뒤 고치지 않는다).
- 고칠 때: 새 관례가 생기면 여기 규칙을 더한다. 문항 글은 출력하지 않고 파일 이름 · 검사 id 만 낸다.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

EVALS = Path(__file__).resolve().parents[2] / "evals"
EDITABLE = [EVALS / "scenarios", EVALS / "path", *sorted((EVALS / "regression-holdout").glob("v*"))]


def _scenarios() -> list[tuple[Path, dict[str, Any]]]:
    out = []
    for folder in EDITABLE:
        for p in sorted(folder.glob("*.json")):
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict) and "expected" in d:
                out.append((p, d))
    return out


#: 있어야 한다고 보는 검사(없어야 한다는 `not_in_order` · `absent`는 실패 사건 · 다시 평가를 '하지 않음'을 볼 수 있다)
POSITIVE = ("in_order", "precedes", "contains", "equals", "one_of")


def _checks(d: dict[str, Any]) -> list[dict[str, Any]]:
    exp = d.get("expected") or {}
    found = list(exp.get("checks") or [])
    for t in d.get("turns") or []:
        found += list(((t.get("expected") or {}).get("checks")) or [])
    return found


def test_editable_folders_exist() -> None:
    assert all(f.is_dir() for f in EDITABLE[:2]) and len(_scenarios()) > 100


def test_no_scenario_expects_an_exec_event_for_a_failed_action() -> None:
    """실패한 행동은 실행 사건을 남기지 않고 `actions_executed`에 FAILED 로 남는다(`evals.py` · `fake_be.py`)."""
    bad = []
    for p, d in _scenarios():
        for c in _checks(d):
            if c.get("observe") == "trajectory" and c.get("compare") in POSITIVE and any(
                isinstance(e, str) and e.startswith("exec:") and e.endswith(":FAIL") for e in c.get("expect") or []
            ):
                bad.append(f"{p.parent.name}/{p.name}:{c.get('check_id')}")
    assert not bad, f"실패 행동의 실행 사건을 기대한 검사: {bad} — actions_executed 의 FAILED 로 본다"


def _expects_reassess_card(d: dict[str, Any]) -> bool:
    for c in _checks(d):
        if c.get("compare") not in POSITIVE:
            continue
        exp = c.get("expect")
        items = exp if isinstance(exp, list) else [exp]
        if any(isinstance(e, str) and e.startswith("propose:REASSESS") for e in items):
            return True
    return False


def test_reassess_scenarios_give_the_pitch_criteria_for_the_take_standard() -> None:
    """다시 평가 확인 카드는 Take 의 평가 기준 판(`standard_version`, 가상 기록 기본 1)과 맞는 피치 평가 기준이 있어야 그려진다
    (기능 문서 15-2). 기준이 없는 피치에 판이 있는 Take 를 두면 카드 대신 '작업 완료를 확인하지 못했어요'로 끝난다."""
    bad = []
    for p, d in _scenarios():
        if not _expects_reassess_card(d):
            continue
        for pitch in (d.get("fixtures") or {}).get("pitches") or []:
            takes = pitch.get("takes") or []
            if pitch.get("criteria") is None and any(t.get("standard_version", 1) is not None for t in takes):
                bad.append(f"{p.parent.name}/{p.name}:{pitch.get('pitch_id')}")
    assert not bad, f"평가 기준 없는 피치에서 다시 평가 카드를 기대함: {bad}"
