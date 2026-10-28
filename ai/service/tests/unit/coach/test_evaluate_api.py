"""1초 판단 · Take 결과 엔드포인트 `POST /coach/evaluate` · `/coach/finalize` (`api/coach.py`).

판정 모듈은 서비스의 실제 모듈 넷(`api.coach.JUDGES`)이다. 예시 요청을 보내면 예시 응답이 나오는지,
BE 가 받는 오류(409 · 422)와 로그를 본다. 계약은 기능 문서 5 · 7 · 11절.
"""

import copy
import json
import logging
from typing import Any

import pytest
from fastapi.testclient import TestClient

from pitch_coach_ai.api import coach as api
from pitch_coach_ai.features.coach import core
from pitch_coach_ai.features.coach.schemas import (
    MAX_T_MS,
    MAX_WINDOW_RECORDS,
    MAX_WINDOW_UTTERANCE_ENDS,
    MAX_WINDOW_WORDS,
)
from pitch_coach_ai.features.filler import core as filler
from pitch_coach_ai.features.gaze import core as gaze
from pitch_coach_ai.features.pace import core as pace
from pitch_coach_ai.features.volume import core as volume

from .test_examples import EXAMPLES, assert_same


def _example(name: str) -> dict[str, Any]:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def _coach_lines(records: list[logging.LogRecord], prefix: str) -> list[str]:
    return [r.getMessage() for r in records if r.getMessage().startswith(prefix)]


# ── 판정 모듈 묶음 ─────────────────────────────────────────────────────────────


def test_judges_are_the_four_service_modules_with_volume_baseline() -> None:
    judges = api.JUDGES
    assert (judges.gaze, judges.pace, judges.volume, judges.filler) == (gaze, pace, volume, filler)
    assert judges.speaking_levels is volume.speaking_levels
    assert judges.baseline is volume.baseline


# ── evaluate ──────────────────────────────────────────────────────────────────


def test_evaluate_example_request_gives_the_example_response(client: TestClient) -> None:
    """실제 판정 모듈로 판정해도 예시 응답(92초 시선 개입)과 같다.

    BE 가 읽는 예시가 서비스의 실제 응답이다.
    """
    res = client.post("/coach/evaluate", json=_example("evaluate/request.json"))
    assert res.status_code == 200
    body = res.json()
    assert_same(body, _example("evaluate/response.json"))
    assert body["action"] == "INTERVENE" and body["feedback"]["area"] == "GAZE"


def test_evaluate_first_request_of_a_take_has_no_state(client: TestClient) -> None:
    request = _example("evaluate/request.json")
    res = client.post("/coach/evaluate", json={**request, "t_ms": 1000, "coach_state": None})
    assert res.status_code == 200
    body = res.json()
    assert body["coach_state"]["last_t_ms"] == 1000
    assert body["meta"]["criteria_versions"].keys() >= {"gaze", "pace", "volume", "filler"}


def test_evaluate_stale_tick_changes_nothing(client: TestClient) -> None:
    request = _example("evaluate/request.json")
    stale = {**request, "t_ms": request["coach_state"]["last_t_ms"]}
    body = client.post("/coach/evaluate", json=stale).json()
    assert (body["action"], body["reason_codes"]) == ("WAIT", ["STALE_TICK"])
    assert body["indicators"] == {} and body["events"] == []


@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"t_ms": None}, id="t_ms 없음"),
        pytest.param({"t_ms": MAX_T_MS + 1}, id="t_ms 상한 초과"),
        pytest.param({"take_id": None}, id="take_id 없음"),
    ],
)
def test_evaluate_bad_request_is_422(client: TestClient, change: dict[str, Any]) -> None:
    request = {**_example("evaluate/request.json"), **change}
    request = {k: v for k, v in request.items() if v is not None}
    res = client.post("/coach/evaluate", json=request)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("gaze_records", MAX_WINDOW_RECORDS),
        ("voice_records", MAX_WINDOW_RECORDS),
        ("words", MAX_WINDOW_WORDS),
        ("utterance_ends", MAX_WINDOW_UTTERANCE_ENDS),
    ],
)
def test_evaluate_window_over_the_limit_is_422(client: TestClient, field: str, limit: int) -> None:
    request = copy.deepcopy(_example("evaluate/request.json"))
    one = request["inputs"][field][0]
    request["inputs"][field] = [one] * (limit + 1)
    assert client.post("/coach/evaluate", json=request).status_code == 422
    request["inputs"][field] = [one] * limit
    assert client.post("/coach/evaluate", json=request).status_code == 200


