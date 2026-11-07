import { useQuery } from '@tanstack/react-query';
import { useId, useRef, useState } from 'react';
import { fetchPitchList, pitchListKey } from '@/shared/api/home';
import type { ConversationListItem } from '@/types/chat';
import {
  conversationsKey,
  fetchConversationList,
  lastActivityText,
  scopeLabel,
} from './chatConversations';
import { scopeLockReason } from './chatLock';
import { LockedReason } from './LockedReason';
import { canChangeScope, useChatStore, type ChatScope } from './chatStore';

/**
 * 대화창 머리의 범위 칩과 대화 목록 (`docs/ai/features/chat-agent.md` 12-4 F2).
 *
 * - 칩은 "전체" 또는 "피치: 발표 이름". 누르면 내 피치 목록에서 고릅니다. 실행이 끝나기 전 · 바꾸는 중에는 막습니다.
 *   피치 목록을 읽지 못하면 지금 선택을 그대로 두고 다시 시도하게 합니다(L3-07).
 * - 대화 목록은 지난 대화의 제목 · 범위 · 마지막 시각이고, 최신이 아닌 답이 있는 대화에는 표시합니다.
 *   [새 대화]는 지금 화면 범위의 새 대화 입력 상태로 옮깁니다. 실행 중에도 옮길 수 있습니다(늦은 응답은 버립니다).
 */

const SMALL_BUTTON =
  'min-h-11 rounded-lg border border-line-strong px-3 text-xs font-bold hover:bg-cream aria-disabled:cursor-not-allowed aria-disabled:text-stone';

function usePitches(enabled: boolean) {
  return useQuery({ queryKey: pitchListKey, queryFn: fetchPitchList, enabled });
}

