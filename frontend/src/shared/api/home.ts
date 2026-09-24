import { useQuery } from '@tanstack/react-query';
import { apiRequest } from './client';
import type { PitchListResponse } from '@/types/home';

export function fetchHome() {
  return apiRequest<PitchListResponse>('/api/pitches/');
}

export function useHome() {
  return useQuery({
    queryKey: ['pitches', 'home'],
    queryFn: fetchHome,
  });
}
