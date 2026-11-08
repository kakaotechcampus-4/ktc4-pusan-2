import type { RefObject } from 'react';
import type { ChatScope } from './chatStore';
import { MemoryOffBadge, MemoryOffSwitch } from './ChatMemoryParts';
import { ScopeChip } from './ChatPanelParts';

export type PanelView = 'messages' | 'list' | 'memory';

type ActionIcon = 'new' | 'history' | 'memory' | 'close';

function ActionIconGraphic({ kind }: { kind: ActionIcon }) {
  const shared = {
    width: 19,
    height: 19,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.8,
    strokeLinecap: 'round' as const,
    strokeLinejoin: 'round' as const,
    'aria-hidden': true as const,
  };

  if (kind === 'new')
    return (
      <svg {...shared}>
        <path d="M12 5v14M5 12h14" />
      </svg>
    );
  if (kind === 'history')
    return (
      <svg {...shared}>
        <path d="M3 11a9 9 0 1 1 2.4 6.1M3 4v7h7" />
        <path d="M12 7v5l3 2" />
      </svg>
    );
  if (kind === 'memory')
    return (
      <svg {...shared}>
        <path d="M12 3v2M12 19v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M3 12h2M19 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4" />
        <circle cx="12" cy="12" r="5" />
        <circle cx="12" cy="12" r="1.5" />
      </svg>
    );
  return (
    <svg {...shared}>
      <path d="M18 6 6 18M6 6l12 12" />
    </svg>
  );
}

const HEADER_ACTION =
  'grid h-11 w-10 shrink-0 place-items-center rounded-lg text-ink transition-colors hover:bg-cream focus-visible:outline-2 focus-visible:outline-offset-0 focus-visible:outline-coral aria-pressed:bg-cream';

/**
 * 새미나이 패널의 공통 헤더.
 * 새 대화·지난 대화·기억 설정·닫기는 아이콘으로 압축하지만,
 * 각각 명확한 접근성 이름과 키보드 포커스 영역을 유지합니다.
 */
export function ChatPanelHeader({
  titleId,
  userId,
  view,
  memoryButtonRef,
  onNew,
  onToggleView,
  onClose,
  onScopeChange,
}: {
  titleId: string;
  userId: string;
  view: PanelView;
  memoryButtonRef: RefObject<HTMLButtonElement>;
  onNew: () => void;
  onToggleView: (view: PanelView) => void;
  onClose: () => void;
  onScopeChange: (scope: ChatScope) => void;
}) {
  return (
    <header className="shrink-0 border-b border-line-strong bg-panel px-4 pb-3 pt-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2.5">
          <div className="h-10 w-10 shrink-0 overflow-hidden rounded-xl border border-line-strong bg-cream">
            <img
              src="/semini.png"
              alt=""
              className="h-full w-full object-cover [image-rendering:pixelated]"
            />
          </div>
          <div className="min-w-0">
            <h2 id={titleId} className="truncate text-base font-bold leading-5">
              새미나이
            </h2>
            <p className="truncate text-[11px] text-stone">Semini · AI 비서 앱</p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          <button
            type="button"
            aria-label="새 대화"
            title="새 대화"
            onClick={onNew}
            className={HEADER_ACTION}
          >
            <ActionIconGraphic kind="new" />
          </button>
          <button
            type="button"
            aria-label="대화 목록"
            title="대화 목록"
            aria-pressed={view === 'list'}
            onClick={() => onToggleView('list')}
            className={HEADER_ACTION}
          >
            <ActionIconGraphic kind="history" />
          </button>
          <button
            ref={memoryButtonRef}
            type="button"
            aria-label="기억 설정"
            title="기억 설정"
            aria-pressed={view === 'memory'}
            onClick={() => onToggleView('memory')}
            className={HEADER_ACTION}
          >
            <ActionIconGraphic kind="memory" />
          </button>
          <button
            type="button"
            aria-label="닫기"
            title="닫기"
            onClick={onClose}
            className={HEADER_ACTION}
          >
            <ActionIconGraphic kind="close" />
          </button>
        </div>
      </div>
      <div className="mt-2 flex min-h-9 flex-wrap items-center justify-between gap-x-2 gap-y-1">
        <div className="flex min-w-0 items-center gap-2">
          <ScopeChip onChange={onScopeChange} />
          <MemoryOffBadge />
        </div>
        {view === 'messages' && <MemoryOffSwitch userId={userId} />}
      </div>
    </header>
  );
}
