import { useMutation, useQuery } from '@tanstack/react-query';
import { apiRequest, postJson } from './client';
import type { PrepareResponse } from '@/types/api';
import type { CalibrationRequest, TakeCreateRequest, TakeCreated } from '@/types/take';

export const prepareKey = (pitchId: string) => ['prepare', pitchId] as const;

/**
 * 장치 점검 준비 정보 — 제목 · 이번 연습의 버전 · 다음 Take 번호 · 지난번 대본 표시 (BE #296).
 * 화면이 쓰는 모양으로는 `fromPrepare` 가 바꿉니다 (`features/rehearsal/prepare/prepareInfo.ts`).
 */
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
 * ★ 멱등 키는 `client_session_id` 입니다 (BE #296). 같은 값으로 다시 보내면 처음 Take 를 돌려받습니다.
 *   시작 중에는 버튼도 막아(`starting`) 한 번만 보냅니다.
 * ★ 경로 끝의 `/` 는 BE 라우트(`@router.post("/")`) 그대로입니다. 빼면 307 리다이렉트를 탑니다.
 */
export function useCreateTake() {
  return useMutation({
    mutationFn: async ({ pitchId, body }: { pitchId: string; body: TakeCreateRequest }) => {
      const res = await postJson<TakeCreated>(`/api/pitches/${pitchId}/takes/`, body);
      return { takeId: res.take_id, takeNumber: res.take_number };
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
