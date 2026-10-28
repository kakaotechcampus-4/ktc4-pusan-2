"""발표자료 PDF 검사. 쪽수를 세면서 PDF 인지도 같이 확인한다.

쪽수는 대본과 짝을 맞추는 데 쓴다 (대본 장 수 = PDF 쪽수). FE 매핑 확인과 같은 규칙이다.
"""

import logging
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
