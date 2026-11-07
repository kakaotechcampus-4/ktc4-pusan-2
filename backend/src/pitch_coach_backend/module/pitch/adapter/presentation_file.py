"""발표자료 PDF 검사. 쪽수를 세면서 PDF 인지도 같이 확인한다.

쪽수는 대본과 짝을 맞추는 데 쓴다 (대본 장 수 = PDF 쪽수). FE 매핑 확인과 같은 규칙이다.
"""

import logging
import subprocess
import sys
import threading
from typing import BinaryIO

from pypdf import PdfReader
from pypdf.generic import IndirectObject

from pitch_coach_backend.module.pitch.exception import PresentationBusy, PresentationTooComplex

logger = logging.getLogger(__name__)

# FE 의 업로드 제한(MAX_SLIDE_BYTES)과 같다
MAX_PRESENTATION_BYTES = 40 * 1024 * 1024

MAX_PDF_OBJECTS = 50_000

MAX_PDF_PAGES = 100

PAGE_COUNT_SLOTS = 2

_page_count_slots = threading.BoundedSemaphore(PAGE_COUNT_SLOTS)

THUMBNAIL_WIDTH = 480
THUMBNAIL_HEIGHT = 270

# 첫 장은 업로드마다 프로세스를 띄워 그린다. 쪽수 세기처럼 동시에 띄우는 수를 제한한다
THUMBNAIL_SLOTS = 2

_thumbnail_slots = threading.BoundedSemaphore(THUMBNAIL_SLOTS)

# 프로세스를 띄우는 시간까지 포함한다. 보통 PDF 는 1초 안에 끝난다
THUMBNAIL_TIMEOUT_SECONDS = 5.0

# -P: 작업 폴더를 import 경로에 넣지 않는다. 패키지는 설치 경로나 PYTHONPATH 에서 찾는다
_THUMBNAIL_COMMAND = [
    sys.executable,
    "-P",
    "-m",
    "pitch_coach_backend.module.pitch.adapter.pdf_thumbnail",
]

# 망가진 파일을 읽을 때마다 경고를 남긴다. 그런 파일은 400 으로 거절하므로 로그에 둘 필요가 없다
logging.getLogger("pypdf").setLevel(logging.ERROR)


def file_size(file: BinaryIO) -> int:
    file.seek(0, 2)
    size = file.tell()
    file.seek(0)
    return size


def _object_count(reader: PdfReader) -> int:
    return sum(len(entries) for entries in reader.xref.values()) + len(reader.xref_objStm)


def _count_pages(reader: PdfReader) -> int:
    count = 0
    seen: set[int] = set()
    stack = [reader.trailer["/Root"].get_object()["/Pages"]]
    while stack:
        ref = stack.pop()
        node = ref.get_object()
        node_type = node.get("/Type")
        if node_type == "/Pages" or "/Kids" in node:
            key = ref.idnum if isinstance(ref, IndirectObject) else id(node)
            if key in seen:
                raise ValueError("page tree cycle")
            seen.add(key)
            stack.extend(node["/Kids"])
        elif node_type in ("/Page", None):
            count += 1
            if count > MAX_PDF_PAGES:
                raise PresentationTooComplex()
        else:
            raise ValueError("not a page")
    return count


def count_pdf_pages(file: BinaryIO) -> int | None:
    """PDF 쪽수. 열리지 않거나(PDF 가 아님 · 깨짐 · 여는 암호) 쪽이 없으면 None.

    쓰기 제한만 걸린 PDF 는 빈 암호로 열린다. 다 읽은 뒤 파일 위치를 처음으로 돌려 둔다.
    """
    # 슬롯을 기다리며 공용 스레드풀을 차지하지 않도록, 빈자리가 없으면 바로 거절한다.
    if not _page_count_slots.acquire(blocking=False):
        raise PresentationBusy()
    try:
        reader = PdfReader(file)
        if _object_count(reader) > MAX_PDF_OBJECTS:
            raise PresentationTooComplex()
        pages = _count_pages(reader)
    except PresentationTooComplex:
        raise
    except Exception as e:
        # pypdf 는 망가진 파일에서 PdfReadError 말고도 ValueError · KeyError 등을 던진다.
        # "PDF 가 안 올라가요" 를 따라갈 수 있게 종류만 남긴다 (파일 이름 · 내용은 남기지 않는다)
        logger.info("발표자료를 PDF 로 열지 못했다: %s", type(e).__name__)
        pages = 0
    finally:
        file.seek(0)
        _page_count_slots.release()
    return pages or None


def render_first_page(file: BinaryIO) -> bytes | None:
    """첫 장 썸네일(WEBP). 그리지 못했거나 시간 상한을 넘기면 None 이고 업로드는 썸네일 없이 된다.

    pdfium 은 그리는 도중에 끊을 수 없어서 별도 프로세스에서 그리고, 상한을 넘기면 프로세스를
    끝낸다. 다 쓴 뒤 파일 위치를 처음으로 돌려 둔다.
    """
    # 쪽수 세기와 같이 빈자리를 기다리지 않는다. 썸네일만 빠지고 업로드는 이어 간다
    if not _thumbnail_slots.acquire(blocking=False):
        logger.info("썸네일 슬롯이 차서 첫 장 그리기를 건너뛴다")
        return None
    try:
        file.seek(0)
        done = subprocess.run(
            [*_THUMBNAIL_COMMAND, str(THUMBNAIL_WIDTH), str(THUMBNAIL_HEIGHT)],
            input=file.read(),
            capture_output=True,
            timeout=THUMBNAIL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        # run 이 프로세스를 끝내고 거둔 뒤 던진다
        logger.info("발표자료 첫 장 그리기가 %s초를 넘어 멈췄다", THUMBNAIL_TIMEOUT_SECONDS)
        return None
    except Exception as e:
        # 썸네일 때문에 업로드가 실패하지 않게 한다
        logger.warning("썸네일 프로세스를 띄우지 못했다: %s", type(e).__name__)
        return None
    finally:
        file.seek(0)
        _thumbnail_slots.release()

    if done.returncode != 0 or not done.stdout:
        # 자식은 예외 종류만 낸다. 그 전에 죽었으면 끝 상태 코드를 남긴다
        reason = done.stderr.decode(errors="replace").strip()[-200:]
        logger.info("발표자료 첫 장을 그리지 못했다: %s", reason or f"exit {done.returncode}")
        return None
    return done.stdout
