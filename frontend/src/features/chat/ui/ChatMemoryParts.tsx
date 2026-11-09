import { useEffect, useId, useState } from 'react';
import { markMemoryIntroSeen, MEMORY_INTRO_TEXT, memoryIntroSeen } from '../api/chatMemory';
import { useChatStore } from '../store/chatStore';
import { useMemorySettings } from '../store/useChatMemory';

/**
 * 코칭 프로필 기억 첫 안내 (L3-43 "첫 안내 한 번") — 처음 대화창을 열었을 때 한 번만 보입니다.
 * 보인 순간 본 것으로 적고, 이번에 그린 동안에는 그대로 둡니다. 다음에 열면 보이지 않습니다
 */
export function MemoryIntro({ userId }: { userId: string }) {
  const [show] = useState(() => !memoryIntroSeen(userId));
  useEffect(() => {
    if (show) markMemoryIntroSeen(userId);
  }, [show, userId]);
  if (!show) return null;
  return (
    <p className="self-start rounded-lg bg-cream px-3 py-2 text-xs leading-5" data-memory-intro="">
      {MEMORY_INTRO_TEXT}
    </p>
  );
}

/** FE 가 정한 문구 — 문서에 없습니다 */
export const MEMORY_OFF_LABEL = '기억 없이';
export const MEMORY_SETTING_OFF_TEXT = '기억 설정이 꺼져 있어요';

/** 대화 머리의 '기억 없이' 표시 (F15 · L3-44 "고른 대화에는 표시를 붙인다") */
export function MemoryOffBadge() {
  const memoryOff = useChatStore((s) => s.memoryOff);
  if (!memoryOff) return null;
  return (
    <span
      className="whitespace-nowrap rounded border border-line-strong px-1.5 text-xs font-bold text-stone"
      data-memory-off=""
    >
      {MEMORY_OFF_LABEL}
    </span>
  );
}

/**
 * '기억 없이' 스위치 (F15 · L3-44) — 새 대화에서 첫 메시지를 보내기 전에만 보입니다. 보낸 뒤 · 대화를 만든 뒤에는
 * 저장소가 바꾸지 않습니다(`setMemoryOff`).
 * 코칭 프로필 기억 설정이 꺼져 있으면 스위치는 꺼진 채 막습니다 — 그 대화에도 기억을 쓰지 않으니 고를 것이 없습니다.
 * 설정을 아직 못 읽었으면 막지 않습니다
 */
export function MemoryOffSwitch({ userId }: { userId: string }) {
  const editable = useChatStore((s) => s.conversationId === null && !s.pending && !s.creating);
  const memoryOff = useChatStore((s) => s.memoryOff);
  const memory = useMemorySettings(userId);
  const settingOff = memory.data?.enabled === false;
  const id = useId();

  // 고른 뒤 설정이 꺼졌으면(다른 탭 · 설정 화면) 고른 것을 풉니다 — 보이는 스위치와 보낼 값을 같게 둡니다
  useEffect(() => {
    if (settingOff && memoryOff) useChatStore.getState().setMemoryOff(false);
  }, [settingOff, memoryOff]);

  if (!editable) return null;
  return (
    <div className="mb-2 flex items-center gap-2 text-xs" data-memory-off-switch="">
      <input
        id={id}
        type="checkbox"
        role="switch"
        checked={memoryOff && !settingOff}
        disabled={settingOff}
        aria-describedby={settingOff ? `${id}-off` : undefined}
        onChange={(event) => useChatStore.getState().setMemoryOff(event.target.checked)}
        className="h-4 w-4 accent-coral-deep"
      />
      <label htmlFor={id} className="font-bold">
        {MEMORY_OFF_LABEL} 대화하기
      </label>
      {settingOff && (
        <span id={`${id}-off`} className="text-stone">
          · {MEMORY_SETTING_OFF_TEXT}
        </span>
      )}
    </div>
  );
}
