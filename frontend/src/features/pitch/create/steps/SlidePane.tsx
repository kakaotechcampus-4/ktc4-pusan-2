import { useId, useRef, useState, type ReactNode, type RefObject } from 'react';
import { useCreateStore } from '../createStore';
import { FileIcon, PdfUploadIcon } from '../icons';
import {
  MAX_SLIDE_BYTES,
  MAX_TITLE_CHARS,
  type SlideUpload,
  type SlideVersion,
} from '../lib/draft';
import { PdfPage } from '@/shared/ui/PdfPage';
import { startSlideUpload } from '../slideUpload';
import { usePdfDocument } from '@/shared/lib/usePdfDocument';
import { PaneFooter, primaryButton } from './PaneFooter';
import { PaneHeading } from './PaneHeading';

/**
 * 시안 슬라이드 — 첫 단계. 올린 것이 없으면 드롭존 → 올리는 중 → 제목 확인 + 썸네일.
 *
 * 피치가 아직 없으면 올릴 때 만듭니다 — 파일 이름이 발표 제목이 됩니다 (`slideUpload.ts`).
 * 썸네일은 서버가 돌려준 URL 로 PDF 를 열어 그립니다. 고른 파일을 바로 그리지 않는 이유도 거기 있습니다.
 */

const FAILURE_MESSAGE: Record<'upload' | 'invalid' | 'open', string> = {
  upload: '업로드하지 못했어요. 네트워크를 확인하고 다시 시도해 주세요.',
  invalid:
    'PDF를 열 수 없어요. 암호가 걸려 있거나 손상되지 않았는지 확인하고 다른 파일을 골라 주세요.',
  open: '올린 PDF를 열지 못했어요. 파일이 손상됐을 수 있어요.',
};

/** 같은 파일을 다시 보내도 같은 400 이면(`invalid`) "다시 시도"를 내지 않습니다 (`slideUpload.ts`) */
const canRetry = (reason: keyof typeof FAILURE_MESSAGE) => reason !== 'invalid';

/** 받을 수 없는 파일이면 그 이유 */
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
        subtitle="발표에 사용할 PDF부터 올려 주세요."
      />
      {children}
    </div>
  );
}

