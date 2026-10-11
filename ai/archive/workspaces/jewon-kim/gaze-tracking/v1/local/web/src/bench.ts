/**
 * Bare benchmark page (no UI): the demo worker on a fake or real camera, for
 * per-frame cost.  Driven by scripts/bench.mjs.
 */
import type { FromWorker } from './worker/protocol';
const out = {
  ready: false,
  isolated: false,
  ms: [] as number[],
  det: [] as number[],
  done: false,
  error: '',
};
(window as unknown as { __bench: typeof out }).__bench = out;
const worker = new Worker(new URL('./worker/gaze.worker.ts', import.meta.url), { type: 'module' });
let busy = false;
worker.onmessage = (e: MessageEvent<FromWorker>) => {
  const m = e.data;
  if (m.type === 'ready') {
    out.ready = true;
    out.isolated = m.isolated;
    start().catch((x) => (out.error = String(x)));
  }
  if (m.type === 'failed') out.error = m.message;
  if (m.type === 'frame') {
    busy = false;
    out.ms.push(m.frame.processMs);
    out.det.push(m.frame.detectMs);
    if (out.ms.length >= 80) out.done = true;
  }
};
worker.postMessage({
  type: 'init',
  assetDir: '/models/',
  delegate: (new URLSearchParams(location.search).get('delegate') ?? 'CPU') as 'CPU' | 'GPU',
});
async function start() {
  const v = document.getElementById('v') as HTMLVideoElement;
  v.srcObject = await navigator.mediaDevices.getUserMedia({ video: { width: 1280, height: 720 } });
  await v.play();
  const t0 = performance.now();
  setInterval(async () => {
    if (busy || out.done) return;
    busy = true;
    const bitmap = await createImageBitmap(v);
    worker.postMessage(
      { type: 'frame', bitmap, tMs: Math.round(performance.now() - t0), mode: 'preview' },
      [bitmap],
    );
  }, 125);
}
