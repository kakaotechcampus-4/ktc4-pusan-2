"""evaluate · finalize 예시 — 다시 만들면 커밋한 파일과 같고, finalize 예시는 코치 응답과 같다."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from coach import finalize
from coach.config import load_config
from coach.schemas import FinalizeRequest, FinalizeResponse
from coach_lab.examples import EXAMPLES_DIR, write
from coach_lab.judges import lab_judges
from tests.unit.test_examples import assert_same

FINALIZE = EXAMPLES_DIR / "finalize"


def _text(path: Path) -> bytes:
    # 체크아웃할 때 줄바꿈이 CRLF 로 바뀔 수 있어 줄바꿈만 맞춰 비교한다
    return path.read_bytes().replace(b"\r\n", b"\n")


def _load(name: str) -> Any:
    return json.loads((FINALIZE / name).read_text(encoding="utf-8"))


def test_examples_are_rebuilt_unchanged(tmp_path: Path) -> None:
    for path in write(tmp_path):
        rel = path.relative_to(tmp_path)
        assert _text(path) == _text(EXAMPLES_DIR / rel), str(rel)


@pytest.mark.parametrize("suffix", ["", "_replay"])
def test_finalize_response_matches_example(suffix: str) -> None:
    request = _load(f"request{suffix}.json")
    FinalizeRequest.model_validate(request)
    expected = _load(f"response{suffix}.json")
    FinalizeResponse.model_validate(expected)
    actual = finalize(request, lab_judges(), load_config())
    assert_same(actual.model_dump(mode="json"), expected)


def test_finalize_examples_show_normal_and_replay():
    normal, replayed = _load("response.json"), _load("response_replay.json")
    assert normal["take_result"]["replayed"] is False
    assert replayed["take_result"]["replayed"] is True
    assert _load("request_replay.json")["coach_state"] is None
    # 원자료로 다시 만들어도 Take 결과의 지표 · 문제 구간은 같다
    assert replayed["take_result"]["areas"] == normal["take_result"]["areas"]
    assert replayed["take_result"]["problem_segments"] == normal["take_result"]["problem_segments"]
    # replay 응답 이벤트는 효과 기록이 없는 실제 개입의 NOT_MEASURED 뿐이다
    assert {e["kind"] for e in replayed["events"]} <= {"OUTCOME"}
