/**
 * The face guide over the mirrored preview: a soft oval around the face with a
 * 3D cross on it (midline and eye line on a turning ellipsoid, `cross3d.ts`),
 * following the face smoothly.  Worker results arrive at ~8 Hz; this eases
 * toward each new target at display rate, so the guide glides instead of
 * jumping.  Without a face it falls back to a static dashed oval in the
 * middle -- "put your face here".
 */
import type { FaceGuide } from '../engine/types';
import { crossCurves, toPoints } from './cross3d';

interface Shape {
  cx: number;
  cy: number;
  rx: number;
  ry: number;
  /** Rotation of the face's vertical axis, radians (0 = upright). */
  angle: number;
  /** Eye line, as a signed offset from the centre along the face axis (px). */
  eye: number;
  /** Head angles, degrees (raw frame convention). */
  yaw: number;
  pitch: number;
}

export interface GuideOptions {
  /** How the video fills its box (`object-fit`): the whole picture, or the box filled and cropped. */
  fit?: 'cover' | 'contain';
  /** Draw the oval and the empty-box placeholder (the camera view has its ring instead). */
  oval?: boolean;
}

const SVG_NS = 'http://www.w3.org/2000/svg';
/** Easing time constant: ~95% of the way to a new target in 240 ms. */
const TAU_MS = 80;

export class FaceGuideView {
  readonly svg: SVGSVGElement;
  #oval: SVGEllipseElement;
  #vertical: SVGPolylineElement;
  #horizontal: SVGPolylineElement;
  #fit: 'cover' | 'contain';
  #placeholder: SVGEllipseElement;
  #current: Shape | null = null;
  #target: Shape | null = null;
  #visible = 0;
  #lastFrame = 0;
  #video: HTMLVideoElement;
  #container: HTMLElement;

  constructor(container: HTMLElement, video: HTMLVideoElement, options: GuideOptions = {}) {
    this.#container = container;
    this.#video = video;
    this.#fit = options.fit ?? 'cover';
    this.svg = document.createElementNS(SVG_NS, 'svg');
    this.svg.classList.add('gzc-guide');
    this.svg.setAttribute('aria-hidden', 'true');
    const make = <K extends 'ellipse' | 'polyline'>(tag: K, cls: string) => {
      const el = document.createElementNS(SVG_NS, tag);
      el.setAttribute('class', cls);
      this.svg.appendChild(el);
      return el as K extends 'ellipse' ? SVGEllipseElement : SVGPolylineElement;
    };
    this.#placeholder = make('ellipse', 'gzc-guide-placeholder');
    this.#oval = make('ellipse', 'gzc-guide-oval') as SVGEllipseElement;
    this.#vertical = make('polyline', 'gzc-guide-line') as SVGPolylineElement;
    this.#horizontal = make('polyline', 'gzc-guide-line') as SVGPolylineElement;
    if (options.oval === false) {
      this.#oval.style.display = 'none';
      this.#placeholder.style.display = 'none';
    }
    container.appendChild(this.svg);
  }

