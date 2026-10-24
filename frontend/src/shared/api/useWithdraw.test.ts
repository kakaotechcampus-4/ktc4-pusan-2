import { afterEach, expect, it, vi } from 'vitest';
import { withdraw } from './useWithdraw';
import { getAccessToken } from './tokenStore';

/** deleteDatabase 요청 흉내. 다음 틱에 onsuccess 또는 onblocked 를 부릅니다 */
function stubIndexedDb(outcome: 'success' | 'blocked') {
  const deleteDatabase = vi.fn(() => {
    const request: Record<string, (() => void) | null> = { onsuccess: null, onblocked: null };
    setTimeout(() => (outcome === 'success' ? request.onsuccess : request.onblocked)?.());
    return request;
  });
  vi.stubGlobal('indexedDB', { deleteDatabase });
  return deleteDatabase;
}

afterEach(() => vi.unstubAllGlobals());

it('DELETE /api/users/me 를 보내고 토큰과 브라우저 기록을 지운다', async () => {
  const fetch = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
  vi.stubGlobal('fetch', fetch);
  const deleteDatabase = stubIndexedDb('success');

  await withdraw();

  expect(fetch.mock.calls[0][0]).toMatch(/\/api\/users\/me$/);
  expect(fetch.mock.calls[0][1]).toMatchObject({ method: 'DELETE' });
  expect(getAccessToken()).toBeNull();
  expect(deleteDatabase).toHaveBeenCalledWith('pitchcoach');
});

it('브라우저 기록이 막혀도 탈퇴는 성공으로 끝낸다', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
  stubIndexedDb('blocked');
  await expect(withdraw()).resolves.toBeUndefined();
});

it('서버가 실패하면 브라우저 기록을 지우지 않는다', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue(Response.json({ code: 'FAILED' }, { status: 500 })),
  );
  const deleteDatabase = stubIndexedDb('success');
  await expect(withdraw()).rejects.toMatchObject({ status: 500 });
  expect(deleteDatabase).not.toHaveBeenCalled();
});
