import type { ReactNode } from 'react';
import { Navigate } from 'react-router';
import { launchIsOpen, useLaunchStatus } from '@/shared/api/launch';

/** 세션 훅이 실행되기 전에 공개 상태를 확인한다. */
export function LaunchGate({ children }: { children: ReactNode }) {
  const launch = useLaunchStatus();
  if (!launch.required) return children;
  if (launch.isPending)
    return (
      <p role="status" className="bg-panel p-8 text-ink">
        공개 상태를 확인하고 있어요.
      </p>
    );
  // 재조회 실패는 마지막 성공 응답을 유지합니다. 일시적인 통신 오류로 진행 중인 발표를 끊지 않습니다.
  // 최초 조회 실패(data 없음)와 서버의 명시적인 비공개 응답은 계속 차단합니다.
  if (!launchIsOpen(launch.data)) return <Navigate replace to="/welcome" />;
  return children;
}
