import { useEffect, useState } from 'react';

export const LAUNCH_AT = '2026-10-30T14:00:00+09:00';

export function countdownParts(remainingMs: number) {
  const seconds = Math.max(0, Math.ceil(remainingMs / 1000));
  return [
    Math.floor(seconds / 86400),
    Math.floor(seconds / 3600) % 24,
    Math.floor(seconds / 60) % 60,
    seconds % 60,
  ];
}

export function useLaunchStatus() {
  const required = import.meta.env.VITE_LAUNCH_GATE_ENABLED === 'true';
  // shortcut: 기기 시각으로 화면만 연다. 서버 공개 상태 연동이 필요해지면 교체한다.
  const [remaining, setRemaining] = useState(() => Date.parse(LAUNCH_AT) - Date.now());
  useEffect(() => {
    if (!required) return;
    const tick = () => setRemaining(Date.parse(LAUNCH_AT) - Date.now());
    const interval = window.setInterval(tick, 1000);
    document.addEventListener('visibilitychange', tick);
    window.addEventListener('focus', tick);
    return () => {
      clearInterval(interval);
      document.removeEventListener('visibilitychange', tick);
      window.removeEventListener('focus', tick);
    };
  }, [required]);
  return { isOpen: !required || remaining <= 0, remaining };
}
