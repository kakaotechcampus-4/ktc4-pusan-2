import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { missionSourceNote, type PrepareInfo } from './prepareInfo';

const PAGE_SIZE = 3;

export function MissionCard({
  info,
  exam,
  compact = false,
}: {
  info: PrepareInfo;
  exam: boolean;
  compact?: boolean;
}) {
  const [requestedPage, setPage] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const { missions } = info;
  const items = missions?.items ?? [];
  const page = Math.min(requestedPage, Math.max(0, Math.ceil(items.length / PAGE_SIZE) - 1));
  const offset = page * PAGE_SIZE;
  const selected = items.findIndex((mission) => mission.id === selectedId);
  const note = missionSourceNote(info);
  const coachNote = exam
    ? '실전 모드에서는 코치가 알려 주지 않아요. 끝난 뒤 리포트에서 결과를 확인해요.'
    : '발표 중 코치가 이 미션을 알려 드려요.';

  return (
    <section
      className={`prepare-missions border border-line bg-panel px-3 py-2.5 prepare-compact:px-2.5 prepare-compact:py-1.5 ${compact ? 'prepare-missions-compact block' : 'flex h-[164px] flex-col prepare-compact:h-[156px]'}`}
      aria-label="이번 Take 미션"
    >
      <div className="prepare-section-heading flex min-h-6 flex-wrap items-center gap-x-2.5 gap-y-1.5 text-xs leading-[18px] [&>h2]:text-[15px] [&>h2]:leading-[22px] [&>h2]:font-bold">
        <h2>이번 Take 미션</h2>
        <span>{items.length}개</span>
        {missions && (
          <span className="prepare-mission-source ml-auto">
            Take {missions.sourceTakeNumber}에서 정함
          </span>
        )}
        {compact && items.length > 0 && (
          <button
            type="button"
            className="prepare-text-action min-h-6 shrink-0 cursor-pointer py-0.5 text-xs text-ink underline underline-offset-3 hover:decoration-2"
            onClick={() => setSelectedId(items[0]!.id)}
          >
            전체 미션 보기
          </button>
        )}
      </div>
      {!items.length ? (
        <p className="prepare-empty-mission mt-1 flex-1 text-xs leading-[18px]">
          {missions
            ? `Take ${missions.sourceTakeNumber} 리뷰에서 정한 미션이 없어요. 편하게 연습해 보세요.`
            : '첫 Take라 아직 미션이 없어요. 편하게 연습해 보세요. 끝나면 다음에 해 볼 것을 알려 드려요.'}
        </p>
      ) : (
        !compact && (
          <>
            <ol
              start={offset + 1}
              className="prepare-mission-list mt-1 flex-[0_0_72px]"
              aria-label="미션 목록"
            >
              {items.slice(offset, offset + PAGE_SIZE).map((mission, index) => (
                <MissionItem
                  key={mission.id}
                  description={mission.description}
                  number={offset + index + 1}
                  onRead={() => setSelectedId(mission.id)}
                />
              ))}
            </ol>
            <div className="prepare-mission-footer mt-auto flex min-h-9 flex-wrap items-center justify-between gap-x-2 gap-y-1 text-xs leading-[18px] [&_nav]:flex [&_nav]:items-center [&_nav]:gap-1.5 [&_nav]:tabular-nums [&_button]:min-h-6! [&_button]:px-[7px]! [&_button]:py-px!">
              <p>
                {items.length > PAGE_SIZE
                  ? `전체 ${items.length}개가 이번 Take에 적용돼요.`
                  : coachNote}
              </p>
              {items.length > PAGE_SIZE && (
                <nav aria-label="미션 페이지">
                  <span aria-live="polite">
                    {offset + 1}–{Math.min(offset + PAGE_SIZE, items.length)} / {items.length}
                  </span>
                  <button
                    type="button"
                    className="prepare-action inline-flex min-h-8 shrink-0 items-center justify-center gap-1 border border-ink bg-panel px-2.5 py-1 text-xs leading-[18px] font-semibold text-ink hover:bg-cream disabled:pointer-events-none disabled:cursor-default disabled:border-dashed disabled:border-line-strong disabled:bg-cream disabled:text-ink prepare-low:min-h-7 prepare-low:py-0.5"
                    aria-label="이전 미션 페이지"
                    disabled={page === 0}
                    onClick={() => setPage(page - 1)}
                  >
                    이전
                  </button>
                  <button
                    type="button"
                    className="prepare-action inline-flex min-h-8 shrink-0 items-center justify-center gap-1 border border-ink bg-panel px-2.5 py-1 text-xs leading-[18px] font-semibold text-ink hover:bg-cream disabled:pointer-events-none disabled:cursor-default disabled:border-dashed disabled:border-line-strong disabled:bg-cream disabled:text-ink prepare-low:min-h-7 prepare-low:py-0.5"
                    aria-label="다음 미션 페이지"
                    disabled={offset + PAGE_SIZE >= items.length}
                    onClick={() => setPage(page + 1)}
                  >
                    다음
                  </button>
                </nav>
              )}
            </div>
            {note && <p className="sr-only">{note}</p>}
          </>
        )
      )}
      {selected >= 0 && missions && (
        <MissionReader
          items={items}
          index={selected}
          source={missions.sourceTakeNumber}
          note={coachNote}
          onSelect={(index) => setSelectedId(items[index]!.id)}
          onClose={() => setSelectedId(null)}
        />
      )}
    </section>
  );
}

