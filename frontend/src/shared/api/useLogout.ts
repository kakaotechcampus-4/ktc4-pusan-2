import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router';
import { logout } from './tokenStore';

export function useLogout() {
  const client = useQueryClient();
  const navigate = useNavigate();

  return useMutation({
    mutationFn: logout,
    // 세션을 먼저 비우면 RequireSession 이 /login?next= 로 보냅니다. 온보딩으로 옮긴 **뒤에** 비웁니다
    onSuccess: async () => {
      await navigate('/welcome', { replace: true });
      client.clear();
      client.setQueryData(['session'], null);
    },
  });
}
