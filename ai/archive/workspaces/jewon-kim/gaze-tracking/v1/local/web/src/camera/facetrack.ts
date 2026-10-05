/**
 * What follows the face in the camera picture: the ring, the 3D face cross and
 * the 3D head arrow, drawn over the face wherever it is in the (mirrored)
 * picture and however the picture fits its box.
 *
 * The worker reports at ~8 Hz; the ring's position and size ease toward each
 * result at display rate, so it glides with the face.  Without a face the ring
 * waits, faded, in the middle of the picture.
 */
import type { FaceGuide } from '../engine/types';
import { HeadArrowView } from './arrow3d';
import { FaceGuideView } from './guide';

/** Ring radius (the arc, r 49 of its 60 units) as a share of the face height. */
const RING_SHARE = 0.66;
const TAU_MS = 90;

interface Place {
  cx: number;
  cy: number;
  k: number;
}

export class FaceTrackView {
  readonly arrow: HeadArrowView;
  readonly cross: FaceGuideView;
  #box: HTMLElement;
  #video: HTMLVideoElement;
  #svg: SVGSVGElement;
  #ring: SVGGElement;
  #fit: 'contain' | 'cover';
  #guide: FaceGuide | null = null;
  #imageSize: readonly [number, number] = [640, 480];
  #current: Place | null = null;
  #last = 0;
  #arrowOn = false;
  #head: [number, number] = [0, 0];

  constructor(
    box: HTMLElement,
    video: HTMLVideoElement,
    svg: SVGSVGElement,
    fit: 'contain' | 'cover' = 'contain',
  ) {
    this.#box = box;
    this.#video = video;
    this.#svg = svg;
    this.#fit = fit;
    this.#ring = svg.querySelector<SVGGElement>('.gzc-ring')!;
    // The 3D cross under the ring and the arrow (the ring stands in for the oval).
    this.cross = new FaceGuideView(box, video, { fit, oval: false });
    box.insertBefore(this.cross.svg, svg);
    this.arrow = new HeadArrowView(svg);
  }

  /** A worker result: the face guide (or null) and the head angles in degrees. */
  update(
    guide: FaceGuide | null,
    imageSize: readonly [number, number],
    yawDeg: number,
    pitchDeg: number,
  ): void {
    this.#guide = guide;
    this.#imageSize = imageSize;
    this.#head = [yawDeg, pitchDeg];
    this.cross.update(guide, imageSize, yawDeg, pitchDeg);
    this.#feedArrow();
  }

  /** Show the arrow (from the given reference pose) or hide it. */
  showArrow(on: boolean, reference: readonly [number, number] | null = null): void {
    this.#arrowOn = on;
    this.arrow.setReference(reference);
    this.#feedArrow();
  }

  render(now: number): void {
    const dt = this.#last ? Math.min(100, now - this.#last) : 16;
    this.#last = now;
    const w = this.#box.clientWidth;
    const h = this.#box.clientHeight;
    if (!w || !h) return;
    this.#svg.setAttribute('viewBox', `0 0 ${w} ${h}`);
    const goal = this.#placeOf(w, h);
    const k = 1 - Math.exp(-dt / TAU_MS);
    const c = (this.#current ??= { ...goal });
    c.cx += (goal.cx - c.cx) * k;
    c.cy += (goal.cy - c.cy) * k;
    c.k += (goal.k - c.k) * k;
    this.#ring.setAttribute('transform', `translate(${c.cx} ${c.cy}) scale(${c.k})`);
    this.#ring.classList.toggle('is-waiting', this.#guide === null);
    this.cross.render(now);
    this.arrow.render(now);
  }

  /** Normalised raw-frame point -> px in the box (mirrored, through `object-fit`). */
  toScreen(p: readonly [number, number]): [number, number] {
    const w = this.#box.clientWidth;
    const h = this.#box.clientHeight;
    const vw = this.#video.videoWidth || this.#imageSize[0];
    const vh = this.#video.videoHeight || this.#imageSize[1];
    const s = (this.#fit === 'cover' ? Math.max : Math.min)(w / vw, h / vh);
    const ox = (w - vw * s) / 2;
    const oy = (h - vh * s) / 2;
    return [ox + (1 - p[0]) * vw * s, oy + p[1] * vh * s];
  }

  #faceHeight(g: FaceGuide): number {
    const a = this.toScreen(g.forehead);
    const b = this.toScreen(g.chin);
    return Math.hypot(b[0] - a[0], b[1] - a[1]);
  }

  #placeOf(w: number, h: number): Place {
    const g = this.#guide;
    if (!g) return { cx: w / 2, cy: h / 2, k: (0.3 * h) / 49 };
    const a = this.toScreen(g.forehead);
    const b = this.toScreen(g.chin);
    return {
      cx: (a[0] + b[0]) / 2,
      cy: (a[1] + b[1]) / 2,
      k: (RING_SHARE * this.#faceHeight(g)) / 49,
    };
  }

  #feedArrow(): void {
    const g = this.#guide;
    if (!this.#arrowOn || !g) return this.arrow.update(null);
    this.arrow.update({
      nose: this.toScreen(g.nose),
      faceHeight: this.#faceHeight(g),
      yawDeg: this.#head[0],
      pitchDeg: this.#head[1],
    });
  }
}
