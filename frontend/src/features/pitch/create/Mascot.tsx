/**
 * 사이드바 아래의 마스코트와 말풍선.
 *
 * 모든 생성 화면에서 현재 상태에 맞는 안내를 보여 줍니다.
 */
export function Mascot({ message }: { message: string }) {
  return (
    <div className="mt-auto flex items-end gap-2 pt-4">
      <img
        src="/onboarding/chichi-mascot.png"
        alt=""
        width={80}
        height={96}
        className="h-24 w-20 shrink-0 object-contain [image-rendering:pixelated]"
      />
      <p
        role="status"
        aria-live="polite"
        className="relative mb-2 rounded border-2 border-ink bg-panel px-2.5 py-1.5 text-xs font-bold leading-snug before:absolute before:-left-[7px] before:bottom-3 before:h-3 before:w-3 before:rotate-45 before:border-b-2 before:border-l-2 before:border-ink before:bg-panel"
      >
        {message}
      </p>
    </div>
  );
}
