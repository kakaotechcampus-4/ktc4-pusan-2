import { LockedReason } from './LockedReason';
import { ASK_HELP_ABILITIES, ASK_HELP_EXAMPLES, ASK_HELP_TITLE } from './askHelpText';

/**
 * 할 수 있는 일과 눌러서 보낼 수 있는 질문 예시 — 대화창 첫 화면과 거절(`REFUSED`) 답 아래 (12-4 F8 `REFUSED`).
 * AI 답과 섞이지 않게 말풍선 밖에 둡니다
 */

export function AskHelp({
  canSend,
  lockedReason,
  onAsk,
}: {
  canSend: boolean;
  lockedReason: string | null;
  onAsk: (text: string) => void;
}) {
  return (
    <div className="self-start text-xs leading-5 text-stone" data-ask-help="">
      <p className="font-bold text-ink">{ASK_HELP_TITLE}</p>
      <ul className="mt-1 list-inside list-disc">
        {ASK_HELP_ABILITIES.map((ability) => (
          <li key={ability}>{ability}</li>
        ))}
      </ul>
      <ul aria-label="질문 예시" className="mt-2 flex flex-wrap gap-1.5">
        {ASK_HELP_EXAMPLES.map((example) => (
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
