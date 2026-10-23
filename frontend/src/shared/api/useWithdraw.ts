import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router';
import { apiRequest } from './client';
import { clearTokens } from './tokenStore';
import { deleteLocalRecords } from '@/shared/lib/localData';

/**
 * 회원 탈퇴 (#288). 서버가 계정과 그 아래 데이터를 바로 지우고 세션 쿠키도 지웁니다.
 * 브라우저 기록은 서버가 못 지우므로 여기서 지웁니다. 막혀도 탈퇴는 이미 끝났으니 넘어갑니다
 * (약관 6항이 사이트 데이터 삭제를 안내합니다).
 */
export async function withdraw(): Promise<void> {
  await apiRequest<void>('/api/users/me', { method: 'DELETE' });
  clearTokens();
  await deleteLocalRecords().catch(() => undefined);
}

export function useWithdraw() {
  const client = useQueryClient();
  const navigate = useNavigate();

  return useMutation({
    mutationFn: withdraw,
    onSuccess: () => {
      navigate('/welcome', { replace: true });
      client.clear();
      client.setQueryData(['session'], null);
    },
  });
}
