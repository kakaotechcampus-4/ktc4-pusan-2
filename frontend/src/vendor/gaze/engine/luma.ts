/**
 * Face / background brightness from a frame, for the TOO_DARK, BACKLIT and
 * LIGHTING_CHANGED checks (Python `frame_quality`).
 *
 * The frame is drawn once into a small OffscreenCanvas (works in a worker, no
 * DOM) and luma is summed there: means survive the downscale, and reading back
 * a 160 px wide image costs well under a millisecond.
 */
import type { LumaSampler } from './observe';
import type { Bbox } from './types';

const MAX_WIDTH = 160;

type Drawable = CanvasImageSource & { width: number; height: number };

let canvas: OffscreenCanvas | null = null;
let ctx: OffscreenCanvasRenderingContext2D | null = null;

export function canvasLumaSampler(frame: Drawable): LumaSampler | null {
  if (typeof OffscreenCanvas === 'undefined' || !(frame.width > 0) || !(frame.height > 0))
    return null;
  const scale = Math.min(1, MAX_WIDTH / frame.width);
  const w = Math.max(1, Math.round(frame.width * scale));
  const h = Math.max(1, Math.round(frame.height * scale));
  if (!canvas || canvas.width !== w || canvas.height !== h) {
    canvas = new OffscreenCanvas(w, h);
    ctx = canvas.getContext('2d', { willReadFrequently: true });
  }
  if (!ctx) return null;
  ctx.drawImage(frame, 0, 0, w, h);
  const rgba = ctx.getImageData(0, 0, w, h).data;
  const luma = new Float32Array(w * h);
  let total = 0;
  for (let i = 0, p = 0; p < luma.length; i += 4, p++) {
    const y = 0.299 * rgba[i]! + 0.587 * rgba[i + 1]! + 0.114 * rgba[i + 2]!;
    luma[p] = y;
    total += y;
  }
  return {
    measure(bbox: Bbox, imageSize: readonly [number, number]) {
      const sx = w / imageSize[0];
      const sy = h / imageSize[1];
      const x0 = Math.max(0, Math.floor(bbox[0] * sx));
      const y0 = Math.max(0, Math.floor(bbox[1] * sy));
      const x1 = Math.min(w, Math.ceil((bbox[0] + bbox[2]) * sx));
      const y1 = Math.min(h, Math.ceil((bbox[1] + bbox[3]) * sy));
      let sum = 0;
      let n = 0;
      for (let y = y0; y < y1; y++) {
        for (let x = x0; x < x1; x++) {
          sum += luma[y * w + x]!;
          n += 1;
        }
      }
      if (n === 0) return null;
      const rest = luma.length - n;
      return { face: sum / n, background: rest > 0 ? (total - sum) / rest : 0 };
    },
  };
}
