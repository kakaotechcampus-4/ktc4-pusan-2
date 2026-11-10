import { QueryClient } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import type { User } from '@/shared/api/useSession';

const api = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock('@/shared/api/client', () => ({ apiRequest: api.request }));

import { ensureRawDataConsent } from './rawDataConsent';

const user = (consent: boolean): User => ({
  id: 'u1',
  email: 'test@example.com',
  name: '테스트',
  raw_data_consent: consent,
  test_consent_at: null,
  test_consent_version: null,
  tutorial_report_at: null,
});

let client: QueryClient;
beforeEach(() => {
  client = new QueryClient();
  api.request.mockReset();
});
afterEach(() => client.clear());

it('이미 켜져 있으면 요청하지 않는다', async () => {
  client.setQueryData(['session'], user(true));
  await ensureRawDataConsent(client);
  expect(api.request).not.toHaveBeenCalled();
});

it('꺼져 있으면 켜고, 서버가 돌려준 내 정보로 세션을 바꾼다', async () => {
  client.setQueryData(['session'], user(false));
  api.request.mockResolvedValue(user(true));
  await ensureRawDataConsent(client);
  expect(api.request).toHaveBeenCalledWith('/api/users/me', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ raw_data_consent: true }),
  });
  expect(client.getQueryData<User>(['session'])?.raw_data_consent).toBe(true);
});

it('켜지 못하면 던지고 세션은 그대로 둔다', async () => {
  client.setQueryData(['session'], user(false));
  api.request.mockRejectedValue(new Error('네트워크'));
  await expect(ensureRawDataConsent(client)).rejects.toThrow('네트워크');
  expect(client.getQueryData<User>(['session'])?.raw_data_consent).toBe(false);
});
