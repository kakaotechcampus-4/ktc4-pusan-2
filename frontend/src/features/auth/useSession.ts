import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { apiRequest } from '@/shared/api/client';
import { ApiFailure, getAccessToken, refresh, logout } from '@/shared/api/tokenStore';

export type User = { id: string; email: string; name: string };
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
