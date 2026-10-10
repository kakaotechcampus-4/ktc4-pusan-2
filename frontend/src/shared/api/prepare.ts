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
 *
 * 경로는 BE 그대로입니다 — #69 부터 Take 경로는 전부 `pitch_id` 를 품습니다.
 *
 * ★ 본문은 아직 BE 와 맞지 않습니다. BE `CalibrationDTO` 는 `face_detected` · `mic_detected` ·
 *   `base_volume` · `gaze_confidence` 를 받고, 아래 요약(`quality` · `separability` …)과 겹치는
 *   필드가 없어 지금은 기본값만 저장됩니다. 필드는 BE 와 합의한 뒤 맞춥니다.
 */
export function postCalibration(pitchId: string, takeId: string, summary: CalibrationSummary) {
  return postJson<unknown>(`/api/pitches/${pitchId}/takes/${takeId}/calibration`, summary);
}
