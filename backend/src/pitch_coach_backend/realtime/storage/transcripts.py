"""확정 전사(final)의 번호 · 저장 재시도 · 화면에 못 보낸 final 보관. Take 스트림 하나에 하나다.

final 은 `TranscriptStore` 로 저장한다 (take 모듈 service 에 위임). 스트림이 새로 만들어질 때는
저장된 마지막 번호를 이어 받는다 — grace 가 만료돼 스트림이 사라진 뒤 다시 붙어도 `segment_id` 가
`"1-1"` 부터 다시 나오지 않아야 FE 가 앞부분을 덮어쓰지 않고, `UNIQUE(take_id, seq)` 도 지켜진다.
저장에 실패한 final 은 들고 있다가 다음 final 을 저장할 때와 스트림이 정상으로 끝날 때 다시
저장한다.

FE 가 떨어져 있는 동안 온 final 은 모아 두었다가 다시 붙으면 보낸다. 실제 WebSocket 전송은 스트림
(`stream/runtime.py`)이 하고, 여기서는 그 결과를 받아 목록을 고친다. connection · stream 을
import 하지 않는다.
"""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable

from pitch_coach_backend.core.logging import sqlstate
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

    async def persist(
        self, message: TranscriptMessage, final_at_ms: int, stt_session_no: int
    ) -> bool:
        """final 하나를 저장할 목록에 넣고 저장한다. Take 가 지워져 저장할 곳이 없으면 False."""
        self.unsaved.append(self._segment(message, final_at_ms, stt_session_no))
        if len(self.unsaved) > MAX_UNSAVED_FINALS:
            dropped = self.unsaved.pop(0)
            logger.error(
                "저장 못 한 final 이 너무 많아 오래된 것을 버린다 take=%s seq=%d",
                self.take_id,
                dropped.seq,
            )
        return await self.save_unsaved()

    def _segment(
        self, message: TranscriptMessage, final_at_ms: int, stt_session_no: int
    ) -> TranscriptSegmentCreateDTO:
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

    async def save_unsaved(self) -> bool:
        """저장 못 한 final 을 번호 순서대로 저장한다. 실패하면 거기서 멈추고 다음에 다시 한다.

        Take 가 지워져 저장할 곳이 없으면 남은 것을 버리고 False. 스트림은 이걸 보고 끊는다.

        DB 가 죽어 있으면 하나씩 다 시도해도 다 실패한다. 첫 실패에서 멈춰야 final 하나마다
        DB 왕복이 하나로 끝난다.
        """
        while self.unsaved:
            segment = self.unsaved[0]
            try:
                await self._store.append(self.take_id, segment)
            except NonExistentTake:
                # Take 가 지워졌다. 지운 요청이 이 스트림을 끊기 직전에 온 final 이거나, 그 요청이
                # 이 스트림을 보지 못했다. 저장할 곳이 없으니 더 돌 이유가 없다
                logger.warning(
                    "Take 가 지워져 final 을 저장하지 못했다. 스트림을 끊는다 take=%s seq=%d",
                    self.take_id,
                    segment.seq,
                )
                self.unsaved.clear()
                return False
            except DuplicateTranscriptSegment:
                # 실패로 알았던 저장이 실제로는 커밋됐다 (커밋 뒤 연결이 끊김). 남겨 두면 뒤의
                # final 이 이것에 막혀 영영 저장되지 않는다
                logger.warning(
                    "이미 저장된 final 이다. 넘어간다 take=%s seq=%d", self.take_id, segment.seq
                )
            except Exception as exc:
                # DB 장애로 스트림까지 멈추면 안 된다 — 화면 전사는 이미 나갔고 발표는 계속된다.
                # 전사 원문은 로그에 남기지 않는다 (seq 와 길이만). SQLAlchemy 예외 본문에는 SQL
                # 매개변수와 DB 오류 메시지가 붙어 원문이 섞이므로 traceback 도 남기지 않는다
                logger.error(
                    "final 저장 실패 take=%s seq=%d len=%d 남은 개수=%d error=%s sqlstate=%s",
                    self.take_id,
                    segment.seq,
                    len(segment.transcript),
                    len(self.unsaved),
                    type(exc).__name__,
                    sqlstate(exc),
                )
                return True
            self.unsaved.pop(0)
        return True

    def report_leftovers(self) -> None:
        """스트림이 닫힐 때 남은 것을 로그로 남긴다. 화면에 못 보낸 final 과 저장하지 못한 final."""
        if self.missed_finals:
            # 저장은 화면과 따로 한다 (final 은 붙어 있는 클라이언트와 무관하게 저장한다).
            # 실시간 화면에만 못 간 것이라 개수만 남긴다
            logger.info(
                "재연결 없이 끝나 화면에 못 보낸 final take=%s count=%d",
                self.take_id,
                len(self.missed_finals),
            )
        if self.unsaved:
            # 끝날 때까지 저장하지 못했다. 이 구간은 리포트에서 빠진다
            logger.error(
                "저장하지 못한 final 을 버린다 take=%s count=%d seq=%d~%d",
                self.take_id,
                len(self.unsaved),
                self.unsaved[0].seq,
                self.unsaved[-1].seq,
            )
