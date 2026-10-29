import { useEffect, useId, useRef, type RefObject } from 'react';
import { usePdfDocument } from '@/shared/lib/usePdfDocument';
import { useCreateStore } from './createStore';
import type { DraftNode, SlideVersion } from './lib/draft';
import { Pages } from './steps/SlidePane';

function SlidePreview({ slide }: { slide: SlideVersion }) {
  const opened = usePdfDocument(slide.fileUrl);
  if (!slide.fileUrl) return <p>이 버전에는 업로드된 PDF가 없어요.</p>;
  if (!opened) return <p role="status">PDF를 불러오는 중…</p>;
  if ('failed' in opened)
    return (
      <div role="alert">
        <p>PDF를 열지 못했어요. 다시 시도해 주세요.</p>
        <button type="button" className="create-secondary mt-3" onClick={opened.retry}>
          다시 시도
        </button>
      </div>
    );
  return <Pages slide={slide} doc={opened.doc} />;
}

export function VersionPreviewDialog({
  node,
  version,
  returnFocus,
  onClose,
}: {
  node: DraftNode;
  version: number;
  returnFocus: RefObject<HTMLButtonElement | null>;
  onClose: () => void;
}) {
  const draft = useCreateStore((s) => s.draft);
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const slide = draft.slides.find((v) => v.version === version);
  const script = draft.scripts.find((v) => v.version === version);
  const criteria = draft.criteria.find((v) => v.version === version);
  const labels = { slides: '슬라이드', script: '대본', criteria: '평가기준' };
  useEffect(() => {
    const dialog = ref.current!;
    const previous = returnFocus.current;
    const overflow = document.body.style.overflow;
    dialog.showModal();
    document.body.style.overflow = 'hidden';
    return () => {
      dialog.close();
      document.body.style.overflow = overflow;
      previous?.focus({ preventScroll: true });
    };
  }, [returnFocus]);
  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onClose={onClose}
      className="create-mapping-dialog create-version-dialog"
    >
      <header>
        <h2 id={titleId}>
          {labels[node]} V{version} · 자료 보기
        </h2>
        <button
          type="button"
          autoFocus
          className="create-secondary"
          onClick={() => ref.current?.close()}
        >
          닫기
        </button>
      </header>
      <p className="text-sm">확인용 창이에요. 현재 작성 중인 자료와 연습할 버전은 바뀌지 않아요.</p>
      {node === 'slides' && slide && <SlidePreview slide={slide} />}
      {node === 'script' && script && (
        <div className="min-h-0 flex-1 overflow-auto bg-white p-5">
          <p className="mb-4 text-sm">
            연결된 슬라이드: {script.slideVersion === null ? '없음' : `V${script.slideVersion}`}
          </p>
          {script.blocks ? (
            script.blocks.map((text, index) => (
              <section key={index} className="mb-5">
                <h3 className="mb-2 font-bold">{index + 1}장 대본</h3>
                <p className="whitespace-pre-wrap break-words">{text || '대본 없음'}</p>
              </section>
            ))
          ) : (
            <p className="whitespace-pre-wrap break-words">
              {script.text || '작성된 대본이 없어요.'}
            </p>
          )}
        </div>
      )}
      {node === 'criteria' && criteria && (
        <div className="min-h-0 flex-1 overflow-auto bg-white p-5">
          {criteria.items.length ? (
            <ol className="list-decimal space-y-4 pl-5">
              {criteria.items.map((item) => (
                <li key={item.id} className="whitespace-pre-wrap break-words">
                  {item.text}
                </li>
              ))}
            </ol>
          ) : (
            <p>저장된 평가기준이 없어요.</p>
          )}
        </div>
      )}
    </dialog>
  );
}
