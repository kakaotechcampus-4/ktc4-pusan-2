import { useQuery } from '@tanstack/react-query';

import { apiRequest } from '@/shared/api/client';
import { ApiFailure, getAccessToken, refresh } from '@/shared/api/tokenStore';

/**
 * `raw_data_consent`: 원자료 사용 동의 (#361, `docs/ai/architecture.md` 1-1). 마이페이지에서 바꿉니다.
 * `test_consent_*`: 사용자 테스트 참여 동의 (#776). 동의하지 않았으면 둘 다 null 이고, 철회는 아직 없습니다
 */
export type User = {
  id: string;
  email: string;
  name: string;
  raw_data_consent: boolean;
  test_consent_at: string | null;
  test_consent_version: number | null;
};

export { useLogout } from '@/shared/api/useLogout';

export function safeDestination(value: string | null) {
  if (
    !value ||
    !value.startsWith('/') ||
    value.startsWith('//') ||
    /[\\%\r\n]/.test(value) ||
    value.includes('://') ||
    value.split(/[?#]/)[0] === '/login'
  )
    return '/';

  return value;
}

export function useSession() {
  return useQuery({
    queryKey: ['session'],
    queryFn: async (): Promise<User | null> => {
      try {
        if (!getAccessToken()) await refresh();

        return await apiRequest<User>('/api/users/me');
      } catch (error) {
        if (error instanceof ApiFailure && error.status === 401) return null;

        throw error;
      }
    },
    retry: false,
    staleTime: 0,
  });
}
