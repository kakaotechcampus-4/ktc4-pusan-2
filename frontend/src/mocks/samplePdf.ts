/**
 * 목에서 쓰는 PDF 를 그 자리에서 만듭니다. 장마다 "Slide n" 한 줄입니다.
 *
 * 목에는 S3 가 없어 업로드 목은 받은 파일을 blob URL 로 돌려줍니다. 수정 화면 목은 올린 파일이
 * 없으므로 이걸로 만든 PDF 를 줍니다 — pdf.js 가 실제로 열어 장수를 세는 길을 그대로 탑니다.
 */
export function samplePdfUrl(pageCount: number, label: string): string {
  const objects: string[] = [];
  const pageIds = Array.from({ length: pageCount }, (_, i) => 3 + i * 2);
  const fontId = 3 + pageCount * 2;

  objects.push('<< /Type /Catalog /Pages 2 0 R >>');
  objects.push(
    `<< /Type /Pages /Kids [${pageIds.map((id) => `${id} 0 R`).join(' ')}] /Count ${pageCount} >>`,
  );
  for (let i = 0; i < pageCount; i++) {
    const text = `BT /F1 48 Tf 72 300 Td (${label} - Slide ${i + 1}) Tj ET`;
    objects.push(
      `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 960 540] /Contents ${pageIds[i]! + 1} 0 R /Resources << /Font << /F1 ${fontId} 0 R >> >> >>`,
    );
    objects.push(`<< /Length ${text.length} >>\nstream\n${text}\nendstream`);
  }
  objects.push('<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>');

  let body = '%PDF-1.4\n';
  const offsets: number[] = [];
  objects.forEach((obj, i) => {
    offsets.push(body.length);
    body += `${i + 1} 0 obj\n${obj}\nendobj\n`;
  });
  const xrefAt = body.length;
  body += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  body += offsets.map((o) => `${String(o).padStart(10, '0')} 00000 n \n`).join('');
  body += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xrefAt}\n%%EOF`;

  return URL.createObjectURL(new Blob([body], { type: 'application/pdf' }));
}