export function ScopeChip({ onChange }: { onChange: (scope: ChatScope) => void }) {
  const scope = useChatStore((s) => s.scope);
  const changeable = useChatStore((s) => canChangeScope(s) && s.scope !== null);
  const chipLockedReason = useChatStore(scopeLockReason);
  const [menuWanted, setMenuOpen] = useState(false);
  // 바꿀 수 없게 되면(실행이 붙음 등) 펼친 목록을 보이지 않습니다
  const menuOpen = menuWanted && changeable;
  const chipRef = useRef<HTMLButtonElement>(null);
  const menuId = useId();
  // 이름을 모르는 피치 범위(피치 화면에서 연 경우)는 목록에서 이름을 찾습니다
  const needTitle = scope !== null && scope.pitchId !== null && scope.title === null;
  const pitches = usePitches(menuOpen || needTitle);
  const titleOf = (pitchId: string) =>
    pitches.data?.pitches.find((p) => p.pitch_id === pitchId)?.pitch_title ?? null;

  const pick = (next: ChatScope) => {
    setMenuOpen(false);
    chipRef.current?.focus();
    if (next.pitchId === scope?.pitchId) return;
    onChange(next);
  };

  return (
    <div className="relative mt-1">
      <button
        ref={chipRef}
        type="button"
        data-chat-scope=""
        aria-expanded={menuOpen}
        aria-controls={menuOpen ? menuId : undefined}
        aria-disabled={!changeable}
        onClick={() => {
          if (changeable) setMenuOpen(!menuOpen);
        }}
        className="min-h-8 rounded-full border border-line-strong bg-cream px-3 text-xs font-bold aria-disabled:cursor-not-allowed aria-disabled:text-stone"
      >
        {/* 이름은 글자로 둡니다 — aria-label 을 두면 안쪽 잠긴 이유(sr-only)가 덮여 읽히지 않습니다 */}
        <span className="sr-only">범위: </span>
        {scopeLabel(scope, titleOf)}
        <LockedReason reason={chipLockedReason} />
      </button>
      {menuOpen && (
        <div
          id={menuId}
          role="group"
          aria-label="범위 고르기"
          onKeyDown={(event) => {
            // 펼친 목록만 닫습니다 — 대화창까지 닫지 않게
            if (event.key === 'Escape') {
              event.stopPropagation();
              setMenuOpen(false);
              chipRef.current?.focus();
            }
          }}
          className="absolute left-0 top-full z-10 mt-1 max-h-60 w-64 overflow-y-auto rounded-lg border-2 border-ink bg-white p-1 shadow-[3px_3px_0_var(--color-ink)]"
        >
          <ScopeOption
            label="전체"
            current={scope?.pitchId === null}
            onPick={() => pick({ pitchId: null, title: null })}
          />
          {pitches.isPending && (
            <p className="px-2 py-1 text-xs text-stone">피치 목록을 불러오고 있어요.</p>
          )}
          {pitches.isError && (
            <div role="alert" className="px-2 py-1 text-xs text-coral-deep">
              <p>피치 목록을 불러오지 못했으니 다시 시도해 주세요.</p>
              <button
                type="button"
                onClick={() => pitches.refetch().catch(() => undefined)}
                className={SMALL_BUTTON}
              >
                다시 시도
              </button>
            </div>
          )}
          {pitches.data?.pitches.map((p) => (
            <ScopeOption
              key={p.pitch_id}
              label={`피치: ${p.pitch_title}`}
              current={scope?.pitchId === p.pitch_id}
              onPick={() => pick({ pitchId: p.pitch_id, title: p.pitch_title })}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function ScopeOption({
  label,
  current,
  onPick,
}: {
  label: string;
  current: boolean;
  onPick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={current}
      onClick={onPick}
      className="block min-h-11 w-full rounded px-2 text-left text-sm hover:bg-cream aria-pressed:font-bold"
    >
      {label}
    </button>
  );
}

/** 대화 목록 — 고르면 그 대화로, [새 대화]는 지금 화면 범위의 새 대화로 */
export function ConversationList({
  userId,
  onPick,
  onNew,
}: {
  userId: string;
  onPick: (item: ConversationListItem) => void;
  onNew: () => void;
}) {
  const current = useChatStore((s) => s.conversationId);
  const list = useQuery({
    queryKey: conversationsKey(userId),
    queryFn: ({ signal }) => fetchConversationList(signal),
  });
  return (
    <div className="flex flex-col gap-2" data-conversation-list="">
      <button type="button" onClick={onNew} className={`${SMALL_BUTTON} self-start`}>
        새 대화
      </button>
      {list.isPending && <p className="text-xs text-stone">대화 목록을 불러오고 있어요.</p>}
      {list.isError && (
        <div role="alert" className="text-xs text-coral-deep">
          <p>대화 목록을 불러오지 못했으니 다시 시도해 주세요.</p>
          <button
            type="button"
            onClick={() => list.refetch().catch(() => undefined)}
            className={SMALL_BUTTON}
          >
            다시 시도
          </button>
        </div>
      )}
      {list.data && list.data.length === 0 && (
        <p className="text-xs text-stone">지난 대화가 없어요.</p>
      )}
      {list.data && list.data.length > 0 && (
        <ul aria-label="지난 대화" className="flex flex-col gap-1">
          {list.data.map((c) => (
            <li key={c.conversation_id}>
              <button
                type="button"
                aria-current={c.conversation_id === current ? 'true' : undefined}
                onClick={() => onPick(c)}
                className="block w-full rounded-lg border border-line-strong px-3 py-2 text-left text-sm hover:bg-cream aria-[current=true]:border-ink"
              >
                <span className="block truncate font-bold">{c.title || '제목 없는 대화'}</span>
                <span className="block text-xs text-stone">
                  {scopeLabel({ pitchId: c.scope_pitch_id, title: c.scope_pitch_title })} ·{' '}
                  {lastActivityText(c.last_user_activity_at)}
                  {c.stale && <span className="ml-1 font-bold text-coral-deep">· 최신 아님</span>}
                  {c.memory_off && <span className="ml-1 font-bold">· 기억 없이</span>}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
