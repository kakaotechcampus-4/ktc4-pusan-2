import { useQuery } from '@tanstack/react-query';
import { apiRequest } from './client';
import type { PitchListResponse } from '@/types/home';

export const pitchesKey = ['pitches'] as const;
export const pitchListKey = [...pitchesKey, 'list'] as const;

export function fetchPitchList() {
  return apiRequest<PitchListResponse>('/api/pitches/');
}

export function usePitchList() {
  return useQuery({
    queryKey: pitchListKey,
    queryFn: fetchPitchList,
    refetchOnMount: 'always',
  });
}
