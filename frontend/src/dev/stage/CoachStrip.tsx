import type { ReactNode } from 'react';

/** 대기 문구는 알림 밖에 둔다. 서버 메시지가 사라질 때 기본 안내를 다시 낭독하지 않는다. */
export function CoachStrip({
  message,
  exam = false,
  praise = false,
  children,
}: {
  message: string | null;
  exam?: boolean;
  praise?: boolean;
  children?: ReactNode;
}) {
  const text = exam ? '' : (message ?? '');
  return (
    <div className="coach" data-empty={!text} data-tone={praise ? 'praise' : 'correct'}>
      <img className="coach-avatar" src="/onboarding/pico-bust.png" alt="" />
      <b className="coach-label">{exam ? '코치 OFF' : '피코'}</b>
      <span className="coach-text" role="status" aria-atomic="true">
        {text}
      </span>
      {!text && (
        <span className="coach-idle">
          {exam ? '실전 모드에서는 코치가 말하지 않아요' : '코치 안내가 여기에 표시돼요'}
        </span>
      )}
      {!exam && children}
    </div>
  );
}
