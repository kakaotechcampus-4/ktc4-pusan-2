import { useEffect } from 'react';
import { Link } from 'react-router';
import { FlipNumber } from './FlipNumber';
import {
  countdownParts,
  LAUNCH_AT,
  launchIsOpen,
  useLaunchRemaining,
  useLaunchStatus,
} from '@/shared/api/launch';

export function LaunchStart({
  className,
  children,
}: {
  className: string;
  children: React.ReactNode;
}) {
  const launch = useLaunchStatus();
  const remaining = useLaunchRemaining(launch.data);
  if (!launch.required || (!launch.isError && launchIsOpen(launch.data))) {
    return (
      <Link to="/login?next=/" className={className}>
        {children}
      </Link>
    );
  }
  let label = '오픈 예정';
  if (launch.isPending) label = '확인 중';
  else if (launch.isError) label = '확인 필요';
  else if (launch.data && remaining <= 0) label = '오픈 준비 중';
  return (
    <button type="button" disabled className={`${className} cursor-not-allowed opacity-60`}>
      {label}
    </button>
  );
}

export function LaunchCountdown() {
  const launch = useLaunchStatus();
  const data = launch.data;
  const { refetch } = launch;
  const launchAt = data?.launch_at ?? LAUNCH_AT;
  const remaining = useLaunchRemaining(data);

  useEffect(() => {
    if (!launch.required || !data || launchIsOpen(data)) return;
    const delay =
      Date.parse(data.launch_at) -
      Date.parse(data.server_now) -
      (performance.now() - data.receivedAt);
    if (delay <= 0) return;
    const timeout = window.setTimeout(
      () => refetch().catch(() => undefined),
      Math.min(delay, 2147483647),
    );
    return () => clearTimeout(timeout);
  }, [data, refetch, launch.required]);

  if (!launch.required || (!launch.isError && launchIsOpen(data))) return null;
  const dateLabel = new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul',
    month: 'long',
    day: 'numeric',
    weekday: 'long',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  }).format(new Date(launchAt));

  return (
    <section aria-label="서비스 공개 안내" className="mt-8 border-2 border-ink bg-white p-4 sm:p-5">
      <p className="font-bold">{dateLabel}에 만나요.</p>
      <p className="mt-1 text-xs">한국 시간 기준</p>
      {launch.isPending && (
        <p role="status" className="mt-4 text-sm">
          공개 상태를 확인하고 있어요.
        </p>
      )}
      {launch.isError && (
        <div className="mt-4 text-sm" role="status">
          <p>공개 상태를 확인하지 못했어요. 다시 시도해 주세요.</p>
          <button
            type="button"
            disabled={launch.isFetching}
            className="mt-2 underline disabled:opacity-60"
            onClick={() => refetch().catch(() => undefined)}
          >
            다시 시도
          </button>
        </div>
      )}
      {!launch.isError && data && remaining > 0 && (
        <div role="timer" aria-live="off" className="mt-4 grid grid-cols-4 gap-2">
          {countdownParts(remaining).map((value, index) => (
            <div key={index} className="min-w-0 text-center">
              <FlipNumber value={value} />
              <span className="mt-3 block text-xs">{['일', '시간', '분', '초'][index]}</span>
            </div>
          ))}
        </div>
      )}
      {!launch.isError && data && remaining <= 0 && (
        <p role="status" className="mt-4 text-sm">
          조금만 기다려 주세요. 오픈 준비 중이에요.
        </p>
      )}
    </section>
  );
}
