"""WebSocket 연결 — 인증 · 인식 용어 · 타임라인 · Deepgram/FE 재연결 · grace · Take 검사."""

import json
import struct
import time
import uuid

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.pitch.entity import (
    ScriptParseStatus,
    ScriptSlide,
    ScriptVersion,
)
from pitch_coach_backend.module.take.entity import Take
from pitch_coach_backend.module.user.entity import User
from pitch_coach_backend.realtime.audio import BYTES_PER_MS
from pitch_coach_backend.realtime.connection import session as service
from pitch_coach_backend.realtime.dto import ErrorMessage, WsErrorCode
from pitch_coach_backend.realtime.stream import abandon, registry, runtime
from pitch_coach_backend.realtime.stt.fillers import KEYTERM_FILLERS
from pitch_coach_backend.realtime.stt.loop import FAST_ATTEMPTS
from tests.realtime.conftest import (
    TAKE_ID,
    WS_PATH,
    FakeSttAdapter,
    FakeTranscriptStore,
    Plan,
    auth,
    closed_with,
    frame,
    handshake,
    make_take,
    stop,
    transcript,
    use,
    wait_state,
)

# ── fixture ───────────────────────────────────────────────────────────


@pytest.fixture
def other_token(db_session: Session) -> str:
    other = User(email="other@example.com", name="다른 사람")
    db_session.add(other)
    db_session.commit()
    return create_access_token(other.id)


# ── 인증 ──────────────────────────────────────────────────────────────


def test_bad_token_is_rejected_with_1008(client: TestClient, stt: FakeSttAdapter):
    with client.websocket_connect(WS_PATH) as ws:
        auth(ws, "garbage")
        assert ws.receive_json()["code"] == "UNAUTHORIZED"
        assert closed_with(ws) == 1008
    assert stt.configs == [], "인증 전에 Deepgram 에 붙지 않는다"


def test_unknown_user_is_rejected(client: TestClient, stt: FakeSttAdapter):
    with client.websocket_connect(WS_PATH) as ws:
        auth(ws, create_access_token(uuid.uuid7()))
        assert ws.receive_json()["code"] == "UNAUTHORIZED"
        assert closed_with(ws) == 1008


def test_audio_before_auth_is_rejected(client: TestClient, stt: FakeSttAdapter):
    with client.websocket_connect(WS_PATH) as ws:
        ws.send_bytes(frame(1, 0))
        assert ws.receive_json()["code"] == "UNAUTHORIZED"
        assert closed_with(ws) == 1008


def test_foreign_origin_is_closed_before_accept(client: TestClient, stt: FakeSttAdapter):
    with (
        pytest.raises(Exception) as info,
        client.websocket_connect(WS_PATH, headers={"origin": "https://evil.example"}),
    ):
        pass
    assert getattr(info.value, "code", None) == 1008


def test_frontend_origin_is_allowed(client: TestClient, stt: FakeSttAdapter, token: str):
    with client.websocket_connect(WS_PATH, headers={"origin": settings.frontend_base_url}) as ws:
        assert handshake(ws, token)["type"] == "ready"


# ── 연결과 상태 ────────────────────────────────────────────────────────


def test_ready_reports_state_before_deepgram_is_up(
    client: TestClient, stt: FakeSttAdapter, token: str
):
    with client.websocket_connect(WS_PATH) as ws:
        ready = handshake(ws, token)
        # Deepgram 연결을 기다리지 않는다. 붙으면 ok 가 따라온다
        assert ready == {
            "type": "ready",
            "take_id": str(TAKE_ID),
            "stt_session_no": 0,
            "stt_state": "connecting",
        }
        assert wait_state(ws, "ok")["stt_session_no"] == 1

    (config,) = stt.configs
    assert "음" in config.keyterms and "이제" in config.keyterms
    assert config.tag == f"take:{TAKE_ID}"


