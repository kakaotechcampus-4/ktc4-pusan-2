import { useMutation } from '@tanstack/react-query';
import { apiRequest } from './client';
import type {
  ActiveTake,
  ActiveTakeResponse,
  TakeAbandoned,
  TakeEndRequest,
  TakeUpdated,
} from '@/types/take';

/**
 * 발표 종료. Take 의 종료 시각을 BE 에 적습니다 (`PUT /pitches/{pitch_id}/takes/{take_id}`).
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

/**
 * 창을 닫았다 다시 연 Take 를 실패로 끝냅니다. 창을 닫은 Take 는 이어 가지 않습니다.
 * 다시 불러도 결과가 같습니다. 다른 창이 아직 발표 중이면 BE 가 409 `TAKE_IN_PROGRESS` 로 거절합니다.
 */
export function useAbandonTake(pitchId: string, takeId: string) {
  return useMutation({
    mutationFn: () =>
      apiRequest<TakeAbandoned>(`/api/pitches/${pitchId}/takes/${takeId}/abandon`, {
        method: 'POST',
      }),
  });
}

/**
 * 어느 창 · 기기에서 발표 중인 Take. 없으면 null 입니다 (`GET /api/takes/active`, BE #718).
 * 시작을 누를 때 한 번 묻습니다 — 화면에 들어올 때 받아 두면 그사이 바뀐 것을 모릅니다
 */
export async function fetchActiveTake(): Promise<ActiveTake | null> {
  return (await apiRequest<ActiveTakeResponse>('/api/takes/active')).take;
}

/**
 * 다른 곳에서 발표 중인 Take 를 끝내고 여기서 새로 시작합니다 (`abandon?replace=true`).
 * 발표 중이어도 409 없이 실패로 끝내고, 그 창에는 BE 가 "다른 곳에서 새 연습을 시작해…" 를 보냅니다.
 * 이미 끝났으면 바꾸지 않고 지금 상태가 옵니다
 */
export function replaceTake(take: Pick<ActiveTake, 'pitch_id' | 'take_id'>) {
  return apiRequest<TakeAbandoned>(
    `/api/pitches/${take.pitch_id}/takes/${take.take_id}/abandon?replace=true`,
    { method: 'POST' },
  );
}
