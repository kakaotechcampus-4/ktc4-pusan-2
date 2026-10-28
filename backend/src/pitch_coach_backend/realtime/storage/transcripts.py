"""확정 전사(final)의 번호 · 화면에 못 보낸 final 보관(`TranscriptBuffer`, 스트림 하나에 하나)과
저장(`FinalBuffer`, Take 하나에 하나).

final 은 `TranscriptStore` 로 저장한다 (take 모듈 service 에 위임). 스트림이 새로 만들어질 때는
저장된 마지막 번호와 아직 못 넣은 final 의 번호 중 큰 쪽을 이어 받는다 — grace 가 만료돼 스트림이
사라진 뒤 다시 붙어도 `segment_id` 가 `"1-1"` 부터 다시 나오지 않아야 FE 가 앞부분을 덮어쓰지 않고,
`UNIQUE(take_id, seq)` 도 지켜진다. 저장에 실패한 final 은 Take 가 들고 있다가 다시 넣는다.

FE 가 떨어져 있는 동안 온 final 은 모아 두었다가 다시 붙으면 보낸다. 실제 WebSocket 전송은 스트림
(`stream/runtime.py`)이 하고, 여기서는 그 결과를 받아 목록을 고친다. connection · stream 을
import 하지 않는다.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable

from pitch_coach_backend.core.row_buffer import RowBuffer
from pitch_coach_backend.module.take.dto import TranscriptSegmentCreateDTO, TranscriptWordDTO
from pitch_coach_backend.module.take.exception import DuplicateTranscriptSegment, NonExistentTake
from pitch_coach_backend.realtime.dto import TranscriptMessage
from pitch_coach_backend.realtime.storage.store import TranscriptStore

logger = logging.getLogger(__name__)

# FE 가 떨어져 있는 동안 모아 둘 final 개수. 10분 발표가 200개 안팎이다
MAX_MISSED_FINALS = 200
# 저장에 실패해 들고 있을 final 개수. 넘치면 오래된 것부터 버린다
MAX_UNSAVED_FINALS = 200


class TranscriptBuffer:
    def __init__(self, take_id: uuid.UUID, store: TranscriptStore, *, segment_no: int = 1) -> None:
        self.take_id = take_id
        self._store = store
        # Take 기준이다. 스트림이 새로 만들어질 때 저장된 마지막 값에서 이어 받는다
        self.segment_no = segment_no
        # FE 가 떨어져 있는 동안 온 final. 다시 붙으면 순서대로 보낸다
        self.missed_finals: list[TranscriptMessage] = []
        # 저장에 실패한 final. 번호 순서대로 다시 저장한다 (`save_unsaved`)
        self.unsaved: list[TranscriptSegmentCreateDTO] = []
        # resend() 를 감싼다. 탭 두 개가 거의 동시에 붙으면(예: 재연결이 겹칠 때)
        # 서로 다른 RealtimeSession 이 이 메서드를 동시에 부를 수 있다. 락 없이 같은
        # 리스트를 각자 확인(while)->접근([0])->제거(pop) 하면, await 로 갈라진 그 틈에
        # 한쪽이 이미 비운 자리를 다른 쪽이 또 pop(0) 하다가 IndexError 가 난다
        self._resend_lock = asyncio.Lock()

    def segment_id(self, stt_session_no: int) -> str:
        """FE 가 덮어쓸 구간을 고르는 번호. 같으면 덮어쓴다."""
        return f"{stt_session_no}-{self.segment_no}"

    def next_segment(self) -> None:
        """final 이 구간을 닫았다 (빈 final 포함). 다음 interim 은 새 번호로."""
        self.segment_no += 1

    def hold_missed(self, message: TranscriptMessage) -> None:
        """FE 가 떨어져 있다. interim 은 다음 것이 덮으니 버리고 final 만 모은다."""
        self.missed_finals.append(message)
        del self.missed_finals[:-MAX_MISSED_FINALS]

    async def resend(self, send: Callable[[TranscriptMessage], Awaitable[bool]]) -> None:
        """떨어져 있는 동안 온 final 을 순서대로 다시 보낸다.

        `ready` 뒤에 스트림이 부른다 (`TakeStream.resend_missed`). `send` 는 실제 전송이고 성공
        여부를 돌려준다. 저장은 화면과 따로 하므로 리포트에는 영향이 없다 — 이건 실시간
        화면(자막·2단 코치)이 끊긴 구간을 건너뛰지 않게 하는 용도다.

        **성공한 것만 지운다.** 한꺼번에 비우고 보내면, 재연결 직후 클라이언트가
        다시 끊기는 것처럼 중간에 전송이 실패했을 때 아직 못 보낸 나머지가 이미
        비워진 리스트와 함께 통째로 사라진다. 하나씩 확인하며 지워서, 실패하면
        그 지점부터는 다음 재연결 때 다시 시도할 수 있게 남겨 둔다.

        **락으로 감싼다.** 이 메서드가 두 번 동시에 돌면 같은 자리를 두 번 pop 하게 된다.
        """
        async with self._resend_lock:
            while self.missed_finals:
                message = self.missed_finals[0]
                if not await send(message):
                    logger.warning(
                        "final 재전송이 중간에 끊겼다 take=%s 남은 개수=%d",
                        self.take_id,
                        len(self.missed_finals),
                    )
                    return
                # 전송을 기다리는 동안 새 final이 쌓여 상한으로 이 메시지가 이미 밀려났을 수
                # 있다. 그때 다음 메시지를 대신 지우면 아직 보내지 않은 final을 잃는다.
                if self.missed_finals and self.missed_finals[0] is message:
                    self.missed_finals.pop(0)
            logger.info("재연결 중 놓친 final 을 모두 다시 보냈다 take=%s", self.take_id)

    def segment(
        self, message: TranscriptMessage, final_at_ms: int, stt_session_no: int
    ) -> TranscriptSegmentCreateDTO:
        """저장할 행. 번호는 지금 구간이다 — `next_segment` 전에 부른다."""
        return TranscriptSegmentCreateDTO(
            seq=self.segment_no,
            stt_session_no=stt_session_no,
            start_ms=message.start_ms,
            end_ms=message.end_ms,
            transcript=message.text,
            words=[
                TranscriptWordDTO(
                    word=w.word,
                    punctuated_word=w.punctuated_word,
                    start_ms=w.start_ms,
                    end_ms=w.end_ms,
                    confidence=w.confidence,
                )
                for w in message.words
            ],
            confidence=message.confidence,
            speech_final=message.speech_final,
            final_at_ms=final_at_ms,
        )

    def report_leftovers(self) -> None:
        """스트림이 닫힐 때 화면에 못 보낸 final 을 로그로 남긴다. 저장하지 못한 final 은 Take 에
        남아 있다 (`FinalBuffer`)."""
        if self.missed_finals:
            # 저장은 화면과 따로 한다 (final 은 붙어 있는 클라이언트와 무관하게 저장한다).
            # 실시간 화면에만 못 간 것이라 개수만 남긴다
            logger.info(
                "재연결 없이 끝나 화면에 못 보낸 final take=%s count=%d",
                self.take_id,
                len(self.missed_finals),
            )


class FinalBuffer(RowBuffer[TranscriptSegmentCreateDTO]):
    """확정 전사 (take_transcript_segments). 받는 대로 백그라운드로 하나씩 넣는다.

    STT 결과 처리는 저장을 기다리지 않는다 — DB 가 느려도 다음 결과 · 문장 끝 신호가 밀리지 않는다.
    저장은 하나씩만 돌아 번호 순서가 지켜진다. 버퍼는 스트림이 아니라 Take 에 붙어 있어
    (`stream/record.TakeRecord`) grace 가 지나 스트림이 새로 열려도 앞 스트림이 못 넣은 final 이
    이어진다. 다시 넣기 · 지워진 Take · 끝낼 때의 `seal` · `flush` 규칙은 1초 원자료와 같다
    (`RowBuffer`).
    """

    def __init__(self, take_id: uuid.UUID, store: TranscriptStore) -> None:
        super().__init__(
            take_id,
            self._append_all,
            what="확정 전사",
            batch=1,
            key=lambda segment: segment.start_ms,
            max_unsaved=MAX_UNSAVED_FINALS,
            gone_errors=(NonExistentTake,),
        )
        self._store = store
        # 받은 final 의 가장 큰 번호 · Deepgram 세션 번호. 새 스트림이 저장된 번호보다 뒤에서 잇게
        # 한다 — 아직 못 넣은 final 의 번호를 다시 쓰면 `UNIQUE(take_id, seq)` 에 걸린다
        self.last_seq = 0
        self.last_session_no = 0

    def add(self, row: TranscriptSegmentCreateDTO) -> None:
        self.last_seq = max(self.last_seq, row.seq)
        self.last_session_no = max(self.last_session_no, row.stt_session_no)
        super().add(row)

    async def _append_all(
        self, take_id: uuid.UUID, segments: list[TranscriptSegmentCreateDTO]
    ) -> None:
        # 넣은 것을 목록 앞에서 뺀다. 실패하면 `RowBuffer` 가 이 목록을 되돌려 두므로 남은 것만
        # 다시 넣는다
        while segments:
            try:
                await self._store.append(take_id, segments[0])
            except DuplicateTranscriptSegment:
                # 실패로 알았던 저장이 실제로는 커밋됐다 (커밋 뒤 연결이 끊김). 남겨 두면 뒤의
                # final 이 이것에 막혀 영영 저장되지 않는다
                logger.warning(
                    "이미 저장된 final 이다. 넘어간다 take=%s seq=%d", take_id, segments[0].seq
                )
            segments.pop(0)
