import { postJson } from './client';
import type { StandardTextRequest, StandardTextResponse } from '@/types/standards';

/** 자연어로 적은 평가기준을 서버가 항목으로 나눠 돌려줍니다 */
export function parseStandards(pitchId: string, text: string) {
  const body: StandardTextRequest = { standard_text: text };
  return postJson<StandardTextResponse>(`/api/pitches/add/${pitchId}/standards`, body);
}
