import { apiRequest, postJson } from './client';
import type {
  ParseRequested,
  ScriptCreateRequest,
  ScriptCreated,
  ScriptDetail,
} from '@/types/script';

/** 대본 새 버전. 202 로 바로 돌아오고, 슬라이드 분리는 `getScript` 로 폴링해 받습니다 */
export function createScript(pitchId: string, content: string) {
  const body: ScriptCreateRequest = { content };
  return postJson<ScriptCreated>(`/api/pitches/${pitchId}/scripts`, body);
}

export function getScript(pitchId: string, scriptVersionId: string) {
  return apiRequest<ScriptDetail>(`/api/pitches/${pitchId}/scripts/${scriptVersionId}`);
}

/** FAILED 인 대본을 다시 나눕니다. 원문은 BE 가 저장해 둔 것을 씁니다 */
export function reparseScript(pitchId: string, scriptVersionId: string) {
  return postJson<ParseRequested>(`/api/pitches/${pitchId}/scripts/${scriptVersionId}/parse`, {});
}
