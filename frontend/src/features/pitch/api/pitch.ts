import { apiRequest, postJson } from '@/shared/api/client';
import type { PitchDetail, PitchRequest, PitchResources, PitchSavedResponse } from '@/types/pitch';

/** 피치를 만듭니다. 발표자료 · 대본 · 평가기준은 전부 이 응답의 pitch_id 아래에 올라갑니다 */
export function createPitch(body: PitchRequest) {
  return postJson<PitchSavedResponse>('/api/pitches/add', body);
}

/**
 * 발표정보를 고칩니다 (`PATCH /pitches/update/{id}`). 본문은 만들 때와 같은 `PitchSaveRequestDTO` 이고,
 * BE 는 받은 값으로 덮어씁니다(제목 · 시간 · 허용오차 · 날짜). 화면은 발표정보를 한 번에
 * 저장하므로 다섯 필드를 모두 보냅니다.
 */
export function updatePitch(pitchId: string, body: PitchRequest) {
  return apiRequest<PitchSavedResponse>(`/api/pitches/update/${pitchId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

/** 피치의 버전 목록 — 수정 화면이 사이드바를 채울 때 씁니다 */
export function getPitchResources(pitchId: string) {
  return apiRequest<PitchResources>(`/api/pitches/${pitchId}/resources`);
}

/** 허용 오차를 포함한 발표 기본정보 — BE #570 */
export function getPitch(pitchId: string) {
  return apiRequest<PitchDetail>(`/api/pitches/${pitchId}`);
}
