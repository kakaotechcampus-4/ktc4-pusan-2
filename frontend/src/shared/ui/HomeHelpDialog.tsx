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

/** 설명용 목업의 번호는 실제 리허설 화면의 위치를 기준으로 합니다. */
const stageGuide = [
  ['슬라이드', '가운데에서 현재 슬라이드를 보고, 오른쪽에서 다음 슬라이드를 미리 확인해요.'],
  ['카메라', '오른쪽 위의 4:3 화면에서 자기 모습을 확인해요.'],
  ['마이크', '카메라 아래의 음량 막대에서 입력 상태를 확인해요.'],
  ['피코 코치', '슬라이드 아래에 코칭 메시지가 나타나요. 실전 모드에서는 코칭이 꺼져요.'],
  ['대본', '키 안내 아래에 선택한 표시 모드로 대본이 나와요. 실전 모드에서는 숨겨져요.'],
  ['이동 방법', '←·→ 슬라이드 이동, Space 다음 장, 대본 표시 중에는 ↑·↓로 스크롤해요.'],
  [
    '발표 끝내기',
    '상단 오른쪽의 종료 버튼은 마지막 장에서만 사용할 수 있어요. 한 번 더 확인하고 끝내며, 말이 오래 없으면 자동으로 끝날 수도 있어요.',
  ],
  [
    '시선 테두리',
    '슬라이드와 카메라 영역을 감싼 테두리 색으로 청중·화면·대본·판정 불가를 구분해요.',
  ],
] as const;

/** 채팅 패널의 실제 명칭·동작을 간략화한 정적 시각 안내. */
const chatGuide = [
  [
    '채팅 열기',
    '홈·연습 기록 등 지원되는 화면의 왼쪽 아래에서 새미나이 열기를 누르세요. 실제 발표 연습 중에는 버튼이 보이지 않아요.',
  ],
  [
    '대화 관리',
    '상단에서 새 대화를 시작하거나 대화 목록·기억 설정을 열고, 닫기 버튼으로 패널을 닫을 수 있어요.',
  ],
  [
    '대화 범위',
    '범위 칩을 눌러 전체 또는 특정 피치를 선택하면 관련 기록을 중심으로 질문할 수 있어요.',
  ],
  ['기억 없이 대화', '새 대화의 첫 메시지를 보내기 전에 기억 없이 대화하기를 선택할 수 있어요.'],
  ['질문 예시', '할 수 있는 일과 질문 예시를 확인하고, 예시 버튼을 눌러 바로 질문할 수 있어요.'],
  [
    '메시지 입력',
    '아래 입력칸에서 직접 질문을 작성해 보낼 수 있어요. 실제 답변 범위는 준비된 자료와 서비스 상태에 따라 달라져요.',
  ],
] as const;

function HelpIllustration({ src, alt, caption }: { src: string; alt: string; caption: string }) {
  return (
    <figure className="min-w-0">
      <img
        src={src}
        alt={alt}
        width={1040}
        height={600}
        loading="lazy"
        className="block h-auto w-full border border-line-strong bg-white"
      />
      <figcaption className="mt-2 flex flex-wrap items-center justify-between gap-x-4 gap-y-1 text-xs leading-5 text-stone">
        <span>{caption} · 개인정보가 없는 설명용 목업</span>
        <a
          href={src}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`${caption} 이미지 크게 보기 (새 탭)`}
          className="font-semibold text-ink underline underline-offset-2 hover:text-coral-deep"
        >
          이미지 크게 보기 (새 탭)
        </a>
      </figcaption>
    </figure>
  );
}

