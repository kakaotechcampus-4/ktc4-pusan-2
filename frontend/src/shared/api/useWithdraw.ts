import { useMutation } from '@tanstack/react-query';
import { apiRequest } from './client';
import { clearTokens } from './tokenStore';
import { deleteLocalRecords } from '@/shared/lib/localData';
import { announceSignedOut } from '@/shared/lib/sessionSignal';

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
    // useLogout 과 같습니다 — 화면을 새로 열어야 /login 으로 튕기지 않습니다.
    // 새로 열기 전에 알립니다 — 탈퇴한 계정의 대화 사본을 지우고 다른 탭도 비우게 합니다.
    // 지우는 쪽은 앱 진입점(`main.tsx`)에서 늘 묶여 있습니다
    onSuccess: () => {
      announceSignedOut();
      window.location.replace('/welcome');
    },
  });
}
