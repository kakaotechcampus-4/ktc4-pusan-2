import { useState, type ReactNode } from 'react';
import { Navigate } from 'react-router';
import { isTouchOnlyDevice } from '@/features/info/unsupportedReason';

/**
 * 카메라 · 마이크로 연습하는 화면(장치 점검 · 리허설 · 실전)을 휴대폰 · 태블릿에서 막는다 (#710).
 * 홈 · 피치 · 리포트 · 마이페이지는 감싸지 않아 모바일에서도 열린다.
 *
 * 로그인보다 먼저 판단한다 — 어차피 못 할 연습 주소로 로그인부터 시키지 않으려는 것이다.
 * 기기는 화면을 여는 동안 바뀌지 않으니 처음 한 번만 본다. replace 라서 뒤로 가기가 이 주소로 되돌아오지 않는다.
 */
export function DesktopGate({ children }: { children: ReactNode }) {
  const [touchOnly] = useState(() => isTouchOnlyDevice());
  if (touchOnly) return <Navigate replace to="/unsupported" />;
  return children;
}