function RehearsalGuide() {
  return (
    <div className="mt-5 space-y-6">
      <section aria-labelledby="help-device-title">
        <h4 id="help-device-title" className="mb-3 text-sm font-bold">
          시작 전 장치 점검
        </h4>
        <HelpIllustration
          src="/help/device-check.svg"
          alt="장치 점검 화면 목업. 예시 피치 제목과 TAKE 배지, 왼쪽 카메라·오디오 입력, 오른쪽 장비 준비·이번 Take 미션·발표 중 대본 표시, 왼쪽 아래 채팅 에이전트 버튼과 오른쪽 Take 시작 버튼이 있습니다."
          caption="장치 점검 화면"
        />
        <p className="mt-2 text-sm leading-6 text-stone">
          카메라·마이크와 시선 기준을 확인해 주세요. 평소 목소리 측정은 선택 항목이에요.
        </p>
        <p className="mt-2 text-sm leading-6 text-stone">
          왼쪽 아래 채팅 버튼으로 연습 기록을 볼 수 있어요. 발표 중에는 숨겨져요.
        </p>
      </section>

      <section aria-labelledby="help-stage-title">
        <h4 id="help-stage-title" className="mb-3 text-sm font-bold">
          발표 연습 화면
        </h4>
        <HelpIllustration
          src="/help/rehearsal-stage.svg"
          alt="발표 연습 화면 목업. 1 현재 슬라이드와 다음 슬라이드, 2 오른쪽 카메라, 3 음량 막대, 4 가운데 아래 피코 코치, 5 맨 아래 대본, 6 코치 아래 키보드 이동 안내, 7 오른쪽 위 비활성 발표 끝내기 버튼, 8 시선 테두리와 범례."
          caption="실시간 코칭 모드 예시"
        />
        <ol
          className="mt-4 grid gap-3 sm:grid-cols-2"
          aria-label="발표 연습 화면의 번호별 사용방법"
        >
          {stageGuide.map(([title, description], index) => (
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
    </div>
  );
}

function ChatGuide() {
  return (
    <section aria-labelledby="help-chat-title" className="mt-5">
      <h4 id="help-chat-title" className="mb-3 text-sm font-bold">
        새미나이 채팅 사용방법
      </h4>
      <HelpIllustration
        src="/help/chat-agent.svg"
        alt="새미나이 채팅 설명용 목업. 1 왼쪽 아래 채팅 버튼, 2 상단 새 대화·대화 목록·기억 설정·닫기, 3 전체·피치 대화 범위, 4 기억 없이 대화 스위치, 5 할 수 있는 일과 질문 예시, 6 메시지 입력칸을 번호로 나타냅니다."
        caption="새미나이 채팅 화면"
      />
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

export function HomeHelpDialog({ onDismiss }: { onDismiss: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = dialogRef.current;
    const { style } = document.body;
    const previousOverflow = style.overflow;
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
      className="pitch-create fixed inset-0 m-auto h-dvh max-h-dvh w-full max-w-none overflow-y-auto overscroll-contain border-0 bg-panel p-0 text-ink backdrop:bg-ink/20 sm:h-auto sm:max-h-[85dvh] sm:max-w-4xl sm:border-2 sm:border-ink"
    >
      <header className="sticky top-0 z-10 flex items-center justify-between gap-5 border-b border-line-strong bg-panel px-4 py-4 sm:px-8 sm:py-5">
        <h2 id={titleId} className="text-xl font-bold">
          사용방법
        </h2>
        <button
          type="button"
          autoFocus
          onClick={() => dialogRef.current?.close()}
          className="shrink-0 border border-ink px-4 py-2 text-sm font-bold hover:bg-cream"
        >
          닫기
        </button>
      </header>
      <div className="min-w-0 px-4 py-6 sm:px-8 sm:py-8">
        <p className="text-sm leading-7 text-stone">
          자료를 준비하고, 연습하고, 기록을 돌아보세요.
        </p>
        <ol className="mt-7 space-y-7">
          {steps.map(([title, description], index) => (
            <li
              key={title}
              className="flex min-w-0 gap-3 border-t border-line-strong pt-5 sm:gap-4"
            >
              <span
                aria-hidden="true"
                className="shrink-0 tabular-nums text-2xl font-bold text-coral-deep"
              >
                0{index + 1}
              </span>
              <div className="min-w-0 flex-1">
                <h3 className="font-bold">
                  <span className="sr-only">{index + 1}단계 </span>
                  {title}
                </h3>
                <p className="mt-2 text-sm leading-7 text-stone">{description}</p>
                {index === 1 && <RehearsalGuide />}
                {index === 2 && <ChatGuide />}
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