def test_script_terms_follow_fillers_in_keyterms(
    client: TestClient, stt: FakeSttAdapter, token: str, take: Take, db_session: Session
):
    script = db_session.get(ScriptVersion, take.script_version_id)
    script.parse_status = ScriptParseStatus.DONE
    script.terms = ["SeatFlow", "음"]
    db_session.add(
        ScriptSlide(
            script_version_id=script.id,
            slide_number=1,
            full_content="본문",
            keywords=["좌석 예측"],
        )
    )
    db_session.commit()

    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")

    (config,) = stt.configs
    # filler 가 먼저, 대본 terms 가 뒤. filler 와 겹치는 "음" 은 한 번만.
    # 슬라이드 keywords 는 넣지 않는다
    assert config.keyterms == (*KEYTERM_FILLERS, "SeatFlow")
    assert config.min_keyterms == len(KEYTERM_FILLERS)


def test_rejected_keyterms_drop_five_from_the_end_without_backoff(
    client: TestClient, token: str, take: Take, db_session: Session
):
    # 토큰 추정이 빗나가 Deepgram 이 거절하면 우선순위 낮은 뒤쪽 용어부터 5개씩 뺀다.
    # 거절은 Deepgram 장애가 아니라 바로 다시 붙는다 — 실패로 세면 backoff·degraded 가 끼어든다
    terms = [f"용어{i:02d}" for i in range(12)]
    script = db_session.get(ScriptVersion, take.script_version_id)
    script.parse_status = ScriptParseStatus.DONE
    script.terms = terms
    db_session.commit()
    adapter = use(FakeSttAdapter(reject_times=FAST_ATTEMPTS))

    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        first_status = ws.receive_json()
        assert first_status["state"] == "ok", first_status
        assert first_status["stt_session_no"] == 1

    assert [config.keyterms for config in adapter.configs] == [
        (*KEYTERM_FILLERS, *terms),
        (*KEYTERM_FILLERS, *terms[:7]),
        (*KEYTERM_FILLERS, *terms[:2]),
        KEYTERM_FILLERS,
    ]


def test_rejected_keyterms_never_drop_fillers(
    client: TestClient, token: str, take: Take, db_session: Session
):
    # 대본 용어를 다 빼고도 거절되면 filler 는 그대로 두고 평소처럼 backoff 재시도한다
    script = db_session.get(ScriptVersion, take.script_version_id)
    script.parse_status = ScriptParseStatus.DONE
    script.terms = ["SeatFlow"]
    db_session.commit()
    adapter = use(FakeSttAdapter(reject_times=2))

    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        assert wait_state(ws, "ok")["stt_session_no"] == 1

    first, *rest = adapter.configs
    assert first.keyterms == (*KEYTERM_FILLERS, "SeatFlow")
    assert [config.keyterms for config in rest] == [KEYTERM_FILLERS, KEYTERM_FILLERS]


def test_fillers_only_config_has_nothing_to_trim(
    client: TestClient, stt: FakeSttAdapter, token: str
):
    # 대본 용어가 없으면 줄일 것도 없다
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")

    (config,) = stt.configs
    assert config.keyterms == KEYTERM_FILLERS
    assert config.min_keyterms == len(KEYTERM_FILLERS)


def test_script_term_lookup_failure_still_connects(
    client: TestClient, stt: FakeSttAdapter, token: str, monkeypatch: pytest.MonkeyPatch
):
    # 용어 조회는 부가 기능이다. 실패해도 연결을 닫지 않고 filler 만으로 STT 를 연다
    def broken(*args, **kwargs):
        raise RuntimeError("statement timeout")

    monkeypatch.setattr(service.script_service, "stt_keyterm_candidates", broken)

    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        assert wait_state(ws, "ok")["stt_session_no"] == 1

    (config,) = stt.configs
    assert config.keyterms == KEYTERM_FILLERS


def test_deepgram_outage_degrades_instead_of_closing(client: TestClient, token: str):
    # 3번 실패하면 degraded 를 알리고 그래도 재시도를 이어간다
    use(FakeSttAdapter(fail_times=3))
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        assert wait_state(ws, "degraded")["stt_session_no"] == 0
        # 연결은 살아 있다 — FE 1단 코치가 계속 돌아야 한다
        assert wait_state(ws, "ok")["stt_session_no"] == 1


