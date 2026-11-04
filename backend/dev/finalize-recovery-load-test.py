"""합성 backlog 의 복구 RSS · DB 대기 · 소요 시간을 로컬 임시 DB 에서 잰다.

backend 에서 실행:
    uv run python dev/finalize-recovery-load-test.py --takes 24 --seconds 3600 --http-delay 1

DB 조회 · replay · 결과 저장은 실제 코드이고 HTTP 응답만 대역이다. 이전 방식(전체 ID · 태스크,
HTTP 만 4개 제한)과 작업 제한 2 · 4개를 각각 새 프로세스에서 비교한다. DB 는 끝나면 지운다.
RSS 는 프로세스 최고값이며 DB 대기는 연결 획득 시간과 pg_stat_activity 대기 표본을 구분한다.
실제 AI 처리 시간이나 운영 서버의 안전 한도를 재는 도구는 아니다.
"""

import argparse
import asyncio
import json
import logging
import os
import resource
import statistics
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import create_engine, insert, select, text, update
from sqlalchemy.engine import make_url


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--takes", type=int, default=24)
    parser.add_argument("--seconds", type=int, default=3600)
    parser.add_argument("--http-delay", type=float, default=1.0)
    parser.add_argument("--limits", default="2,4")
    parser.add_argument("--worker", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.takes < 1 or not 1 <= args.seconds <= 3600 or args.http_delay < 0:
        parser.error("takes 는 양수, seconds 는 1~3600, http-delay 는 0 이상이어야 한다")
    return args


def seed(args):
    from pitch_coach_backend.core.database import SessionLocal
    from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion, ScriptVersion
    from pitch_coach_backend.module.take.entity import (
        Take,
        TakeMetricSecond,
        TakeSlideEvent,
        TakeTranscriptSegment,
    )
    from pitch_coach_backend.module.user.entity import User

    with SessionLocal() as db:
        user = User(email=f"{uuid.uuid4()}@load.test", name="복구 부하")
        db.add(user)
        db.flush()
        pitch = Pitch(user_id=user.id, title="합성 발표", time_limit_sec=args.seconds)
        db.add(pitch)
        db.flush()
        script = ScriptVersion(pitch_id=pitch.id, version=1, content="합성 대본")
        deck = PresentationVersion(pitch_id=pitch.id, version=1, file_key="synthetic.pdf")
        db.add_all([script, deck])
        db.flush()
        for number in range(1, args.takes + 1):
            take = Take(
                pitch_id=pitch.id,
                take_number=number,
                script_version_id=script.id,
                presentation_version_id=deck.id,
                mode="COACHING",
                script_mode="HIGHLIGHT",
                goal_time_sec=args.seconds,
                status="ANALYZING",
                analysis_stage="FINALIZING",
                raw_data_consent=True,
            )
            db.add(take)
            db.flush()
            db.add(TakeSlideEvent(take_id=take.id, slide_number=1, started_ms=0))
            db.execute(
                insert(TakeMetricSecond),
                [
                    {
                        "take_id": take.id,
                        "t_ms": sec * 1000,
                        "slide_number": 1,
                        "stt_status": "ok",
                        "gaze": {
                            "t_ms": (sec - 1) * 1000,
                            "duration_ms": 1000,
                            "state": "CAMERA",
                            "confidence": 0.9,
                        },
                        "voice": {
                            "t_ms": (sec - 1) * 1000,
                            "duration_ms": 1000,
                            "level_db": -30.0,
                            "voiced_ms": 800,
                            "silence_ms": 200,
                            "audio_live": True,
                        },
                    }
                    for sec in range(1, args.seconds + 1)
                ],
            )
            db.execute(
                insert(TakeTranscriptSegment),
                [
                    {
                        "take_id": take.id,
                        "seq": sec,
                        "stt_session_no": 1,
                        "confidence": 0.9,
                        "speech_final": False,
                        "start_ms": sec * 1000,
                        "end_ms": (sec + 1) * 1000,
                        "final_at_ms": (sec + 1) * 1000,
                        "transcript": "합성 단어",
                        "words": [
                            {"word": "합성", "start_ms": sec * 1000, "end_ms": sec * 1000 + 400},
                            {
                                "word": "단어",
                                "start_ms": sec * 1000 + 500,
                                "end_ms": sec * 1000 + 900,
                            },
                        ],
                    }
                    for sec in range(args.seconds)
                ],
            )
            db.commit()


async def measure(args):
    import httpx
    from starlette.concurrency import run_in_threadpool

    from pitch_coach_backend.coach import finalize
    from pitch_coach_backend.coach.client import HttpCoachClient
    from pitch_coach_backend.coach.store import DbCoachStore
    from pitch_coach_backend.core.database import engine
    from pitch_coach_backend.module.take.entity import Take

    acquired, active, maximum = [], 0, 0
    source_active, source_peak, tasks_peak, completed = 0, 0, 0, 0
    waits_peak = 0
    lock = threading.Lock()
    monitor_engine = create_engine(os.environ["DATABASE_URL"], pool_size=1, max_overflow=0)
    template = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "ai/service/src/pitch_coach_ai/features/coach/examples/finalize/response.json"
        ).read_text()
    )

    class TimedStore(DbCoachStore):
        def _with(self, fn, *values):
            nonlocal active, maximum
            db = self._session_factory()
            started = time.perf_counter()
            try:
                db.connection()
                with lock:
                    acquired.append((time.perf_counter() - started) * 1000)
                    active += 1
                    maximum = max(maximum, active)
                try:
                    return fn(db, *values)
                finally:
                    with lock:
                        active -= 1
            finally:
                db.close()

        async def load_finalize_source(self, take_id):
            nonlocal source_active, source_peak
            result = await super().load_finalize_source(take_id)
            if result is not None:
                source_active += 1
                source_peak = max(source_peak, source_active)
            return result

        async def save_take_result(self, take_id, reply, events):
            nonlocal source_active, completed
            saved = await super().save_take_result(take_id, reply, events)
            source_active -= 1
            completed += int(saved)
            return saved

    async def respond(request):
        await asyncio.sleep(args.http_delay)
        data = json.loads(request.content)
        body = {
            **template,
            "take_result": {
                **template["take_result"],
                "take_id": data["take_id"],
                "duration_ms": args.seconds * 1000,
                "replayed": True,
            },
        }
        return httpx.Response(200, json=body)

    def db_waits():
        with monitor_engine.connect() as conn:
            return conn.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                    "AND wait_event_type IN ('Lock', 'LWLock', 'IO') AND pid <> pg_backend_pid()"
                )
            )

    async def sample():
        nonlocal tasks_peak, waits_peak
        while True:
            tasks_peak = max(tasks_peak, len(finalize._running))
            waits_peak = max(waits_peak, await run_in_threadpool(db_waits))
            await asyncio.sleep(0.05)

    store = TimedStore()
    client = HttpCoachClient("http://synthetic.invalid", transport=httpx.MockTransport(respond))
    # 0 은 변경 전 전체 dispatch 다. 모든 비교에서 실제 HTTP 클라이언트의 4개 제한을 쓴다.
    finalize.FINALIZE_CONCURRENCY = args.worker or args.takes
    finalize.RESUME_BATCH_SIZE = args.worker or args.takes
    baseline_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    monitoring = asyncio.create_task(sample())
    started = time.perf_counter()
    try:
        if args.worker == 0:

            def all_ids():
                with engine.connect() as conn:
                    return list(conn.scalars(select(Take.id).order_by(Take.id)))

            ids = await run_in_threadpool(all_ids)
            for take_id in ids:
                finalize.spawn(take_id, None, client=client, store=store)
            await asyncio.gather(*finalize._running.tasks())
        else:
            await finalize.resume(client=client, store=store)
            await asyncio.gather(*finalize._recovery.tasks())
    finally:
        elapsed = time.perf_counter() - started
        monitoring.cancel()
        await asyncio.gather(monitoring, return_exceptions=True)
        monitor_engine.dispose()
    assert completed == args.takes, f"복구 누락: {completed}/{args.takes}"
    # macOS 는 바이트, Linux 는 KiB 이다.
    unit = 1024**2 if sys.platform == "darwin" else 1024
    ordered = sorted(acquired)
    return {
        "limit": args.worker,
        "backlog": args.takes,
        "seconds_per_take": args.seconds,
        "http_delay_sec": args.http_delay,
        "completed": completed,
        "elapsed_sec": round(elapsed, 3),
        "baseline_rss_mib": round(baseline_rss / unit, 1),
        "peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / unit, 1),
        "peak_finalize_tasks": tasks_peak,
        "peak_loaded_sources": source_peak,
        "peak_db_connections": maximum,
        "peak_sampled_db_waits": waits_peak,
        "connection_acquire_p95_ms": round(ordered[int((len(ordered) - 1) * 0.95)], 2),
        "connection_acquire_max_ms": round(max(acquired), 2),
        "connection_acquire_mean_ms": round(statistics.mean(acquired), 2),
    }