function DropZone({ upload, version }: { upload: SlideUpload; version: number | null }) {
  const pitchId = useCreateStore((s) => s.pitchId);
  const inputRef = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const accept = (file: File | undefined) => {
    if (!file) return;
    const reason = rejectReason(file);
    setError(reason);
    if (reason === null) startSlideUpload(file);
  };

  const failed = upload.status === 'failed' ? upload : null;

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
        <p className="mt-1.5 text-sm text-ink/80">
          파일 이름으로 발표 제목을 채워 드려요 · 나중에 고칠 수 있어요 · 최대 40MB
        </p>
        {pitchId === null && (
          <p className="mt-3 text-xs text-ink/80">
            PDF를 올리면 피치가 자동으로 만들어져요.
            <br />
            업로드에 실패하거나 나가도 내 피치에 남아, 나중에 이어서 준비할 수 있어요.
          </p>
        )}

        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          className="mt-6 inline-flex items-center gap-2.5 rounded-lg bg-coral-deep px-12 py-3 text-sm font-bold text-white hover:bg-ink"
        >
          <FileIcon />
          PDF 파일 선택
        </button>

        {error && (
          <p role="alert" className="mt-3 text-xs font-bold text-ink">
            {error}
          </p>
        )}

        {/* 실패한 파일은 들고 있습니다 — 다시 고르게 하지 않습니다. 열 수 없는 PDF(`invalid`)는
            다시 보내도 같으니 다른 파일을 고르게 합니다 */}
        {failed && !error && (
          <div role="alert" className="mt-4 flex flex-col items-center gap-2">
            <p className="text-xs font-bold text-ink">
              {failed.file.name} · {FAILURE_MESSAGE[failed.reason]}
            </p>
            {pitchId !== null && (
              <p className="text-xs text-ink/80">
                피치는 내 피치에 보관돼 있어요. 이 피치에서 다시 업로드할 수 있어요.
              </p>
            )}
            {canRetry(failed.reason) && (
              <button
                type="button"
                onClick={() => startSlideUpload(failed.file)}
                className="rounded-lg border border-coral px-4 py-1.5 text-xs font-bold text-ink hover:bg-coral-wash"
              >
                다시 시도
              </button>
            )}
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
        <p className="mt-1.5 max-w-full truncate text-sm text-ink/80">{file.name}</p>
        <p className="mt-4 text-xs text-ink/80">
          다른 자료를 먼저 준비해도 괜찮아요. 업로드는 계속돼요.
        </p>
      </div>
    </UploadFrame>
  );
}

/* ------------------------------------------------------------------ */
/* 올린 뒤 — 제목 확인 · 썸네일                                           */
/* ------------------------------------------------------------------ */

function Message({ children }: { children: ReactNode }) {
  return (
    <div className="mb-5 flex min-h-40 items-center justify-center rounded-lg border border-line bg-white text-sm text-ink/80">
      {children}
    </div>
  );
}

/**
 * 슬라이드를 올려 피치를 만들었으면 파일 이름이 발표 제목입니다. 여기서 바로 고치거나 되돌립니다.
 * 고친 제목은 발표정보처럼 아직 저장 전이고, 발표정보 확인에서 저장합니다 (`infoEdits`).
 */
function TitleCheck({ autoTitle }: { autoTitle: string }) {
  const id = useId();
  const title = useCreateStore((s) => (s.infoEdits ?? s.draft).title);
  const editInfo = useCreateStore((s) => s.editInfo);

  return (
    <div className="mb-4 flex flex-col gap-2 rounded-lg border border-coral/40 bg-coral-wash px-4 py-3">
      <label htmlFor={id} className="text-sm font-bold">
        파일 이름으로 발표 제목을 정했어요
      </label>
      <div className="flex flex-wrap items-center gap-3">
        <input
          id={id}
          value={title}
          maxLength={MAX_TITLE_CHARS}
          onChange={(e) => editInfo({ title: e.target.value })}
          className="h-10 min-w-0 flex-1 rounded-md border border-line-strong bg-white px-3 text-sm outline-none focus:border-ink"
        />
        {title !== autoTitle && (
          <button
            type="button"
            onClick={() => editInfo({ title: autoTitle })}
            className="text-xs font-bold text-coral-deep"
          >
            파일 이름으로 되돌리기
          </button>
        )}
      </div>
      <p className="text-xs text-ink/80">
        여기서 바로 고치거나, 나중에 발표정보에서 고칠 수 있어요.
      </p>
    </div>
  );
}

function Thumb({ children }: { children: ReactNode }) {
  return (
    <li className="tabular flex aspect-video items-center justify-center overflow-hidden rounded-md border border-line bg-cream text-xs text-ink/80">
      {children}
    </li>
  );
}

const thumbGrid = 'mb-5 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5';

/** 장마다 썸네일. 서버가 돌려준 URL 로 연 PDF 를 그립니다 (`shared/lib/pdf`) */
function Thumbs({ slide }: { slide: SlideVersion }) {
  const opened = usePdfDocument(slide.fileUrl);

  if (!slide.fileUrl) return <Message>이 버전에는 파일 주소가 없어요</Message>;
  // BE 가 쪽수를 주면 업로드 때 PDF 를 열지 않아(#298) 처음 여는 곳이 여기입니다. 실서버에서는
  // S3 CORS · 만료된 presigned URL(1시간)로 못 열 수 있습니다. 버전은 저장돼 있으니 다시 열기만 둡니다
  if (opened !== null && 'failed' in opened)
    return (
      <Message>
        <div role="alert" className="flex flex-col items-center gap-2 text-center">
          <span>PDF를 열지 못했어요. 파일은 저장돼 있어요.</span>
          <span className="text-xs">계속 안 되면 새로고침해 주세요.</span>
          <button
            type="button"
            onClick={opened.retry}
            className="rounded-lg border border-coral px-4 py-1.5 text-xs font-bold text-coral-deep hover:bg-coral-wash"
          >
            다시 열기
          </button>
        </div>
      </Message>
    );
  // 여는 동안은 BE 가 센 장수만큼 번호 칸을 둡니다. 연 뒤에는 실제로 연 문서의 쪽수를 그립니다 —
  // 둘이 다른 PDF 에서 없는 쪽을 열다 오류가 나지 않게
  if (opened === null)
    return (
      <ul aria-busy="true" className={thumbGrid}>
        {Array.from({ length: slide.pageCount ?? 0 }, (_, i) => (
          <Thumb key={i}>{i + 1}</Thumb>
        ))}
      </ul>
    );
  return (
    <ul className={thumbGrid}>
      {Array.from({ length: opened.doc.numPages }, (_, i) => (
        <Thumb key={i}>
          <PdfPage doc={opened.doc} pageNumber={i + 1} fit="width" className="w-full" />
        </Thumb>
      ))}
    </ul>
  );
}

function Uploaded({ slide }: { slide: SlideVersion }) {
  const select = useCreateStore((s) => s.select);
  const autoTitle = useCreateStore((s) => s.autoTitle);
  const versions = useCreateStore((s) => s.draft.slides.length);

  return (
    <div className="flex flex-1 flex-col">
      <PaneHeading
        title={versions > 1 ? `슬라이드 V${slide.version}` : '슬라이드'}
        subtitle="올린 자료를 확인해 주세요."
      />
      {autoTitle !== null && <TitleCheck autoTitle={autoTitle} />}

      <p className="mb-3 flex flex-wrap items-center gap-2 text-sm">
        <span className="min-w-0 font-bold [overflow-wrap:anywhere]">
          {slide.fileName ?? `슬라이드 V${slide.version}.pdf`}
        </span>
        <span className="tabular text-ink/80">· {slide.pageCount}장</span>
      </p>
      <Thumbs slide={slide} />

      {/* 저장한 버전은 고치지 않습니다 — 다른 파일은 사이드바의 "+ 새 버전"으로 올립니다 */}
      <PaneFooter hint="다음은 슬라이드에 맞춰 대본을 붙여요.">
        <button type="button" onClick={() => select('script')} className={primaryButton}>
          다음 · 대본 →
        </button>
      </PaneFooter>
    </div>
  );
}

export function SlidePane({ slide }: { slide: SlideVersion | null }) {
  const upload = useCreateStore((s) => s.slideUpload);

  // 비어 있는 자리(버전 없음 · 파일 기다리는 빈 버전)에서만 업로드 상태를 보입니다.
  // 지난 버전을 열어 보는 중이면 그 버전을 그대로 보여 줍니다
  if (slide === null || slide.pageCount === null) {
    const version = slide?.version ?? null;
    if (upload.status === 'uploading') return <Uploading file={upload.file} version={version} />;
    return <DropZone upload={upload} version={version} />;
  }

  return <Uploaded key={slide.version} slide={slide} />;
}