# ── 전사 ──────────────────────────────────────────────────────────────


def test_transcripts_are_shifted_to_take_timeline(client: TestClient, token: str):
    # Deepgram 세션 기준 0~100ms 가 Take 기준 5000~5100ms 여야 한다
    adapter = use(
        FakeSttAdapter(
            Plan(
                replies=[
                    transcript(0, 100, "안녕", is_final=False),
                    transcript(0, 200, "안녕하세요", is_final=True),
                    transcript(200, 300, "그래서", is_final=False),
                ]
            )
        )
    )
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")

        ws.send_bytes(frame(1, 5000))
        interim = ws.receive_json()
        ws.send_bytes(frame(2, 5100))
        final = ws.receive_json()
        ws.send_bytes(frame(3, 5200))
        next_interim = ws.receive_json()

    assert interim["type"] == "transcript"
    assert (interim["segment_id"], interim["is_final"]) == ("1-1", False)
    assert (interim["start_ms"], interim["end_ms"]) == (5000, 5100)
    assert interim["words"][0]["start_ms"] == 5000

    assert (final["segment_id"], final["is_final"], final["text"]) == ("1-1", True, "안녕하세요")
    assert (final["start_ms"], final["end_ms"]) == (5000, 5200)

    # final 뒤의 interim 은 새 구간
    assert (next_interim["segment_id"], next_interim["start_ms"]) == ("1-2", 5200)
    assert adapter.session is not None


# ── 프레임 처리 ────────────────────────────────────────────────────────


def test_gap_is_filled_with_silence(client: TestClient, stt: FakeSttAdapter, token: str):
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 0))  # 0~100
        ws.send_bytes(frame(2, 400))  # 300ms 비었다
        status = stop(ws)

    session = stt.session
    assert session is not None
    assert [len(a) for a in session.audio] == [3200, 300 * BYTES_PER_MS, 3200]
    assert session.audio[1] == bytes(300 * BYTES_PER_MS)
    assert status["silence_ms"] == 300 and status["frames"] == 2


def test_jitter_is_not_a_gap(client: TestClient, stt: FakeSttAdapter, token: str):
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 0))
        ws.send_bytes(frame(2, 130))  # 30ms 어긋남은 타이머 지터
        status = stop(ws)

    assert stt.session is not None
    assert [len(a) for a in stt.session.audio] == [3200, 3200]
    assert status["silence_ms"] == 0


def test_duplicate_and_out_of_order_frames_are_dropped(
    client: TestClient, stt: FakeSttAdapter, token: str
):
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(2, 100))
        ws.send_bytes(frame(1, 0))  # 역행
        ws.send_bytes(frame(2, 100))  # 중복
        ws.send_bytes(frame(3, 200))
        status = stop(ws)

    assert (status["frames"], status["dropped_frames"]) == (2, 2)
    assert stt.session is not None
    assert len(stt.session.audio) == 2


def test_malformed_frame_is_reported_once_and_ignored(
    client: TestClient, stt: FakeSttAdapter, token: str
):
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(struct.pack("<II", 1, 0) + b"\x01" * 3201)  # 홀수 바이트
        assert ws.receive_json()["code"] == "BAD_AUDIO_FRAME"
        ws.send_bytes(frame(2, 100))
        status = stop(ws)

    assert status["frames"] == 1
    assert stt.session is not None
    assert [len(a) for a in stt.session.audio] == [3200]


def test_unknown_text_message_is_an_error_not_a_close(
    client: TestClient, stt: FakeSttAdapter, token: str
):
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_text(json.dumps({"type": "dance"}))
        assert ws.receive_json()["code"] == "BAD_MESSAGE"
        assert stop(ws)["state"] == "closed"


# ── WS-2 재연결 (Deepgram 이 끊긴다) ───────────────────────────────────


