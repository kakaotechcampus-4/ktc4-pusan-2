import { postJson } from './client';
import type { StandardTextRequest, StandardsPosted } from '@/types/standards';

/** 자연어로 적은 평가기준을 서버가 항목으로 나누고 저장합니다. 결과는 `pitch_id` 키 안에 옵니다 */
export function parseStandards(pitchId: string, text: string) {
  const body: StandardTextRequest = { standard_text: text };
  return postJson<StandardsPosted>(`/api/pitches/add/${pitchId}/standards`, body);
}
