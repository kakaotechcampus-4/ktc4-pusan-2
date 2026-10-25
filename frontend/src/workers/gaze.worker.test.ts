import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GazeWorkerIn, GazeWorkerOut, ZoneReference } from './gaze.contract';

/**
 * 워커를 브라우저 없이 돌립니다. `self` 를 흉내 내고 워커 파일을 불러오면 `self.onmessage` 가
 * 걸리므로, 메시지를 직접 넣고 `postMessage` 로 나온 것을 봅니다. 분류기는 기본값인 더미입니다.
 */
const sent: GazeWorkerOut[] = [];
const fakeSelf = {
  location: { search: '' },
  postMessage: (msg: GazeWorkerOut) => sent.push(msg),
  onmessage: null as ((e: MessageEvent<GazeWorkerIn>) => void) | null,
};

const send = (msg: GazeWorkerIn) =>
  fakeSelf.onmessage?.({ data: msg } as MessageEvent<GazeWorkerIn>);
const bitmap = () => ({ close: () => undefined }) as unknown as ImageBitmap;
const samples = () => sent.flatMap((m) => (m.type === 'samples' ? m.samples : []));

beforeAll(async () => {
  vi.stubGlobal('self', fakeSelf);
  await import('./gaze.worker');
});

beforeEach(async () => {
  send({ type: 'stop' });
  sent.length = 0;
  send({ type: 'init' });
  await vi.waitFor(() => expect(sent.some((m) => m.type === 'ready')).toBe(true));
  // 더미는 기준이 있어야 판단합니다. 모양만 맞으면 됩니다
  send({ type: 'calibrate', ref: { model: { kind: 'dummy' } } as unknown as ZoneReference });
  sent.length = 0;
});

/** 첫 프레임 시각부터 `untilMs` 직전까지 0.1초마다 프레임을 넣습니다 */
function frames(fromMs: number, untilMs: number) {
  for (let t = fromMs; t < untilMs; t += 100) send({ type: 'frame', bitmap: bitmap(), tMs: t });
}

describe('Take 끝 마지막 1초 조각 (flush)', () => {
  it('1초가 안 찬 마지막 조각을 끝난 시각까지의 실제 길이로 낸다', () => {
    // 첫 프레임 5000 → 1초 격자 5000 · 6000 · 7000. 7000 부터 7600 까지는 1초가 안 찹니다
    frames(5000, 7700);
    const before = samples();
    expect(before.map((s) => s.t_ms)).toEqual([5000, 6000]);

    send({ type: 'flush', tEndMs: 7600 });

    const last = samples().at(-1)!;
    expect(last.t_ms).toBe(7000);
    expect(last.duration_ms).toBe(600);
    // 마지막 조각이 먼저 오고, 그다음에 다 처리했다는 신호가 옵니다
    expect(sent.at(-1)).toEqual({ type: 'flushed' });
  });

  it('받은 프레임이 없으면 조각 없이 신호만 보낸다', () => {
    // 1초 조각은 다음 1초의 프레임이 와야 닫히므로, "남은 것이 없음"은 프레임이 하나도 없을 때뿐입니다
    send({ type: 'flush', tEndMs: 6000 });
    expect(sent).toEqual([{ type: 'flushed' }]);
  });
});
