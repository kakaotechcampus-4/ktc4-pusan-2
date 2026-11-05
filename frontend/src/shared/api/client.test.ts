import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiRequest, tokenSubject } from './client';
import { clearTokens, getAccessToken, refresh, logout } from './tokenStore';

beforeEach(() => {
  clearTokens();
  vi.stubGlobal('document', { cookie: 'csrf_token=test-csrf' });
});

function fakeResponseBody(url: string): object {
  if (url.endsWith('/refresh')) return { access_token: 'restored' };
  if (url.endsWith('/logout')) return { detail: 'done' };
  return { id: 'user', email: 'test@example.com', name: '테스트' };
}

afterEach(() => vi.unstubAllGlobals());

describe('인증 갱신', () => {
  it('새로고침 복원 후 내 정보 조회와 로그아웃을 처리한다', async () => {
    const mock = vi.fn((url: string) => Promise.resolve(Response.json(fakeResponseBody(url))));
    vi.stubGlobal('fetch', mock);

    await refresh();
    expect(await apiRequest('/api/users/me')).toMatchObject({ name: '테스트' });

    await logout();
    expect(getAccessToken()).toBeNull();
  });

  it('늦게 도착한 401은 이미 받은 토큰을 사용해 추가 갱신하지 않는다', async () => {
    let finish!: (value: Response) => void;
    const late = new Promise<Response>((resolve) => {
      finish = resolve;
    });

    const mock = vi.fn((url: string, init: RequestInit) => {
      if (url.endsWith('/refresh'))
        return Promise.resolve(Response.json({ access_token: 'fresh' }));

      if (new Headers(init.headers).has('Authorization'))
        return Promise.resolve(Response.json({ ok: true }));

      return url.endsWith('/late') ? late : Promise.resolve(new Response(null, { status: 401 }));
    });
    vi.stubGlobal('fetch', mock);

    const lateRequest = apiRequest('/api/late');
    await apiRequest('/api/first');

    finish(new Response(null, { status: 401 }));
    await lateRequest;

    expect(mock.mock.calls.filter(([url]) => url.endsWith('/refresh'))).toHaveLength(1);
  });

  it('동시 401 두 건과 진행 중 합류한 세 번째 요청은 한 번 갱신하고 새 토큰으로 재시도한다', async () => {
    let resolveRefresh!: (response: Response) => void;
    const held = new Promise<Response>((resolve) => {
      resolveRefresh = resolve;
    });

    const fetchMock = vi.fn((url: string, init: RequestInit) => {
      if (url.endsWith('/refresh')) return held;

      return Promise.resolve(
        new Headers(init.headers).get('Authorization') === 'Bearer fresh'
          ? Response.json({ ok: true })
          : new Response(null, { status: 401 }),
      );
    });
    vi.stubGlobal('fetch', fetchMock);

    const first = apiRequest('/api/a');
    const second = apiRequest('/api/b');
    await vi.waitFor(() =>
      expect(fetchMock.mock.calls.filter(([url]) => url.endsWith('/refresh'))).toHaveLength(1),
    );

    const third = apiRequest('/api/c');
    await vi.waitFor(() =>
      expect(fetchMock.mock.calls.some(([url]) => url.endsWith('/c'))).toBe(true),
    );

    resolveRefresh(Response.json({ access_token: 'fresh' }));

    expect(await Promise.all([first, second, third])).toEqual([
      { ok: true },
      { ok: true },
      { ok: true },
    ]);
    expect(fetchMock.mock.calls.filter(([url]) => url.endsWith('/refresh'))).toHaveLength(1);

    const init = fetchMock.mock.calls.find(([url]) => url.endsWith('/refresh'))![1];
    expect(init.credentials).toBe('include');
    expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('test-csrf');
  });

  it('refresh 401은 재시도하지 않고 토큰을 비운다', async () => {
    const mock = vi.fn().mockResolvedValue(new Response(null, { status: 401 }));
    vi.stubGlobal('fetch', mock);

    await expect(refresh()).rejects.toMatchObject({ status: 401 });
    expect(mock).toHaveBeenCalledTimes(1);
    expect(getAccessToken()).toBeNull();
  });

  it('쿠키가 없으면 서버 갱신 없이 비로그인으로 처리한다', async () => {
    vi.stubGlobal('document', { cookie: '' });
    const mock = vi.fn();
    vi.stubGlobal('fetch', mock);

    await expect(refresh()).rejects.toMatchObject({ status: 401 });
    expect(mock).not.toHaveBeenCalled();
  });

  it('갱신한 토큰이 다른 사용자(sub)면 다시 보내지 않는다 — 다른 탭에서 계정이 바뀐 경우 (L3-16)', async () => {
    const jwt = (sub: string) =>
      `h.${btoa(JSON.stringify({ sub })).replace(/=+$/, '').replace(/\+/g, '-').replace(/\//g, '_')}.s`;
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string) =>
        Promise.resolve(
          url.endsWith('/refresh')
            ? Response.json({ access_token: jwt('user-a') })
            : new Response(null, { status: 401 }),
        ),
      ),
    );
    await refresh();
    expect(tokenSubject(getAccessToken())).toBe('user-a');

    const sent: string[] = [];
    vi.stubGlobal(
      'fetch',
      vi.fn((url: string, init: RequestInit) => {
        if (url.endsWith('/refresh'))
          return Promise.resolve(Response.json({ access_token: jwt('user-b') }));
        sent.push(new Headers(init.headers).get('Authorization') ?? '');
        return Promise.resolve(new Response(null, { status: 401 }));
      }),
    );
    await expect(
      apiRequest('/api/chat/messages', { method: 'POST', body: '{}' }),
    ).rejects.toMatchObject({ status: 401, code: 'SESSION_CHANGED' });
    // 처음 한 번만 — B 의 토큰으로 다시 보내지 않았습니다
    expect(sent).toEqual([`Bearer ${jwt('user-a')}`]);
  });

  it('토큰에서 사용자를 읽지 못하면 null', () => {
    expect([tokenSubject(null), tokenSubject('opaque'), tokenSubject('a.!!!.b')]).toEqual([
      null,
      null,
      null,
    ]);
  });

  it('갱신 뒤에도 401이면 요청을 한 번만 재시도한다', async () => {
    const mock = vi.fn((url: string) =>
      Promise.resolve(
        url.endsWith('/refresh')
          ? Response.json({ access_token: 'fresh' })
          : new Response(null, { status: 401 }),
      ),
    );
    vi.stubGlobal('fetch', mock);

    await expect(apiRequest('/api/a')).rejects.toMatchObject({ status: 401 });
    expect(mock).toHaveBeenCalledTimes(3);
    expect(getAccessToken()).toBeNull();
  });
});
