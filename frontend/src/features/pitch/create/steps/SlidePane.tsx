import { useRef, useState, type ReactNode, type RefObject } from 'react';
import type { PDFDocumentProxy } from 'pdfjs-dist';
import { useCreateStore } from '../createStore';
import {
  CheckIcon,
  ChevronIcon,
  DocIcon,
  FileIcon,
  FitWidthIcon,
  FullscreenIcon,
  PaperclipIcon,
  PdfUploadIcon,
} from '../icons';
import { MAX_SLIDE_BYTES, type SlideUpload, type SlideVersion } from '../lib/draft';
import { PdfPage } from '../PdfPage';
import { startSlideUpload } from '../slideUpload';
import { usePdfDocument } from '../usePdfDocument';
import { NeedPitch } from './NeedPitch';
import { PaneHeading } from './PaneHeading';

/**
 * 목업 슬라이드 — 올린 것이 없으면 드롭존 → 올리는 중 → 썸네일 + 뷰어.
 *
 * 뷰어는 서버가 돌려준 URL 로 PDF 를 열어 그립니다 (`shared/lib/pdf`).
 * 고른 파일을 바로 그리지 않는 이유는 `slideUpload.ts` 에 있습니다.
 */

const FAILURE_MESSAGE: Record<'upload' | 'open', string> = {
  upload: '업로드하지 못했어요. 네트워크를 확인하고 다시 시도해 주세요.',
  open: '올린 PDF를 열지 못했어요. 파일이 손상됐을 수 있어요.',
};

/** 받을 수 없는 파일이면 그 이유. 드롭존과 "파일 교체"가 같은 규칙을 씁니다 */
function rejectReason(file: File): string | null {
  if (file.type !== 'application/pdf') return 'PDF 파일만 올릴 수 있어요';
  if (file.size > MAX_SLIDE_BYTES) return '40MB를 넘는 파일은 올릴 수 없어요';
  return null;
}

/** 숨겨 둔 파일 고르기. 같은 파일을 다시 골라도 onChange 가 오게 매번 비웁니다 */
function PdfInput({
  inputRef,
  onPick,
}: {
  inputRef: RefObject<HTMLInputElement>;
  onPick: (file: File | undefined) => void;
}) {
  return (
    <input
      ref={inputRef}
      type="file"
      accept="application/pdf"
      hidden
      onChange={(e) => {
        onPick(e.target.files?.[0]);
        e.target.value = '';
      }}
    />
  );
}

/* ------------------------------------------------------------------ */
/* 빈 자리 — 드롭존 · 올리는 중                                          */
/* ------------------------------------------------------------------ */

function UploadFrame({ version, children }: { version: number | null; children: ReactNode }) {
  return (
    <div className="flex flex-1 flex-col">
      <PaneHeading
        title={version === null ? '슬라이드' : `슬라이드 V${version}`}
        subtitle="발표에 사용할 PDF를 올려 주세요."
      />
      {children}
    </div>
  );
}

function DropZone({ upload, version }: { upload: SlideUpload; version: number | null }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const accept = (file: File | undefined) => {
    if (!file) return;
    const reason = rejectReason(file);
    setError(reason);
    if (reason === null) startSlideUpload(file);
  };

  // "파일 교체" 의 실패는 뷰어가 보여 줍니다. 여기서는 빈 자리에 올리다 실패한 것만
  const failed = upload.status === 'failed' && upload.replace === null ? upload : null;

  return (
    <UploadFrame version={version}>
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
          'flex flex-1 flex-col items-center justify-center rounded-lg border-2 border-dashed px-6 py-10 text-center',
          over ? 'border-coral bg-coral-wash' : 'border-line-strong bg-white',
        ].join(' ')}
      >
        <PdfUploadIcon />
        <p className="mt-5 text-lg font-bold">PDF를 여기에 끌어다 놓으세요</p>
        <p className="mt-1.5 text-sm text-stone">또는 파일을 선택해 주세요 · 최대 40MB</p>

        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          className="mt-6 inline-flex items-center gap-2.5 rounded-lg bg-coral px-12 py-3 text-sm font-bold text-white hover:bg-coral-deep"
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
              onClick={() => startSlideUpload(failed.file)}
              className="rounded-lg border border-coral px-4 py-1.5 text-xs font-bold text-coral-deep hover:bg-coral-wash"
            >
              다시 시도
            </button>
          </div>
        )}

        <PdfInput inputRef={inputRef} onPick={accept} />
      </div>
    </UploadFrame>
  );
}

