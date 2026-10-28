"""발표자료 쪽수 세기(`count_pdf_pages`)의 쪽수 상한 · 페이지 트리 검사.

쪽수는 페이지 트리를 직접 따라가며 센다. 상한을 넘거나 트리가 자기 자신 · 조상으로 돌아가면 끝까지
따라가지 않고 멈춘다. 어느 경로로 끝나든 파일 위치는 처음으로 돌아가고 파싱 슬롯은 반환된다.
"""

import io
from collections.abc import Callable

import pytest

from pitch_coach_backend.module.pitch.adapter import presentation_file
from pitch_coach_backend.module.pitch.adapter.presentation_file import (
    MAX_PDF_PAGES,
    PAGE_COUNT_SLOTS,
    count_pdf_pages,
)
from pitch_coach_backend.module.pitch.exception import PresentationTooComplex

_PAGE = b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 200 200] >>"


def _raw_pdf(objects: dict[int, bytes]) -> bytes:
    """객체 번호 → 본문으로 PDF 를 직접 쓴다. 1번이 Catalog 이고 2번이 페이지 트리 뿌리다.

    `PdfWriter` 는 쓰면서 페이지 트리를 따라가므로 순환 트리를 만들 수 없다. 순환 트리는 쪽을
    자식 목록 끝에 둔다 — 순환 검사가 빠지면 같은 쪽을 거듭 세다 쪽수 상한에 걸려 멈추고 테스트가
    실패한다. 앞에 두면 같은 /Pages 만 계속 쌓여 테스트가 끝나지 않는다.
    """
    out = io.BytesIO()
    out.write(b"%PDF-1.7\n")
    offsets: dict[int, int] = {}
    for number in sorted(objects):
        offsets[number] = out.tell()
        out.write(b"%d 0 obj\n%s\nendobj\n" % (number, objects[number]))
    size = max(objects) + 1
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % size)
    for number in range(1, size):
        if number in offsets:
            out.write(b"%010d 00000 n \n" % offsets[number])
        else:
            out.write(b"0000000000 65535 f \n")
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (size, xref))
    return out.getvalue()


def _nested_tree() -> bytes:
    """뿌리 아래 /Pages 가 한 단 더 있는 정상 트리. 쪽은 셋이다 (2 → [3 → [4, 5], 6])."""
    return _raw_pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [3 0 R 6 0 R] /Count 3 >>",
            3: b"<< /Type /Pages /Parent 2 0 R /Kids [4 0 R 5 0 R] /Count 2 >>",
            4: _PAGE % 3,
            5: _PAGE % 3,
            6: _PAGE % 2,
        }
    )


def _self_cycle() -> bytes:
    """뿌리가 자기 자신을 자식으로 둔다."""
    return _raw_pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [2 0 R 3 0 R] /Count 2 >>",
            3: _PAGE % 2,
        }
    )


def _ancestor_cycle() -> bytes:
    """자식 /Pages 가 뿌리로 돌아간다 (2 → 3 → 2)."""
    return _raw_pdf(
        {
            1: b"<< /Type /Catalog /Pages 2 0 R >>",
            2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            3: b"<< /Type /Pages /Parent 2 0 R /Kids [2 0 R 4 0 R] /Count 1 >>",
            4: _PAGE % 3,
        }
    )


def _count(data: bytes) -> tuple[int | None, int]:
    """쪽수와 다 센 뒤의 파일 위치. 끝에서 시작해 위치를 되돌리는지 본다."""
    file = io.BytesIO(data)
    file.seek(0, io.SEEK_END)
    pages = count_pdf_pages(file)
    return pages, file.tell()


def _assert_slots_free() -> None:
    """파싱 슬롯이 전부 반환됐다. 하나라도 새면 다음 업로드가 `PresentationBusy` 를 받는다."""
    slots = presentation_file._page_count_slots
    taken = 0
    try:
        for _ in range(PAGE_COUNT_SLOTS):
            assert slots.acquire(blocking=False), "파싱 슬롯이 반환되지 않았다"
            taken += 1
    finally:
        for _ in range(taken):
            slots.release()


@pytest.mark.parametrize("pages", [1, MAX_PDF_PAGES])
def test_pages_up_to_the_limit_are_counted(make_pdf: Callable[[int], bytes], pages: int) -> None:
    assert _count(make_pdf(pages)) == (pages, 0)
    _assert_slots_free()


def test_pdf_over_the_page_limit_is_too_complex(make_pdf: Callable[[int], bytes]) -> None:
    file = io.BytesIO(make_pdf(MAX_PDF_PAGES + 1))
    file.seek(0, io.SEEK_END)

    with pytest.raises(PresentationTooComplex):
        count_pdf_pages(file)

    assert file.tell() == 0
    _assert_slots_free()


def test_nested_page_tree_counts_every_leaf() -> None:
    assert _count(_nested_tree()) == (3, 0)


@pytest.mark.parametrize("data", [_self_cycle(), _ancestor_cycle()], ids=["self", "ancestor"])
def test_cyclic_page_tree_is_not_a_pdf(data: bytes) -> None:
    assert _count(data) == (None, 0)
    _assert_slots_free()


def test_rejected_files_do_not_keep_slots_for_the_next_upload(
    make_pdf: Callable[[int], bytes],
) -> None:
    # 슬롯 수보다 많이 거절해도 다음 정상 파일을 처리한다. 하나라도 새면 여기서 PresentationBusy 다
    for _ in range(PAGE_COUNT_SLOTS + 1):
        with pytest.raises(PresentationTooComplex):
            count_pdf_pages(io.BytesIO(make_pdf(MAX_PDF_PAGES + 1)))
        assert count_pdf_pages(io.BytesIO(_self_cycle())) is None
        assert count_pdf_pages(io.BytesIO(b"not a pdf")) is None

    assert _count(make_pdf(2)) == (2, 0)
    _assert_slots_free()
