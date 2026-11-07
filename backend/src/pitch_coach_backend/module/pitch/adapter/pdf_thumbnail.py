"""발표자료 첫 장 썸네일 그리기. 업로드 요청이 별도 프로세스로 띄워 쓴다 (presentation_file).

pdfium 은 C 라이브러리라 스레드에서는 그리는 도중에 끊을 수 없다. 프로세스로 띄우면 시간을 넘길 때
프로세스째 끝낼 수 있다. 띄울 때마다 import 하므로 pdfium · Pillow 말고는 불러오지 않는다.

표준 입력으로 PDF 를 받아 표준 출력으로 WEBP 를 낸다. 인자는 썸네일 가로 · 세로(px) 다.
"""

import io
import sys

import pypdfium2 as pdfium


def render_webp(data: bytes, width: int, height: int) -> bytes:
    pdf = pdfium.PdfDocument(data)
    try:
        page = pdf[0]
        scale = min(width / page.get_width(), height / page.get_height())
        image = page.render(scale=scale).to_pil()
    finally:
        pdf.close()
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="WEBP", quality=80)
    return buffer.getvalue()


def main() -> None:
    width, height = int(sys.argv[1]), int(sys.argv[2])
    try:
        webp = render_webp(sys.stdin.buffer.read(), width, height)
    except Exception as e:
        # 부른 쪽이 로그에 남긴다. 파일 내용이 섞이지 않게 종류만 낸다
        sys.stderr.write(type(e).__name__)
        sys.exit(1)
    sys.stdout.buffer.write(webp)


if __name__ == "__main__":
    main()
