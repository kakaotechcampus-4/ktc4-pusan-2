import { useRef, useState } from 'react';
import { useCreateStore } from '../createStore';
import { MAX_SLIDE_BYTES, type SlideVersion } from '../lib/draft';
import { PaneHeading, StatusPill, outlineBtn } from './ui';

/**
 * 시안 — 슬라이드. 올린 것이 없으면 드롭존, 있으면 썸네일 + 뷰어.
 *
 * ★ 실제 PDF 렌더링은 아직 없습니다. 페이지 수와 썸네일은 서버 변환이
 *   내려 줘야 하고(`PitchDetail.presentation.convertStatus`), 그 API 가 없습니다.
 *   지금은 파일을 받으면 장수를 `STUB_PAGE_COUNT` 로 두고 빈 장을 그립니다.
 */

/**
 * 서버 변환이 붙기 전까지의 임시 장수.
 *
 * ★ 이 상수가 남아 있는 한 화면은 **가짜**입니다. 업로드 API 가 생기면
 *   응답의 `convertStatus` 를 따라가고 이 줄은 지웁니다.
 */
const STUB_PAGE_COUNT = 12;

const ZOOM_STEP = 25;
const ZOOM_MIN = 50;
const ZOOM_MAX = 200;

/** 파일을 받아 검사합니다. 통과한 것만 `onPick` 으로 넘깁니다 */
function usePdfPicker(onPick: (file: File) => void) {
  const inputRef = useRef<HTMLInputElement>(null);
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
    onPick(file);
  };

  const input = (
    <input
      ref={inputRef}
      type="file"
      accept="application/pdf"
      hidden
      onChange={(e) => {
        accept(e.target.files?.[0]);
        // 같은 파일을 다시 골라도 change 가 나도록 비웁니다
        e.target.value = '';
      }}
    />
  );

  return { open: () => inputRef.current?.click(), accept, error, input };
}

function DropZone({ onPick }: { onPick: (file: File) => void }) {
  const [over, setOver] = useState(false);
  const picker = usePdfPicker(onPick);

  return (
    <div className="flex flex-1 flex-col gap-4">
      <PaneHeading title="슬라이드" subtitle="발표에 쓸 슬라이드 PDF를 올려 주세요." />

      <div
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          picker.accept(e.dataTransfer.files[0]);
        }}
        className={[
          'flex min-h-72 flex-1 flex-col items-center justify-center rounded border-2 border-dashed',
          over ? 'border-coral bg-coral-wash' : 'border-line-strong bg-panel',
        ].join(' ')}
      >
        <button
          type="button"
          onClick={picker.open}
          className="rounded border-2 border-ink bg-panel px-6 py-4 text-center"
        >
          <span className="block text-sm font-bold">PDF를 여기로 끌어다 놓으세요</span>
          <span className="mt-1 block text-xs text-stone">또는 클릭해서 파일 선택 · 최대 40MB</span>
        </button>

        <p className="mt-4 text-xs text-stone">슬라이드를 올려야 대본 매핑을 할 수 있어요</p>
        {picker.error && (
          <p role="alert" className="mt-2 text-xs font-bold text-coral-deep">
            {picker.error}
          </p>
        )}
        {picker.input}
      </div>
    </div>
  );
}

const iconBtn =
  'flex h-8 w-8 items-center justify-center rounded text-base hover:bg-cream disabled:cursor-not-allowed disabled:text-line-strong';

