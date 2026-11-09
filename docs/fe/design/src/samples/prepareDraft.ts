/** PM 검토용 고정 예시. 제품 응답 타입·API·장비를 사용하지 않는다. */
export interface DraftPrepareInfo {
  missions: {
    sourceTakeNumber: number;
    items: { id: string; description: string }[];
  } | null;
}

export function prepareDraftFixture(stateId: string) {
  let audioLevel = 0.78;
  if (stateId === 'audio-low') audioLevel = 0.24;
  if (stateId === 'audio-high') audioLevel = 0.94;
  let count = 3;
  if (stateId === 'first-take' || stateId === 'empty') count = 0;
  if (stateId === 'one') count = 1;
  if (stateId === 'two') count = 2;
  if (stateId === 'four') count = 4;
  if (stateId === 'many' || stateId === 'long') count = 20;
  const disconnected = ['default', 'no-camera', 'permission-error'].includes(stateId);
  let gaze: 'IDLE' | 'DONE' | 'UNAVAILABLE' = 'DONE';
  if (disconnected || stateId === 'gaze-unavailable') gaze = 'UNAVAILABLE';
  if (stateId === 'calibration') gaze = 'IDLE';
  let voice: 'IDLE' | 'READING' | 'DONE' = 'IDLE';
  if (stateId === 'voice-reading') voice = 'READING';
  if (stateId === 'voice-done') voice = 'DONE';
  const info: DraftPrepareInfo = {
    missions:
      stateId === 'first-take'
        ? null
        : {
            sourceTakeNumber: 2,
            items: Array.from({ length: count }, (_, index) => ({
              id: `draft-mission-${index + 1}`,
              description:
                stateId === 'long'
                  ? '문장 끝에서 청중을 바라보며 자연스럽게 말해 주세요. '.repeat(30).slice(0, 500)
                  : ['문장 끝에서 청중을 바라보기', '3번 장에서 숫자를 천천히 말하기'][index % 2]!,
            })),
          },
  };
  return {
    audioLevel,
    camera: !disconnected,
    mic: !disconnected && stateId !== 'mic-error',
    gaze,
    voice,
    info,
    exam: stateId === 'exam',
    starting: stateId === 'starting',
    permissionError: stateId === 'permission-error',
    longDevices: stateId === 'long-devices',
  };
}
