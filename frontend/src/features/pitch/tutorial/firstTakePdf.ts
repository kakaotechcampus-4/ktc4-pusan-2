import { FIRST_TAKE_SLIDES } from './firstTakeMaterials';

/**
 * #931: 텍스트로 버전 관리하는 샘플을 브라우저에서 PDF로 생성합니다.
 * 바이너리 자산이나 PDF 생성 패키지를 추가하지 않고, 현재 화면에 설치된 한글 폰트를
 * Canvas에 그려 JPEG 페이지 5장을 하나의 PDF로 묶습니다.
 * 결과는 기존 발표자료 업로드 API가 요구하는 일반 PDF File입니다.
 */
const WIDTH = 1280;
const HEIGHT = 720;
const PAGE_WIDTH = 960;
const PAGE_HEIGHT = 540;

function drawSlide(index: number): Uint8Array {
  const slide = FIRST_TAKE_SLIDES[index];
  const canvas = document.createElement('canvas');
  canvas.width = WIDTH;
  canvas.height = HEIGHT;

  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('이 브라우저에서는 샘플 발표자료를 만들 수 없어요.');

  ctx.fillStyle = '#F5F0E9';
  ctx.fillRect(0, 0, WIDTH, HEIGHT);
  ctx.fillStyle = '#EDE6DD';
  ctx.fillRect(36, 36, WIDTH - 72, HEIGHT - 72);

  ctx.fillStyle = '#E8532A';
  ctx.fillRect(80, 78, 10, 42);
  ctx.fillStyle = '#2A2723';
  ctx.font = '700 20px Pretendard, sans-serif';
  ctx.fillText('PITCHCOACH / FIRST TAKE', 110, 108);

  ctx.fillStyle = '#C9421C';
  ctx.font = '700 25px Pretendard, sans-serif';
  ctx.fillText(slide.mission, 82, 190);

  ctx.fillStyle = '#2A2723';
  ctx.font = '700 52px Pretendard, sans-serif';
  ctx.fillText(slide.title, 80, 280, 1120);
  ctx.fillStyle = '#696055';
  ctx.font = '400 27px Pretendard, sans-serif';
  ctx.fillText(slide.subtitle, 82, 336, 1100);

  ctx.fillStyle = '#F5F0E9';
  ctx.fillRect(80, 378, 1120, 206);
  ctx.fillStyle = '#2A2723';
  ctx.font = '500 27px Pretendard, sans-serif';
  slide.points.forEach((point, pointIndex) => {
    ctx.fillStyle = '#E8532A';
    ctx.fillRect(108, 414 + 55 * pointIndex, 10, 10);
    ctx.fillStyle = '#2A2723';
    ctx.fillText(point, 140, 428 + 55 * pointIndex, 1030);
  });

  ctx.fillStyle = '#696055';
  ctx.font = '500 22px Pretendard, sans-serif';
  ctx.fillText(slide.hint, 82, 626, 1090);
  ctx.font = '700 20px Pretendard, sans-serif';
  ctx.fillText(`${index + 1} / ${FIRST_TAKE_SLIDES.length}`, 1108, 626);

  const encoded = canvas.toDataURL('image/jpeg', 0.85).split(',')[1];
  if (!encoded) throw new Error('샘플 발표자료를 이미지로 변환하지 못했어요.');
  return Uint8Array.from(atob(encoded), (value) => value.charCodeAt(0));
}

/** JPEG 이미지로 PDF XObject를 만드는 최소한의 직렬화기. */
export function buildImagePdf(images: readonly Uint8Array[]): Blob {
  if (images.length === 0) throw new Error('PDF에 넣을 슬라이드가 없어요.');

  const encoder = new TextEncoder();
  const parts: Uint8Array[] = [];
  const offsets: number[] = [0];
  let byteLength = 0;
  const append = (value: string | Uint8Array) => {
    const bytes = typeof value === 'string' ? encoder.encode(value) : value;
    parts.push(bytes);
    byteLength += bytes.byteLength;
  };
  const object = (id: number, body: string) => {
    offsets[id] = byteLength;
    append(`${id} 0 obj\n${body}\nendobj\n`);
  };

  append('%PDF-1.4\n% PITCHCOACH TUTORIAL\n');
  object(1, '<< /Type /Catalog /Pages 2 0 R >>');
  const pageIds = images.map((_, index) => 3 + index * 3);
  const totalObjects = images.length * 3 + 2;
  object(
    2,
    `<< /Type /Pages /Count ${images.length} /Kids [${pageIds.map((id) => `${id} 0 R`).join(' ')}] >>`,
  );

  images.forEach((jpeg, index) => {
    const pageId = pageIds[index];
    const imageId = pageId + 1;
    const contentId = pageId + 2;
    object(
      pageId,
      `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${PAGE_WIDTH} ${PAGE_HEIGHT}] /Resources << /XObject << /Im0 ${imageId} 0 R >> >> /Contents ${contentId} 0 R >>`,
    );

    offsets[imageId] = byteLength;
    append(
      `${imageId} 0 obj\n<< /Type /XObject /Subtype /Image /Width ${WIDTH} /Height ${HEIGHT} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${jpeg.byteLength} >>\nstream\n`,
    );
    append(jpeg);
    append('\nendstream\nendobj\n');

    const content = `q\n${PAGE_WIDTH} 0 0 ${PAGE_HEIGHT} 0 0 cm\n/Im0 Do\nQ\n`;
    object(
      contentId,
      `<< /Length ${encoder.encode(content).byteLength} >>\nstream\n${content}endstream`,
    );
  });

  const crossReferenceStart = byteLength;
  append(`xref\n0 ${totalObjects + 1}\n0000000000 65535 f \n`);
  for (let id = 1; id <= totalObjects; id += 1) {
    append(`${String(offsets[id]).padStart(10, '0')} 00000 n \n`);
  }
  append(
    `trailer\n<< /Size ${totalObjects + 1} /Root 1 0 R >>\nstartxref\n${crossReferenceStart}\n%%EOF\n`,
  );

  const result = new Uint8Array(byteLength);
  let cursor = 0;
  for (const part of parts) {
    result.set(part, cursor);
    cursor += part.byteLength;
  }
  return new Blob([result.buffer], { type: 'application/pdf' });
}

export async function createFirstTakePdf(): Promise<File> {
  await document.fonts.load('700 52px Pretendard');
  const pages = FIRST_TAKE_SLIDES.map((_, index) => drawSlide(index));
  const pdf = buildImagePdf(pages);
  return new File([pdf], 'pitchcoach-first-take.pdf', { type: 'application/pdf' });
}
