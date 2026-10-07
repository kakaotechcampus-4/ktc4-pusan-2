/**
 * The ring around the face: one tick per sweep direction, a gauge arc per
 * direction (8, outside the ticks) that fills as the head turns further that
 * way, a progress arc, a hint arrow and a check mark, in a -60..60 unit system
 * the caller places over the face (`FaceTrackView`).
 *
 * Angles are the sweep's: 0 = the presenter's right, counter-clockwise.  The
 * picture is mirrored, so the presenter's right is the screen's right and a
 * tick at angle `a` is drawn at (cos a, -sin a) -- turning your head to the
 * right lights the ticks on the right of the circle.  Where the head points is
 * shown by the 3D arrow (`arrow3d.ts`); the ring marks that tick in coral.
 */
const SVG_NS = 'http://www.w3.org/2000/svg';
const R_ARC = 49;
const R_TICK_IN = 52;
const R_TICK_OUT = 56;
/** Extra length of a lit tick, and of the tick the head points at when fully turned. */
const LIT_GROW = 2.5;
const CUR_GROW = 4.5;
/** Direction gauges: radius, and half the span of each 45-degree sector (a gap between them). */
const R_SECTOR = 64;
const SECTOR_HALF_DEG = 19;

/** An arc of radius `r` from `a0` to `a1` degrees (presenter angles, counter-clockwise on screen). */
function arcPath(r: number, a0: number, a1: number): string {
  const p = (a: number) => {
    const t = (a * Math.PI) / 180;
    return `${(r * Math.cos(t)).toFixed(2)} ${(-r * Math.sin(t)).toFixed(2)}`;
  };
  return `M ${p(a0)} A ${r} ${r} 0 0 0 ${p(a1)}`;
}

export class RingView {
  #ticks: SVGLineElement[] = [];
  #angles: number[] = [];
  #fg: SVGCircleElement;
  #nudge: SVGGElement;
  #sectorFills: SVGPathElement[] = [];

  constructor(group: SVGGElement, count: number) {
    const ticks = group.querySelector('.gzc-ticks')!;
    for (let i = 0; i < count; i++) {
      const a = (i * 2 * Math.PI) / count;
      const line = document.createElementNS(SVG_NS, 'line');
      line.setAttribute('class', 'gzc-tick');
      line.setAttribute('x1', `${R_TICK_IN * Math.cos(a)}`);
      line.setAttribute('y1', `${-R_TICK_IN * Math.sin(a)}`);
      ticks.appendChild(line);
      this.#ticks.push(line);
      this.#angles.push(a);
      this.#length(i, R_TICK_OUT);
    }
    const sectors = group.querySelector('.gzc-sectors')!;
    for (let k = 0; k < 8; k++) {
      const a = k * 45;
      const bg = document.createElementNS(SVG_NS, 'path');
      bg.setAttribute('class', 'gzc-sector-bg');
      bg.setAttribute('d', arcPath(R_SECTOR, a - SECTOR_HALF_DEG, a + SECTOR_HALF_DEG));
      const fill = document.createElementNS(SVG_NS, 'path');
      fill.setAttribute('class', 'gzc-sector-fill');
      sectors.append(bg, fill);
      this.#sectorFills.push(fill);
    }
    this.#fg = group.querySelector('.gzc-ring-fg')!;
    this.#nudge = group.querySelector('.gzc-nudge')!;
    this.#fg.style.strokeDasharray = `${2 * Math.PI * R_ARC}`;
    this.progress(0);
  }

  /** The thin arc: how long the set-up check has held. */
  progress(value: number): void {
    const c = 2 * Math.PI * R_ARC;
    this.#fg.style.strokeDashoffset = `${c * (1 - Math.max(0, Math.min(1, value)))}`;
  }

  /** Lit ticks, and the tick the head points at (lengthened by how far it turned). */
  ticks(lit: readonly boolean[], pointerDeg: number | null, reach: number): void {
    const n = this.#ticks.length;
    const current =
      pointerDeg === null
        ? -1
        : Math.floor(((((pointerDeg + 180 / n) % 360) + 360) % 360) / (360 / n)) % n;
    this.#ticks.forEach((tick, i) => {
      const on = !!lit[i];
      const here = i === current;
      tick.classList.toggle('is-lit', on);
      tick.classList.toggle('is-cur', here);
      this.#length(
        i,
        R_TICK_OUT + (on ? LIT_GROW : 0) + (here ? CUR_GROW * Math.min(1, reach) : 0),
      );
    });
  }

  /** One gauge per direction (0..1, in `GAZE_DIRECTIONS` order), filling from its middle out. */
  sectors(progress: readonly number[]): void {
    this.#sectorFills.forEach((el, k) => {
      const p = Math.max(0, Math.min(1, progress[k] ?? 0));
      const a = k * 45;
      el.setAttribute(
        'd',
        p > 0.02 ? arcPath(R_SECTOR, a - SECTOR_HALF_DEG * p, a + SECTOR_HALF_DEG * p) : '',
      );
      el.classList.toggle('is-full', p >= 0.999);
    });
  }

  clear(): void {
    this.ticks(new Array(this.#ticks.length).fill(false), null, 0);
    this.sectors([]);
    this.nudge(null);
  }

  /** An arrow outside the ring toward a direction (angle in degrees), or hide it. */
  nudge(angleDeg: number | null): void {
    this.#nudge.classList.toggle('is-on', angleDeg !== null);
    if (angleDeg !== null) this.#nudge.setAttribute('transform', `rotate(${-angleDeg})`);
  }

  #length(i: number, r: number): void {
    const a = this.#angles[i]!;
    this.#ticks[i]!.setAttribute('x2', `${r * Math.cos(a)}`);
    this.#ticks[i]!.setAttribute('y2', `${-r * Math.sin(a)}`);
  }
}
