import { apiRequest, postJson } from './client';
import type { PitchRequest, PitchSavedResponse } from '@/types/pitch';

/** 피치를 만듭니다. 발표자료 · 대본 · 평가기준은 전부 이 응답의 pitch_id 아래에 올라갑니다 */
export function createPitch(body: PitchRequest) {
  return postJson<PitchSavedResponse>('/api/pitches/add', body);
}

/**
 * 발표정보를 고칩니다 (`PitchUpdateDTO`). BE 는 **보낸 필드만** 덮어씁니다 (`exclude_unset`).
 *
 * 화면은 발표정보를 한 번에 저장하므로 다섯 필드를 모두 보냅니다. `title` 은 BE 에서 필수이고,
 * 시간 · 허용오차는 null 을 받지 않습니다 — 그래서 빈 값을 보내지 않습니다.
 *
 * ★ 경로는 BE #70 기준입니다 (`PATCH /pitches/update/{id}`). 리뷰에서 `PATCH /pitches/{id}` 로
 *   바꾸자는 의견이 있어 다음 주에 바뀔 수 있습니다 — 그때는 이 줄만 고치면 됩니다.
 */
export function updatePitch(pitchId: string, body: PitchRequest) {
  return apiRequest<PitchSavedResponse>(`/api/pitches/update/${pitchId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
