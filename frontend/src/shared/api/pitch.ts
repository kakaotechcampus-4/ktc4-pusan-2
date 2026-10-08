import { apiRequest, postJson } from './client';
import type { PitchRequest, PitchSavedResponse } from '@/types/pitch';

/** 피치를 만듭니다. 발표자료 · 대본 · 평가기준은 전부 이 응답의 pitch_id 아래에 올라갑니다 */
export function createPitch(body: PitchRequest) {
  return postJson<PitchSavedResponse>('/api/pitches/add', body);
}

/**
 * 발표정보를 고칩니다 (`PATCH /pitches/update/{id}`). 본문은 만들 때와 같은 `PitchSaveRequestDTO` 이고,
 * BE 는 받은 값으로 덮어씁니다. 화면은 발표정보를 한 번에 저장하므로 다섯 필드를 모두 보냅니다.
 *
 * ★ BE `update_pitch_service` 는 지금 제목 · 시간 · 날짜만 반영하고 **허용오차는 버립니다**
 *   (200 으로 답합니다). BE 가 고칠 부분이라 FE 는 그대로 보냅니다.
 */
export function updatePitch(pitchId: string, body: PitchRequest) {
  return apiRequest<PitchSavedResponse>(`/api/pitches/update/${pitchId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
