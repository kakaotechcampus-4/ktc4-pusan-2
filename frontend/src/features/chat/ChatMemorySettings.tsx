import { useId, useState } from 'react';
import type { ProfileItem } from '@/types/chat';
import { MEMORY_INTRO_TEXT, MEMORY_TEXT, PROFILE_KEY_LABEL } from './chatMemory';
import {
  useClearMemory,
  useMemoryChanging,
  useMemorySettings,
  useRemoveMemoryItem,
  useSaveMemoryEnabled,
} from './useChatMemory';

/**
 * 기억 설정 화면 (`docs/ai/features/chat-agent.md` 12-4 F14 · L3-43) — 대화창 안에서 메시지 자리를 바꿔 그립니다
 * (문서 1-3 "팝업 컴포넌트에 기억 설정 포함").
 *
 * - 코칭 프로필 기억 켜기 · 끄기, 저장된 항목(종류 · 값)과 항목마다 [지우기], [전체 지우기].
 * - 성공 표시는 서버 응답 뒤에만 합니다. 실패하면 이전 상태를 그대로 두고 [다시 시도]를 보입니다.
 * - 바꾸는 요청이 나가 있는 동안에는 모든 버튼을 막습니다(설정 번호 `memory_version` 이 요청마다 오릅니다).
 * - 열 때마다 · 창으로 돌아올 때마다 서버 값을 다시 읽습니다(다른 탭에서 바뀜).
 *
 * 시안이 없어 지금 카드 · 버튼 모양을 그대로 씁니다.
 */

const BUTTON =
  'min-h-11 border border-line-strong px-3 text-sm font-bold hover:bg-cream aria-disabled:cursor-not-allowed aria-disabled:text-stone aria-disabled:hover:bg-transparent';

export function ChatMemorySettings({ userId, onBack }: { userId: string; onBack: () => void }) {
  const memory = useMemorySettings(userId);
  const changing = useMemoryChanging(userId);
  const toggle = useSaveMemoryEnabled(userId);
  const clear = useClearMemory(userId);
  /** 서버가 받은 뒤의 결과 한 줄 — 다음 요청을 보내면 지웁니다 */
  const [done, setDone] = useState<string | null>(null);
  const switchLabel = useId();

  const saveEnabled = (enabled: boolean) => {
    if (changing) return;
    setDone(null);
    clear.reset();
    toggle.mutate(enabled, {
      onSuccess: (data) => setDone(data.enabled ? MEMORY_TEXT.enabledOn : MEMORY_TEXT.enabledOff),
    });
  };
  const clearAll = () => {
    if (changing) return;
    setDone(null);
    toggle.reset();
    clear.mutate(undefined, { onSuccess: () => setDone(MEMORY_TEXT.cleared) });
  };

  return (
    <div className="flex flex-col gap-3 text-sm leading-6" data-memory-settings="">
      <div className="flex items-center justify-between gap-3">
        <h3 className="font-bold">기억 설정</h3>
        <button type="button" onClick={onBack} className={BUTTON}>
          돌아가기
        </button>
      </div>
      <p className="text-xs leading-5 text-stone">{MEMORY_INTRO_TEXT}</p>

      {memory.isPending && <p className="text-xs text-stone">{MEMORY_TEXT.loading}</p>}
      {!memory.data && memory.isError && (
        <div role="alert" className="text-xs leading-5 text-coral-deep">
          <p>{MEMORY_TEXT.loadFailed}</p>
          <button
            type="button"
            onClick={() => memory.refetch().catch(() => undefined)}
            className={`${BUTTON} mt-1 text-ink`}
          >
            다시 시도
          </button>
        </div>
      )}

      {memory.data && (
        <>
          <div className="flex items-center justify-between gap-3">
            <span id={switchLabel} className="font-bold">
              코칭 프로필 기억
            </span>
            {/* 서버가 돌려준 값만 보입니다 — 누르는 동안에도 앞 값 그대로 두고 "바꾸는 중" 만 붙입니다 */}
            <button
              type="button"
              role="switch"
              aria-checked={memory.data.enabled}
              aria-labelledby={switchLabel}
              aria-disabled={changing}
              onClick={() => saveEnabled(!memory.data.enabled)}
              className={BUTTON}
            >
              {memory.data.enabled ? '켜짐' : '꺼짐'}
              {toggle.isPending && <span className="ml-1 text-xs text-stone">· 바꾸는 중</span>}
            </button>
          </div>
          {toggle.isError && (
            <Failure
              text={MEMORY_TEXT.enabledFailed}
              onRetry={() => saveEnabled(toggle.variables!)}
            />
          )}

          {memory.data.items.length === 0 ? (
            <p className="text-xs text-stone">{MEMORY_TEXT.empty}</p>
          ) : (
            <ul aria-label="저장된 기억" className="flex flex-col gap-2">
              {memory.data.items.map((item) => (
                <MemoryRow
                  key={item.item_id}
                  userId={userId}
                  item={item}
                  changing={changing}
                  onRemoved={() => setDone(MEMORY_TEXT.removed)}
                  onStart={() => {
                    setDone(null);
                    toggle.reset();
                    clear.reset();
                  }}
                />
              ))}
            </ul>
          )}

          <button
            type="button"
            aria-disabled={changing || memory.data.items.length === 0}
            onClick={() => {
              if (memory.data.items.length > 0) clearAll();
            }}
            className={`${BUTTON} self-start`}
          >
            {clear.isPending ? '지우는 중' : '전체 지우기'}
          </button>
          {clear.isError && <Failure text={MEMORY_TEXT.clearFailed} onRetry={clearAll} />}
        </>
      )}

      {/* 성공은 서버 응답 뒤에만 알립니다 */}
      <p role="status" className="text-xs text-stone">
        {done}
      </p>
    </div>
  );
}

function MemoryRow({
  userId,
  item,
  changing,
  onStart,
  onRemoved,
}: {
  userId: string;
  item: ProfileItem;
  /** 다른 요청이 나가 있음 — 이 줄의 [지우기]도 막습니다 */
  changing: boolean;
  onStart: () => void;
  onRemoved: () => void;
}) {
  const { remove, busy, failed } = useRemoveMemoryItem(userId, item.item_id, onRemoved);
  const press = () => {
    if (changing) return;
    onStart();
    remove();
  };
  return (
    <li className="rounded-lg border border-line-strong px-3 py-2" data-memory-row={item.item_id}>
      <div className="flex items-center justify-between gap-3">
        <p className="min-w-0 break-words">
          <span className="block text-xs text-stone">{PROFILE_KEY_LABEL[item.key]}</span>
          {item.value}
        </p>
        <button
          type="button"
          aria-disabled={changing}
          aria-label={`${PROFILE_KEY_LABEL[item.key]} 지우기`}
          onClick={press}
          className={`${BUTTON} shrink-0`}
        >
          {busy ? '지우는 중' : '지우기'}
        </button>
      </div>
      {failed && <Failure text={MEMORY_TEXT.clearFailed} onRetry={press} />}
    </li>
  );
}

function Failure({ text, onRetry }: { text: string; onRetry: () => void }) {
  return (
    <div role="alert" className="text-xs leading-5 text-coral-deep">
      <p>{text}</p>
      <button type="button" onClick={onRetry} className={`${BUTTON} mt-1 text-ink`}>
        다시 시도
      </button>
    </div>
  );
}
