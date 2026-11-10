import { BASE_VOICE_UNCONFIRMED_TEXT, isBaseVoiceUnconfirmed } from '../lib/sendCalibration';

/**
 * 무대 안내 칸(`stage-status`)의 한 줄. 막지 않고 알리기만 합니다 — 저장은 됐는데 응답만 잃었을 수 있습니다.
 * 실시간 코칭(`/rehearsal`)과 실전(`/exam`)이 같은 무대(`RehearsalPage`)라 둘 다 이 줄을 씁니다.
 * 새로고침해도 state 가 남아 다시 보입니다(여전히 모르는 일이라 맞습니다)
 */
export function BaseVoiceUnconfirmedNote({ state }: { state: unknown }) {
  if (!isBaseVoiceUnconfirmed(state)) return null;
  return <span role="status">{BASE_VOICE_UNCONFIRMED_TEXT}</span>;
}
