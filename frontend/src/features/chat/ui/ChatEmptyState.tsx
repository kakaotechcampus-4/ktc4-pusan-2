/** 새미나이 빈 대화 안내. 추천 질문 기능은 #1093에서 별도로 관리합니다. */
export function ChatEmptyState() {
  return (
    <div
      className="flex w-full flex-1 flex-col items-center justify-center py-8 text-center"
      data-chat-empty=""
    >
      <div className="mb-3 h-16 w-16 overflow-hidden rounded-2xl border border-line-strong bg-cream">
        <img
          src="/semini-mascot.svg"
          alt=""
          className="h-full w-full object-contain [image-rendering:pixelated]"
        />
      </div>
      <p className="text-base font-bold">새미나이에게 물어보세요</p>
      <p className="mt-1 text-xs leading-5 text-stone">
        발표 기록을 분석하거나 다음 연습을 준비할 수 있어요.
      </p>
    </div>
  );
}
