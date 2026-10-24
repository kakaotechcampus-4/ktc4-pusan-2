"""1초 원자료(take_metric_seconds) 저장 부하를 같은 Postgres 의 임시 DB 에서 잰다.

앱과 같은 길로 저장한다: MetricBuffer → DbTranscriptStore → threadpool → 앱 엔진(커넥션 풀 기본값).
Take 마다 1초에 1행씩 넣고 묶음 크기 · 동시 Take 수를 바꿔 가며 저장 한 번에 걸린 시간과 커넥션
사용량을 잰다. 임시 DB 는 끝나면 지운다. 같은 Postgres 에 부하를 주므로 쓰는 사람이 없을 때 돌린다.
develop 서버에서 잰 결과는 #338 에 있다.

실행
    uv run python dev/metrics-load-test.py      # 로컬. .env 의 LOCAL_DATABASE_URL 로 붙는다
    LOAD_SCENARIOS=50:5,200:5 LOAD_DURATION=10 uv run python dev/metrics-load-test.py

    # 서버: 스크립트를 /tmp 에 붙여 넣은 뒤 be 컨테이너에서 돌린다.
    # 컨테이너의 DATABASE_URL 로 붙는다
    sudo docker exec -i infra-be-1 python - < /tmp/metrics-load-test.py

LOAD_SCENARIOS 는 '동시 Take:묶음' 목록(기본 50:1,50:5,50:10,200:1,200:5,200:10,500:5),
LOAD_DURATION 은 시나리오마다 넣는 초(기본 30)다.
"""

import asyncio
import os
import random
import statistics
import sys
import time
import uuid
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

TMP_DB = "pitch_coach_load_tmp"
DURATION_SEC = float(os.environ.get("LOAD_DURATION", "30"))
SCENARIOS = [
    (int(n), int(b))
    for n, b in (
        pair.split(":")
        for pair in os.environ.get(
            "LOAD_SCENARIOS", "50:1,50:5,50:10,200:1,200:5,200:10,500:5"
        ).split(",")
    )
]

# 서버(be 컨테이너 안)는 컨테이너 환경 변수 DATABASE_URL 을 쓴다. 로컬은 backend/.env 의
# DATABASE_URL 이 도커 안 주소라 LOCAL_DATABASE_URL 을 쓴다. 어느 쪽이든 그 Postgres 에 임시 DB 를
# 따로 만들어 쓰고 원래 DB 는 건드리지 않는다
env_file = Path.cwd() / ".env"
env = dotenv_values(env_file) if env_file.exists() else {}
base = make_url(os.environ.get("DATABASE_URL") or env["LOCAL_DATABASE_URL"])
admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
with admin.connect() as conn:
    conn.execute(text(f'DROP DATABASE IF EXISTS "{TMP_DB}"'))
    conn.execute(text(f'CREATE DATABASE "{TMP_DB}"'))

# settings 가 읽기 전에 임시 DB 로 돌린다 (.env 의 DATABASE_URL 은 도커 안 주소다)
os.environ["DATABASE_URL"] = base.set(database=TMP_DB).render_as_string(hide_password=False)

from pitch_coach_backend import entities  # noqa: E402,F401
from pitch_coach_backend.core.database import Base, SessionLocal, engine  # noqa: E402
from pitch_coach_backend.module.pitch.entity import (  # noqa: E402
    Pitch,
    PresentationVersion,
    ScriptVersion,
)
from pitch_coach_backend.module.take.dto import MetricSecondCreateDTO  # noqa: E402
from pitch_coach_backend.module.take.entity import Take  # noqa: E402
from pitch_coach_backend.module.user.entity import User  # noqa: E402
from pitch_coach_backend.realtime import metric_buffer  # noqa: E402
from pitch_coach_backend.realtime.transcript_store import DbTranscriptStore  # noqa: E402


def make_takes(count: int) -> list[uuid.UUID]:
    with SessionLocal() as db:
        user = User(email=f"{uuid.uuid4()}@load.test", name="부하")
        db.add(user)
        db.flush()
        pitch = Pitch(user_id=user.id, title="부하", time_limit_sec=600)
        db.add(pitch)
        db.flush()
        presentation = PresentationVersion(pitch_id=pitch.id, version=1, file_key="d.pdf")
        script = ScriptVersion(pitch_id=pitch.id, version=1, content="대본")
        db.add_all([presentation, script])
        db.flush()
        takes = [
            Take(
                pitch_id=pitch.id,
                take_number=i + 1,
                presentation_version_id=presentation.id,
                script_version_id=script.id,
                mode="COACHING",
                script_mode="HIGHLIGHT",
                goal_time_sec=600,
                status="RUNNING",
            )
            for i in range(count)
        ]
        db.add_all(takes)
        db.commit()
        return [t.id for t in takes]


