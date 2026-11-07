import { expect, it } from 'vitest';
import { buildImagePdf } from './firstTakePdf';

it('샘플 PDF의 페이지 개수와 객체 참조 오프셋을 일관되게 기록한다', async () => {
  // 페이지 이미지를 실제로 렌더링하지 않고 PDF 컨테이너의 직렬화 구조를 검증합니다.
  const jpeg = new Uint8Array([0xff, 0xd8, 0xff, 0xd9]);
  const pdf = buildImagePdf(Array.from({ length: 5 }, () => jpeg));
  expect(pdf.type).toBe('application/pdf');

  const bytes = new Uint8Array(await pdf.arrayBuffer());
  const decoder = new TextDecoder();
  const contents = decoder.decode(bytes);
  expect(contents.startsWith('%PDF-1.4')).toBe(true);
  expect(contents).toContain('/Type /Pages /Count 5');

  const start = contents.match(/startxref\n(\d+)\n%%EOF/);
  expect(start).not.toBeNull();
  const xrefLines = decoder.decode(bytes.slice(Number(start![1]))).split('\n');
  expect(xrefLines[0]).toBe('xref');
  expect(xrefLines[1]).toBe('0 18');

  for (let id = 1; id <= 17; id += 1) {
    const offset = Number(xrefLines[id + 2]?.slice(0, 10));
    const marker = decoder.decode(bytes.slice(offset, offset + 16));
    expect(marker.startsWith(`${id} 0 obj\n`)).toBe(true);
  }
});
