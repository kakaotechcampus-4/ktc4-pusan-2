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

/**
 * AI 코치 흉내. 실서버는 FE `metrics` 를 받을 때마다 AI 코치를 부르고, 말을 걸 때와 상태 표시가
 * 바뀔 때만 `coach` 를 보냅니다. 여기서는 오디오 프레임 수로 시간을 세서 비슷한 간격으로 보냅니다 —
 * 화면이 한마디(8초)와 상태 표시를 어떻게 그리는지 보려는 것입니다. 판단을 흉내 내지 않습니다.
 */
const INDICATORS_EVERY_FRAMES = 50;
const INTERVENE_EVERY_FRAMES = 150;

const INDICATOR_STEPS: Record<string, string>[] = [
  {
    GAZE: 'AUDIENCE',
    SPEED: 'NORMAL',
    VOLUME: 'NORMAL',
    PAUSE: 'NORMAL',
    FILLER: 'NORMAL',
    TIME: 'ON_TRACK',
  },
  { GAZE: 'SCRIPT' },
  { SPEED: 'FAST', TIME: 'AHEAD' },
  { GAZE: 'AUDIENCE', SPEED: 'NORMAL' },
  { FILLER: 'HIGH' },
  { FILLER: 'NORMAL', TIME: 'ON_TRACK' },
];

/**
 * AI 코치 문구 템플릿 그대로입니다 (`ai/research/coach-agent/src/coach/config.py`, coach-1.2).
 * `{remaining_slides}` 같은 자리는 그럴듯한 값으로 채웠습니다. 길이 · 말투를 실제와 같게 보려는 것입니다
 */
const FEEDBACKS = [
  { area: 'GAZE', instruction: 'LOOK_AT_CAMERA', message: '대본보다 청중을 조금 더 바라보세요' },
  { area: 'SPEED', instruction: 'SLOW_DOWN', message: '문장 끝에서 한 박자 쉬고 이어가 보세요' },
  { area: 'FILLER', instruction: 'REDUCE_FILLER', message: "'음' 대신 잠시 호흡하고 이어가세요" },
  { area: 'GAZE', instruction: 'CONTINUE', message: '좋아요, 지금처럼 이어가세요' },
  {
    area: 'VOLUME',
    instruction: 'SPEAK_LOUDER',
    message: '뒷자리 청중에게 말한다고 생각하고 소리를 키워 보세요',
  },
  { area: 'TIME', instruction: 'SPEED_UP', message: '조금만 빠르게 — 남은 4장, 1분 30초' },
  {
    area: 'TIME',
    instruction: 'MOVE_ON',
    message: '이 장은 목표보다 25초 넘었어요 — 정리하고 다음 장으로',
  },
  { area: 'PAUSE', instruction: 'RESUME', message: '다음 문장으로 이어가 보세요' },
  { area: 'TIME', instruction: 'WRAP_UP', message: '남은 시간이 짧으니 결론으로 넘어가세요' },
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

        const tMs = frames * 100;
        if (frames % INDICATORS_EVERY_FRAMES === 0) {
          const step = frames / INDICATORS_EVERY_FRAMES - 1;
          const feedback =
            frames % INTERVENE_EVERY_FRAMES === 0
              ? FEEDBACKS[(frames / INTERVENE_EVERY_FRAMES - 1) % FEEDBACKS.length]!
              : null;
          client.send(
            JSON.stringify({
              type: 'coach',
              t_ms: tMs,
              action: feedback ? 'INTERVENE' : 'WAIT',
              feedback: feedback && { ...feedback, priority: 70, confidence: 0.85 },
              indicators: INDICATOR_STEPS[step % INDICATOR_STEPS.length],
            }),
          );
        }

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
