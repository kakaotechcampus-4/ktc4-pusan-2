import { postJson } from './client';
import type {
  StandardTextRequest,
  StandardsPosted,
  StandardsSaveRequest,
  StandardsSaved,
} from '@/types/standards';

/**
 * 자연어로 적은 평가기준을 서버가 항목으로 나눕니다. **저장하지 않습니다** — 사용자가 고친 뒤
 * `saveStandards` 로 확정합니다 (BE #192). 결과는 `pitch_id` 키 안에 옵니다
 */
export function parseStandards(pitchId: string, text: string) {
  const body: StandardTextRequest = { standard_text: text };
  return postJson<StandardsPosted>(`/api/pitches/add/${pitchId}/standards`, body);
}

/** 사용자가 고친 평가기준을 확정해 저장합니다. 화면에 보인 순서 그대로 보냅니다 */
export function saveStandards(pitchId: string, standards: string[]) {
  const body: StandardsSaveRequest = { standards: standards.map((standard) => ({ standard })) };
  return postJson<StandardsSaved>(`/api/pitches/${pitchId}/standards`, body);
}
