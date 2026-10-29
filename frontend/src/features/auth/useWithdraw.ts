import { useMutation } from '@tanstack/react-query';
import { apiRequest } from '@/shared/api/client';
import { clearTokens } from '@/shared/api/tokenStore';
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
  return useMutation({
    mutationFn: withdraw,
    // useLogout 과 같습니다 — 화면을 새로 열어야 /login 으로 튕기지 않습니다
    onSuccess: () => window.location.replace('/welcome'),
  });
}
