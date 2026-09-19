import { useMutation, useQuery } from '@tanstack/react-query';
import { apiRequest } from './client';
import type {
  CalibrationSummary,
  CreateTakeRequest,
  CreateTakeResponse,
  PrepareResponse,
} from '@/types/api';

export const prepareKey = (pitchId: string) => ['prepare', pitchId] as const;

/** JSON 본문을 보내는 요청. 경로는 `/api` 로 시작해야 apiRequest 가 받습니다 */
const postJson = <T>(path: string, body: unknown) =>
  apiRequest<T>(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

/** 준비 화면(P4)과 장치 점검이 같이 씁니다 — 제목·버전·Take 번호가 한 곳에서 옵니다 */
export function usePrepare(pitchId: string) {
  return useQuery({
    queryKey: prepareKey(pitchId),
    queryFn: () => apiRequest<PrepareResponse>(`/api/pitches/${pitchId}/prepare`),
    enabled: pitchId !== '',
  });
}

/**
 * ★ Take는 준비 화면의 시작 CTA에서만 생깁니다 (CLAUDE.md 8번).
 *
 * 홈이나 리포트의 'Take N 시작'에서 이걸 부르면, 준비 화면에서 이탈한 만큼
 * 빈 Take가 쌓이고 takeNumber가 실제 연습 횟수와 어긋납니다.
 *
 * 멱등키는 IndexedDB 세션 키(`clientSessionId`)입니다. 버튼을 두 번 눌러도
 * 서버는 같은 Take를 돌려줍니다.
 */
export function useCreateTake() {
  return useMutation({
    mutationFn: (body: CreateTakeRequest) => postJson<CreateTakeResponse>('/api/takes', body),
  });
}

/**
 * 캘리브레이션 **품질 요약만** 보냅니다.
 * 기준 벡터는 브라우저(IndexedDB)에 남고 서버로 가지 않습니다 (CLAUDE.md 1번).
 */
export function postCalibration(takeId: string, summary: CalibrationSummary) {
  return postJson<void>(`/api/takes/${takeId}/calibration`, summary);
}
