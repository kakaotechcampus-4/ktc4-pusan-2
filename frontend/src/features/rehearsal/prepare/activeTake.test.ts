import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiFailure } from '@/shared/api/tokenStore';
import type { ActiveTake } from '@/types/take';
import { activeTakeOrNone, endActiveTake } from './activeTake';

const other: ActiveTake = {
  take_id: 't-live',
  pitch_id: 'p-other',
  pitch_title: '다른 발표',
  take_number: 3,
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe('시작 전에 발표 중인 Take 묻기', () => {
  it('BE 가 알려 준 Take 를 그대로 돌려준다', async () => {
    expect(await activeTakeOrNone(() => Promise.resolve(other))).toEqual(other);
    expect(await activeTakeOrNone(() => Promise.resolve(null))).toBeNull();
  });

  /** BE 가 아직 이 API 를 열지 않았어도(404) · 네트워크가 흔들려도 지금처럼 시작합니다 */
  it('묻지 못하면 없는 것으로 보고 시작한다', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => undefined);
    expect(await activeTakeOrNone(() => Promise.reject(new ApiFailure(404, 'NOT_FOUND')))).toBe(
      null,
    );
    expect(await activeTakeOrNone(() => Promise.reject(new TypeError('Failed to fetch')))).toBe(
      null,
    );
  });
});

describe('끝내기로 고른 Take 끝내기', () => {
  it('끝내면 시작해도 된다 — 그사이 스스로 끝나 지금 상태가 와도 같다', async () => {
    const replace = vi.fn().mockResolvedValue({ status: 'ANALYZING' });
    await expect(endActiveTake(other, replace)).resolves.toBeUndefined();
    expect(replace).toHaveBeenCalledWith(other);
  });

  it('이미 지워진 Take(404)는 끝낼 것이 없어 시작해도 된다', async () => {
    const replace = vi.fn().mockRejectedValue(new ApiFailure(404, 'TAKE_NOT_FOUND'));
    await expect(endActiveTake(other, replace)).resolves.toBeUndefined();
  });

  /** 끝내지 못한 채 시작하면 새 연결이 TOO_MANY_STREAMS 로 거절됩니다 */
  it('그 밖의 실패는 시작하지 않도록 던진다', async () => {
    const server = new ApiFailure(500, 'INTERNAL_SERVER_ERROR');
    await expect(endActiveTake(other, vi.fn().mockRejectedValue(server))).rejects.toBe(server);
    const offline = new TypeError('Failed to fetch');
    await expect(endActiveTake(other, vi.fn().mockRejectedValue(offline))).rejects.toBe(offline);
  });
});