def test_deepgram_session_replacement_keeps_segment_numbering(client: TestClient, token: str):
    """§6-2 주의 2 — 번호가 리셋되면 FE 가 앞부분 전사를 뒷부분으로 덮어쓴다."""
    adapter = use(
        FakeSttAdapter(
            Plan(replies=[transcript(0, 200, "안녕하세요", is_final=True)], die_after=1),
            Plan(replies=[transcript(0, 100, "그래서", is_final=False)]),
        )
    )
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")

        ws.send_bytes(frame(1, 0))
        first = ws.receive_json()
        assert (first["segment_id"], first["is_final"]) == ("1-1", True)

        # 세션이 죽었다 -> 재접속
        wait_state(ws, "reconnecting")
        assert wait_state(ws, "ok")["stt_session_no"] == 2

        ws.send_bytes(frame(2, 5000))
        second = ws.receive_json()
        status = stop(ws)

    # 세션 번호는 올라가고 세그먼트 번호는 **이어진다** (1 로 리셋되지 않는다)
    assert second["segment_id"] == "2-2"
    # 새 세션의 base 는 그 세션의 첫 프레임 offset. 타임라인은 Take 기준으로 유지된다
    assert second["start_ms"] == 5000
    assert status["stt_session_no"] == 2
    assert len(adapter.sessions) == 2


def test_audio_queued_while_deepgram_is_down_is_sent_after_reconnect(
    client: TestClient, token: str
):
    adapter = use(FakeSttAdapter(fail_times=1))
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        # 첫 연결이 실패한 동안 보낸 오디오도 큐에 남는다
        ws.send_bytes(frame(1, 0))
        wait_state(ws, "ok")
        ws.send_bytes(frame(2, 100))
        stop(ws)

    assert adapter.session is not None
    assert [len(a) for a in adapter.session.audio] == [3200, 3200]


# ── WS-1 재연결 (FE 가 끊긴다) ─────────────────────────────────────────


def test_reconnect_within_grace_keeps_the_same_deepgram_session(client: TestClient, token: str):
    adapter = use(
        FakeSttAdapter(
            Plan(
                replies=[
                    transcript(0, 200, "안녕하세요", is_final=True),
                    transcript(200, 300, "그래서", is_final=False),
                ]
            )
        )
    )
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 0))
        assert ws.receive_json()["segment_id"] == "1-1"

    # 탭이 끊겼다. grace 안에 다시 붙는다
    with client.websocket_connect(WS_PATH) as ws:
        ready = handshake(ws, token)
        assert (ready["stt_session_no"], ready["stt_state"]) == (1, "ok")
        ws.send_bytes(frame(2, 100))
        again = ws.receive_json()
        status = stop(ws)

    assert len(adapter.sessions) == 1, "Deepgram 세션은 그대로여야 한다"
    assert again["segment_id"] == "1-2"
    assert status["frames"] == 2, "프레임 수도 Take 누적이다"


def test_reload_within_grace_restarts_the_timeline_instead_of_dropping_audio(
    client: TestClient, token: str
):
    """탭을 새로고침하면 FE 는 무대 시계와 함께 seq·offset 을 1·0 부터 다시 센다.
    grace 안에 붙어 같은 스트림을 쓰더라도 그 프레임을 역행으로 버리면 안 된다 —
    예전 seq 를 넘을 때까지 오디오가 통째로 사라진다."""
    adapter = use(
        FakeSttAdapter(
            Plan(replies=[transcript(0, 100, "앞부분", is_final=True)]),
            Plan(replies=[transcript(0, 100, "새로고침한 뒤", is_final=True)]),
        )
    )
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        for i in range(3):
            ws.send_bytes(frame(i + 1, i * 100))
        assert ws.receive_json()["segment_id"] == "1-1"

    # 새로고침. 같은 Take 에 grace 안에 다시 붙는데 seq·offset 이 처음부터다
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        ws.send_bytes(frame(1, 0))
        ws.send_bytes(frame(2, 100))
        ws.send_text(json.dumps({"type": "stop"}))
        messages = []
        while not (messages and messages[-1].get("state") == "closed"):
            messages.append(ws.receive_json())

    transcripts = [m for m in messages if m["type"] == "transcript"]
    status = messages[-1]

    # offset 이 0 으로 돌아갔으니 Deepgram 세션을 갈아 base 를 다시 잡는다
    assert len(adapter.sessions) == 2
    assert [len(a) for a in adapter.sessions[1].audio] == [3200, 3200]
    # 세그먼트 번호는 이어지고, 시각은 새 무대 시계 기준이다
    assert [(t["segment_id"], t["start_ms"], t["text"]) for t in transcripts] == [
        ("2-2", 0, "새로고침한 뒤")
    ]
    assert status["frames"] == 5
    assert status["dropped_frames"] == 0


