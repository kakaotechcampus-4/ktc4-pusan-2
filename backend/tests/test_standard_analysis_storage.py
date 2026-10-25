"""평가 기준 전체 저장·빈 버전·Take 고정과 AI 대기 경계를 검사한다."""

import asyncio
import json
from functools import partial

import httpx
import pytest
from sqlalchemy import select

from pitch_coach_backend.module.pitch import service, standard_parser
from pitch_coach_backend.module.pitch.entity import (
    Pitch,
    PresentationVersion,
    ScriptVersion,
    StandardAnalyses,
    Standards,
)
from pitch_coach_backend.module.pitch.exception import StandardParseFailed
from pitch_coach_backend.module.pitch.repository import PitchRepository
from pitch_coach_backend.module.take import coach_service
from pitch_coach_backend.module.take.entity import Take
from tests import test_pitch_standards as base

pitch_id = base.pitch_id
auth_headers = base.auth_headers


def reply(**changes):
    result = {
        "display_criteria": ["청중 70% 이상 보기"],
        "values": {"gaze": "70% 이상", "script_used": True},
        "targets": [
            {
                "display": "청중 70% 이상 보기",
                "metric": "audience_ratio",
                "operator": "GTE",
                "value": 0.7,
            }
        ],
        "excluded": ["시간, 억양"],
        "meta": {
            "schema_version": "1.1",
            "feature_version": "evaluation_criteria-1.0",
            "model": "audit-model",
            "future": {"version": 1},
        },
    }
    result.update(changes)
    return result


def use_ai(monkeypatch, body, on_request=None, status=200):
    requests = []

    def respond(request):
        requests.append(request)
        if on_request:
            on_request(request)
        return httpx.Response(status, content=json.dumps(body).encode())

    monkeypatch.setattr(
        standard_parser.httpx,
        "AsyncClient",
        partial(httpx.AsyncClient, transport=httpx.MockTransport(respond)),
    )
    return requests


def saved(db, pitch):
    return list(
        db.scalars(
            select(StandardAnalyses)
            .where(StandardAnalyses.pitch_id == pitch)
            .order_by(StandardAnalyses.version)
        )
    )


def make_take(db, pitch_id, number):
    presentation = db.scalar(
        select(PresentationVersion).where(PresentationVersion.pitch_id == pitch_id)
    )
    script = db.scalar(select(ScriptVersion).where(ScriptVersion.pitch_id == pitch_id))
    if presentation is None:
        presentation = PresentationVersion(pitch_id=pitch_id, version=1, file_key="audit.pdf")
        script = ScriptVersion(pitch_id=pitch_id, version=1, content="대본", parse_status="DONE")
        db.add_all([presentation, script])
        db.flush()
    take = Take(
        pitch_id=pitch_id,
        take_number=number,
        presentation_version_id=presentation.id,
        script_version_id=script.id,
        mode="COACHING",
        script_mode="OFF",
        status="READY",
        goal_time_sec=600,
    )
    db.add(take)
    db.commit()
    return take


def test_analysis_and_display_are_saved_together(
    client, db_session, auth_headers, pitch_id, monkeypatch
):
    body = reply()
    requests = use_ai(monkeypatch, body)
    response = base._post(client, pitch_id, auth_headers)
    assert response.status_code == 200
    analysis = saved(db_session, pitch_id)[0]
    assert analysis.version == 1 and analysis.script_used is True
    assert analysis.values == body["values"]
    assert analysis.targets == body["targets"]
    assert analysis.excluded == body["excluded"]
    assert analysis.meta == body["meta"]
    assert [
        item.title
        for item in db_session.scalars(select(Standards).where(Standards.pitch_id == pitch_id))
    ] == body["display_criteria"]
    assert response.json()["except_standard"] == "시간, 억양"
    assert requests[0].headers["X-Request-ID"]


def test_empty_versions_advance_without_changing_started_take(
    client,
    db_session,
    auth_headers,
    pitch_id,
    monkeypatch,
):
    use_ai(monkeypatch, reply())
    assert base._post(client, pitch_id, auth_headers).status_code == 200
    first = make_take(db_session, pitch_id, 1)
    assert coach_service.start_take(db_session, first.id)
    empty = reply(display_criteria=[], values={"script_used": None}, targets=[], excluded=[])
    use_ai(monkeypatch, empty)
    for _ in range(2):
        assert base._post(client, pitch_id, auth_headers).status_code == 200
    assert [item.version for item in saved(db_session, pitch_id)] == [1, 2, 3]
    rows = client.get(f"/api/pitches/{pitch_id}/resources", headers=auth_headers).json()
    # resources는 기준이 비어 있어도 저장한 버전을 모두 보여 준다.
    versions = PitchRepository(db_session).get_evaluations(pitch_id)
    assert [item.version for item in versions] == [1, 2, 3]
    assert [item["version"] for item in rows["evaluation_versions"]] == [1, 2, 3]
    response = client.get(f"/api/pitches/{pitch_id}/evaluations/3", headers=auth_headers)
    assert response.status_code == 200 and response.json()["evaluations"] == []
    latest = make_take(db_session, pitch_id, 2)
    assert coach_service.start_take(db_session, latest.id)
    db_session.refresh(first)
    db_session.refresh(latest)
    assert first.standard_version == 1 and latest.standard_version == 3
    assert service.criteria_for_version(db_session, pitch_id, 1)["script_used"] is True
    assert service.criteria_for_version(db_session, pitch_id, 3) == {
        "display_criteria": [],
        "targets": [],
        "script_used": None,
    }


