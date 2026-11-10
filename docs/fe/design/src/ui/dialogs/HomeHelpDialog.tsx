// 제품 `frontend/src/shared/ui/HomeHelpDialog.tsx`에서 복사해 시작했다. 설계서가 관리한다.
import { useEffect, useId, useRef } from 'react';
import { Link } from 'react-router';
import { publicUrl } from '@/lib/publicUrl';
import { CloseButton } from '../CloseButton';

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

/** #944의 새미나이 안내를 제품의 6가지 조작 기준으로 정리한다. */
const chatGuide = [
  [
    '채팅 열기',
    '홈·연습 기록 등 지원되는 화면의 오른쪽 아래에서 새미나이 열기를 누르면 오른쪽에 채팅 패널이 열려요. 장치 점검·발표 연습 중에는 버튼이 보이지 않아요.',
  ],
  [
    '대화 관리',
    '상단에서 새 대화를 시작하거나 대화 목록·기억 설정을 열고, 닫기 버튼으로 패널을 닫을 수 있어요.',
  ],
  [
    '대화 범위',
    '범위 칩을 눌러 전체 또는 특정 피치를 선택하면 관련 기록을 중심으로 질문할 수 있어요.',
  ],
  [
    '기억 없이 대화',
    '새 대화의 첫 메시지를 보내기 전에 기억 없이 대화하기를 선택할 수 있어요.',
  ],
  [
    '질문 예시',
    '할 수 있는 일과 질문 예시를 확인하고, 예시 버튼을 눌러 바로 질문할 수 있어요.',
  ],
  [
    '메시지 입력',
    '아래 입력칸에서 직접 질문을 작성해 보낼 수 있어요. 실제 답변 범위는 준비된 자료와 서비스 상태에 따라 달라져요.',
  ],
] as const;

