/**
 * A 3D arrow out of the nose tip, along the head direction, drawn in
 * perspective over the (mirrored) camera picture.
 *
 * Facing the reference pose the arrow points straight at the viewer and reads
 * as a short cone seen tip-on; turning the head lays it over toward that side
 * and it grows.  Angles are relative to a reference pose (the head circle's
 * centre, or the lens look once calibrated), so "straight at you" means
 * "looking where the reference was taken".
 *
 *     d = (-sin(yaw) cos(pitch), -sin(pitch), cos(yaw) cos(pitch))   screen x right, y down, z toward the viewer
 *
 * Yaw > 0 turns toward the image right, which the mirrored preview shows on
 * the left -- hence the minus on x.
 */
const SVG_NS = 'http://www.w3.org/2000/svg';
/** Easing time constant for the angles (the worker reports at ~8 Hz). */
const TAU_MS = 90;
/** Arrow length as a share of the face height. */
const LENGTH = 0.95;
/** Where the cone starts along the arrow, and its radius (shares of the length). */
const HEAD_AT = 0.72;
const HEAD_RADIUS = 0.075;
const ROOT_RADIUS = 0.045;
/** Perspective strength: the focal length in arrow lengths (larger = flatter). */
const FOCAL = 5;
const SEGMENTS = 18;

type Vec = [number, number, number];

const sub = (a: Vec, b: Vec): Vec => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const add = (a: Vec, b: Vec): Vec => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
const mul = (a: Vec, k: number): Vec => [a[0] * k, a[1] * k, a[2] * k];
const cross = (a: Vec, b: Vec): Vec => [
  a[1] * b[2] - a[2] * b[1],
  a[2] * b[0] - a[0] * b[2],
  a[0] * b[1] - a[1] * b[0],
];
const norm = (a: Vec): Vec => {
  const l = Math.hypot(a[0], a[1], a[2]) || 1;
  return [a[0] / l, a[1] / l, a[2] / l];
};

export interface ArrowInput {
  /** Nose tip on screen, px. */
  nose: [number, number];
  /** Face height on screen, px (sets the arrow's size). */
  faceHeight: number;
  /** Head angles in degrees, raw frame convention. */
  yawDeg: number;
  pitchDeg: number;
}

export class HeadArrowView {
  #group: SVGGElement;
  #root: SVGPolygonElement;
  #shaft: SVGLineElement;
  #base: SVGPolygonElement;
  #sides: SVGPolygonElement[] = [];
  #target: ArrowInput | null = null;
  #current: ArrowInput | null = null;
  #reference: [number, number] | null = null;
  #last = 0;
  #shown = 0;

