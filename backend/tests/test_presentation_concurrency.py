"""PDF 파싱 자리가 없어도 다른 sync 요청은 공용 스레드풀을 쓸 수 있어야 한다."""

import asyncio
import threading
import uuid
from collections.abc import Callable
from typing import BinaryIO
from unittest.mock import MagicMock

import anyio
import httpx
import pytest
from fastapi import FastAPI

from pitch_coach_backend.core.database import get_db
from pitch_coach_backend.core.exceptions import register_exception_handlers
from pitch_coach_backend.main import health
from pitch_coach_backend.module.pitch.adapter import presentation_file
from pitch_coach_backend.module.pitch.controller import router
from pitch_coach_backend.module.pitch.dependencies import get_owned_pitch
from pitch_coach_backend.module.pitch.service import presentation as presentation_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_busy_uploads_leave_shared_threads_for_other_sync_requests(
    monkeypatch: pytest.MonkeyPatch, make_pdf: Callable[[int], bytes]
) -> None:
    # 파싱 중인 두 요청이 슬롯을 모두 쥔 상태다. 테스트 전용 세마포어로 다른 테스트와 격리한다.
    slots = threading.BoundedSemaphore(presentation_file.PAGE_COUNT_SLOTS)
    for _ in range(presentation_file.PAGE_COUNT_SLOTS):
        assert slots.acquire(blocking=False)
    monkeypatch.setattr(presentation_file, "_page_count_slots", slots)
    reader = MagicMock(side_effect=ValueError("파싱 자리가 없으면 PDF 를 열지 않는다"))
    upload = MagicMock()
    monkeypatch.setattr(presentation_file, "PdfReader", reader)
    monkeypatch.setattr(presentation_service, "upload", upload)

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.add_api_route("/api/health", health)
    register_exception_handlers(app)

    # 인증·DB 의 영향을 빼고 실제 sync 업로드 라우트와 PDF 검사 경로를 함께 확인한다.
    async def owned_pitch(pitch_id: uuid.UUID) -> uuid.UUID:
        return pitch_id

    async def no_db() -> None:
        return None

    app.dependency_overrides[get_owned_pitch] = owned_pitch
    app.dependency_overrides[get_db] = no_db

    started = 0
    all_started = anyio.Event()
    thread_count = 2

    def mark_started() -> None:
        nonlocal started
        started += 1
        if started == thread_count:
            all_started.set()

    def count_pages(file: BinaryIO) -> int | None:
        # 업로드 두 개가 모두 sync 라우트에 진입한 뒤에 헬스체크를 보낸다.
        anyio.from_thread.run_sync(mark_started)
        return presentation_file.count_pdf_pages(file)

    monkeypatch.setattr(presentation_service, "count_pdf_pages", count_pages)
    limiter = anyio.to_thread.current_default_thread_limiter()
    original_tokens = limiter.total_tokens
    # 기본 40칸 대신 2칸으로 같은 고갈 상황을 재현한다.
    limiter.total_tokens = thread_count
    tasks: list[asyncio.Task[httpx.Response]] = []

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://testserver"
        ) as client:
            try:
                pdf = make_pdf(1)
                tasks = [
                    asyncio.create_task(
                        client.post(
                            f"/api/pitches/add/{uuid.uuid4()}/presentation",
                            files={"presentation_file": ("deck.pdf", pdf, "application/pdf")},
                        )
                    )
                    for _ in range(thread_count)
                ]
                with anyio.fail_after(5):
                    await all_started.wait()
                probe = asyncio.create_task(client.get("/api/health"))
                tasks.append(probe)

                # 파싱 슬롯을 비우기 전에도 업로드는 503, 다른 sync 요청은 200 이어야 한다.
                done, pending = await asyncio.wait(tasks, timeout=1)
                assert not pending, "PDF 슬롯 대기가 공용 스레드풀을 점유했다"
                assert len(done) == thread_count + 1
                for task in tasks[:-1]:
                    response = task.result()
                    assert response.status_code == 503
                    assert response.json()["code"] == "PRESENTATION_BUSY"
                assert probe.result().status_code == 200
                assert probe.result().json() == {"status": "ok"}
                reader.assert_not_called()
                upload.assert_not_called()
            finally:
                # 회귀가 생겨 acquire 에서 기다려도 스레드를 남기지 않고 정리한다.
                for _ in range(presentation_file.PAGE_COUNT_SLOTS):
                    slots.release()
                await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        limiter.total_tokens = original_tokens
