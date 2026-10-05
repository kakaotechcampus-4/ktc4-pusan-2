import { apiRequest, postJson } from './client';
import type { PitchRequest, PitchSavedResponse } from '@/types/pitch';

/** 피치를 만듭니다. 발표자료 · 대본 · 평가기준은 전부 이 응답의 pitch_id 아래에 올라갑니다 */
export function createPitch(body: PitchRequest) {
  return postJson<PitchSavedResponse>('/api/pitches/add', body);
}

/**
 * 발표정보를 고칩니다.
 *
 * ★ BE 의 `update_pitch_service` 는 지금 제목 · 시간 · 날짜만 저장하고 허용오차
 *   (`upper_deviation` · `lower_deviation`)는 저장하지 않습니다. 보내기는 하지만 반영되지 않습니다.
 */
export function updatePitch(pitchId: string, body: PitchRequest) {
  return apiRequest<PitchSavedResponse>(`/api/pitches/update/${pitchId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}
