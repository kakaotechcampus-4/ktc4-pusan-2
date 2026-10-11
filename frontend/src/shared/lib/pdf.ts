import { getDocument, GlobalWorkerOptions, type PDFDocumentProxy } from 'pdfjs-dist';
import workerSrc from 'pdfjs-dist/build/pdf.worker.min.mjs?url';

/**
 * PDF 를 URL 로 열고 페이지를 canvas 에 그립니다. pdf.js 를 직접 부르는 곳은 여기뿐입니다.
 *
 * ── 왜 서버 변환이 아니라 브라우저인가 ──────────────────────────────
 * 서버는 파일을 S3 에 두고 presigned URL 만 줍니다 (페이지 이미지 · 장수 없음).
 * URL 하나로 장수와 화면이 다 나오므로 BE 에 변환을 따로 요청하지 않습니다.
 *
 * ★ 실서버에서는 S3 버킷에 CORS(GET, 프론트 origin)가 열려 있어야 합니다.
 *   pdf.js 는 `<img>` 처럼 그냥 띄우는 게 아니라 fetch 로 바이트를 읽기 때문입니다.
 */

GlobalWorkerOptions.workerSrc = workerSrc;

/**
 * 같은 URL 은 한 번만 엽니다. 썸네일 열두 장과 본문이 각자 열면 같은 파일을 열세 번 받습니다.
 *
 * 열고 나면 문서가 메모리에 있으므로, 그 뒤에 presigned URL 이 만료돼도 화면은 깨지지 않습니다.
 */
const opened = new Map<string, Promise<PDFDocumentProxy>>();

export function openPdf(url: string): Promise<PDFDocumentProxy> {
  const cached = opened.get(url);
  if (cached) return cached;

  const loading = getDocument({ url }).promise;
  // 실패한 것은 기억하지 않습니다 — "다시 시도"가 같은 실패를 돌려받으면 안 됩니다
  loading.catch(() => opened.delete(url));
  opened.set(url, loading);
  return loading;
}

/**
 * 페이지 하나를 상자 크기에 맞춰 그립니다.
 *
 * `height` 가 0 이면 폭에만 맞춥니다(썸네일). 둘 다 있으면 비율을 지키며 안에 들어가게 합니다(본문).
 * 화면 밀도(devicePixelRatio)만큼 크게 그려야 레티나에서 글자가 흐려지지 않습니다.
 *
 * 돌려주는 함수로 그리기를 멈춥니다. 페이지를 빠르게 넘기면 앞의 그리기가 끝나기 전에
 * 다음 것이 시작되는데, pdf.js 는 **한 canvas 에 두 그리기가 겹치면** 오류를 냅니다.
 */
export async function renderPage(
  doc: PDFDocumentProxy,
  pageNumber: number,
  canvas: HTMLCanvasElement,
  box: { width: number; height: number },
  signal: AbortSignal,
): Promise<void> {
  const page = await doc.getPage(pageNumber);
  if (signal.aborted) return;

  const base = page.getViewport({ scale: 1 });
  const fit =
    box.height > 0
      ? Math.min(box.width / base.width, box.height / base.height)
      : box.width / base.width;
  const dpr = window.devicePixelRatio || 1;
  const viewport = page.getViewport({ scale: fit * dpr });

  canvas.width = Math.floor(viewport.width);
  canvas.height = Math.floor(viewport.height);
  canvas.style.width = `${Math.floor(viewport.width / dpr)}px`;
  canvas.style.height = `${Math.floor(viewport.height / dpr)}px`;

  const task = page.render({ canvas, viewport });
  signal.addEventListener('abort', () => task.cancel(), { once: true });
  // 취소는 실패로 돌아옵니다 — 페이지를 넘긴 것뿐이라 알릴 일이 아닙니다
  await task.promise.catch(() => undefined);
}