  constructor(svg: SVGSVGElement, className = 'gzc-arrow') {
    this.#group = document.createElementNS(SVG_NS, 'g');
    this.#group.setAttribute('class', className);
    const make = <K extends 'polygon' | 'line'>(tag: K, cls: string) => {
      const el = document.createElementNS(SVG_NS, tag);
      el.setAttribute('class', cls);
      this.#group.appendChild(el);
      return el;
    };
    this.#root = make('polygon', 'gzc-a3-root') as SVGPolygonElement;
    this.#shaft = make('line', 'gzc-a3-shaft') as SVGLineElement;
    this.#base = make('polygon', 'gzc-a3-base') as SVGPolygonElement;
    for (let i = 0; i < SEGMENTS; i++)
      this.#sides.push(make('polygon', 'gzc-a3-side') as SVGPolygonElement);
    svg.appendChild(this.#group);
  }

  /** The pose that reads as "straight at you"; null = the raw camera axis. */
  setReference(reference: readonly [number, number] | null): void {
    this.#reference = reference ? [reference[0], reference[1]] : null;
  }

  /** A new measurement, or null to fade the arrow out. */
  update(input: ArrowInput | null): void {
    this.#target = input;
    if (input && !this.#current) this.#current = { ...input, nose: [...input.nose] };
  }

  /** Call once per animation frame. */
  render(now: number): void {
    const dt = this.#last ? Math.min(100, now - this.#last) : 16;
    this.#last = now;
    const k = 1 - Math.exp(-dt / TAU_MS);
    this.#shown += ((this.#target ? 1 : 0) - this.#shown) * k;
    this.#group.style.opacity = `${this.#shown}`;
    const t = this.#target;
    const c = this.#current;
    if (!c) return;
    if (t) {
      c.nose[0] += (t.nose[0] - c.nose[0]) * k;
      c.nose[1] += (t.nose[1] - c.nose[1]) * k;
      c.faceHeight += (t.faceHeight - c.faceHeight) * k;
      c.yawDeg += (t.yawDeg - c.yawDeg) * k;
      c.pitchDeg += (t.pitchDeg - c.pitchDeg) * k;
    }
    this.#draw(c);
  }

  #draw(c: ArrowInput): void {
    const ref = this.#reference ?? [0, 0];
    const yaw = ((c.yawDeg - ref[0]) * Math.PI) / 180;
    const pitch = ((c.pitchDeg - ref[1]) * Math.PI) / 180;
    const d: Vec = [
      -Math.sin(yaw) * Math.cos(pitch),
      -Math.sin(pitch),
      Math.cos(yaw) * Math.cos(pitch),
    ];
    const length = LENGTH * c.faceHeight;
    const focal = FOCAL * length;
    const origin: Vec = [c.nose[0], c.nose[1], 0];
    // Weak perspective about the nose: nearer (z > 0) draws larger and further out.
    const project = (p: Vec): [number, number] => {
      const s = focal / Math.max(focal * 0.25, focal - p[2]);
      return [origin[0] + (p[0] - origin[0]) * s, origin[1] + (p[1] - origin[1]) * s];
    };
    const pts = (ps: [number, number][]) =>
      ps.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ');

    // A frame perpendicular to the arrow, for its round root, base and cone.
    const helper: Vec = Math.abs(d[2]) < 0.9 ? [0, 0, 1] : [1, 0, 0];
    const u = norm(cross(d, helper));
    const v = cross(d, u);
    const ring = (centre: Vec, r: number): Vec[] =>
      Array.from({ length: SEGMENTS }, (_, i) => {
        const a = (i / SEGMENTS) * 2 * Math.PI;
        return add(centre, add(mul(u, r * Math.cos(a)), mul(v, r * Math.sin(a))));
      });

    this.#root.setAttribute('points', pts(ring(origin, ROOT_RADIUS * length).map(project)));
    const baseCentre = add(origin, mul(d, HEAD_AT * length));
    const tip = add(origin, mul(d, length));
    const [x1, y1] = project(origin);
    const [x2, y2] = project(baseCentre);
    this.#shaft.setAttribute('x1', `${x1}`);
    this.#shaft.setAttribute('y1', `${y1}`);
    this.#shaft.setAttribute('x2', `${x2}`);
    this.#shaft.setAttribute('y2', `${y2}`);
    this.#shaft.style.strokeWidth = `${Math.max(2, 0.035 * length)}`;

    const base = ring(baseCentre, HEAD_RADIUS * length);
    this.#base.setAttribute('points', pts(base.map(project)));
    const tip2 = project(tip);
    // Cone sides, painter's order: the ones facing away first, lit by how much they face the viewer.
    const sides = base.map((p, i) => {
      const q = base[(i + 1) % SEGMENTS]!;
      const n = norm(cross(sub(q, p), sub(tip, p)));
      const mid = mul(add(add(p, q), tip), 1 / 3);
      // n points out of the cone (u, v, d is right-handed), so +z faces the viewer.
      return { p, q, facing: n[2], depth: mid[2] };
    });
    sides.sort((a, b) => a.depth - b.depth);
    sides.forEach((s, i) => {
      const el = this.#sides[i]!;
      el.setAttribute('points', pts([project(s.p), project(s.q), tip2]));
      // Coral, darker on the sides turned away and brighter on the ones facing the viewer.
      const k = 0.5 + 0.6 * Math.max(0, Math.min(1, (s.facing + 1) / 2));
      el.style.fill = `rgb(${Math.min(255, Math.round(232 * k))}, ${Math.round(83 * k)}, ${Math.round(42 * k)})`;
    });
    // The base disc shows only when the cone points away from the viewer.
    this.#base.style.opacity = d[2] < 0 ? '1' : '0';
  }
}