function ChatGuide() {
  return (
    <section aria-labelledby="help-chat-title" className="mt-5">
      <h4 id="help-chat-title" className="mb-3 text-sm font-bold">
        새미나이 채팅 사용방법
      </h4>
      <figure className="min-w-0">
        <img
          src={publicUrl('help/chat-agent.svg')}
          alt="새미나이 채팅 설명용 목업. 1 오른쪽 아래 채팅 버튼과 오른쪽 패널, 2 상단 새 대화·대화 목록·기억 설정·닫기, 3 전체·피치 대화 범위, 4 기억 없이 대화 스위치, 5 할 수 있는 일과 질문 예시, 6 메시지 입력칸을 번호로 나타냅니다."
          width={1040}
          height={600}
          loading="lazy"
          className="block h-auto w-full border border-line-strong bg-white"
        />
        <figcaption className="mt-2 text-xs text-stone">
          개인정보가 없는 설명용 목업입니다. 실제 답변 범위는 서비스 상태에 따라 달라질 수 있어요.
        </figcaption>
      </figure>
      <ol className="mt-4 grid gap-3 sm:grid-cols-2" aria-label="새미나이 채팅의 번호별 사용방법">
        {chatGuide.map(([title, description], index) => (
          <li key={title} className="flex min-w-0 items-start gap-3">
            <span
              aria-hidden="true"
              className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-coral-deep text-sm font-bold text-white"
            >
              {index + 1}
            </span>
            <div className="min-w-0">
              <h5 className="text-sm font-bold">
                <span className="sr-only">{index + 1}번 </span>
                {title}
              </h5>
              <p className="mt-1 text-sm leading-6 text-stone">{description}</p>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}

/** 사용방법 창. 휴대폰에서는 화면 전체, 넓은 화면에서는 가운데 창이다 */
export function HomeHelpDialog({ onDismiss }: { onDismiss: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = dialogRef.current;
    const { style } = document.body;
    const previousOverflow = style.overflow;
    // 설계서 html에는 scrollbar-gutter: stable이 있어 추가 paddingRight 보정이 필요 없다.
    dialog?.showModal();
    style.overflow = 'hidden';
    return () => {
      style.overflow = previousOverflow;
    };
  }, []);

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      onClose={() => {
        if (!dialogRef.current?.open) onDismiss();
      }}
      className="pitch-create fixed inset-0 m-auto h-dvh max-h-dvh w-full max-w-none overflow-y-auto overscroll-none border-0 bg-panel p-0 text-ink backdrop:bg-ink/20 sm:h-auto sm:max-h-[85dvh] sm:max-w-4xl sm:border-2 sm:border-ink"
    >
      <header className="sticky top-0 flex items-center justify-between gap-5 border-b border-line-strong bg-panel px-6 py-5">
        <h2 id={titleId} className="text-xl font-bold">
          사용방법
        </h2>
        <CloseButton autoFocus onClick={() => dialogRef.current?.close()} />
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
                  <div className="mt-5 space-y-6">
                    <figure className="min-w-0">
                      <figcaption className="mb-3 text-sm font-bold">시작 전 장치 점검</figcaption>
                      <img
                        src={publicUrl('help/device-check.svg')}
                        alt="장치 점검 화면 목업. 예시 피치 제목과 TAKE 배지, 왼쪽 카메라·오디오 입력, 오른쪽 장비 준비(카메라·마이크·시선 기준·기준 목소리)·이번 Take 미션·발표 중 대본 표시, 오른쪽 Take 시작 버튼이 있습니다."
                        width={1040}
                        height={600}
                        loading="lazy"
                        className="block h-auto w-full border border-line-strong bg-white"
                      />
                      <p className="mt-2 text-xs text-stone">
                        카메라·마이크·시선 기준과 기준 목소리를 확인해 주세요. 기준 목소리는 시작 조건이며, 같은 환경에서 잰 값을 다시 사용하거나 안내된 대체 방법을 선택할 수 있어요.
                      </p>
                      <p className="mt-2 text-xs text-stone">
                        장치 점검 중에는 새미나이 채팅 버튼이 보이지 않아요. 홈이나 연습 기록 화면에서 사용할 수 있어요.
                      </p>
                    </figure>
                    <figure className="min-w-0">
                      <figcaption className="mb-3 text-sm font-bold">발표 연습 화면</figcaption>
                      <img
                        src={publicUrl('help/rehearsal-stage.svg')}
                        alt="발표 연습 화면 목업. 1 현재 슬라이드와 다음 슬라이드, 2 오른쪽 카메라, 3 음량 막대, 4 가운데 아래 피코 코치, 5 맨 아래 대본, 6 코치 아래 마우스·키보드 이동 안내, 7 오른쪽 위 비활성 발표 끝내기 버튼, 8 시선 테두리와 범례."
                        width={1040}
                        height={600}
                        loading="lazy"
                        className="block h-auto w-full border border-line-strong bg-white"
                      />
                      <p className="mt-2 text-xs text-stone">
                        개인정보가 없는 설명용 목업입니다. 실제 UI는 리허설 모드와 장치 상태에 따라
                        달라져요.
                      </p>
                    </figure>
                  </div>
                )}
                {index === 2 && <ChatGuide />}
              </div>
            </li>
          ))}
        </ol>
        <div className="mt-8 border-2 border-ink bg-coral-wash p-4">
          <p className="font-bold">직접 연습하며 익히고 싶나요?</p>
          <p className="mt-2 text-sm leading-7">준비된 PDF와 대본으로 튜토리얼을 진행해 보세요.</p>
          <Link
            to="/pitch/tutorial"
            onClick={() => dialogRef.current?.close()}
            className="mt-3 inline-flex border border-ink bg-panel px-4 py-2 text-sm font-bold hover:bg-cream"
          >
            튜토리얼 해 보기 →
          </Link>
        </div>
        <p className="mt-8 border border-line-strong bg-cream p-4 text-sm leading-7">
          데스크톱 최신 Google Chrome에서 연습하는 것을 권장해요. Edge에서는 시선 추적이 정상적으로
          진행되지 않을 수 있어요. 도움말을 닫으면 보던 목록에서 이어갈 수 있어요.
        </p>
      </div>
    </dialog>
  );
}
