// 제품 `frontend/src/shared/ui/HomeHelpDialog.tsx`에서 복사해 시작했다. 설계서가 관리한다.
import { useEffect, useId, useRef } from 'react';

const steps = [
  [
    '자료 준비',
    '새 피치를 만들고 발표할 PDF 슬라이드와 대본을 준비해 주세요. 연습 전에 자료와 목표 시간을 확인해 주세요.',
  ],
  [
    '장치 점검과 연습',
    '마이크와 카메라를 점검하고 브라우저의 장치 사용 권한을 허용해 주세요. 준비가 끝나면 발표 연습을 시작하세요.',
  ],
  [
    '연습 기록 확인',
    '홈에서 피치별 연습 기록을 펼쳐 발표 시간과 대본 모드, 제공된 점수를 확인할 수 있어요. 기록을 참고해 다음 연습을 준비해 보세요.',
  ],
] as const;

/** 사용방법 창. 휴대폰에서는 화면 전체, 넓은 화면에서는 가운데 창이다 */
export function HomeHelpDialog({ onDismiss }: { onDismiss: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = dialogRef.current;
    const { style } = document.body;
    const previousOverflow = style.overflow;
    const previousPadding = style.paddingRight;
    // 스크롤바가 사라진 만큼 본문을 밀어 가운데 정렬된 상단바가 옆으로 움직이지 않게 한다
    const scrollbar = window.innerWidth - document.documentElement.clientWidth;
    dialog?.showModal();
    style.overflow = 'hidden';
    if (scrollbar > 0) style.paddingRight = `${scrollbar}px`;
    return () => {
      style.overflow = previousOverflow;
      style.paddingRight = previousPadding;
    };
  }, []);

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      onClose={() => {
        if (!dialogRef.current?.open) onDismiss();
      }}
      className="pitch-create fixed inset-0 m-auto h-dvh max-h-dvh w-full max-w-none overflow-y-auto overscroll-contain border-0 bg-panel p-0 text-ink backdrop:bg-ink/20 sm:h-auto sm:max-h-[85dvh] sm:max-w-2xl sm:border-2 sm:border-ink"
    >
      <header className="sticky top-0 flex items-center justify-between gap-5 border-b border-line-strong bg-panel px-6 py-5">
        <h2 id={titleId} className="text-xl font-bold">
          사용방법
        </h2>
        <button
          type="button"
          autoFocus
          onClick={() => dialogRef.current?.close()}
          className="border border-ink px-4 py-2 text-sm font-bold hover:bg-cream"
        >
          닫기
        </button>
      </header>
      <div className="px-6 py-8 sm:px-8">
        <p className="text-sm leading-7 text-stone">
          자료를 준비하고, 연습하고, 기록을 돌아보세요.
        </p>
        <ol className="mt-7 space-y-6">
          {steps.map(([title, description], index) => (
            <li key={title} className="flex gap-4 border-t border-line-strong pt-5">
              <span aria-hidden="true" className="font-mono text-2xl font-bold text-coral-deep">
                0{index + 1}
              </span>
              <div>
                <h3 className="font-bold">{title}</h3>
                <p className="mt-2 text-sm leading-7 text-stone">{description}</p>
                {index === 1 && (
                  <div className="mt-4 space-y-3">
                    <figure className="border border-line-strong bg-cream p-3">
                      <figcaption className="mb-2 text-xs font-bold">시작 전 세팅 · 설명용 목업</figcaption>
                      <div className="grid gap-2 sm:grid-cols-[1.3fr_1fr]">
                        <div className="flex aspect-video items-center justify-center border border-ink bg-stage text-xs text-white">① 카메라 · 시선 기준</div>
                        <div className="space-y-2 text-xs">
                          <div className="border border-ink bg-white p-3">② 마이크 입력</div>
                          <div className="border border-ink bg-white p-3">③ 장치 점검</div>
                        </div>
                      </div>
                    </figure>
                    <figure className="border border-line-strong bg-cream p-3">
                      <figcaption className="mb-2 text-xs font-bold">리허설 · 설명용 목업</figcaption>
                      <div className="grid gap-2 sm:grid-cols-[1.5fr_1fr]">
                        <div className="flex aspect-video items-center justify-center border border-ink bg-white text-xs">① 슬라이드 · ← → Space</div>
                        <div className="space-y-2 text-xs"><div className="border border-ink bg-white p-2">② 카메라</div><div className="border border-ink bg-white p-2">③ 마이크</div><div className="border border-ink bg-coral-wash p-2">④ 피코 코치</div></div>
                      </div>
                      <div className="mt-2 grid grid-cols-2 gap-2 text-xs"><div className="border border-ink bg-white p-2">⑤ 대본</div><div className="border border-ink bg-white p-2">⑥ 키보드 이동 · ⑦ 종료</div></div>
                    </figure>
                    <p className="text-xs text-stone">실제 캡처가 아닌 안내용 시안입니다. 발표 중 대본 표시 모드와 종료 버튼의 위치를 확인해 주세요.</p>
                  </div>
                )}
              </div>
            </li>
          ))}
        </ol>
        <p className="mt-8 border border-line-strong bg-cream p-4 text-sm leading-7">
          데스크톱 Chrome 또는 Edge에서 연습하는 것을 권장해요. 도움말을 닫으면 보던 목록에서 이어갈
          수 있어요.
        </p>
      </div>
    </dialog>
  );
}