def test_grace_expiry_closes_the_stream(
    client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch, store: FakeTranscriptStore
):
    monkeypatch.setattr(runtime, "GRACE_SEC", 0.05)
    adapter = use(FakeSttAdapter())
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 0))

    deadline = time.monotonic() + 3.0
    while registry.active_count() and time.monotonic() < deadline:
        time.sleep(0.02)

    assert registry.active_count() == 0
    assert adapter.session is not None
    # 그냥 버리지 않고 CloseStream 으로 마지막 전사까지 받아 둔다
    assert adapter.session.controls == ["CloseStream"]
    # 스트림만 정리한다. FE 는 끊긴 뒤에도 다시 붙으니 Take 는 끝내지 않는다
    assert store.ended == []
    assert TAKE_ID not in registry._ended
    # 대신 아무도 다시 붙지 않으면 끝낼 타이머를 건다
    assert TAKE_ID in abandon._abandon_timers


def test_reconnect_after_grace_expiry_resumes_on_a_new_stream(
    client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch
):
    """FE 는 빠른 백오프가 다 실패해도 포기하지 않고 5초마다 다시 붙는다. 그래서 grace 가
    지난 뒤에 붙는 연결도 받아야 한다 — 새 스트림을 열고, FE 가 리셋하지 않은 seq 와
    Take 기준 offset 을 그대로 받아 전사 시각이 Take 타임라인에 맞게 나온다."""
    monkeypatch.setattr(runtime, "GRACE_SEC", 0.05)
    adapter = use(
        FakeSttAdapter(Plan(), Plan(replies=[transcript(0, 100, "다시 붙었다", is_final=True)]))
    )
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 0))

    deadline = time.monotonic() + 3.0
    while registry.active_count() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert registry.active_count() == 0

    # 40초 뒤에 붙었다. 그동안 FE 는 seq 를 계속 올렸다 (끊긴 동안의 프레임은 버려졌다)
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(400, 40_000))
        resumed = ws.receive_json()
        stop(ws)

    assert len(adapter.sessions) == 2
    assert (resumed["type"], resumed["is_final"]) == ("transcript", True)
    assert resumed["start_ms"] == 40_000


def test_second_tab_takes_over_and_evicts_the_first(client: TestClient, token: str):
    adapter = use(FakeSttAdapter())
    with client.websocket_connect(WS_PATH) as first:
        handshake(first, token)
        wait_state(first, "ok")

        with client.websocket_connect(WS_PATH) as second:
            assert handshake(second, token)["stt_session_no"] == 1

            assert first.receive_json()["code"] == "TAKE_TAKEN_OVER"
            assert closed_with(first) == 1008

            second.send_bytes(frame(1, 0))
            stop(second)

    assert len(adapter.sessions) == 1


# ── Metadata 대조 ─────────────────────────────────────────────────────


def test_duration_mismatch_is_logged(
    client: TestClient, stt: FakeSttAdapter, token: str, caplog: pytest.LogCaptureFixture
):
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        ws.send_bytes(frame(1, 0))
        # 우리는 100ms 를 보냈는데 Deepgram 은 40ms 만 받았다고 한다 = 그만큼 유실
        assert stt.session is not None
        stt.session.report_ms = 40
        with caplog.at_level("WARNING"):
            stop(ws)

    assert "sent_ms=100 duration_ms=40 drift_ms=-60" in caplog.text


# ── 소유권·정리 (코드 리뷰 지적) ───────────────────────────────────────


