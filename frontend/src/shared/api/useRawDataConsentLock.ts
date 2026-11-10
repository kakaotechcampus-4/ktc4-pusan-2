import { useEffect } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { apiRequest } from '@/shared/api/client';
import type { User } from '@/shared/api/useSession';

/**
 * 테스트 기간에는 원자료 사용 동의(#361)를 항상 켜 둡니다. 서버 값이 꺼져 있으면 한 번 켜서 맞춥니다.
 * Take 는 시작할 때 사용자 값을 복사하므로, 로그인한 화면에 들어오자마자 맞춥니다.
 * 테스트 기간이 끝나면 이 훅과 마이페이지 체크박스 잠금을 함께 걷어 냅니다.
 */
export function useRawDataConsentLock(user: User | null | undefined) {
  const client = useQueryClient();
  const sync = useMutation({
    mutationFn: () =>
      apiRequest<User>('/api/users/me', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ raw_data_consent: true }),
      }),
    onSuccess: (me) => client.setQueryData(['session'], me),
  });

  const needsSync = !!user && !user.raw_data_consent;
  const { isIdle, mutate } = sync;
  useEffect(() => {
    if (needsSync && isIdle) mutate();
  }, [needsSync, isIdle, mutate]);
}
