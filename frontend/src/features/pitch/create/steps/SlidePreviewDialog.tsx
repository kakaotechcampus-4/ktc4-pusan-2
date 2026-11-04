import { useEffect, useId, useRef } from 'react';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import { PdfPage } from '@/shared/ui/PdfPage';

/**
 * 슬라이드 한 장을 크게 보는 창. 슬라이드 단계의 썸네일을 누르면 뜹니다.
 *
 * 썸네일이 이미 연 문서(`doc`)를 그대로 그립니다 — PDF 를 다시 받지 않습니다.
 * 창은 브라우저 `<dialog>` 의 `showModal()` 이라 포커스 가두기 · Esc 닫기를 브라우저가 맡습니다
 * (`HomeHelpDialog` 와 같은 방식). ←/→ 로 넘기고, 창 바깥(배경)을 누르면 닫습니다.
 */
export function SlidePreviewDialog({
  doc,
  page,
  onPage,
  onClose,
}: {
  doc: PDFDocumentProxy;
  page: number;
  onPage: (page: number) => void;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const total = doc.numPages;
  const go = (n: number) => onPage(Math.min(total, Math.max(1, n)));

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    dialogRef.current?.showModal();
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, []);

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      onClose={onClose}
      // 배경을 누르면 dialog 자신이 눌립니다 — 안쪽(내용)을 누른 것과 가립니다
      onClick={(e) => {
        if (e.target === dialogRef.current) dialogRef.current.close();
      }}
      onKeyDown={(e) => {
        if (e.key === 'ArrowLeft') go(page - 1);
        if (e.key === 'ArrowRight') go(page + 1);
      }}
      className="pitch-create fixed inset-0 m-auto h-dvh max-h-dvh w-full max-w-none border-0 bg-panel p-0 text-ink backdrop:bg-ink/40 sm:h-[90dvh] sm:max-h-[90dvh] sm:w-[90vw] sm:max-w-6xl sm:border-2 sm:border-ink"
    >
      <div className="flex h-full flex-col">
        <header className="flex items-center justify-between gap-4 border-b border-line-strong px-5 py-3">
          <h2 id={titleId} className="tabular text-sm font-bold">
            {page} / {total}
          </h2>
          <button
            type="button"
            autoFocus
            onClick={() => dialogRef.current?.close()}
            className="border border-ink px-4 py-1.5 text-sm font-bold hover:bg-cream"
          >
            닫기
          </button>
        </header>

        <div className="flex min-h-0 flex-1 items-stretch gap-2 p-3 sm:p-5">
          <NavButton label="이전 슬라이드" disabled={page <= 1} onClick={() => go(page - 1)}>
            ‹
          </NavButton>
          {/* PdfPage 는 바깥이 정한 크기에 맞춰 그립니다 — 이 칸이 남는 공간을 모두 받습니다 */}
          <PdfPage
            doc={doc}
            pageNumber={page}
            fit="contain"
            className="min-h-0 min-w-0 flex-1 bg-panel/30"
          />
          <NavButton label="다음 슬라이드" disabled={page >= total} onClick={() => go(page + 1)}>
            ›
          </NavButton>
        </div>
      </div>
    </dialog>
  );
}

function NavButton({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string;
  disabled: boolean;
  onClick: () => void;
  children: string;
}) {
  return (
    <button
      type="button"
      aria-label={label}
      disabled={disabled}
      onClick={onClick}
      className="flex w-10 shrink-0 items-center justify-center text-3xl font-bold hover:bg-cream disabled:cursor-not-allowed disabled:text-line-strong disabled:hover:bg-transparent"
    >
      {children}
    </button>
  );
}