def test_analysis_failure_rolls_back_new_display_version(
    client,
    db_session,
    auth_headers,
    pitch_id,
    monkeypatch,
):
    use_ai(monkeypatch, reply())
    assert base._post(client, pitch_id, auth_headers).status_code == 200

    def fail(*args):
        raise RuntimeError("분석 저장 실패")

    monkeypatch.setattr(PitchRepository, "save_standard_analysis", fail)
    with pytest.raises(RuntimeError, match="분석 저장 실패"):
        base._post(client, pitch_id, auth_headers)
    db_session.rollback()
    assert [item.version for item in saved(db_session, pitch_id)] == [1]
    assert list(
        db_session.scalars(select(Standards.version).where(Standards.pitch_id == pitch_id))
    ) == [1]


@pytest.mark.parametrize(
    "case",
    [
        "missing-meta",
        "bad-major",
        "bad-script-used",
        "bad-target",
        "too-many-targets",
        "unknown-display",
        "nul",
        "nan",
        "surrogate",
    ],
)
def test_invalid_analysis_keeps_previous_version(
    client,
    db_session,
    auth_headers,
    pitch_id,
    monkeypatch,
    case,
):
    use_ai(monkeypatch, reply())
    assert base._post(client, pitch_id, auth_headers).status_code == 200
    body = reply()
    if case == "missing-meta":
        del body["meta"]
    elif case == "bad-major":
        body["meta"]["schema_version"] = "2.0"
    elif case == "bad-script-used":
        body["values"]["script_used"] = "false"
    elif case == "bad-target":
        body["targets"][0]["value"] = True
    elif case == "too-many-targets":
        body["targets"] *= 21
    elif case == "unknown-display":
        body["targets"][0]["display"] = "없는 표시 기준"
    elif case == "nul":
        body["meta"]["future"] = "원문\x00"
    elif case == "nan":
        body["targets"][0]["value"] = float("nan")
    else:
        body["values"]["gaze"] = "\ud800"
    use_ai(monkeypatch, body)
    response = base._post(client, pitch_id, auth_headers)
    assert response.status_code == 502
    assert [item.version for item in saved(db_session, pitch_id)] == [1]


def test_ai_wait_releases_authorization_connection(
    client, db_session, auth_headers, pitch_id, monkeypatch
):
    def check(request):
        assert not db_session.in_transaction()

    use_ai(monkeypatch, reply(), on_request=check)
    assert base._post(client, pitch_id, auth_headers).status_code == 200


def test_deleted_pitch_during_ai_call_is_not_saved(
    client, db_session, auth_headers, pitch_id, monkeypatch
):
    # identity map에 남은 객체도 삭제 판정을 가리지 않아야 한다.
    pitch = db_session.get(Pitch, pitch_id)

    def remove(request):
        db_session.delete(pitch)
        db_session.commit()

    use_ai(monkeypatch, reply(), on_request=remove)
    assert base._post(client, pitch_id, auth_headers).status_code == 404
    assert saved(db_session, pitch_id) == []


def test_missing_version_is_404(client, auth_headers, pitch_id):
    assert (
        client.get(f"/api/pitches/{pitch_id}/evaluations/99", headers=auth_headers).status_code
        == 404
    )


def test_total_timeout_closes_slow_response(monkeypatch):
    closed = []

    class Slow(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(100):
                await asyncio.sleep(0.01)
                yield b" "

        async def aclose(self):
            closed.append(True)

    transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=Slow()))
    monkeypatch.setattr(
        standard_parser.httpx, "AsyncClient", partial(httpx.AsyncClient, transport=transport)
    )
    monkeypatch.setattr(standard_parser, "ATTEMPT_TIMEOUT_SEC", 0.03)
    with pytest.raises(StandardParseFailed):
        asyncio.run(standard_parser.parse_standard_text("기준"))
    assert closed
