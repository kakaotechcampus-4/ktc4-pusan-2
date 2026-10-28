import { Link } from 'react-router';
import { FlipNumber } from './FlipNumber';
import { countdownParts, LAUNCH_AT, useLaunchStatus } from '@/shared/api/launch';

export function LaunchStart({
  className,
  children,
}: {
  className: string;
  children: React.ReactNode;
}) {
  const { isOpen } = useLaunchStatus();
  if (isOpen) {
    return (
      <Link to="/login?next=/" className={className}>
        {children}
      </Link>
    );
  }
  return (
    <button type="button" disabled className={`${className} cursor-not-allowed opacity-60`}>
      오픈 예정
    </button>
  );
}

export function LaunchCountdown() {
  const { isOpen, remaining } = useLaunchStatus();
  if (isOpen) return null;
  const dateLabel = new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    month: 'long',
    day: 'numeric',
    weekday: 'long',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  }).format(new Date(LAUNCH_AT));

  return (
    <section aria-label="서비스 공개 안내" className="mt-8 border-2 border-ink bg-white p-4 sm:p-5">
      <p className="font-bold">{dateLabel}에 만나요.</p>
      <p className="mt-1 text-xs">한국 시간 기준</p>
      <div role="timer" aria-live="off" className="mt-4 grid grid-cols-4 gap-2">
        {countdownParts(remaining).map((value, index) => (
          <div key={index} className="min-w-0 text-center">
            <FlipNumber value={value} />
            <span className="mt-3 block text-xs">{['일', '시간', '분', '초'][index]}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
