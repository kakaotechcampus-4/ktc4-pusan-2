import { useMutation, useQuery } from '@tanstack/react-query';
import { apiRequest, postJson } from './client';
import type { PrepareResponse } from '@/types/api';
import type { CalibrationRequest, TakeCreateRequest, TakeCreated } from '@/types/take';

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
 * 발표 전 점검 결과를 보냅니다. **요약 수치만** 갑니다 — 보정 모델은 브라우저를 떠나지 않습니다.
 * 본문은 `toCalibrationRequest` 가 만듭니다 (`features/rehearsal/lib/calibrationRequest.ts`).
 *
 * 경로는 BE 그대로입니다 — #69 부터 Take 경로는 전부 `pitch_id` 를 품습니다.
 */
export function postCalibration(pitchId: string, takeId: string, body: CalibrationRequest) {
  return postJson<unknown>(`/api/pitches/${pitchId}/takes/${takeId}/calibration`, body);
}
