import { useCallback } from 'react';
import { useQueryClient, type QueryClient } from '@tanstack/react-query';

import { apiRequest } from '@/shared/api/client';
import type { User } from '@/shared/api/useSession';

/**
 * 테스트 기간에는 원자료 사용 동의(#361)를 켠 채로 연습합니다 (#1208).
 *
 * BE 는 Take 가 시작될 때 사용자의 `raw_data_consent` 를 그 Take 에 고정합니다. 그래서 Take 를 만들기 전에
 * 서버 값이 켜져 있는지 보고, 꺼져 있으면 켠 뒤에 돌아옵니다. 켜지 못하면 던지고, 시작하는 쪽은 Take 를
 * 만들지 않습니다 — 다시 누르면 다시 시도합니다.
 *
 * 장치 점검의 시작 버튼에서만 부릅니다. 그 화면은 개인정보 안내에 동의한 뒤에만 쓸 수 있어서,
 * 동의 전에 이 요청이 나가지 않습니다.
 * 테스트 기간이 끝나면 이 함수와 마이페이지 체크박스 잠금을 함께 걷어 냅니다.
 */
export async function ensureRawDataConsent(client: QueryClient): Promise<void> {
  if (client.getQueryData<User | null>(['session'])?.raw_data_consent) return;

  const me = await apiRequest<User>('/api/users/me', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ raw_data_consent: true }),
  });
  client.setQueryData(['session'], me);
}

/** 시작 버튼이 부를 `ensureRawDataConsent` */
export function useEnsureRawDataConsent(): () => Promise<void> {
  const client = useQueryClient();
  return useCallback(() => ensureRawDataConsent(client), [client]);
}

export const RAW_DATA_CONSENT_FAILED = '연습 기록 사용 동의를 켜지 못해 연습을 시작하지 않았어요.';