function Viewer({ slide }: { slide: SlideVersion }) {
  const replaceSlides = useCreateStore((s) => s.replaceSlides);
  const addSlideVersion = useCreateStore((s) => s.addSlideVersion);
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState(100);
  const stageRef = useRef<HTMLDivElement>(null);

  const total = slide.pageCount ?? 0;
  const picker = usePdfPicker((file) => {
    // 파일을 바꾸면 보던 장이 없어질 수 있어 처음으로 돌립니다
    replaceSlides(slide.version, file.name, STUB_PAGE_COUNT);
    setPage(1);
  });

  // 지난 버전으로 옮겨 가도 같은 컴포넌트라, 장수가 줄었으면 범위 안으로 당깁니다
  const current = Math.min(page, Math.max(total, 1));

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <h2 className="text-3xl font-bold">슬라이드 V{slide.version}</h2>
          <StatusPill tone="idle">{total}장</StatusPill>
          <StatusPill tone="ok">
            <span aria-hidden="true" className="text-emerald-700">
              ✓
            </span>
            저장됨
          </StatusPill>
        </div>
        <div className="flex gap-2">
          <button type="button" onClick={picker.open} className={outlineBtn}>
            파일 교체
          </button>
          <button
            type="button"
            onClick={addSlideVersion}
            className={`${outlineBtn} !border-coral !text-coral-deep hover:!bg-coral-wash`}
          >
            + 새 버전
          </button>
          {picker.input}
        </div>
      </div>
      {picker.error && (
        <p role="alert" className="text-xs font-bold text-coral-deep">
          {picker.error}
        </p>
      )}

      <div
        ref={stageRef}
        className="flex min-h-96 flex-1 flex-col overflow-hidden rounded border border-line bg-panel"
      >
        {/* 도구 줄 */}
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-2">
          <span className="truncate text-sm font-bold">{slide.fileName ?? '슬라이드.pdf'}</span>
          <div className="flex items-center gap-1 text-sm">
            <button
              type="button"
              aria-label="이전 장"
              disabled={current <= 1}
              onClick={() => setPage(current - 1)}
              className={iconBtn}
            >
              ‹
            </button>
            <span className="tabular w-16 text-center text-xs">
              {current} / {total}
            </span>
            <button
              type="button"
              aria-label="다음 장"
              disabled={current >= total}
              onClick={() => setPage(current + 1)}
              className={iconBtn}
            >
              ›
            </button>

            <span aria-hidden="true" className="mx-2 h-5 w-px bg-line" />

            <button
              type="button"
              aria-label="축소"
              disabled={zoom <= ZOOM_MIN}
              onClick={() => setZoom((z) => z - ZOOM_STEP)}
              className={iconBtn}
            >
              −
            </button>
            <span className="tabular w-12 text-center text-xs">{zoom}%</span>
            <button
              type="button"
              aria-label="확대"
              disabled={zoom >= ZOOM_MAX}
              onClick={() => setZoom((z) => z + ZOOM_STEP)}
              className={iconBtn}
            >
              +
            </button>

            <span aria-hidden="true" className="mx-2 h-5 w-px bg-line" />

            <button
              type="button"
              aria-label="화면에 맞추기"
              onClick={() => setZoom(100)}
              className={iconBtn}
            >
              ⇔
            </button>
            <button
              type="button"
              aria-label="전체 화면"
              onClick={() => stageRef.current?.requestFullscreen?.().catch(() => undefined)}
              className={iconBtn}
            >
              ⛶
            </button>
          </div>
        </div>

        <div className="flex min-h-0 flex-1">
          {/* 썸네일 열 — 변환이 붙으면 이미지가 들어옵니다 */}
          <ul className="w-36 shrink-0 overflow-y-auto border-r border-line bg-cream/40 p-3">
            {Array.from({ length: total }, (_, i) => i + 1).map((n) => (
              <li key={n} className="mb-3 text-center">
                <button
                  type="button"
                  onClick={() => setPage(n)}
                  aria-label={`${n}번 슬라이드`}
                  aria-current={current === n ? 'true' : undefined}
                  className={[
                    'tabular flex aspect-[4/3] w-full items-center justify-center rounded border bg-white font-mono text-xs text-stone',
                    current === n ? 'border-2 border-coral' : 'border-line',
                  ].join(' ')}
                >
                  {String(n).padStart(2, '0')}
                </button>
                <span className="tabular mt-1 block text-xs text-stone">{n}</span>
              </li>
            ))}
          </ul>

          {/* 100% 는 뷰어 높이에 맞춘 크기입니다 — 확대하면 스크롤됩니다 */}
          <div className="min-w-0 flex-1 overflow-auto bg-cream/60 p-6">
            <div
              style={{ height: `${zoom}%` }}
              className="tabular m-auto flex aspect-[16/9] max-w-none items-center justify-center rounded border border-line bg-white font-mono text-sm text-stone shadow-sm"
            >
              {String(current).padStart(2, '0')}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

export function SlidePane({ slide }: { slide: SlideVersion | null }) {
  const attachSlides = useCreateStore((s) => s.attachSlides);

  if (slide === null || slide.pageCount === null) {
    return <DropZone onPick={(file) => attachSlides(file.name, STUB_PAGE_COUNT)} />;
  }

  // 버전마다 보던 장 · 확대 상태를 따로 갖도록 key 를 겁니다
  return <Viewer key={slide.version} slide={slide} />;
}
