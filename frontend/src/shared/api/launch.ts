import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { apiBase } from './tokenStore';

export const LAUNCH_AT = '2026-10-30T14:00:00+09:00';

// BE 확인 전 임시 계약. docs/fe/launch-countdown.md 참고.
export type LaunchStatus = { server_now: string; launch_at: string; is_open: boolean };

export function parseLaunchStatus(body: unknown): LaunchStatus {
  if (
    !body ||
    typeof body !== 'object' ||
    !('server_now' in body) ||
    typeof body.server_now !== 'string' ||
    !('launch_at' in body) ||
    typeof body.launch_at !== 'string' ||
    !('is_open' in body) ||
    typeof body.is_open !== 'boolean' ||
    !Number.isFinite(Date.parse(body.server_now)) ||
    !Number.isFinite(Date.parse(body.launch_at))
  ) {
    throw new Error('Invalid launch status');
  }
  return { server_now: body.server_now, launch_at: body.launch_at, is_open: body.is_open };
}

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
  const query = useQuery({
    queryKey: ['launch-status'],
    queryFn: async ({ signal }) => {
      const response = await fetch(`${apiBase}/api/launch-status`, {
        cache: 'no-store',
        credentials: 'omit',
        signal,
      });
      if (!response.ok) throw new Error('Launch status unavailable');
      const status = parseLaunchStatus(await response.json());
      return { ...status, receivedAt: performance.now() };
    },
    retry: false,
    staleTime: 0,
    refetchInterval: 30_000,
    refetchOnWindowFocus: 'always',
  });

  const { refetch } = query;

  useEffect(() => {
    const refresh = () => {
      if (document.visibilityState === 'visible') refetch().catch(() => undefined);
    };
    document.addEventListener('visibilitychange', refresh);
    return () => document.removeEventListener('visibilitychange', refresh);
  }, [refetch]);

  return query;
}

export function launchIsOpen(data: ReturnType<typeof useLaunchStatus>['data']) {
  return !!data && data.is_open && Date.parse(data.server_now) >= Date.parse(data.launch_at);
}

export function useLaunchRemaining(data: ReturnType<typeof useLaunchStatus>['data']) {
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => {
    const tick = () => setNow(performance.now());
    tick();
    const interval = window.setInterval(tick, 1000);
    document.addEventListener('visibilitychange', tick);
    return () => {
      clearInterval(interval);
      document.removeEventListener('visibilitychange', tick);
    };
  }, []);
  return data
    ? Date.parse(data.launch_at) -
        (Date.parse(data.server_now) + Math.max(0, now - data.receivedAt))
    : 0;
}
