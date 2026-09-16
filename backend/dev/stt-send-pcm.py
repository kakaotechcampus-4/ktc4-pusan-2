"""로컬 서버의 /api/ws/takes/{take_id} 에 PCM 파일을 실시간 속도로 흘려 전사를 본다.

FE 없이 BE -> Deepgram 경로를 끝까지 확인하는 용도. 앱이 서빙하지 않는다.

준비
    uv run fastapi dev src/pitch_coach_backend/main.py      # 서버. .env 에 DEEPGRAM_API_KEY 필요
    ffmpeg -i 녹음.m4a -ar 16000 -ac 1 -f s16le -acodec pcm_s16le 녹음.pcm
    # 마이크로 바로: ffmpeg -f avfoundation -i ":0" -t 20 -ar 16000 -ac 1 -f s16le 녹음.pcm

실행
    uv run python dev/stt-send-pcm.py 녹음.pcm --email me@example.com
    # --email 은 로그인해 둔 계정. 그 사용자의 Access 토큰을 직접 만든다
    # (같은 .env 의 JWT_SECRET_KEY 로 서명하므로 서버가 받아준다)

16 kHz mono 16-bit wav 도 된다. 다른 규격의 wav 는 거절한다
(에러 없이 쓰레기 전사가 나오는 것보다 낫다).
"""

import argparse
import asyncio
import json
import sys
import time
import uuid
import wave
from pathlib import Path

from websockets.asyncio.client import connect

from pitch_coach_backend.core.database import SessionLocal
from pitch_coach_backend.core.security import create_access_token
from pitch_coach_backend.module.user import service as user_service
from pitch_coach_backend.realtime.audio import BYTES_PER_MS
from pitch_coach_backend.realtime.event_ingestion import FRAME_HEADER

CHUNK_MS = 100
CHUNK_BYTES = CHUNK_MS * BYTES_PER_MS


def load_pcm(path: Path) -> bytes:
    if path.suffix.lower() != ".wav":
        return path.read_bytes()
    with wave.open(str(path), "rb") as w:
        params = (w.getnchannels(), w.getsampwidth(), w.getframerate())
        if params != (1, 2, 16_000):
            sys.exit(f"wav 는 mono/16-bit/16kHz 여야 합니다. 지금: {params}")
        return w.readframes(w.getnframes())


def access_token_for(email: str) -> str:
    with SessionLocal() as db:
        user = user_service.find_by_email(db, email)
        if user is None:
            sys.exit(f"사용자가 없습니다: {email} (먼저 구글 로그인으로 가입)")
        return create_access_token(user.id)


async def send_audio(ws, pcm: bytes) -> None:
    """FE 처럼 100ms 마다 한 프레임. offset 은 파일 기준이라 프레임 번호 * 100."""
    started = time.monotonic()
    for seq, start in enumerate(range(0, len(pcm), CHUNK_BYTES), start=1):
        chunk = pcm[start : start + CHUNK_BYTES]
        if len(chunk) % 2:
            chunk = chunk[:-1]
        offset_ms = (seq - 1) * CHUNK_MS
        await ws.send(FRAME_HEADER.pack(seq, offset_ms) + chunk)
        # 실시간 속도 유지. 몰아 보내면 Deepgram 의 start 가 밀린다
        due = started + seq * CHUNK_MS / 1000
        await asyncio.sleep(max(0.0, due - time.monotonic()))
    await ws.send(json.dumps({"type": "stop"}))


async def print_messages(ws) -> None:
    async for raw in ws:
        message = json.loads(raw)
        match message["type"]:
            case "transcript":
                line = f"[{message['start_ms']:>6}~{message['end_ms']:>6}] {message['text']}"
                if message["is_final"]:
                    print(f"\r{line:<100}")
                    for w in message["words"]:
                        print(
                            f"        {w['start_ms']:>6}~{w['end_ms']:>6}  "
                            f"{w['confidence']:.2f}  {w['word']}"
                        )
                else:
                    print(f"\r{line:<100}", end="", flush=True)
            case "stt_status":
                print(f"\n{message}")
                if message["state"] == "closed":
                    return
            case _:
                print(f"\n{message}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("audio", type=Path, help="raw s16le 16kHz mono PCM, 또는 같은 규격의 wav")
    parser.add_argument("--email", required=True, help="토큰을 만들 사용자")
    parser.add_argument("--url", default="ws://localhost:8000/api/ws/takes")
    parser.add_argument("--take-id", default=str(uuid.uuid7()))
    args = parser.parse_args()

    pcm = load_pcm(args.audio)
    print(f"{len(pcm) / BYTES_PER_MS / 1000:.1f}s 오디오, take={args.take_id}")

    async with connect(f"{args.url}/{args.take_id}") as ws:
        await ws.send(json.dumps({"type": "auth", "token": access_token_for(args.email)}))
        ready = json.loads(await ws.recv())
        if ready["type"] != "ready":
            sys.exit(f"연결 실패: {ready}")
        print(f"ready: stt_session_no={ready['stt_session_no']}")

        async with asyncio.TaskGroup() as tg:
            tg.create_task(send_audio(ws, pcm))
            tg.create_task(print_messages(ws))


if __name__ == "__main__":
    asyncio.run(main())