def test_other_user_cannot_take_over_the_stream(
    client: TestClient, stt: FakeSttAdapter, token: str, other_token: str
):
    """남의 Take 는 DB 검사에서 막힌다. 존재 여부를 흘리지 않도록 "없음" 과 같은 코드다."""
    with client.websocket_connect(WS_PATH) as mine:
        handshake(mine, token)
        wait_state(mine, "ok")

        with client.websocket_connect(WS_PATH) as theirs:
            auth(theirs, other_token)
            assert theirs.receive_json()["code"] == "TAKE_NOT_FOUND"
            assert closed_with(theirs) == 1008

        # 내 연결은 그대로다 — 쫓겨나지 않았다
        mine.send_bytes(frame(1, 0))
        assert stop(mine)["frames"] == 1

    assert len(stt.sessions) == 1


def test_long_gap_rotates_the_session_and_keeps_take_timeline(client: TestClient, token: str):
    """무음으로 메울 수 없는 갭 뒤에도 전사 시각이 Take 기준이어야 한다."""
    adapter = use(
        FakeSttAdapter(
            # 세션 1 에 답을 둘 준다. 교체가 안 되면 두 번째 프레임이 여기로 가서
            # Deepgram 시각 5,100ms(= 100 + 무음 5,000) 로 답이 오고 아래 assert 가 잡는다.
            # 답이 하나뿐이면 교체 실패 시 전사가 안 와서 테스트가 실패 대신 멈춘다
            Plan(
                replies=[
                    transcript(0, 100, "앞", is_final=True),
                    transcript(5_100, 5_200, "뒤", is_final=True),
                ]
            ),
            Plan(replies=[transcript(0, 100, "뒤", is_final=True)]),
        )
    )
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")

        ws.send_bytes(frame(1, 0))
        first = ws.receive_json()
        # 10초 갭 — MAX_SILENCE_FILL_MS(5초) 를 넘는다
        ws.send_bytes(frame(2, 10_100))
        second = ws.receive_json()
        status = stop(ws)

    assert first["start_ms"] == 0
    # 예전 동작: 5초만 무음으로 채우고 5,100ms 로 보고했다 (Take 시계와 5초 어긋남)
    assert second["start_ms"] == 10_100
    assert second["segment_id"] == "2-2", "세션은 갈리고 세그먼트 번호는 이어진다"
    assert len(adapter.sessions) == 2
    # 못 받은 오디오는 유실로 기록하되 무음으로 밀어 넣지는 않는다
    assert status["lost_ms"] == 10_000
    assert status["silence_ms"] == 0
    assert [len(a) for a in adapter.sessions[1].audio] == [3200]


def test_error_code_must_be_a_known_value():
    """code 는 FE 분기 키다. 오타를 런타임에서 막는다 (이 프로젝트에 정적 타입 검사기는 없다)."""
    assert (
        json.loads(ErrorMessage(code=WsErrorCode.BAD_MESSAGE, message="x").model_dump_json())[
            "code"
        ]
        == "BAD_MESSAGE"
    )
    with pytest.raises(ValidationError):
        ErrorMessage(code="TYPO_CODE", message="x")


# ── P1 인증 후 DB 커넥션을 다시 여는 문제 ───────────────────────────────


def test_authenticate_does_not_touch_user_after_rollback(
    client: TestClient,
    stt: FakeSttAdapter,
    db_session: Session,
    user: User,
    token: str,
    monkeypatch: pytest.MonkeyPatch,
):
    """rollback() 은 세션의 객체를 전부 expire 시킨다. 그 뒤 user.id 를 읽으면 SQLAlchemy
    가 값을 다시 읽으려고 커넥션을 풀에서 또 꺼낸다 — 방금 돌려준 의미가 없어진다.
    이미 알고 있는 user_id(요청 값) 를 그대로 쓰면 이 문제 자체가 없다."""
    tripwire = {"rolled_back": False, "id_read_after_rollback": False}

    class TripwireUser:
        def __init__(self, real_id: uuid.UUID) -> None:
            self._id = real_id

        @property
        def id(self) -> uuid.UUID:
            if tripwire["rolled_back"]:
                tripwire["id_read_after_rollback"] = True
            return self._id

    monkeypatch.setattr(service.user_service, "find", lambda db, uid: TripwireUser(user.id))
    original_rollback = db_session.rollback

    def rollback_and_arm() -> None:
        original_rollback()
        tripwire["rolled_back"] = True

    monkeypatch.setattr(db_session, "rollback", rollback_and_arm)

    with client.websocket_connect(WS_PATH) as ws:
        auth(ws, token)
        ready = ws.receive_json()

    assert ready["type"] == "ready"
    assert tripwire["rolled_back"] is True
    assert tripwire["id_read_after_rollback"] is False, (
        "user.id 를 rollback 이후에 읽었다 — 커넥션을 다시 연다"
    )


