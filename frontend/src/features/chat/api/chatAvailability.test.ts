import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiFailure } from '@/shared/api/tokenStore';
import { AVAILABILITY_TIMEOUT_MS, fetchChatAvailability } from './chatAvailability';

const reply = vi.hoisted(() => ({
  next: (_signal: AbortSignal | undefined): Promise<unknown> => Promise.resolve({ enabled: true }),
}));
vi.mock('@/shared/api/client', async (original) => ({
  ...(await original<typeof import('@/shared/api/client')>()),
  apiRequest: (path: string, init: RequestInit = {}) => {
    if (path !== '/api/chat/availability') return Promise.reject(new Error(path));
    return reply.next(init.signal ?? undefined);
  },
}));

afterEach(() => {
  vi.useRealTimers();
});

describe('사용 가능 여부 조회 (12-4 F1 · L3-02)', () => {
  it('켜짐 · 꺼짐은 서버의 enabled 그대로', async () => {
    reply.next = () => Promise.resolve({ enabled: true });
    await expect(fetchChatAvailability()).resolves.toBe(true);
    reply.next = () => Promise.resolve({ enabled: false });
    await expect(fetchChatAvailability()).resolves.toBe(false);
  });

  it('AGENT_DISABLED 만 꺼짐이고, 그 밖의 실패 · 모양 틀림은 실패로 던진다', async () => {
    reply.next = () => Promise.reject(new ApiFailure(503, 'AGENT_DISABLED'));
    await expect(fetchChatAvailability()).resolves.toBe(false);

    reply.next = () => Promise.reject(new ApiFailure(503, 'SERVICE_UNAVAILABLE'));
    await expect(fetchChatAvailability()).rejects.toBeInstanceOf(ApiFailure);
    reply.next = () => Promise.resolve({ enabled: 'yes' });
    await expect(fetchChatAvailability()).rejects.toThrow();
    reply.next = () => Promise.resolve(null);
    await expect(fetchChatAvailability()).rejects.toThrow();
  });

  it('5초 안에 답이 없으면 끊고 실패로 던진다', async () => {
    vi.useFakeTimers();
    let aborted = false;
    reply.next = (signal) =>
      new Promise((_, reject) =>
        signal?.addEventListener('abort', () => {
          aborted = true;
          reject(new DOMException('끊김', 'AbortError'));
        }),
      );
    const result = fetchChatAvailability();
    const settled = result.catch((error: unknown) => error);
    await vi.advanceTimersByTimeAsync(AVAILABILITY_TIMEOUT_MS - 1);
    expect(aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(1);
    expect(aborted).toBe(true);
    expect(await settled).toBeInstanceOf(DOMException);
  });

  it('부른 쪽이 끊으면 함께 끊는다', async () => {
    let aborted = false;
    reply.next = (signal) =>
      new Promise((_, reject) =>
        signal?.addEventListener('abort', () => {
          aborted = true;
          reject(new DOMException('끊김', 'AbortError'));
        }),
      );
    const caller = new AbortController();
    const result = fetchChatAvailability(caller.signal).catch(() => undefined);
    caller.abort();
    await result;
    expect(aborted).toBe(true);
  });
});
