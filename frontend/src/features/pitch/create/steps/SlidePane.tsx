import { useRef, useState } from 'react';
import { useCreateStore } from '../createStore';
import { MAX_SLIDE_BYTES, type SlideVersion } from '../lib/draft';

/**
 * 목업 04(업로드)와 05(뷰어)는 **같은 화면의 두 상태**입니다.
 * 올린 것이 없으면 드롭존, 있으면 썸네일 + 뷰어.
 *
 * ★ 실제 PDF 렌더링은 아직 없습니다. 페이지 수와 썸네일은 서버 변환이
 *   내려 줘야 하고(`PitchDetail.presentation.convertStatus`), 그 API 가 없습니다.
 *   지금은 파일을 받은 뒤 장수를 사용자가 확인하는 자리만 비워 둡니다.
 */

function DropZone({ onPick }: { onPick: (file: File) => void }) {
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
    onPick(file);
  };

  return (
    <div className="flex flex-1 flex-col">
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="text-lg font-bold">슬라이드 PDF를 올려주세요</h2>
      </div>

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
          'flex flex-1 flex-col items-center justify-center rounded border-2 border-dashed',
          over ? 'border-coral bg-coral-wash' : 'border-line-strong',
        ].join(' ')}
      >
        <button
          type="button"
          onClick={() => inputRef.current?.click()}
          className="rounded border-2 border-ink bg-panel px-6 py-4 text-center"
        >
          <span className="block text-sm font-bold">PDF를 여기로 끌어다 놓으세요</span>
          <span className="mt-1 block text-xs text-stone">또는 클릭해서 파일 선택 · 최대 40MB</span>
        </button>

        <p className="mt-4 text-xs text-stone">슬라이드를 올려야 대본 매핑을 할 수 있어요</p>
        {error && (
          <p role="alert" className="mt-2 text-xs font-bold text-coral">
            {error}
          </p>
        )}

        <input
          ref={inputRef}
          type="file"
          accept="application/pdf"
          hidden
          onChange={(e) => accept(e.target.files?.[0])}
        />
      </div>
    </div>
  );
}

function Viewer({ slide }: { slide: SlideVersion }) {
  const [page, setPage] = useState(1);
  const total = slide.pageCount ?? 0;

  return (
    <div className="flex flex-1 flex-col">
      <div className="mb-3 flex items-baseline justify-between">
        <h2 className="text-lg font-bold">
          슬라이드 V{slide.version}
          {total > 0 && <span className="text-stone"> · {total}장</span>}
        </h2>
        <button
          type="button"
          className="rounded border border-line-strong px-4 py-2 text-xs font-bold text-stone"
        >
          V{slide.version} 저장됨
        </button>
      </div>

      <div className="flex flex-1 gap-0 rounded border-2 border-ink bg-panel">
        {/* 썸네일 열 — 변환이 붙으면 이미지가 들어옵니다 */}
        <ul className="w-32 shrink-0 overflow-y-auto border-r border-line p-3">
          {Array.from({ length: total }, (_, i) => i + 1).map((n) => (
            <li key={n}>
              <button
                type="button"
                onClick={() => setPage(n)}
                aria-current={page === n ? 'true' : undefined}
                className={[
                  'tabular mb-2 flex aspect-[4/3] w-full items-center justify-center rounded border font-mono text-xs',
                  page === n ? 'border-ink border-2' : 'border-line text-stone',
                ].join(' ')}
              >
                {String(n).padStart(2, '0')}
              </button>
            </li>
          ))}
        </ul>

        <div className="relative flex flex-1 items-center justify-center p-6">
          <span className="tabular absolute right-4 top-4 rounded border border-line px-2 py-1 font-mono text-xs">
            {page} / {total}
          </span>
          <div className="flex aspect-[4/3] w-full max-w-3xl items-center justify-center rounded border border-line bg-white text-sm text-stone">
            PDF 뷰어 · 스크롤
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * 서버 변환이 붙기 전까지의 임시 장수.
 *
 * ★ 이 상수가 남아 있는 한 화면은 **가짜**입니다. 업로드 API 가 생기면
 *   응답의 `convertStatus` 를 따라가고 이 줄은 지웁니다.
 */
const STUB_PAGE_COUNT = 12;

export function SlidePane({ slide }: { slide: SlideVersion | null }) {
  const attachSlides = useCreateStore((s) => s.attachSlides);

  if (slide === null || slide.pageCount === null) {
    return <DropZone onPick={() => attachSlides(STUB_PAGE_COUNT)} />;
  }

  return <Viewer slide={slide} />;
}
