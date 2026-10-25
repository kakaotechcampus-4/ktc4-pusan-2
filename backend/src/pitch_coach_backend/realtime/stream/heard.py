"""끊김 없이 STT 로 보낸 오디오의 Take 시각 범위. 1초 원자료의 STT 상태(`ok`)를 이걸로 정한다.

FE 는 끊긴 동안 쌓아 둔 1초를 다시 붙은 뒤 보낸다. 그 1초가 이 범위 밖이면 STT 가 듣지 못한
구간이라 `ok` 로 적지 않는다 (`runtime.TakeStream._stt_status_of`). 네트워크도 Deepgram 도 모른다.
"""

from pitch_coach_backend.realtime.event_ingestion import Accepted

# 1초 원자료의 STT 상태를 ok 로 볼 때, 그 1초 끝보다 이만큼 늦게 받은 오디오까지 봐준다. FE 는
# 오디오 프레임과 metrics 를 따로 보내 그 1초의 마지막 프레임이 metrics 뒤에 올 수 있다
HEARD_SLACK_MS = 1_000


class HeardRange:
    """[시작, 끝) (ms). 세션을 갈아야 하는 끊김(`timeline_break`)에서 다시 시작한다."""

    def __init__(self) -> None:
        self._from_ms: int | None = None
        self._until_ms: int | None = None

    def add(self, accepted: Accepted) -> None:
        """받은 프레임으로 범위를 늘린다."""
        if self._from_ms is None or accepted.timeline_break:
            self._from_ms = accepted.frame.offset_ms
        self._until_ms = accepted.frame.end_ms

    def drop_until(self, end_ms: int) -> None:
        """STT 에 닿지 못한 오디오다. 그 뒤부터를 끊김 없이 들은 것으로 본다."""
        if self._from_ms is not None:
            self._from_ms = max(self._from_ms, end_ms)

    def covers(self, start_ms: int, end_ms: int) -> bool:
        """[start_ms, end_ms) 를 끊김 없이 들었다."""
        return (
            self._from_ms is not None
            and self._until_ms is not None
            and start_ms >= self._from_ms
            and end_ms <= self._until_ms + HEARD_SLACK_MS
        )