def main():
    args = arguments()
    logging.disable(logging.CRITICAL)
    if args.worker is not None:
        print(json.dumps(asyncio.run(measure(args)), ensure_ascii=False))
        return
    limits = [int(value) for value in args.limits.split(",")]
    if any(value < 1 for value in limits):
        raise ValueError("limits 는 양수여야 한다")
    env = dotenv_values(Path.cwd() / ".env")
    base = make_url(
        os.environ.get("LOCAL_DATABASE_URL")
        or env.get("LOCAL_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or env["DATABASE_URL"]
    )
    name = f"coach_recovery_load_{uuid.uuid4().hex[:12]}"
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    created = False
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        created = True
        os.environ["DATABASE_URL"] = base.set(database=name).render_as_string(hide_password=False)
        from pitch_coach_backend import entities  # noqa: F401
        from pitch_coach_backend.core.database import Base, engine
        from pitch_coach_backend.module.take.entity import Take

        Base.metadata.create_all(engine)
        seed(args)
        for limit in [0, *limits]:
            with engine.begin() as conn:
                conn.execute(update(Take).values(status="ANALYZING", analysis_stage="FINALIZING"))
            command = [
                sys.executable,
                __file__,
                "--worker",
                str(limit),
                "--takes",
                str(args.takes),
                "--seconds",
                str(args.seconds),
                "--http-delay",
                str(args.http_delay),
            ]
            # 연결 정보는 환경변수로만 넘기고 출력이나 명령 인자에 싣지 않는다.
            subprocess.run(command, check=True, env=os.environ.copy())
        engine.dispose()
    finally:
        if created:
            with admin.connect() as conn:
                conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        admin.dispose()


if __name__ == "__main__":
    main()
