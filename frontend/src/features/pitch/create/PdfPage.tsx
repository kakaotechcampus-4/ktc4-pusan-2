import { useEffect, useRef, useState } from 'react';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import { renderPage } from '@/shared/lib/pdf';

/**
 * PDF 한 페이지. 상자 크기를 재서 거기 맞춰 그리고, 상자가 바뀌면 다시 그립니다.
 *
 * `fit="width"` 는 폭에만 맞춥니다 — 썸네일처럼 높이가 내용을 따라가는 곳.
 * `fit="contain"` 은 상자 안에 통째로 들어가게 합니다 — 본문 뷰어.
 */
export function PdfPage({
  doc,
  pageNumber,
  fit,
  className = '',
}: {
  doc: PDFDocumentProxy;
  pageNumber: number;
  fit: 'width' | 'contain';
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
    queue.current = queue.current.then(() =>
      ac.signal.aborted ? undefined : renderPage(doc, pageNumber, canvas, box, ac.signal),
    );
    return () => ac.abort();
  }, [doc, pageNumber, box]);

  return (
    <div ref={boxRef} className={`flex items-center justify-center ${className}`}>
      <canvas ref={canvasRef} className="block bg-white shadow-sm" />
    </div>
  );
}