  /** A new worker result (or `null`: no face) and the head angles in degrees. */
  update(
    guide: FaceGuide | null,
    imageSize: readonly [number, number],
    yawDeg = 0,
    pitchDeg = 0,
  ): void {
    this.#target = guide ? this.#shapeOf(guide, imageSize, yawDeg, pitchDeg) : null;
    if (this.#target && !this.#current) this.#current = { ...this.#target };
  }

  /** Call once per animation frame. */
  render(now: number): void {
    const dt = this.#lastFrame ? Math.min(100, now - this.#lastFrame) : 16;
    this.#lastFrame = now;
    const k = 1 - Math.exp(-dt / TAU_MS);
    const w = this.#container.clientWidth;
    const h = this.#container.clientHeight;
    this.svg.setAttribute('viewBox', `0 0 ${w} ${h}`);

    const wanted = this.#target ? 1 : 0;
    this.#visible += (wanted - this.#visible) * k;
    if (this.#target && this.#current) {
      const c = this.#current;
      const t = this.#target;
      c.cx += (t.cx - c.cx) * k;
      c.cy += (t.cy - c.cy) * k;
      c.rx += (t.rx - c.rx) * k;
      c.ry += (t.ry - c.ry) * k;
      c.angle += (t.angle - c.angle) * k;
      c.eye += (t.eye - c.eye) * k;
      c.yaw += (t.yaw - c.yaw) * k;
      c.pitch += (t.pitch - c.pitch) * k;
    }

    // Static placeholder in the centre while no face is followed.
    this.#placeholder.setAttribute('cx', `${w / 2}`);
    this.#placeholder.setAttribute('cy', `${h * 0.47}`);
    this.#placeholder.setAttribute('rx', `${h * 0.25}`);
    this.#placeholder.setAttribute('ry', `${h * 0.33}`);
    this.#placeholder.style.opacity = `${0.9 * (1 - this.#visible)}`;

    const s = this.#current;
    for (const el of [this.#oval, this.#vertical, this.#horizontal])
      el.style.opacity = `${this.#visible}`;
    if (!s) return;
    const deg = (s.angle * 180) / Math.PI;
    this.#oval.setAttribute('cx', `${s.cx}`);
    this.#oval.setAttribute('cy', `${s.cy}`);
    this.#oval.setAttribute('rx', `${s.rx}`);
    this.#oval.setAttribute('ry', `${s.ry}`);
    this.#oval.setAttribute('transform', `rotate(${deg} ${s.cx} ${s.cy})`);
    // 3D cross: the midline and the eye line on the turning face.
    const { mid, eye } = crossCurves(s, s.yaw, s.pitch);
    this.#vertical.setAttribute('points', toPoints(mid));
    this.#horizontal.setAttribute('points', toPoints(eye));
  }

  /** Normalised landmark -> container pixels, through `object-fit` and the mirror. */
  toScreen(p: readonly [number, number], imageSize: readonly [number, number]): [number, number] {
    const cw = this.#container.clientWidth;
    const ch = this.#container.clientHeight;
    const vw = this.#video.videoWidth || imageSize[0];
    const vh = this.#video.videoHeight || imageSize[1];
    const scale = (this.#fit === 'cover' ? Math.max : Math.min)(cw / vw, ch / vh);
    const ox = (cw - vw * scale) / 2;
    const oy = (ch - vh * scale) / 2;
    return [ox + (1 - p[0]) * vw * scale, oy + p[1] * vh * scale];
  }

  #shapeOf(g: FaceGuide, imageSize: readonly [number, number], yaw: number, pitch: number): Shape {
    const top = this.toScreen(g.forehead, imageSize);
    const chin = this.toScreen(g.chin, imageSize);
    const left = this.toScreen(g.sideLeft, imageSize);
    const right = this.toScreen(g.sideRight, imageSize);
    const eyeL = this.toScreen(g.eyeLeft, imageSize);
    const eyeR = this.toScreen(g.eyeRight, imageSize);
    const cx = (top[0] + chin[0]) / 2;
    const cy = (top[1] + chin[1]) / 2;
    const axis = Math.atan2(chin[1] - top[1], chin[0] - top[0]) - Math.PI / 2;
    const ry = (Math.hypot(chin[0] - top[0], chin[1] - top[1]) / 2) * 1.14;
    const rx = (Math.hypot(right[0] - left[0], right[1] - left[1]) / 2) * 1.06;
    const ex = (eyeL[0] + eyeR[0]) / 2 - cx;
    const ey = (eyeL[1] + eyeR[1]) / 2 - cy;
    const eye = -ex * Math.sin(axis) + ey * Math.cos(axis);
    return { cx, cy, rx, ry, angle: axis, eye, yaw, pitch };
  }
}
