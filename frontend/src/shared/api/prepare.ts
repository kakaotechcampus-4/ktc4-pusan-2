import { useMutation, useQuery } from '@tanstack/react-query';
import { apiRequest, postJson } from './client';
import type { CalibrationSummary, PrepareResponse } from '@/types/api';
import type { TakeCreateRequest, TakeCreated } from '@/types/take';

export const prepareKey = (pitchId: string) => ['prepare', pitchId] as const;

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
 * ★ BE 에 멱등키가 없습니다 — 두 번 보내면 Take 가 두 개 생깁니다. 장치 점검이 시작 중에는
 *   버튼을 막아(`starting`) 한 번만 보냅니다.
 * ★ 경로 끝의 `/` 는 BE 라우트(`@router.post("/")`) 그대로입니다. 빼면 307 리다이렉트를 탑니다.
 */
export function useCreateTake() {
  return useMutation({
    mutationFn: async ({ pitchId, body }: { pitchId: string; body: TakeCreateRequest }) => {
      const res = await postJson<TakeCreated>(`/api/pitches/${pitchId}/takes/`, body);
      return { takeId: res.take_id };
    },
  });
}

/**
 * 캘리브레이션 **품질 요약만** 보냅니다.
 * 기준 벡터는 브라우저(IndexedDB)에 남고 서버로 가지 않습니다 (CLAUDE.md 1번).
 */
export function postCalibration(takeId: string, summary: CalibrationSummary) {
  return postJson<void>(`/api/takes/${takeId}/calibration`, summary);
}
