import type { ReactNode } from 'react';
import { Navigate } from 'react-router';
import { useLaunchStatus } from '@/shared/api/launch';

/** 세션 훅이 실행되기 전에 공개 예정 시각을 확인한다. */
export function LaunchGate({ children }: { children: ReactNode }) {
  const { isOpen } = useLaunchStatus();
  if (!isOpen) return <Navigate replace to="/welcome" />;
  return children;
}
