import { useMutation } from '@tanstack/react-query';
import { apiRequest } from './client';
import type { TakeEndRequest, TakeUpdated } from '@/types/take';

/**
 * 발표 종료. Take 의 시작 · 종료 시각을 BE 에 적습니다 (`PUT /pitches/{pitch_id}/takes/{take_id}`).
 * 실패하면 P15(재시도)로 보냅니다 — 기록은 브라우저에 남아 있으므로 여기서 잃는 것은 없습니다.
 * BE 가 보낸 필드만 덮어써서 같은 값으로 다시 보내도 결과가 같습니다.
 *
 * 예전에는 `POST /takes/{id}/complete` 로 기록을 한꺼번에 보냈습니다. BE 는 그 경로를 만들지 않기로
 * 했고(#134), 종료는 WS `stop` 으로 알리고 기록은 발표 중 WS `metrics` 로 보내는 것이 계약입니다
 * (`docs/ai/architecture.md` 1-3 · 1-4). 그래서 여기서는 시각만 남깁니다.
 */
export function useEndTake(pitchId: string, takeId: string) {
  return useMutation({
    mutationFn: (body: TakeEndRequest) =>
      apiRequest<TakeUpdated>(`/api/pitches/${pitchId}/takes/${takeId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      }),
  });
}
