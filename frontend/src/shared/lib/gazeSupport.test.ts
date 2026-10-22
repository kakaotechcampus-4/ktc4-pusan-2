import { afterEach, describe, expect, it, vi } from 'vitest';
import { checkSupport } from '@/vendor/gaze/engine';
import { checkGazeSupport } from './gazeSupport';

/** 브라우저 기능을 있게 · 없게 바꿔 가며 엔진의 판단과 우리 판단을 비교합니다 */
const FEATURES = {
  WebAssembly: { present: {}, absent: undefined },
  createImageBitmap: { present: () => undefined, absent: undefined },
} as const;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('시선 엔진 지원 확인 — AI 엔진 checkSupport 와 같은 조건', () => {
  const combos = [
    { WebAssembly: true, createImageBitmap: true },
    { WebAssembly: false, createImageBitmap: true },
    { WebAssembly: true, createImageBitmap: false },
    { WebAssembly: false, createImageBitmap: false },
  ];

  for (const combo of combos) {
    it(`WebAssembly ${combo.WebAssembly ? '있음' : '없음'} · createImageBitmap ${combo.createImageBitmap ? '있음' : '없음'}`, () => {
      vi.stubGlobal(
        'WebAssembly',
        combo.WebAssembly ? FEATURES.WebAssembly.present : FEATURES.WebAssembly.absent,
      );
      vi.stubGlobal(
        'createImageBitmap',
        combo.createImageBitmap
          ? FEATURES.createImageBitmap.present
          : FEATURES.createImageBitmap.absent,
      );
      // 워커 확인은 우리 몫이라 비교에서는 있다고 둡니다
      vi.stubGlobal('Worker', function Worker() {});

      const engine = checkSupport();
      const ours = checkGazeSupport();
      expect(ours.ok).toBe(engine.ok);
      expect(ours.missing).toEqual(engine.missing);
    });
  }

  it('워커가 없으면 우리가 막는다 — 엔진은 워커를 보지 않는다', () => {
    vi.stubGlobal('WebAssembly', {});
    vi.stubGlobal('createImageBitmap', () => undefined);
    vi.stubGlobal('Worker', undefined);
    expect(checkGazeSupport()).toEqual({ ok: false, missing: ['Worker'] });
  });
});