# ── Take 연결: 존재·소유·상태 검사 ──────────────────────────────────────


def test_unknown_take_is_rejected_before_deepgram(
    client: TestClient, stt: FakeSttAdapter, user: User
):
    with client.websocket_connect(f"/api/ws/takes/{uuid.uuid7()}") as ws:
        auth(ws, create_access_token(user.id))
        assert ws.receive_json()["code"] == "TAKE_NOT_FOUND"
        assert closed_with(ws) == 1008
    assert stt.configs == [], "Take 검사에 걸리면 Deepgram 에 붙지 않는다"
    assert registry.active_count() == 0


def test_ended_take_is_rejected(
    client: TestClient, stt: FakeSttAdapter, db_session: Session, user: User
):
    """끝난 연습에 전사를 더 붙이면 리포트가 오염된다. FE 는 이 코드로 재연결하지 않는다."""
    ended = make_take(db_session, user.id, status="COMPLETED")
    with client.websocket_connect(f"/api/ws/takes/{ended.id}") as ws:
        auth(ws, create_access_token(user.id))
        assert ws.receive_json()["code"] == "TAKE_ENDED"
        assert closed_with(ws) == 1008
    assert stt.configs == []


def test_someone_elses_take_is_rejected_even_without_a_live_stream(
    client: TestClient, stt: FakeSttAdapter, db_session: Session, other_token: str
):
    """소유권 검사는 레지스트리(owner_id)가 아니라 DB(takes JOIN pitches.user_id) 가 한다.
    스트림이 하나도 없어도 남의 Take 는 열리지 않고, "없는 Take" 와 같은 코드로 답한다."""
    # TAKE_ID 는 `take` fixture(=token 의존) 가 만드는데 여기선 그걸 안 쓰고 다른 사람 걸 만든다
    with client.websocket_connect(WS_PATH) as ws:
        auth(ws, other_token)
        assert ws.receive_json()["code"] == "TAKE_NOT_FOUND"
        assert closed_with(ws) == 1008
    assert stt.configs == [], "Deepgram 에 붙지 않는다"
    assert registry.active_count() == 0, "스트림도 만들지 않는다"


def test_reconnect_is_authorized_again_even_while_the_stream_is_alive(
    client: TestClient, stt: FakeSttAdapter, db_session: Session, token: str, take: Take
):
    """검사는 연결마다 한다. grace 동안 스트림이 살아 있어도 그 사이 Take 가 끝났으면
    (예: complete 처리) 재연결은 거절된다 — 레지스트리에 있다고 통과시키지 않는다."""
    with client.websocket_connect(WS_PATH) as ws:
        handshake(ws, token)
        wait_state(ws, "ok")
        # 오디오를 한 번도 안 받은 스트림은 끊기면 바로 정리된다. grace 에 들어가려면
        # 받은 오디오가 있어야 한다
        ws.send_bytes(frame(1, 0))
    assert registry.active_count() == 1, "grace 중이라 스트림은 살아 있다"

    take.status = "COMPLETED"
    db_session.commit()

    with client.websocket_connect(WS_PATH) as ws:
        auth(ws, token)
        assert ws.receive_json()["code"] == "TAKE_ENDED"
        assert closed_with(ws) == 1008
    assert len(stt.sessions) == 1, "재연결이 거절돼 새 Deepgram 세션이 생기지 않는다"
