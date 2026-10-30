// PM 검토용 독립 시안. 닫힌 #789의 표시 코드를 가져왔으며 제품 모듈·API·장비와 연결하지 않는다.
import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import type { DraftPrepareInfo } from '../screens/prepareDraftFixtures';

const PAGE_SIZE = 3;

export function PrepareDraftMissionCard({
  info,
  exam,
  compact = false,
}: {
  info: DraftPrepareInfo;
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
  const note = missions ? `Take ${missions.sourceTakeNumber} 리뷰에서 정한 미션이에요` : null;
  const coachNote = exam
    ? '실전 모드에서는 코치가 알려 주지 않아요. 끝난 뒤 리포트에서 결과를 확인해요.'
    : '발표 중 코치가 이 미션을 알려 드려요.';

  return (
    <section
      className={`prepare-draft-missions${compact ? ' prepare-draft-missions-compact' : ''}`}
      aria-label="이번 Take 미션"
    >
      <div className="prepare-draft-section-heading">
        <h2>이번 Take 미션</h2>
        <span>{items.length}개</span>
        {missions && (
          <span className="prepare-draft-mission-source">
            Take {missions.sourceTakeNumber}에서 정함
          </span>
        )}
        {compact && items.length > 0 && (
          <button
            type="button"
            className="prepare-draft-text-action"
            onClick={() => setSelectedId(items[0]!.id)}
          >
            전체 미션 보기
          </button>
        )}
      </div>
      {!items.length ? (
        <p className="prepare-draft-empty-mission">
          {missions
            ? `Take ${missions.sourceTakeNumber} 리뷰에서 정한 미션이 없어요. 편하게 연습해 보세요.`
            : '첫 Take라 아직 미션이 없어요. 편하게 연습해 보세요. 끝나면 다음에 해 볼 것을 알려 드려요.'}
        </p>
      ) : (
        !compact && (
          <>
            <ol start={offset + 1} className="prepare-draft-mission-list" aria-label="미션 목록">
              {items.slice(offset, offset + PAGE_SIZE).map((mission, index) => (
                <MissionItem
                  key={mission.id}
                  description={mission.description}
                  number={offset + index + 1}
                  onRead={() => setSelectedId(mission.id)}
                />
              ))}
            </ol>
            <div className="prepare-draft-mission-footer">
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
                    className="prepare-draft-action"
                    aria-label="이전 미션 페이지"
                    disabled={page === 0}
                    onClick={() => setPage(page - 1)}
                  >
                    이전
                  </button>
                  <button
                    type="button"
                    className="prepare-draft-action"
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
        row.querySelector<HTMLElement>('.prepare-draft-mission-number')?.offsetWidth ?? 0;
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
    <li ref={rowRef}>
      <span className="prepare-draft-mission-number" aria-hidden="true">
        {number}
      </span>
      <p ref={textRef}>{description}</p>
      {truncated && (
        <button
          type="button"
          className="prepare-draft-text-action"
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
  items: NonNullable<DraftPrepareInfo['missions']>['items'];
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
      className="pitch-create prepare-draft-mission-reader"
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
        <button type="button" className="prepare-draft-action" onClick={onClose}>
          닫기
        </button>
      </header>
      <p className="prepare-draft-mission-description" aria-live="polite">
        {items[index]!.description}
      </p>
      <p>{note}</p>
      <footer>
        <p>모든 미션이 이번 Take에 적용돼요.</p>
        <button
          type="button"
          className="prepare-draft-action"
          disabled={index === 0}
          onClick={() => onSelect(index - 1)}
        >
          이전 미션
        </button>
        <button
          type="button"
          className="prepare-draft-action"
          disabled={index === items.length - 1}
          onClick={() => onSelect(index + 1)}
        >
          다음 미션
        </button>
        <button
          type="button"
          className="prepare-draft-action prepare-draft-action-primary"
          onClick={onClose}
        >
          목록으로
        </button>
      </footer>
    </dialog>
  );
}