def test_state_reset_is_logged_once_per_response(
    client: TestClient, log_records: list[logging.LogRecord]
) -> None:
    """모르는 버전의 coach_state 는 새로 시작하고, 리셋 건수를 셀 수 있게 한 줄 남긴다(C-027)."""
    request = {**_example("evaluate/request.json"), "coach_state": {"v": 999}}
    body = client.post("/coach/evaluate", json=request).json()
    assert "STATE_RESET" in body["reason_codes"]
    lines = _coach_lines(log_records, "coach_evaluate ")
    assert len(lines) == 1
    assert "take_id=take-123 t_ms=92000 reasons=STATE_RESET duration_ms=" in lines[0]


def test_normal_evaluate_writes_no_coach_line(
    client: TestClient, log_records: list[logging.LogRecord]
) -> None:
    client.post("/coach/evaluate", json=_example("evaluate/request.json"))
    assert _coach_lines(log_records, "coach_evaluate ") == []


def test_t_ms_jump_is_logged(client: TestClient, log_records: list[logging.LogRecord]) -> None:
    request = _example("evaluate/request.json")
    body = client.post("/coach/evaluate", json={**request, "t_ms": 92000 + 60_000}).json()
    assert body["reason_codes"] == ["T_MS_JUMP"]
    assert "reasons=T_MS_JUMP" in _coach_lines(log_records, "coach_evaluate ")[0]


def test_internal_error_is_200_wait_and_logs_without_the_message(
    client: TestClient, log_records: list[logging.LogRecord], monkeypatch: pytest.MonkeyPatch
) -> None:
    """코치 안 예외는 200 WAIT(INTERNAL_ERROR).

    예외 메시지(사용자 원문이 섞일 수 있음)는 응답 · 로그에 없다.
    """

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("단어 원문 비밀")

    monkeypatch.setattr(core, "decide", boom)
    res = client.post("/coach/evaluate", json=_example("evaluate/request.json"))
    assert res.status_code == 200
    body = res.json()
    assert (body["action"], body["reason_codes"]) == ("WAIT", ["INTERNAL_ERROR"])
    assert body["coach_state"]["last_t_ms"] == 92000  # 같은 요청이 다시 오면 STALE_TICK
    assert "단어 원문 비밀" not in res.text
    line = _coach_lines(log_records, "coach_evaluate ")[0]
    assert "reasons=INTERNAL_ERROR" in line
    assert "단어 원문 비밀" not in line


# ── finalize ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("suffix", ["", "_replay"])
def test_finalize_example_request_gives_the_example_response(
    client: TestClient, suffix: str
) -> None:
    res = client.post("/coach/finalize", json=_example(f"finalize/request{suffix}.json"))
    assert res.status_code == 200
    assert_same(res.json(), _example(f"finalize/response{suffix}.json"))


def test_finalize_without_state_is_409_replay_required(
    client: TestClient, log_records: list[logging.LogRecord]
) -> None:
    """BE 는 상태 409 와 `error.code` 로 replay 를 실어 다시 부른다(기능 문서 7절)."""
    request = {**_example("finalize/request.json"), "coach_state": None}
    res = client.post("/coach/finalize", json=request)
    assert res.status_code == 409
    error = res.json()["error"]
    assert error["code"] == "REPLAY_REQUIRED"
    assert "빠진 구간 [0, 92000)" in error["message"]
    line = _coach_lines(log_records, "coach_finalize ")[0]
    assert "result=REPLAY_REQUIRED missing_ms=92000" in line


def test_finalize_with_a_gap_is_409_with_the_missing_span(client: TestClient) -> None:
    """센 구간이 Take 를 덮지 못하면 빠진 구간을 메시지에 싣는다."""
    request = copy.deepcopy(_example("finalize/request.json"))
    request["coach_state"]["covered"] = [[0, 30_000], [60_000, 91_000]]
    res = client.post("/coach/finalize", json=request)
    assert res.status_code == 409
    assert "[30000, 60000)" in res.json()["error"]["message"]


def test_finalize_then_replay_follows_the_be_flow(
    client: TestClient, log_records: list[logging.LogRecord]
) -> None:
    """409 를 받은 BE 가 replay 를 실어 다시 부르면 replayed 인 Take 결과가 나온다."""
    first = {**_example("finalize/request.json"), "coach_state": None}
    assert client.post("/coach/finalize", json=first).status_code == 409
    res = client.post("/coach/finalize", json=_example("finalize/request_replay.json"))
    assert res.status_code == 200
    take_result = res.json()["take_result"]
    assert take_result["replayed"] is True
    assert (take_result["take_id"], take_result["duration_ms"]) == ("take-123", 92000)
    assert "replay=True result=OK" in _coach_lines(log_records, "coach_finalize ")[-1]


def test_finalize_bad_request_is_422(client: TestClient) -> None:
    request = {**_example("finalize/request.json"), "t_ms": MAX_T_MS + 1}
    res = client.post("/coach/finalize", json=request)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "VALIDATION_ERROR"
