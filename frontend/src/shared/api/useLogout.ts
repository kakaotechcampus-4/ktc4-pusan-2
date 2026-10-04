import { useMutation, useQueryClient } from '@tanstack/react-query';
import { logout } from './tokenStore';

export function useLogout() {
  const client = useQueryClient();

  return useMutation({
    mutationFn: logout,
    onSuccess: () => {
      client.clear();
      client.setQueryData(['session'], null);
    },
  });
}
