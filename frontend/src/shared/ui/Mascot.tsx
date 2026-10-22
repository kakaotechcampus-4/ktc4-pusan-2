import type { ReactNode } from 'react';

/** 마스코트 한마디. 치치는 연습생, 피코는 교수입니다 */
export function Mascot({ who, children }: { who: 'chichi' | 'pico'; children: ReactNode }) {
  return (
    <div className="flex items-center gap-4">
      <img
        src={who === 'pico' ? '/onboarding/pico-portrait.png' : '/onboarding/chichi-mascot.png'}
        alt=""
        width={80}
        height={80}
        className="h-20 w-20 shrink-0 object-contain [image-rendering:pixelated]"
      />
      <p className="border-2 border-ink bg-panel px-3 py-2 text-sm font-bold leading-snug">
        {children}
      </p>
    </div>
  );
}
