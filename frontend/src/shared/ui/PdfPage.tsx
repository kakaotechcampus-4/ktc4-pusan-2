import { useEffect, useRef, useState } from 'react';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import { renderPage } from '@/shared/lib/pdf';

/**
 * PDF 한 페이지. 상자 크기를 재서 거기 맞춰 그리고, 상자가 바뀌면 다시 그립니다.
 *
 * `fit="width"` 는 폭에만 맞춥니다 — 썸네일, 그리고 뷰어의 "폭 맞춤".
 * `fit="contain"` 은 상자 안에 통째로 들어가게 합니다 — 본문 뷰어.
 * `zoom` 은 그 위에 곱합니다. 상자보다 커지면 상자 안에서 스크롤됩니다.
 *
 * ★ 상자는 **바깥이 정한 크기**여야 합니다. 그린 canvas 가 상자를 키우면
 *   상자가 커져서 다시 크게 그리는 고리가 생깁니다 — 그래서 상자에 overflow 를 두고,
 *   가운데 정렬은 canvas 의 `m-auto` 로 합니다 (넘칠 때 왼쪽이 잘리지 않습니다).
 */
export function PdfPage({
  doc,
  pageNumber,
  fit,
  zoom = 1,
  className = '',
}: {
  doc: PDFDocumentProxy;
  pageNumber: number;
  fit: 'width' | 'contain';
  zoom?: number;
  className?: string;
}) {
  const boxRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [box, setBox] = useState({ width: 0, height: 0 });
  // 앞 그리기가 다 멈춘 뒤에 다음을 시작합니다 — 한 canvas 에 두 그리기가 겹치면 pdf.js 가 오류를 냅니다
  const queue = useRef<Promise<void>>(Promise.resolve());

  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => {
      if (!entry) return;
      const next = {
        width: Math.floor(entry.contentRect.width),
        height: fit === 'contain' ? Math.floor(entry.contentRect.height) : 0,
      };
      // 크기가 그대로면 다시 그리지 않습니다 — 그릴 때마다 새 객체가 들어가 무한히 돕니다
      setBox((prev) => (prev.width === next.width && prev.height === next.height ? prev : next));
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, [fit]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || box.width === 0) return;
    const ac = new AbortController();
    const target = { width: box.width * zoom, height: box.height * zoom };
    queue.current = queue.current.then(() =>
      ac.signal.aborted ? undefined : renderPage(doc, pageNumber, canvas, target, ac.signal),
    );
    return () => ac.abort();
  }, [doc, pageNumber, box, zoom]);

  return (
    <div ref={boxRef} className={`flex ${className}`}>
      <canvas ref={canvasRef} className="m-auto block shrink-0 bg-white shadow-sm" />
    </div>
  );
}
