import { useMutation } from '@tanstack/react-query';
import { logout } from './tokenStore';

export function useLogout() {
  return useMutation({
    mutationFn: logout,
    // 화면을 새로 엽니다. 라우터 이동 뒤에 세션을 비우면, 이동이 그려지기 전에 RequireSession 이
    // 빈 세션을 보고 /login?next= 로 보냅니다 (실서버 공용 헤더, #240 리뷰). 새로 열면 메모리의
    // 토큰과 쿼리 캐시도 함께 사라집니다
    onSuccess: () => window.location.replace('/welcome'),
  });
}