/** 글자 수 대신 실제 남은 폭으로 판단해, 다 읽히는 미션에는 추가 조작을 두지 않습니다. */
function MissionItem({
  description,
  number,
  onRead,
}: {
  description: string;
  number: number;
  onRead: () => void;
}) {
  const rowRef = useRef<HTMLLIElement>(null);
  const textRef = useRef<HTMLParagraphElement>(null);
  const [truncated, setTruncated] = useState(false);
  useLayoutEffect(() => {
    const row = rowRef.current;
    const text = textRef.current;
    if (!row || !text) return;
    let alive = true;
    const measure = () => {
      if (!alive) return;
      const numberWidth =
        row.querySelector<HTMLElement>('.prepare-mission-number')?.offsetWidth ?? 0;
      const gap = parseFloat(getComputedStyle(row).columnGap) || 0;
      // '더 보기'가 차지하는 폭은 빼지 않아, 버튼 때문에 잘리는 순환을 피합니다.
      setTruncated(text.scrollWidth > row.clientWidth - numberWidth - gap);
    };
    measure();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    observer?.observe(row);
    document.fonts?.ready.then(measure).catch(() => undefined);
    return () => {
      alive = false;
      observer?.disconnect();
    };
  }, [description]);
  return (
    <li
      ref={rowRef}
      className="flex h-6 items-center gap-2 border-t border-line text-[13px] [&>p]:min-w-0 [&>p]:flex-1 [&>p]:truncate"
    >
      <span
        className="prepare-mission-number grid h-[18px] flex-[0_0_18px] place-items-center bg-coral-deep text-[11px] text-white tabular-nums"
        aria-hidden="true"
      >
        {number}
      </span>
      <p ref={textRef}>{description}</p>
      {truncated && (
        <button
          type="button"
          className="prepare-text-action min-h-6 shrink-0 cursor-pointer py-0.5 text-xs text-ink underline underline-offset-3 hover:decoration-2"
          aria-label={`미션 ${number} 전체 보기`}
          onClick={onRead}
        >
          더 보기
        </button>
      )}
    </li>
  );
}