def row(t_ms: int) -> MetricSecondCreateDTO:
    start = t_ms - 1000
    return MetricSecondCreateDTO(
        t_ms=t_ms,
        slide_number=3,
        # 실제 기록은 200바이트 안팎이다 (realtime/dto.py 주석). 비슷한 크기로 만든다
        gaze={
            "t_ms": start,
            "duration_ms": 1000,
            "state": "CAMERA",
            "confidence": 0.91,
            "measured_ms": 1000,
            "camera_ms": 820,
            "bottom_ms": 180,
            "uncertain_ms": 0,
        },
        voice={
            "t_ms": start,
            "duration_ms": 1000,
            "level_db": -31.2,
            "voiced_ms": 640,
            "silence_ms": 360,
            "audio_live": True,
        },
        stt_status="ok",
    )


class TimedStore(DbTranscriptStore):
    def __init__(self) -> None:
        super().__init__()
        self.latencies: list[float] = []
        self.errors = 0
        self.rows = 0

    async def append_metrics(self, take_id, rows) -> None:
        started = time.perf_counter()
        try:
            await super().append_metrics(take_id, rows)
            self.rows += len(rows)
        except Exception:
            self.errors += 1
            raise
        finally:
            self.latencies.append(time.perf_counter() - started)


def table_size() -> tuple[int, int]:
    with engine.connect() as conn:
        size = conn.scalar(text("SELECT pg_total_relation_size('take_metric_seconds')"))
        count = conn.scalar(text("SELECT count(*) FROM take_metric_seconds"))
    return int(size), int(count)


async def scenario(take_ids: list[uuid.UUID], batch: int) -> dict:
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE take_metric_seconds"))
    metric_buffer.METRICS_BATCH = batch
    store = TimedStore()
    buffers = [metric_buffer.MetricBuffer(take_id, store) for take_id in take_ids]
    peak = {"checkedout": 0, "overflow": 0}
    done = asyncio.Event()

    async def sample() -> None:
        while not done.is_set():
            peak["checkedout"] = max(peak["checkedout"], engine.pool.checkedout())
            peak["overflow"] = max(peak["overflow"], engine.pool.overflow())
            await asyncio.sleep(0.02)

    async def speak(buffer: metric_buffer.MetricBuffer) -> None:
        # Take 마다 시작을 흩는다 — 모두 같은 순간에 넣지 않게
        await asyncio.sleep(random.random())
        began = time.monotonic()
        second = 0
        while time.monotonic() - began < DURATION_SEC:
            second += 1
            buffer.add(row(second * 1000))
            await asyncio.sleep(1.0)

    sampler = asyncio.create_task(sample())
    started = time.perf_counter()
    await asyncio.gather(*(speak(buffer) for buffer in buffers))
    await asyncio.gather(*(buffer.flush() for buffer in buffers))
    elapsed = time.perf_counter() - started
    done.set()
    await sampler
    size, count = table_size()
    latencies = sorted(store.latencies)
    return {
        "commits_per_sec": len(latencies) / elapsed,
        "rows_per_sec": store.rows / elapsed,
        "p50_ms": statistics.median(latencies) * 1000,
        "p95_ms": latencies[int(len(latencies) * 0.95) - 1] * 1000,
        "max_ms": latencies[-1] * 1000,
        "peak_conn": peak["checkedout"],
        "peak_overflow": peak["overflow"],
        "errors": store.errors,
        "bytes_per_row": size / count if count else 0,
        "rows": count,
    }


async def main() -> None:
    Base.metadata.create_all(engine)
    take_ids = make_takes(max(n for n, _ in SCENARIOS))
    print(
        f"{'Take':>5} {'묶음':>4} {'커밋/초':>8} {'행/초':>7} "
        f"{'p50ms':>7} {'p95ms':>7} {'최대ms':>8} "
        f"{'커넥션':>6} {'넘침':>4} {'실패':>4} {'행당B':>6}",
        flush=True,
    )
    for n, batch in SCENARIOS:
        r = await scenario(take_ids[:n], batch)
        print(
            f"{n:>5} {batch:>4} {r['commits_per_sec']:>8.1f} {r['rows_per_sec']:>7.1f} "
            f"{r['p50_ms']:>7.1f} {r['p95_ms']:>7.1f} {r['max_ms']:>8.1f} "
            f"{r['peak_conn']:>6} {r['peak_overflow']:>4} {r['errors']:>4} "
            f"{r['bytes_per_row']:>6.0f}",
            flush=True,
        )


try:
    asyncio.run(main())
finally:
    engine.dispose()
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{TMP_DB}" WITH (FORCE)'))
    admin.dispose()
    print("임시 DB 를 지웠다", file=sys.stderr)
