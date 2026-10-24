import { useEffect, useState } from 'react';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import { openPdf } from '@/shared/lib/pdf';

export type OpenedPdf =
  | { url: string; doc: PDFDocumentProxy }
  /** `retry` 는 같은 URL 로 다시 엽니다 */
  | { url: string; failed: true; retry: () => void };

/**
 * URL 로 연 PDF. 여는 중이면 null.
 *
 * 같은 URL 은 `openPdf` 가 한 번만 받습니다 — 뷰어와 매핑 확인이 같은 슬라이드를 열어도
 * 파일은 한 번만 내려옵니다.
 *
 * 못 열었으면 `retry` 로 다시 엽니다. URL 이 그대로면 이 훅은 스스로 다시 열지 않고,
 * `openPdf` 는 실패를 기억하지 않아 다시 부르면 새로 받습니다.
 */
export function usePdfDocument(url: string | undefined): OpenedPdf | null {
  const [opened, setOpened] = useState<OpenedPdf | null>(null);
  /** 다시 열기를 누를 때마다 늘려 아래 effect 를 다시 돌립니다 */
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    if (!url) return;
    let live = true;
    const retry = () => {
      setOpened(null);
      setAttempt((n) => n + 1);
    };
    openPdf(url).then(
      (doc) => live && setOpened({ url, doc }),
      () => live && setOpened({ url, failed: true, retry }),
    );
    return () => {
      live = false;
    };
  }, [url, attempt]);

  // 버전을 바꾼 직후에는 앞 버전의 문서가 남아 있습니다 — 그것을 새 버전처럼 보이면 안 됩니다
  return opened?.url === url ? opened : null;
}