function Uploading({ file, version }: { file: File; version: number | null }) {
  return (
    <UploadFrame version={version}>
      <div
        role="status"
        className="flex flex-1 flex-col items-center justify-center rounded-lg border-2 border-dashed border-coral bg-coral-wash/40 px-6 py-10 text-center"
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

/* ------------------------------------------------------------------ */
/* 뷰어                                                                */
/* ------------------------------------------------------------------ */

/** 확대 단계. 100% 는 상자에 통째로 들어가는 크기입니다 */
const ZOOM_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2];

const toolButton =
  'flex h-8 w-8 items-center justify-center rounded-md hover:bg-panel disabled:cursor-not-allowed disabled:opacity-30 disabled:hover:bg-transparent';

function ViewerMessage({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-1 items-center justify-center text-sm text-stone">{children}</div>
  );
}

function Toolbar({
  fileName,
  page,
  total,
  onPage,
  zoomIndex,
  onZoom,
  fitWidth,
  onFitWidth,
  onFullscreen,
}: {
  fileName: string;
  page: number;
  total: number;
  onPage: (page: number) => void;
  zoomIndex: number;
  onZoom: (index: number) => void;
  fitWidth: boolean;
  onFitWidth: () => void;
  onFullscreen: () => void;
}) {
  return (
    <div className="flex items-center gap-3 border-b border-line px-4 py-2.5">
      <DocIcon className="h-4 w-4 shrink-0 stroke-ink" />
      <span className="min-w-0 flex-1 truncate text-sm font-bold">{fileName}</span>

      <button
        type="button"
        aria-label="이전 슬라이드"
        disabled={page <= 1}
        onClick={() => onPage(page - 1)}
        className={toolButton}
      >
        <span className="rotate-180">
          <ChevronIcon up={false} />
        </span>
      </button>
      <span className="tabular text-sm" aria-live="polite">
        {page} / {total}
      </span>
      <button
        type="button"
        aria-label="다음 슬라이드"
        disabled={page >= total}
        onClick={() => onPage(page + 1)}
        className={toolButton}
      >
        <ChevronIcon up={false} />
      </button>

      <span className="h-5 w-px bg-line" aria-hidden="true" />

      <button
        type="button"
        aria-label="축소"
        disabled={zoomIndex <= 0}
        onClick={() => onZoom(zoomIndex - 1)}
        className={`${toolButton} text-lg`}
      >
        −
      </button>
      <span className="tabular w-12 text-center text-sm">
        {Math.round((ZOOM_STEPS[zoomIndex] ?? 1) * 100)}%
      </span>
      <button
        type="button"
        aria-label="확대"
        disabled={zoomIndex >= ZOOM_STEPS.length - 1}
        onClick={() => onZoom(zoomIndex + 1)}
        className={`${toolButton} text-lg`}
      >
        +
      </button>

      <span className="h-5 w-px bg-line" aria-hidden="true" />

      <button
        type="button"
        aria-label="폭에 맞추기"
        aria-pressed={fitWidth}
        onClick={onFitWidth}
        className={`${toolButton} ${fitWidth ? 'bg-panel' : ''}`}
      >
        <FitWidthIcon />
      </button>
      <button type="button" aria-label="전체 화면" onClick={onFullscreen} className={toolButton}>
        <FullscreenIcon />
      </button>
    </div>
  );
}

function Pages({ slide, doc }: { slide: SlideVersion; doc: PDFDocumentProxy }) {
  const cardRef = useRef<HTMLDivElement>(null);
  const [page, setPage] = useState(1);
  const [zoomIndex, setZoomIndex] = useState(ZOOM_STEPS.indexOf(1));
  const [fitWidth, setFitWidth] = useState(false);
  const total = slide.pageCount ?? 0;

  const goTo = (n: number) => setPage(Math.min(total, Math.max(1, n)));

  const fullscreen = () => {
    cardRef.current?.requestFullscreen().catch((err: unknown) => {
      // 브라우저가 막은 경우(iframe 정책 등) — 뷰어는 그대로 쓸 수 있으니 남기기만 합니다
      console.error('[슬라이드] 전체 화면을 열지 못했습니다', err);
    });
  };

  return (
    <div
      ref={cardRef}
      tabIndex={-1}
      onKeyDown={(e) => {
        if (e.key === 'ArrowRight' || e.key === 'PageDown') goTo(page + 1);
        if (e.key === 'ArrowLeft' || e.key === 'PageUp') goTo(page - 1);
      }}
      className="flex min-h-0 flex-1 flex-col rounded-lg border border-line bg-white outline-none"
    >
      <Toolbar
        fileName={slide.fileName ?? `슬라이드 V${slide.version}.pdf`}
        page={page}
        total={total}
        onPage={goTo}
        zoomIndex={zoomIndex}
        onZoom={setZoomIndex}
        fitWidth={fitWidth}
        onFitWidth={() => setFitWidth((v) => !v)}
        onFullscreen={fullscreen}
      />

      <div className="flex min-h-0 flex-1">
        <ul className="w-44 shrink-0 overflow-y-auto border-r border-line bg-panel/50 p-4">
          {Array.from({ length: total }, (_, i) => i + 1).map((n) => (
            <li key={n} className="mb-4">
              <button
                type="button"
                onClick={() => setPage(n)}
                aria-current={page === n ? 'true' : undefined}
                aria-label={`${n}번 슬라이드`}
                className={[
                  'block w-full rounded-md border-2 p-1',
                  page === n ? 'border-coral' : 'border-transparent hover:border-line-strong',
                ].join(' ')}
              >
                <PdfPage doc={doc} pageNumber={n} fit="width" className="w-full" />
              </button>
              <span className="tabular mt-1.5 block text-center text-xs text-stone">{n}</span>
            </li>
          ))}
        </ul>

        <PdfPage
          doc={doc}
          pageNumber={page}
          fit={fitWidth ? 'width' : 'contain'}
          zoom={ZOOM_STEPS[zoomIndex] ?? 1}
          className="min-h-0 min-w-0 flex-1 overflow-auto bg-panel/30 p-6"
        />
      </div>
    </div>
  );
}

function Viewer({ slide, upload }: { slide: SlideVersion; upload: SlideUpload }) {
  const addSlideVersion = useCreateStore((s) => s.addSlideVersion);
  const inputRef = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<string | null>(null);
  const opened = usePdfDocument(slide.fileUrl);
  const total = slide.pageCount ?? 0;

  const replacing = upload.status === 'uploading' && upload.replace === slide.version;
  const replaceFailed =
    upload.status === 'failed' && upload.replace === slide.version ? upload : null;

  const replace = (file: File | undefined) => {
    if (!file) return;
    const reason = rejectReason(file);
    setError(reason);
    if (reason === null) startSlideUpload(file, slide.version);
  };

  const body = (() => {
    if (!slide.fileUrl) return <ViewerMessage>이 버전에는 파일 주소가 없어요</ViewerMessage>;
    if (opened === null) return <ViewerMessage>PDF 여는 중…</ViewerMessage>;
    // presigned URL 은 1시간 뒤 만료됩니다. 한 번 연 문서는 메모리에 있어 여기까지 오지 않습니다
    if ('failed' in opened) return <ViewerMessage>PDF를 열지 못했어요</ViewerMessage>;
    // 파일을 바꾸면 첫 장부터 다시 봅니다
    return <Pages key={slide.fileUrl} slide={slide} doc={opened.doc} />;
  })();

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <PaneHeading
        title={`슬라이드 V${slide.version}`}
        badges={
          <>
            {total > 0 && (
              <span className="tabular rounded-md bg-panel px-2.5 py-1 text-sm text-stone">
                {total}장
              </span>
            )}
            <span className="flex items-center gap-1.5 rounded-md border border-line-strong bg-panel px-2.5 py-1 text-sm font-bold">
              {replacing ? (
                '교체하는 중…'
              ) : (
                <>
                  <CheckIcon />
                  저장됨
                </>
              )}
            </span>
          </>
        }
        actions={
          <>
            <button
              type="button"
              disabled={replacing}
              onClick={() => inputRef.current?.click()}
              className="flex h-11 items-center gap-2 rounded-lg border border-line-strong bg-white px-5 text-sm font-bold hover:bg-panel disabled:cursor-not-allowed disabled:opacity-50"
            >
              <PaperclipIcon />
              파일 교체
            </button>
            <button
              type="button"
              disabled={replacing}
              onClick={addSlideVersion}
              className="flex h-11 items-center gap-2 rounded-lg border border-coral bg-white px-5 text-sm font-bold text-coral hover:bg-coral-wash disabled:cursor-not-allowed disabled:opacity-50"
            >
              + 새 버전
            </button>
            <PdfInput inputRef={inputRef} onPick={replace} />
          </>
        }
      />

      {(error || replaceFailed) && (
        <p role="alert" className="mb-3 flex items-center gap-3 text-sm font-bold text-coral">
          {error ?? (replaceFailed && FAILURE_MESSAGE[replaceFailed.reason])}
          {replaceFailed && !error && (
            <button
              type="button"
              onClick={() => startSlideUpload(replaceFailed.file, slide.version)}
              className="rounded-lg border border-coral px-3 py-1 text-xs text-coral-deep hover:bg-coral-wash"
            >
              다시 시도
            </button>
          )}
        </p>
      )}

      {body}
    </div>
  );
}

export function SlidePane({ slide }: { slide: SlideVersion | null }) {
  const upload = useCreateStore((s) => s.slideUpload);
  const hasPitch = useCreateStore((s) => s.pitchId !== null);

  // 비어 있는 자리(버전 없음 · 파일 기다리는 빈 버전)에서만 업로드 상태를 보입니다.
  // 지난 버전을 열어 보는 중이면 그 버전을 그대로 보여 줍니다
  if (slide === null || slide.pageCount === null) {
    const version = slide?.version ?? null;
    if (!hasPitch) {
      return (
        <UploadFrame version={version}>
          <NeedPitch what="발표자료" />
        </UploadFrame>
      );
    }
    if (upload.status === 'uploading' && upload.replace === null) {
      return <Uploading file={upload.file} version={version} />;
    }
    return <DropZone upload={upload} version={version} />;
  }

  // 버전마다 첫 장부터 봅니다
  return <Viewer key={slide.version} slide={slide} upload={upload} />;
}
