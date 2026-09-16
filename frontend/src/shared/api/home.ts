import { useQuery } from '@tanstack/react-query';

import { apiRequest } from './client';
import type { HomeResponse } from '@/types/api';

/** 홈은 Pitch 목록의 요약만 받는다. 상세 자료와 대본은 카드에서 불러오지 않는다. */
export function useHome() {
  return useQuery({
    queryKey: ['home'],
    queryFn: () => apiRequest<HomeResponse>('/api/home'),
  });
}
