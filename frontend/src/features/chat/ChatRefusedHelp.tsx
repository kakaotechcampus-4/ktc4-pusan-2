import { LockedReason } from './LockedReason';
import { REFUSED_HELP_ABILITIES, REFUSED_HELP_EXAMPLES, REFUSED_HELP_TITLE } from './refusedHelpText';

/**
 * 거절(`REFUSED`) 답 아래 안내 — 할 수 있는 일과 눌러서 보낼 수 있는 질문 예시 (12-4 F8 `REFUSED`).
 * AI 답과 섞이지 않게 말풍선 밖에 둡니다
 */

export function RefusedHelp({
  canSend,
  lockedReason,
  onAsk,
}: {
  canSend: boolean;
  lockedReason: string | null;
  onAsk: (text: string) => void;
}) {
  return (
    <div className="self-start text-xs leading-5 text-stone" data-refused-help="">
      <p className="font-bold text-ink">{REFUSED_HELP_TITLE}</p>
      <ul className="mt-1 list-inside list-disc">
        {REFUSED_HELP_ABILITIES.map((ability) => (
          <li key={ability}>{ability}</li>
        ))}
      </ul>
      <ul aria-label="질문 예시" className="mt-2 flex flex-wrap gap-1.5">
        {REFUSED_HELP_EXAMPLES.map((example) => (
          <li key={example}>
            {/* `disabled` 로 두면 초점이 body 로 빠집니다 (#818) — aria-disabled 로 두고 누름을 무시합니다 */}
            <button
              type="button"
              aria-disabled={!canSend}
              onClick={() => {
                if (canSend) onAsk(example);
              }}
              className="inline-flex min-h-11 items-center rounded-full border border-line-strong bg-cream px-3 font-bold text-ink hover:bg-greige aria-disabled:cursor-not-allowed aria-disabled:text-stone"
            >
              {example}
              <LockedReason reason={canSend ? null : lockedReason} />
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
