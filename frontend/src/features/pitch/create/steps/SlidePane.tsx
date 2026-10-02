import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import { openPdf } from '@/shared/lib/pdf';
import { useCreateStore } from '../createStore';
import { FileIcon, PdfUploadIcon, SparkleIcon } from '../icons';
import { MAX_SLIDE_BYTES, type SlideUpload, type SlideVersion } from '../lib/draft';
import { PdfPage } from '../PdfPage';
import { uploadSlides } from '../slideUpload';

/**
 * 목업 04(업로드)와 05(뷰어)는 **같은 화면의 두 상태**입니다.
 * 올린 것이 없으면 드롭존 → 올리는 중 → 썸네일 + 뷰어.
 *
 * 뷰어는 서버가 돌려준 URL 로 PDF 를 열어 그립니다 (`shared/lib/pdf`).
 * 고른 파일을 바로 그리지 않는 이유는 `slideUpload.ts` 에 있습니다.
 */

/** 드롭존과 "올리는 중"이 같은 틀을 씁니다 — 올리는 동안 화면이 뛰지 않게 */
function UploadFrame({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-1 flex-col">
      <h2 className="text-2xl font-bold">슬라이드 PDF를 올려주세요</h2>
      <p className="mt-2 text-sm text-stone">발표에 사용할 자료부터 준비해 볼까요?</p>
      {children}
      <p className="mt-5 flex items-center gap-2 border-t border-line pt-4 text-xs text-stone">
        <SparkleIcon />
        슬라이드를 올리면 대본을 연결할 수 있어요.
      </p>
    </div>
  );
}

const FAILURE_MESSAGE: Record<'upload' | 'open', string> = {
  upload: '업로드하지 못했어요. 네트워크를 확인하고 다시 시도해 주세요.',
  open: '올린 PDF를 열지 못했어요. 파일이 손상됐을 수 있어요.',
};

function DropZone({ upload }: { upload: SlideUpload }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const accept = (file: File | undefined) => {
    if (!file) return;
    if (file.type !== 'application/pdf') {
      setError('PDF 파일만 올릴 수 있어요');
      return;
    }
    if (file.size > MAX_SLIDE_BYTES) {
      setError('40MB를 넘는 파일은 올릴 수 없어요');
      return;
    }
    setError(null);
    void uploadSlides(file);
  };

  const failed = upload.status === 'failed' ? upload : null;

  return (
    <UploadFrame>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          accept(e.dataTransfer.files[0]);
        }}
        className={[
          'mt-5 flex flex-1 flex-col items-center justify-center rounded border-2 border-dashed px-6 py-10 text-center',
          over ? 'border-coral bg-coral-wash' : 'border-line-strong',
        ].join(' ')}
      >
        <PdfUploadIcon />
        <p className="mt-5 text-lg font-bold">PDF를 여기에 끌어다 놓으세요</p>
        <p className="mt-1.5 text-sm text-stone">또는 파일을 선택해 주세요 · 최대 40MB</p>

        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          className="mt-6 inline-flex items-center gap-2.5 rounded bg-coral px-12 py-3 text-sm font-bold text-white hover:bg-coral-deep"
        >
          <FileIcon />
          PDF 파일 선택
        </button>

        {error && (
          <p role="alert" className="mt-3 text-xs font-bold text-coral">
            {error}
          </p>
        )}

        {/* 실패한 파일은 들고 있습니다 — 다시 고르게 하지 않습니다 */}
        {failed && !error && (
          <div role="alert" className="mt-4 flex flex-col items-center gap-2">
            <p className="text-xs font-bold text-coral">
              {failed.file.name} · {FAILURE_MESSAGE[failed.reason]}
            </p>
            <button
              type="button"
              onClick={() => void uploadSlides(failed.file)}
              className="rounded border border-coral px-4 py-1.5 text-xs font-bold text-coral-deep hover:bg-coral-wash"
            >
              다시 시도
            </button>
          </div>
        )}

        <input
          ref={inputRef}
          type="file"
          accept="application/pdf"
          hidden
          onChange={(e) => {
            accept(e.target.files?.[0]);
            // 같은 파일을 다시 골라도 onChange 가 오게 비웁니다 — 실패 뒤에 흔히 그럽니다
            e.target.value = '';
          }}
        />
      </div>
    </UploadFrame>
  );
}

