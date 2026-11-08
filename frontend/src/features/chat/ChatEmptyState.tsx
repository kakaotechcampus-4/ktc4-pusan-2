const SUGGESTIONS = [
  '최근 연습에서 개선할 점을 알려줘',
  '이전 연습과 달라진 점을 비교해줘',
  '지난 Take에서 군더더기가 많았던 부분을 알려줘',
] as const;

/** 빈 대화에서만 보이는 안내. 선택한 질문은 자동 전송하지 않고 입력창에 채웁니다. */
export function ChatEmptyState({ onSuggest }: { onSuggest: (text: string) => void }) {
  return (
    <div
      className="flex w-full flex-1 flex-col items-center justify-center py-8 text-center"
      data-chat-empty=""
    >
      <div className="mb-3 h-16 w-16 overflow-hidden rounded-2xl border border-line-strong bg-cream">
        <img
          src="/semini.png"
          alt=""
          className="h-full w-full object-cover [image-rendering:pixelated]"
        />
      </div>
      <p className="text-base font-bold">새미나이에게 물어보세요</p>
      <p className="mt-1 text-xs leading-5 text-stone">
        발표 기록을 분석하거나 다음 연습을 준비할 수 있어요.
      </p>
      <div className="mt-5 flex w-full max-w-80 flex-col gap-2" role="group" aria-label="추천 질문">
        {SUGGESTIONS.map((question) => (
          <button
            key={question}
            type="button"
            onClick={() => onSuggest(question)}
            className="min-h-11 rounded-xl border border-line-strong bg-white px-3 py-2.5 text-left text-xs leading-5 text-ink transition-colors hover:border-coral hover:bg-coral-wash"
          >
            <span aria-hidden="true" className="mr-2 font-bold text-coral-deep">
              ↗
            </span>
            {question}
          </button>
        ))}
      </div>
    </div>
  );
}