function MissionReader({
  items,
  index,
  source,
  note,
  onSelect,
  onClose,
}: {
  items: NonNullable<PrepareInfo['missions']>['items'];
  index: number;
  source: number;
  note: string;
  onSelect: (index: number) => void;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = dialogRef.current;
    const previous = document.activeElement;
    dialog?.showModal();
    return () => {
      dialog?.close();
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus();
    };
  }, []);

  return (
    <dialog
      ref={dialogRef}
      className="pitch-create prepare-mission-reader fixed inset-0 m-auto max-h-[calc(100dvh-32px)] w-[min(720px,calc(100%-32px))] overflow-auto border border-line-strong bg-panel p-6 text-xs leading-[18px] text-ink shadow-lg backdrop:bg-ink/35 [&>header]:flex [&>header]:items-center [&>header]:justify-between [&>header]:gap-4 [&>header]:border-b [&>header]:border-line [&>header]:pb-3 [&_h2]:mb-1 [&_h2]:text-xl [&_h2]:leading-[26px] [&_h2]:font-bold [&>footer]:mt-4 [&>footer]:flex [&>footer]:flex-wrap [&>footer]:items-center [&>footer]:gap-2 [&>footer]:border-t [&>footer]:border-line [&>footer]:pt-3 [&>footer>p]:flex-1"
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
    >
      <header>
        <div>
          <h2 id={titleId}>미션 전문</h2>
          <p>
            {index + 1} / {items.length} · Take {source}에서 정함
          </p>
        </div>
        <button
          type="button"
          className="prepare-action inline-flex min-h-8 shrink-0 items-center justify-center gap-1 border border-ink bg-panel px-2.5 py-1 text-xs leading-[18px] font-semibold text-ink hover:bg-cream disabled:pointer-events-none disabled:cursor-default disabled:border-dashed disabled:border-line-strong disabled:bg-cream disabled:text-ink prepare-low:min-h-7 prepare-low:py-0.5"
          onClick={onClose}
        >
          닫기
        </button>
      </header>
      <p
        className="prepare-mission-description my-4 text-sm leading-[22px] whitespace-normal wrap-anywhere"
        aria-live="polite"
      >
        {items[index]!.description}
      </p>
      <p>{note}</p>
      <footer>
        <p>모든 미션이 이번 Take에 적용돼요.</p>
        <button
          type="button"
          className="prepare-action inline-flex min-h-8 shrink-0 items-center justify-center gap-1 border border-ink bg-panel px-2.5 py-1 text-xs leading-[18px] font-semibold text-ink hover:bg-cream disabled:pointer-events-none disabled:cursor-default disabled:border-dashed disabled:border-line-strong disabled:bg-cream disabled:text-ink prepare-low:min-h-7 prepare-low:py-0.5"
          disabled={index === 0}
          onClick={() => onSelect(index - 1)}
        >
          이전 미션
        </button>
        <button
          type="button"
          className="prepare-action inline-flex min-h-8 shrink-0 items-center justify-center gap-1 border border-ink bg-panel px-2.5 py-1 text-xs leading-[18px] font-semibold text-ink hover:bg-cream disabled:pointer-events-none disabled:cursor-default disabled:border-dashed disabled:border-line-strong disabled:bg-cream disabled:text-ink prepare-low:min-h-7 prepare-low:py-0.5"
          disabled={index === items.length - 1}
          onClick={() => onSelect(index + 1)}
        >
          다음 미션
        </button>
        <button
          type="button"
          className="prepare-action inline-flex min-h-8 shrink-0 items-center justify-center gap-1 border border-ink bg-panel px-2.5 py-1 text-xs leading-[18px] font-semibold text-ink hover:bg-cream disabled:pointer-events-none disabled:cursor-default disabled:border-dashed disabled:border-line-strong disabled:bg-cream disabled:text-ink prepare-low:min-h-7 prepare-low:py-0.5 prepare-action-primary bg-coral-deep! text-white! hover:bg-ink! disabled:bg-cream! disabled:text-ink! disabled:border-line-strong!"
          onClick={onClose}
        >
          목록으로
        </button>
      </footer>
    </dialog>
  );
}