function Uploading({ file }: { file: File }) {
  return (
    <UploadFrame>
      <div
        role="status"
        className="mt-5 flex flex-1 flex-col items-center justify-center rounded border-2 border-dashed border-coral bg-coral-wash/40 px-6 py-10 text-center"
      >
        <span className="animate-bounce">
          <PdfUploadIcon />
        </span>
        <p className="mt-5 text-lg font-bold">올리는 중…</p>
        <p className="mt-1.5 max-w-full truncate text-sm text-stone">{file.name}</p>
        <p className="mt-4 text-xs text-stone">
          다른 자료를 먼저 준비해도 괜찮아요. 업로드는 계속돼요.
        </p>
      </div>
    </UploadFrame>
  );
}

type Opened = { url: string; doc: PDFDocumentProxy } | { url: string; failed: true };

/** URL 로 연 문서. 여는 중이면 null */
function useOpenedPdf(url: string | undefined): Opened | null {
  const [opened, setOpened] = useState<Opened | null>(null);

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

function ViewerMessage({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-1 items-center justify-center rounded border-2 border-ink bg-panel text-sm text-stone">
      {children}
    </div>
  );
}

function Viewer({ slide }: { slide: SlideVersion }) {
  const [page, setPage] = useState(1);
  const opened = useOpenedPdf(slide.fileUrl);
  const total = slide.pageCount ?? 0;

  const body = (() => {
    if (!slide.fileUrl) return <ViewerMessage>이 버전에는 파일 주소가 없어요</ViewerMessage>;
    if (opened === null) return <ViewerMessage>PDF 여는 중…</ViewerMessage>;
    // presigned URL 은 1시간 뒤 만료됩니다. 한 번 연 문서는 메모리에 있어 여기까지 오지 않습니다
    if ('failed' in opened) return <ViewerMessage>PDF를 열지 못했어요</ViewerMessage>;

    const { doc } = opened;
    return (
      <div className="flex min-h-0 flex-1 gap-0 rounded border-2 border-ink bg-panel">
        <ul className="w-36 shrink-0 overflow-y-auto border-r border-line p-3">
          {Array.from({ length: total }, (_, i) => i + 1).map((n) => (
            <li key={n} className="mb-3">
              <button
                type="button"
                onClick={() => setPage(n)}
                aria-current={page === n ? 'true' : undefined}
                aria-label={`${n}번 슬라이드`}
                className={[
                  'block w-full rounded border p-1',
                  page === n ? 'border-2 border-ink' : 'border-line hover:border-line-strong',
                ].join(' ')}
              >
                <PdfPage doc={doc} pageNumber={n} fit="width" />
                <span className="tabular mt-1 block font-mono text-[10px] text-stone">
                  {String(n).padStart(2, '0')}
                </span>
              </button>
            </li>
          ))}
        </ul>

        <div className="relative flex min-h-0 min-w-0 flex-1 p-6">
          <span className="tabular absolute right-4 top-4 z-10 rounded border border-line bg-panel px-2 py-1 font-mono text-xs">
            {page} / {total}
          </span>
          <PdfPage doc={doc} pageNumber={page} fit="contain" className="h-full w-full" />
        </div>
      </div>
    );
  })();

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="text-lg font-bold">
          슬라이드 V{slide.version}
          {total > 0 && <span className="text-stone"> · {total}장</span>}
        </h2>
        <span className="rounded border border-line-strong px-4 py-2 text-xs font-bold text-stone">
          V{slide.version} 저장됨
        </span>
      </div>
      {body}
    </div>
  );
}

export function SlidePane({ slide }: { slide: SlideVersion | null }) {
  const upload = useCreateStore((s) => s.slideUpload);

  // 비어 있는 자리(버전 없음 · 파일 기다리는 빈 버전)에서만 업로드 상태를 보입니다.
  // 지난 버전을 열어 보는 중이면 그 버전을 그대로 보여 줍니다
  if (slide === null || slide.pageCount === null) {
    if (upload.status === 'uploading') return <Uploading file={upload.file} />;
    return <DropZone upload={upload} />;
  }

  // 버전마다 첫 장부터 봅니다
  return <Viewer key={slide.version} slide={slide} />;
}
