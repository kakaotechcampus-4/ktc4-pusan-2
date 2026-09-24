import { afterAll, beforeAll, expect, it, vi } from 'vitest';
import { setupServer } from 'msw/node';
import { FRAME_SAMPLES, type TranscriptMessage } from '@/features/rehearsal/lib/sttProtocol';
import { SttSocket, type SocketLike } from '@/features/rehearsal/lib/sttSocket';
import { sttHandlers } from './stt';

/**
 * 목 핸들러와 소켓을 **진짜 WebSocket 으로** 한 번 왕복시킵니다.
 *
 * `sttSocket.test.ts` 는 가짜 소켓으로 규칙(버퍼·seq·재연결)을 봅니다. 여기서는
 * 브라우저 WebSocket 에 실제로 붙는 배선 — `onopen` 에서 auth 를 보내고 `onmessage`
 * 로 받아 파싱하는 경로 — 이 도는지를 봅니다. 목의 메시지 모양이 어긋나면
 * `npm run dev` 에서 무대의 전사 줄이 조용히 비어 있게 되는데, 그건 눈으로만 잡힙니다.
 */

const server = setupServer(...sttHandlers);

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }));
afterAll(() => server.close());

it('ready 로 열고 전사를 주고 closed 로 닫는다', async () => {
  const finals: TranscriptMessage[] = [];
  let state = '';

  const stt = new SttSocket({
    takeId: 'take-1',
    getToken: () => Promise.resolve('token'),
    createSocket: () =>
      new WebSocket('ws://localhost:8000/api/ws/takes/take-1') as unknown as SocketLike,
    onState: (next) => {
      state = next;
    },
    onTranscript: (message) => {
      if (message.is_final) finals.push(message);
    },
  });

  stt.start();
  await vi.waitFor(() => expect(state).toBe('connecting'), { timeout: 2_000 });

  // 100ms 프레임 20장 = 2초. 목은 이 주기로 확정 전사를 하나 내보냅니다
  for (let index = 0; index < 20; index++) {
    stt.sendFrame(new Int16Array(FRAME_SAMPLES).buffer, index * 100);
  }
  await vi.waitFor(() => expect(finals).toHaveLength(1), { timeout: 2_000 });

  expect(finals[0]!.text).not.toBe('');
  expect(finals[0]!.words.length).toBeGreaterThan(0);

  await stt.stop();
  expect(state).toBe('closed');
}, 10_000);
