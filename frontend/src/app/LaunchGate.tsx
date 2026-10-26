import type { ReactNode } from 'react';
import { Navigate } from 'react-router';
import { launchIsOpen, useLaunchStatus } from '@/shared/api/launch';

/** 세션 훅이 실행되기 전에 공개 상태를 확인한다. */
export function LaunchGate({ children }: { children: ReactNode }) {
  const launch = useLaunchStatus();
  if (launch.isPending)
    return (
      <p role="status" className="bg-panel p-8 text-ink">
        공개 상태를 확인하고 있어요.
      </p>
    );
  if (launch.isError || !launchIsOpen(launch.data)) return <Navigate replace to="/welcome" />;
  return children;
}
