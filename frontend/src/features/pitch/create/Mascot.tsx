/**
 * 사이드바 아래의 마스코트와 말풍선.
 *
 * 말풍선이 없는 화면도 있습니다 — 시안에서 평가기준 작성 · 발표정보 일부는
 * 마스코트만 서 있습니다. `message` 를 비우면 풍선을 그리지 않습니다.
 *
 * ★ 시안의 전신 스프라이트가 아직 없어 포트레이트를 줄여 씁니다.
 *   파일만 바꾸면 되도록 크기는 CSS 가 정합니다.
 */
export function Mascot({ message }: { message?: string }) {
  return (
    <div className="mt-auto flex items-end gap-2 pt-4">
      <img
        src="/onboarding/chichi-mascot.png"
        alt=""
        width={72}
        height={72}
        className="h-[72px] w-[72px] shrink-0 rounded border border-line object-cover [image-rendering:pixelated]"
      />
      {message && (
        <p
          role="status"
          className="relative mb-2 rounded border-2 border-ink bg-panel px-2.5 py-1.5 text-xs font-bold leading-snug before:absolute before:-left-[7px] before:bottom-3 before:h-3 before:w-3 before:rotate-45 before:border-b-2 before:border-l-2 before:border-ink before:bg-panel"
        >
          {message}
        </p>
      )}
    </div>
  );
}
