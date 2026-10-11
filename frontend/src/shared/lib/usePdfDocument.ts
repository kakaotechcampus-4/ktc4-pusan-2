import { useEffect, useState } from 'react';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import { openPdf } from '@/shared/lib/pdf';

export type OpenedPdf = { url: string; doc: PDFDocumentProxy } | { url: string; failed: true };

/**
 * URL 로 연 PDF. 여는 중이면 null.
 *
 * 같은 URL 은 `openPdf` 가 한 번만 받습니다 — 뷰어와 매핑 확인이 같은 슬라이드를 열어도
 * 파일은 한 번만 내려옵니다.
 */
export function usePdfDocument(url: string | undefined): OpenedPdf | null {
  const [opened, setOpened] = useState<OpenedPdf | null>(null);

  useEffect(() => {
    if (!url) return;
    let live = true;
    openPdf(url).then(
      (doc) => live && setOpened({ url, doc }),
      () => live && setOpened({ url, failed: true }),
    );
    return () => {
      live = false;
    };
  }, [url]);

  // 버전을 바꾼 직후에는 앞 버전의 문서가 남아 있습니다 — 그것을 새 버전처럼 보이면 안 됩니다
  return opened?.url === url ? opened : null;
}
