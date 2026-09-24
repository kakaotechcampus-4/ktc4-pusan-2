import { ws } from 'msw';

/**
 * 실시간 STT WebSocket 목.
 *
 * ── 왜 필요한가 ──────────────────────────────────────────────────
 *
 * BE에 `GET /api/takes/{id}`가 아직 없어서 리허설 화면 전체를 실서버로 띄울 수
 * 없습니다. 목을 켜면 화면은 뜨지만 WebSocket만 실서버로 나가 `TAKE_NOT_FOUND`로
 * 닫히고, 그러면 무대의 STT 표시를 한 번도 못 보게 됩니다.
 *
 * 그래서 목 모드에서는 여기서 가짜 전사를 흘립니다. **프로토콜 검증용이 아닙니다**
 * — 그건 실서버에 붙는 `/dev/stt`가 합니다. 이쪽은 화면이 전사를 받아 어떻게
 * 그리는지, 상태 문구가 언제 바뀌는지를 BE 없이 보기 위한 것입니다.
 */

const takeStream = ws.link('*/api/ws/takes/:takeId');

/** 100ms 프레임 기준. 5장(0.5초)마다 중간 결과, 20장(2초)마다 확정 */
const INTERIM_EVERY_FRAMES = 5;
const FINAL_EVERY_FRAMES = 20;

const SENTENCES = [
  '안녕하세요 피치코치입니다',
  '오늘 발표에서 말씀드릴 내용은 세 가지입니다',
  '먼저 저희가 풀려는 문제를 짚고 넘어가겠습니다',
  '음 그래서 이 부분이 가장 중요한 지점입니다',
  '다음 슬라이드에서 실제 화면을 보여 드리겠습니다',
];

function words(text: string, startMs: number, endMs: number) {
  const chunks = text.split(' ');
  const step = Math.max(1, Math.floor((endMs - startMs) / chunks.length));

  return chunks.map((word, index) => ({
    word,
    punctuated_word: word,
    start_ms: startMs + index * step,
    end_ms: startMs + (index + 1) * step,
    confidence: 0.92,
  }));
}

export const sttHandlers = [
  takeStream.addEventListener('connection', ({ client, params }) => {
    let frames = 0;
    let segment = 0;

    const status = (state: string) =>
      JSON.stringify({
        type: 'stt_status',
        state,
        stt_session_no: 0,
        frames,
        dropped_frames: 0,
        silence_ms: 0,
        lost_ms: 0,
      });

    const transcript = (isFinal: boolean, text: string, startMs: number, endMs: number) =>
      JSON.stringify({
        type: 'transcript',
        segment_id: `0-${segment}`,
        is_final: isFinal,
        speech_final: isFinal,
        start_ms: startMs,
        end_ms: endMs,
        text,
        confidence: 0.9,
        words: isFinal ? words(text, startMs, endMs) : [],
      });

    client.addEventListener('message', (event) => {
      // 오디오 프레임입니다. 서버가 하는 일(누적·전사)만 흉내 냅니다
      if (typeof event.data !== 'string') {
        frames += 1;

        const sentence = SENTENCES[segment % SENTENCES.length]!;
        const startMs = segment * FINAL_EVERY_FRAMES * 100;
        const endMs = startMs + FINAL_EVERY_FRAMES * 100;
        const spoken = frames % FINAL_EVERY_FRAMES;

        if (spoken === 0) {
          client.send(transcript(true, sentence, startMs, endMs));
          segment += 1;
          return;
        }

        if (spoken % INTERIM_EVERY_FRAMES === 0) {
          // 받아쓰는 중인 것처럼 앞에서부터 늘려 갑니다
          const shown = Math.ceil((sentence.length * spoken) / FINAL_EVERY_FRAMES);
          client.send(transcript(false, sentence.slice(0, shown), startMs, endMs));
        }
        return;
      }

      const message: unknown = JSON.parse(event.data);
      const type = (message as { type?: string }).type;

      if (type === 'auth') {
        client.send(
          JSON.stringify({
            type: 'ready',
            take_id: params.takeId,
            stt_session_no: 0,
            stt_state: 'connecting',
          }),
        );
        // 실제 서버도 Deepgram에 붙은 뒤에야 ok로 바뀝니다
        setTimeout(() => client.send(status('ok')), 400);
        return;
      }

      if (type === 'stop') {
        client.send(status('closed'));
        // 같은 틱에 닫으면 방금 넣은 메시지가 실려 나가기 전에 소켓이 사라집니다.
        // 실제 서버도 closed 를 보낸 뒤에 닫습니다 — FE 는 그걸 받고 정리합니다
        setTimeout(() => client.close(1000), 0);
      }
    });
  }),
];
