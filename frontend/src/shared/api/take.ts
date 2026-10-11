import { useMutation } from '@tanstack/react-query';
import { postJson } from './client';
import type { CompleteRequest } from '@/types/api';

/**
 * 발표 종료. 실패하면 P15(재시도)로 보냅니다 —
 * 기록은 브라우저에 남아 있으므로 여기서 잃는 것은 없습니다.
 * `clientSessionId`가 멱등키라 같은 값으로 다시 보내도 Take가 늘지 않습니다.
 *
 * ★ BE 에는 이 경로가 없습니다. BE 는 `PUT /pitches/{pitch_id}/takes/{take_id}`
 *   (`started_at` · `ended_at` · `event_logs`)만 받습니다. 시선 · 슬라이드 기록을 어디에 담을지
 *   BE 와 정한 뒤 맞춥니다 — 그때까지 실서버에서는 종료 전송이 실패하고 재시도 화면으로 갑니다.
 */
export function useCompleteTake(takeId: string) {
  return useMutation({
    mutationFn: (body: CompleteRequest) =>
      postJson<{ takeId: string; status: string }>(`/api/takes/${takeId}/complete`, body),
  });
}
