/**
 * The camera view: the camera picture and everything drawn inside it, in a box
 * the host page provides.  This is the part of the screen the AI side owns --
 * the camera and the recognition.  The page around it (its layout, going full
 * screen, the buttons that open it, what to do with the results) is the host's.
 *
 *     const view = new GazeCameraView(box, { assetDir: '/models/' });
 *     await view.attach(stream);              // the host's MediaStream; shown mirrored here
 *     view.on('calibrated', (r) => keep(r.model));
 *     view.on('decision', (d) => vote(d));    // live: one decision per analysed frame
 *     view.startSetup();                      // ① set-up check → ② head circle → ③ three looks → live
 *
 * What the box shows (the view fills it; the host sizes it, full screen or not):
 *
 *   idle    the picture, the face cross and the face badge
 *   align   ① a ring round the face fills while the set-up check holds for 1 s
 *   sweep   ② turning the head lights the ring's ticks; a 3D arrow shows the head
 *   calib   ③ screen centre (the ring) → lens (target at the top) → script (band at the bottom)
 *   result  only when there is something to say (a failure, a note)
 *   live    the cross and arrow, where the presenter looks now, the 1 s border (`setZone`)
 *
 * Frames: an ImageBitmap every 125 ms, and only once the worker answered the
 * previous one (at most one in flight).  The engine gets the raw frame; only the
 * picture is mirrored.  Nothing leaves the device and nothing is stored: the
 * calibration model goes to the host (`calibrated`) to keep or drop.
 */
import './camera.css';
import type { CalibrationQualityDict, CalibrationModel } from '../engine';
import { makeConfig } from '../engine/config';
import type { FrameDecision, OtherMapping } from '../engine/contract';
import type { BaselineCheck, ReanchorStatus } from '../engine/engine';
import type { GaugeStatus } from '../engine/gauge';
import type { PlacementResultDict } from '../engine/placement';
import type { PreconditionReport } from '../engine/preconditions';
import type { SweepStatus } from '../engine/sweep';
import { GAZE_DIRECTIONS, type Cue, type GazeDirection, type StateClass } from '../engine/types';
import type {
  EngineFailure,
  FrameMode,
  FrameSummary,
  FromWorker,
  ToWorker,
} from '../worker/protocol';
import { FaceTrackView } from './facetrack';
import { RingView } from './ring';
import {
  CALIBRATION_HINT,
  CHECK_FIX,
  CHECK_NAME,
  CUE_TEXT,
  DIRECTION_ARROW,
  DIRECTION_NAME,
  GAUGE_REASON,
  ISSUE,
  PLACEMENT_HINT,
  PLACEMENT_NAME,
  STATE_NAME,
  STATE_SHORT,
  SWEEP_REASON,
  SWEEP_TEXT,
  UNCERTAIN_REASON,
} from './text';

/** The set-up steps, then live; `idle` = the picture only. */
export type CameraPhase = 'idle' | 'align' | 'sweep' | 'swept' | 'calib' | 'result' | 'live';

/** The rehearsal border's three zones (the frontend's 1 s decision). */
export type CameraZone = 'CAMERA' | 'BOTTOM' | 'UNCERTAIN';

export interface CameraViewOptions {
  /** Where `face_landmarker.task` and the MediaPipe wasm files are served (default `/models/`). */
  assetDir?: string;
  /** A fresh worker built from `src/worker/gaze.worker.ts` (e.g. Vite `?worker`); default: the view makes one. */
  worker?: Worker;
  /** How the picture fills the box: whole (`contain`, default) or filling it, cropped (`cover`). */
  fit?: 'contain' | 'cover';
  /**
   * After a calibration with nothing to report, go live by itself (default).
   * Off: the result card always shows and waits for its button or `goLive()`.
   */
  autoLive?: boolean;
  /** How OTHER maps onto the frontend's three zones (engine default `UNCERTAIN`). */
  otherAs?: OtherMapping;
  delegate?: 'CPU' | 'GPU';
  /** Headless tests only: no waits, and every escape hatch taken without a click. */
  unattended?: boolean;
}

/** Everything the set-up measured, handed to the host when the calibration ends. */
export interface SetupResult {
  /** `status` OK or FAIL with `reason`, the anchors, separations and warnings. */
  quality: CalibrationQualityDict;
  /** Plain data, memory only (face measurements inside): give it back with `useCalibration` in this session, never store it. Null when none was built. */
  model: CalibrationModel | null;
  placement: PlacementResultDict | null;
  /** What live tells apart: CAMERA, SCREEN, BOTTOM, OTHER (fewer when an anchor is missing). */
  classes: StateClass[];
  /** ① The last set-up check, with its measurements (distance, position, brightness, ...). */
  preconditions: PreconditionReport | null;
  /** ② The head circle: its centre and noise, the directions filled and missed. */
  sweep: SweepStatus | null;
  sweepSkipped: boolean;
  /** ③ How the screen-centre look compared with the head circle's centre. */
  baseline: BaselineCheck | null;
  /** The presenter went on past a rejected check, an odd camera position or a failed calibration. */
  forced: boolean;
  /** What the result card says (Korean); empty when there was nothing to say. */
  notes: string[];
}

/** What the view reports as failed: the worker's reasons, or the camera stream ending. */
export type ViewFailure = EngineFailure | 'CAMERA_LOST';

export interface CameraViewEvents {
  ready: { version: string; isolated: boolean };
  /** `reason` when known: the engine did not start, one frame failed, or the camera went away. */
  error: { message: string; reason?: ViewFailure };
  phase: { phase: CameraPhase; previous: CameraPhase };
  /** Every analysed frame: timing, face found or why not, head angles, face guide. */
  frame: FrameSummary;
  check: PreconditionReport;
  sweep: SweepStatus;
  gauge: { status: GaugeStatus; baseline: BaselineCheck | null };
  placement: PlacementResultDict | null;
  calibrated: SetupResult;
  /** Live: the engine's decision for one frame (state, direction, aim, condition). */
  decision: FrameDecision;
  reanchor: ReanchorStatus;
}

type Listener<K extends keyof CameraViewEvents> = (value: CameraViewEvents[K]) => void;

const GRAB_EVERY_MS = 125;
/** The circle starts in the middle of the picture, so the screen-centre look comes first. */
const CUE_ORDER: readonly Cue[] = ['SCREEN', 'CAMERA', 'BOTTOM'];
/** The worker runs the default config, so the windows and counts here match it. */
const CFG = makeConfig();
const MODE: Record<CameraPhase, FrameMode> = {
  idle: 'preview',
  align: 'check',
  sweep: 'sweep',
  swept: 'preview',
  calib: 'calibrate',
  result: 'preview',
  live: 'live',
};
const SETUP: ReadonlySet<CameraPhase> = new Set(['align', 'sweep', 'swept', 'calib', 'result']);
const TARGET_C = 2 * Math.PI * 49;

const TEMPLATE = `
  <video class="gzc-video" muted playsinline></video>
  <div class="gzc-off"><span class="gzc-cam-icon" aria-hidden="true"></span><p>카메라가 꺼져 있어요</p></div>
  <svg class="gzc-overlay" aria-hidden="true">
    <g class="gzc-ring">
      <g class="gzc-ticks"></g>
      <g class="gzc-sectors"></g>
      <circle class="gzc-veil" r="46" />
      <circle class="gzc-ring-bg" r="49" />
      <circle class="gzc-ring-fg" r="49" transform="rotate(-90)" />
      <g class="gzc-nudge"><path d="M 67 -6 L 77 0 L 67 6 Z" /></g>
      <path class="gzc-check" d="M -12 1 L -4 9 L 13 -9" />
    </g>
  </svg>
  <ol class="gzc-steps" aria-label="진행 단계">
    <li data-step="face">얼굴 확인</li>
    <li data-step="calib">시선 보정</li>
    <li data-step="done">완료</li>
  </ol>
  <div class="gzc-lens" aria-hidden="true"><span></span><em>렌즈</em></div>
  <div class="gzc-script" aria-hidden="true"><em>대본 자리</em></div>
  <div class="gzc-target" aria-hidden="true">
    <svg viewBox="-60 -60 120 120">
      <circle class="gzc-target-bg" r="49" />
      <circle class="gzc-target-fg" r="49" transform="rotate(-90)" />
      <path class="gzc-check" d="M -12 1 L -4 9 L 13 -9" />
    </svg>
    <span class="gzc-dot"></span>
  </div>
  <div class="gzc-card">
    <h2 class="gzc-title"></h2>
    <div class="gzc-sub"></div>
    <div class="gzc-meter" data-paused="0">
      <ol class="gzc-meter-steps">
        <li data-cue="SCREEN"><span>1 화면 가운데</span><b><i></i></b></li>
        <li data-cue="CAMERA"><span>2 렌즈</span><b><i></i></b></li>
        <li data-cue="BOTTOM"><span>3 대본</span><b><i></i></b></li>
      </ol>
      <div class="gzc-meter-bar"><i></i></div>
      <p class="gzc-meter-count"><strong>0%</strong><span></span></p>
    </div>
    <ul class="gzc-checks"></ul>
    <p class="gzc-hint" role="status"></p>
    <div class="gzc-actions"></div>
  </div>
  <button class="gzc-skip" type="button" hidden></button>
  <div class="gzc-status" data-state="" aria-live="polite"><i aria-hidden="true"></i><b></b><span></span></div>
  <div class="gzc-badge" data-ok="0"><i aria-hidden="true"></i><span>얼굴을 찾는 중</span></div>
  <div class="gzc-toast" role="alert"></div>`;

export class GazeCameraView {
  /** The view's root, appended to the host's box. */
  readonly element: HTMLDivElement;
  #opts: CameraViewOptions & { assetDir: string; fit: 'contain' | 'cover'; autoLive: boolean };
  #worker: Worker;
  #ownsWorker: boolean;
  #video: HTMLVideoElement;
  #track: FaceTrackView;
  #ring: RingView;
  #el: {
    title: HTMLElement;
    sub: HTMLElement;
    meter: HTMLElement;
    meterFill: HTMLElement;
    meterPct: HTMLElement;
    meterCount: HTMLElement;
    checks: HTMLElement;
    hint: HTMLElement;
    actions: HTMLElement;
    skip: HTMLButtonElement;
    targetArc: SVGCircleElement;
    status: HTMLElement;
    badge: HTMLElement;
    toast: HTMLElement;
  };
  #listeners = new Map<keyof CameraViewEvents, Set<Listener<never>>>();
  #timers = new Set<number>();
  #pumpTimer: number;
  #raf = 0;
  #destroyed = false;
  #stream: MediaStream | null = null;

  #ready = false;
  #version = '';
  #inFlight = false;
  #t0 = performance.now();

  #phase: CameraPhase = 'idle';
  /** Bumped on every phase change; a timer from an older phase does nothing. */
  #token = 0;
  #check: PreconditionReport | null = null;
  #sweep: SweepStatus | null = null;
  #sweepStarted = 0;
  #sweepSkipped = false;
  /** The circle timed out: its frames stop, the circle stays as it got. */
  #sweepEnded = false;
  #tutorialUntil = 0;
  #cueIndex = 0;
  #cueStarted = false;
  /** Timeouts per cue in this calibration: after two, the direction check is dropped. */
  #cueTimeouts: Partial<Record<Cue, number>> = {};
  #baseline: BaselineCheck | null = null;
  #placement: PlacementResultDict | null = null;
  #result: SetupResult | null = null;
  #forced = false;
  /** The pose the live arrow reads as "straight at you": the lens look. */
  #lensPose: [number, number] | null = null;
  #reanchorState: ReanchorStatus['state'] = 'IDLE';
  #restoring: ((ok: boolean) => void) | null = null;
  #toastTimer = 0;

  constructor(container: HTMLElement, options: CameraViewOptions = {}) {
    this.#opts = { assetDir: '/models/', fit: 'contain', autoLive: true, ...options };
    const root = document.createElement('div');
    root.className = 'gzc';
    root.dataset.phase = 'idle';
    root.dataset.on = '0';
    root.dataset.zone = '';
    root.dataset.fit = this.#opts.fit;
    root.innerHTML = TEMPLATE;
    container.appendChild(root);
    this.element = root;

    const q = <T extends Element = HTMLElement>(sel: string) => root.querySelector<T>(sel)!;
    this.#video = q<HTMLVideoElement>('.gzc-video');
    this.#track = new FaceTrackView(
      root,
      this.#video,
      q<SVGSVGElement>('.gzc-overlay'),
      this.#opts.fit,
    );
    this.#ring = new RingView(q<SVGGElement>('.gzc-ring'), CFG.sweep.ticks);
    this.#el = {
      title: q('.gzc-title'),
      sub: q('.gzc-sub'),
      meter: q('.gzc-meter'),
      meterFill: q('.gzc-meter-bar i'),
      meterPct: q('.gzc-meter-count strong'),
      meterCount: q('.gzc-meter-count span'),
      checks: q('.gzc-checks'),
      hint: q('.gzc-hint'),
      actions: q('.gzc-actions'),
      skip: q<HTMLButtonElement>('.gzc-skip'),
      targetArc: q<SVGCircleElement>('.gzc-target-fg'),
      status: q('.gzc-status'),
      badge: q('.gzc-badge'),
      toast: q('.gzc-toast'),
    };
    this.#el.targetArc.style.strokeDasharray = `${TARGET_C}`;

    this.#ownsWorker = !options.worker;
    this.#worker =
      options.worker ??
      new Worker(new URL('../worker/gaze.worker.ts', import.meta.url), { type: 'module' });
    this.#worker.addEventListener('message', this.#onMessage);
    this.#send({
      type: 'init',
      assetDir: this.#opts.assetDir,
      otherAs: options.otherAs,
      delegate: options.delegate,
    });
    this.#pumpTimer = window.setInterval(() => this.#pump().catch(() => undefined), GRAB_EVERY_MS);
    this.#raf = requestAnimationFrame(this.#animate);
  }

  // ---------------------------------------------------------------- public

  get phase(): CameraPhase {
    return this.#phase;
  }

  /** The engine is loaded (`ready` fired). */
  get ready(): boolean {
    return this.#ready;
  }

  /** `modelVersion+backbone+classifier`, the string a Take keeps. */
  get version(): string {
    return this.#version;
  }

  /** The last calibration's result (null before one ended). */
  get result(): SetupResult | null {
    return this.#result;
  }

  on<K extends keyof CameraViewEvents>(type: K, fn: Listener<K>): () => void {
    let set = this.#listeners.get(type);
    if (!set) this.#listeners.set(type, (set = new Set()));
    set.add(fn as Listener<never>);
    return () => set.delete(fn as Listener<never>);
  }

  /** Show and analyse the host's camera stream (the view never stops its tracks). */
  async attach(stream: MediaStream): Promise<void> {
    this.#stream = stream;
    this.#video.srcObject = stream;
    await this.#video.play();
    this.element.dataset.on = '1';
  }

  /** Stop showing and analysing the stream (the host still owns it). */
  detach(): void {
    this.#stream = null;
    this.#video.srcObject = null;
    this.element.dataset.on = '0';
    this.#track.update(null, [640, 480], 0, 0);
  }

  /** Run the set-up: from the face check, or straight to the three looks (the head circle kept). */
  startSetup(from: 'align' | 'calib' = 'align'): void {
    this.#forced = false;
    if (from === 'align') {
      this.#send({ type: 'resetPreconditions' });
      this.#beginAlign();
    } else this.#startCalibration();
  }

  /** Live: one decision per frame (`decision`); the arrow reads from the lens look. */
  goLive(): void {
    this.#setPhase('live');
    this.#track.showArrow(true, this.#lensPose);
    this.#reanchorState = 'IDLE';
    this.setZone(null);
    this.#el.status.dataset.state = '';
    this.#el.status.querySelector('b')!.textContent = '';
    this.#el.status.querySelector('span')!.textContent = '';
  }

  /** Leave the set-up or live for the plain picture. */
  cancel(): void {
    this.#setPhase('idle');
    this.#track.showArrow(false);
  }

  /** Adopt this session's calibration (`SetupResult.model`) and go live; false when it does not fit this engine. */
  useCalibration(model: CalibrationModel): Promise<boolean> {
    return new Promise((resolve) => {
      this.#restoring?.(false);
      this.#restoring = (ok) => {
        this.#restoring = null;
        if (ok) {
          this.#lensPose = model.anchors.CAMERA ?? null;
          this.goLive();
        }
        resolve(ok);
      };
      this.#send({ type: 'restore', model });
    });
  }

  /** Live: look at the lens for about a second to move the calibrated targets with the presenter. */
  reanchor(): void {
    if (this.#phase !== 'live') return;
    this.#send({ type: 'reanchor', tMs: this.#now() });
    this.#toast('렌즈를 1초 정도 바라봐 주세요');
  }

  /** The rehearsal border for the host's 1 s decision (null: none). */
  setZone(zone: CameraZone | null): void {
    this.element.dataset.zone = zone ?? '';
  }

  destroy(): void {
    this.#destroyed = true;
    this.#token += 1;
    window.clearInterval(this.#pumpTimer);
    cancelAnimationFrame(this.#raf);
    for (const id of this.#timers) window.clearTimeout(id);
    window.clearTimeout(this.#toastTimer);
    this.#worker.removeEventListener('message', this.#onMessage);
    if (this.#ownsWorker) this.#worker.terminate();
    this.#restoring?.(false);
    this.#video.srcObject = null;
    this.#listeners.clear();
    this.element.remove();
  }

  // ---------------------------------------------------------------- worker

  #send(msg: ToWorker, transfer: Transferable[] = []): void {
    if (!this.#destroyed) this.#worker.postMessage(msg, transfer);
  }

  #emit<K extends keyof CameraViewEvents>(type: K, value: CameraViewEvents[K]): void {
    for (const fn of this.#listeners.get(type) ?? []) (fn as Listener<K>)(value);
  }

  #now(): number {
    return Math.round(performance.now() - this.#t0);
  }

  async #pump(): Promise<void> {
    if (this.#inFlight || !this.#ready || !this.#stream || this.#video.readyState < 2) return;
    this.#inFlight = true;
    try {
      const bitmap = await createImageBitmap(this.#video);
      const mode = this.#sweepEnded && this.#phase === 'sweep' ? 'preview' : MODE[this.#phase];
      this.#send({ type: 'frame', bitmap, tMs: this.#now(), mode }, [bitmap]);
    } catch {
      this.#inFlight = false;
    }
  }

  #onMessage = (event: MessageEvent<FromWorker>): void => {
    const msg = event.data;
    switch (msg.type) {
      case 'ready':
        this.#ready = true;
        this.#version = msg.version;
        this.#emit('ready', { version: msg.version, isolated: msg.isolated });
        break;
      case 'failed':
        this.#emit('error', { message: msg.message, reason: msg.reason });
        this.#toast(`엔진 오류: ${msg.message}`);
        break;
      case 'frame':
        this.#inFlight = false;
        this.#onFrame(msg);
        break;
      case 'placement':
        this.#placement = msg.result;
        this.#emit('placement', msg.result);
        this.#afterPlacement();
        break;
      case 'calibrated':
        this.#onCalibrated(msg);
        break;
      case 'restored':
        this.#restoring?.(msg.ok);
        break;
    }
  };

  #onFrame(msg: Extract<FromWorker, { type: 'frame' }>): void {
    const f = msg.frame;
    this.#track.update(f.guide, f.imageSize, f.headYawDeg, f.headPitchDeg);
    this.#setBadge(f.faceValid, f.invalidReason);
    this.#emit('frame', f);
    if (msg.check && this.#phase === 'align') this.#onCheck(msg.check);
    if (msg.sweep && this.#phase === 'sweep' && !this.#sweepEnded) this.#onSweep(msg.sweep);
    if (msg.baseline !== undefined && this.#phase === 'calib') this.#baseline = msg.baseline;
    if (msg.gauge && this.#phase === 'calib') this.#onGauge(msg.gauge);
    if (msg.decision && this.#phase === 'live') this.#onDecision(msg.decision);
    if (msg.reanchor && this.#phase === 'live') this.#onReanchor(msg.reanchor);
  }

  // ---------------------------------------------------------------- phases

  #setPhase(phase: CameraPhase): void {
    const previous = this.#phase;
    this.#phase = phase;
    this.#token += 1;
    const root = this.element;
    root.dataset.phase = phase;
    if (phase !== 'calib') root.dataset.cue = '';
    const step = phase === 'calib' ? 'calib' : phase === 'result' ? 'done' : 'face';
    const order = ['face', 'calib', 'done'];
    for (const li of root.querySelectorAll<HTMLElement>('.gzc-steps li')) {
      const mine = order.indexOf(li.dataset.step!);
      li.classList.toggle('is-done', mine < order.indexOf(step));
      li.classList.toggle('is-active', li.dataset.step === step);
    }
    this.#setActions([]);
    this.#setSkip(null);
    this.#el.hint.textContent = '';
    this.#ring.nudge(null);
    if (!SETUP.has(phase)) this.#copy('');
    this.#emit('phase', { phase, previous });
  }

  /** Run `fn` after `ms`, unless the phase changed in the meantime. */
  #later(fn: () => void, ms: number): void {
    const token = this.#token;
    const id = window.setTimeout(() => {
      this.#timers.delete(id);
      if (token === this.#token && !this.#destroyed) fn();
    }, ms);
    this.#timers.add(id);
  }

  #copy(title: string, sub = ''): void {
    this.#el.title.textContent = title;
    this.#el.sub.textContent = sub;
  }

  #setActions(buttons: { label: string; primary?: boolean; run: () => void }[]): void {
    const box = this.#el.actions;
    box.innerHTML = '';
    for (const b of buttons) {
      const el = document.createElement('button');
      el.type = 'button';
      el.className = `gzc-btn ${b.primary ? 'is-primary' : 'is-ghost'}`;
      el.textContent = b.label;
      el.addEventListener('click', b.run);
      box.appendChild(el);
    }
  }

  #setSkip(action: { label: string; run: () => void } | null): void {
    const el = this.#el.skip;
    el.hidden = action === null;
    el.onclick = action ? action.run : null;
    el.textContent = action?.label ?? '';
  }

  // ---------------------------------------------------------------- ① align

  #beginAlign(): void {
    this.#setPhase('align');
    this.#check = null;
    this.#track.showArrow(false);
    this.element.dataset.align = 'off';
    this.#ring.clear();
    this.#ring.progress(0);
    this.#copy('얼굴을 원 안에 맞춰 주세요', '화면을 편하게 바라봐 주세요');
    this.#el.checks.innerHTML = (Object.keys(CHECK_NAME) as (keyof typeof CHECK_NAME)[])
      .map((k) => `<li data-check="${k}"><i></i>${CHECK_NAME[k]}</li>`)
      .join('');
    // Unattended: never wait on a check that does not pass.
    if (this.#opts.unattended)
      this.#later(() => {
        this.#forced = true;
        this.#beginSweep();
      }, 8000);
  }

  #onCheck(report: PreconditionReport): void {
    this.#check = report;
    this.#emit('check', report);
    const failing = new Set<string>(report.checks.filter((c) => !c.ok).map((c) => c.name));
    const seen = new Set<string>(report.checks.map((c) => c.name));
    for (const li of this.#el.checks.querySelectorAll<HTMLElement>('li')) {
      const name = li.dataset.check!;
      li.className = !seen.has(name) ? '' : failing.has(name) ? 'is-fail' : 'is-ok';
    }
    const holding = report.reason === 'OK';
    this.element.dataset.align = holding ? 'on' : 'off';
    this.#ring.progress(report.status === 'PASS' ? 1 : report.held_ms / CFG.preconditions.hold_ms);
    if (report.status === 'PASS') {
      this.#copy('좋아요!', '이 자세 그대로 시작할게요');
      this.#beginSweep();
      return;
    }
    const m = report.measurements;
    this.#copy(
      holding ? '좋아요, 잠시 그대로 계세요' : CHECK_FIX[report.reason],
      m.distance_cm !== undefined ? `거리 약 ${Math.round(m.distance_cm)}cm` : '',
    );
    if (report.status === 'REJECT') {
      this.#el.hint.textContent = '이 상태로는 시선을 정확히 재기 어려워요';
      const goOn = () => {
        this.#forced = true;
        this.#beginSweep();
      };
      this.#setSkip({ label: '이대로 진행', run: goOn });
      if (this.#opts.unattended) goOn();
    }
  }

  // ---------------------------------------------------------------- ② head circle

  #beginSweep(): void {
    this.#setPhase('sweep');
    this.#ring.clear();
    this.#sweep = null;
    this.#sweepSkipped = false;
    this.#sweepEnded = false;
    this.#sweepStarted = performance.now();
    this.#tutorialUntil = performance.now() + 2200;
    this.#copy(SWEEP_TEXT.title, SWEEP_TEXT.sub);
    this.#send({ type: 'startSweep', tMs: this.#now() });
    this.#setSkip({
      label: SWEEP_TEXT.skip,
      run: () => {
        this.#sweepSkipped = true;
        this.#startCalibration();
      },
    });
    // A still test video cannot turn its head: record how far it got, then go on.
    if (this.#opts.unattended) this.#later(() => this.#startCalibration(), 1800);
  }

  #onSweep(st: SweepStatus): void {
    this.#sweep = st;
    this.#emit('sweep', st);
    if (st.pointer_deg !== null) this.#tutorialUntil = 0;
    // The arrow reads from the circle's centre, once measured.
    if (st.neutral_deg) this.#track.showArrow(true, st.neutral_deg);
    if (performance.now() >= this.#tutorialUntil)
      this.#ring.ticks(st.ticks, st.pointer_deg, st.reach);
    else this.#ring.ticks(st.ticks, null, 0);
    this.#ring.sectors(GAZE_DIRECTIONS.map((d) => st.direction_progress[d]));

    if (st.state === 'DONE') return this.#sweepDone();
    if (st.state === 'TIMED_OUT') return this.#sweepPartial(st);

    const pct = Math.round(st.progress * 100);
    let hint = st.last_reason
      ? (SWEEP_REASON[st.last_reason] ?? GAUGE_REASON[st.last_reason] ?? '')
      : '';
    if (!hint && st.filled === 0 && st.state === 'SWEEPING' && st.elapsed_ms > 2500 && st.reach < 1)
      hint = SWEEP_TEXT.bigger;
    this.#el.hint.textContent = hint;
    if (st.hint) {
      this.#ring.nudge(GAZE_DIRECTIONS.indexOf(st.hint) * 45);
      this.#copy(`${DIRECTION_NAME[st.hint]} 쪽으로 천천히 돌려 주세요`, `원 ${pct}% 채움`);
    } else {
      this.#ring.nudge(null);
      this.#copy(SWEEP_TEXT.title, st.filled ? `원 ${pct}% 채움` : SWEEP_TEXT.sub);
    }
  }

  #sweepDone(): void {
    this.#setPhase('swept');
    this.#track.showArrow(false);
    this.#ring.ticks(new Array(CFG.sweep.ticks).fill(true), null, 0);
    this.#ring.sectors(new Array(8).fill(1));
    const sigma = this.#sweep?.neutral_sigma_deg;
    this.#copy(
      SWEEP_TEXT.done,
      sigma
        ? `${SWEEP_TEXT.doneSub} (흔들림 ${Math.max(sigma[0], sigma[1]).toFixed(1)}°)`
        : SWEEP_TEXT.doneSub,
    );
    this.#later(() => this.#startCalibration(), 1200);
  }

  /** Time ran out: say which directions are missing, then carry on unless asked to retry. */
  #sweepPartial(st: SweepStatus): void {
    this.#sweepEnded = true;
    this.#setSkip(null);
    this.#ring.nudge(null);
    const names = (ds: readonly GazeDirection[]) => ds.map((d) => DIRECTION_NAME[d]).join(' · ');
    const lost = (Object.keys(st.lost) as GazeDirection[]).filter((d) => (st.lost[d] ?? 0) > 0);
    this.#copy(SWEEP_TEXT.partial, `확인하지 못한 방향 · ${names(st.missing)}`);
    this.#el.hint.textContent = lost.length
      ? `${names(lost)} 쪽으로 돌리면 얼굴을 놓쳤어요. 그쪽을 볼 때는 "측정 불가"로 기록될 수 있어요.`
      : '';
    let left = 5;
    const tick = () => {
      if (left === 0) return this.#startCalibration();
      this.#setActions([
        { label: '다시 하기', run: () => this.#beginSweep() },
        { label: `계속 (${left})`, primary: true, run: () => this.#startCalibration() },
      ]);
      left -= 1;
      this.#later(tick, 1000);
    };
    tick();
  }

  // ---------------------------------------------------------------- ③ three looks

  #startCalibration(): void {
    this.#cueIndex = 0;
    this.#cueTimeouts = {};
    this.#placement = null;
    this.#result = null;
    this.#baseline = null;
    // The picture stays; the arrow shows where the head points from the circle's centre.
    const neutral = this.#sweep?.neutral_deg ?? null;
    this.#track.showArrow(neutral !== null, neutral);
    this.#send({ type: 'resetCalibration' });
    this.#setPhase('calib');
    this.#ring.clear();
    this.#ring.progress(0);
    this.#showCue();
  }

  #showCue(): void {
    const cue = CUE_ORDER[this.#cueIndex]!;
    this.#cueStarted = false;
    if (cue === 'SCREEN') this.#baseline = null;
    this.element.dataset.cue = cue;
    this.element.dataset.cueState = 'ready';
    this.#copy(CUE_TEXT[cue].title, CUE_TEXT[cue].sub);
    // Two timeouts on this look: take it without the direction check rather than loop.
    const checkDirection = (this.#cueTimeouts[cue] ?? 0) < 2;
    this.#el.hint.textContent = checkDirection ? '' : '이번에는 방향 확인 없이 받을게요';
    this.#targetProgress(0);
    this.#ring.progress(0);
    this.#renderMeter(null);
    this.#later(
      () => {
        if (CUE_ORDER[this.#cueIndex] !== cue) return;
        this.#cueStarted = true;
        this.element.dataset.cueState = 'collect';
        this.#send({ type: 'startCue', cue, tMs: this.#now(), checkDirection });
      },
      this.#opts.unattended ? 0 : 1600,
    );
  }

  #onGauge(status: GaugeStatus): void {
    if (!this.#cueStarted || status.cue !== CUE_ORDER[this.#cueIndex]) return;
    this.#emit('gauge', { status, baseline: this.#baseline });
    // The screen-centre look is the ring round your face; the lens and script have their own target.
    if (status.cue === 'SCREEN') this.#ring.progress(status.progress);
    else this.#targetProgress(status.progress);
    const reason =
      status.last_reason && status.last_reason !== 'SETTLING' ? status.last_reason : null;
    this.#el.hint.textContent = reason
      ? (GAUGE_REASON[reason] ?? '')
      : status.cue === 'SCREEN'
        ? this.#baselineNote(status)
        : '';
    this.#renderMeter(status, reason !== null);
    if (!status.finished) return;
    this.#cueStarted = false;
    if (status.state === 'TIMED_OUT') {
      const cue = CUE_ORDER[this.#cueIndex]!;
      this.#cueTimeouts[cue] = (this.#cueTimeouts[cue] ?? 0) + 1;
      // A still test video never turns its head: having shown the check hold, take the look without it.
      if (this.#opts.unattended) {
        this.#cueTimeouts[cue] = 2;
        this.#later(() => this.#showCue(), 0);
        return;
      }
      this.element.dataset.cueState = 'timeout';
      this.#el.hint.textContent = `${GAUGE_REASON[status.dominant_reason ?? ''] ?? '시간 안에 끝내지 못했어요'} · 잠시 뒤 다시 할게요`;
      this.#later(() => this.#showCue(), 1800);
      return;
    }
    this.element.dataset.cueState = 'done';
    this.#later(() => this.#nextCue(), this.#opts.unattended ? 0 : 450);
  }

  /** The screen-centre look against the head circle's centre: confirming, confirmed, or measured again. */
  #baselineNote(status: GaugeStatus): string {
    const b = this.#baseline;
    if (b && b.confirmed && b.seeded)
      return `고개 원에서 잰 정면 기준이 맞아요 (${b.shift_deg.toFixed(1)}° 차이)`;
    if (b && !b.confirmed)
      return `고개 원 때와 자세가 ${b.shift_deg.toFixed(0)}° 달라 화면 가운데를 다시 재고 있어요`;
    if (!b && status.target < CFG.calibration.target_good_frames)
      return '고개 원에서 잰 정면 기준을 확인하고 있어요';
    return '';
  }

  /** The big gauge under the instruction: three steps, this look's fill, good frames counted. */
  #renderMeter(status: GaugeStatus | null, paused = false): void {
    const now = this.#cueIndex;
    for (const li of this.#el.meter.querySelectorAll<HTMLElement>('.gzc-meter-steps li')) {
      const i = CUE_ORDER.indexOf(li.dataset.cue as Cue);
      const done = i < now || (i === now && status?.state === 'DONE');
      li.dataset.state = done ? 'done' : i === now ? 'now' : 'next';
      const fill = done ? 1 : i === now ? (status?.progress ?? 0) : 0;
      li.querySelector<HTMLElement>('i')!.style.transform = `scaleX(${fill})`;
    }
    const p = status?.progress ?? 0;
    this.#el.meterFill.style.transform = `scaleX(${p})`;
    this.#el.meterPct.textContent = `${Math.round(p * 100)}%`;
    this.#el.meterCount.textContent = !status
      ? '곧 시작해요'
      : status.state === 'DONE'
        ? '완료'
        : paused
          ? `멈춤 · 좋은 프레임 ${status.good} / ${status.target}`
          : `좋은 프레임 ${status.good} / ${status.target}`;
    this.#el.meter.dataset.paused = paused ? '1' : '0';
  }

  #targetProgress(v: number): void {
    this.#el.targetArc.style.strokeDashoffset = `${TARGET_C * (1 - Math.max(0, Math.min(1, v)))}`;
  }

  #nextCue(): void {
    const cue = CUE_ORDER[this.#cueIndex];
    if (cue === 'BOTTOM') return this.#send({ type: 'finishCalibration' });
    this.#cueIndex += 1;
    // Lens and screen centre are both in: read the camera position before the script look.
    if (CUE_ORDER[this.#cueIndex] === 'BOTTOM') return this.#send({ type: 'estimatePlacement' });
    this.#showCue();
  }

  #afterPlacement(): void {
    if (this.#phase !== 'calib') return;
    const p = this.#placement;
    // A camera read as below or beside the screen is worth stopping for; an
    // unreadable position (same head posture for lens and screen) is not.
    if (p && !p.supported && p.placement !== 'INCONCLUSIVE' && !this.#opts.unattended) {
      this.element.dataset.cueState = 'placement';
      this.#copy(
        `카메라가 ${PLACEMENT_NAME[p.placement]}에 있는 것 같아요`,
        PLACEMENT_HINT[p.placement] ?? '',
      );
      this.#setActions([
        { label: '처음부터 다시', run: () => this.#startCalibration() },
        {
          label: '이대로 계속',
          primary: true,
          run: () => {
            this.#forced = true;
            this.#setActions([]);
            this.#showCue();
          },
        },
      ]);
      return;
    }
    this.#showCue();
  }

  // ---------------------------------------------------------------- result

  #onCalibrated(msg: Extract<FromWorker, { type: 'calibrated' }>): void {
    const placement = msg.placement ?? this.#placement;
    this.#placement = placement;
    this.#lensPose = msg.model?.anchors.CAMERA ?? this.#sweep?.neutral_deg ?? null;
    this.#result = {
      quality: msg.quality,
      model: msg.model,
      placement,
      classes: msg.classes,
      preconditions: this.#check,
      sweep: this.#sweep,
      sweepSkipped: this.#sweepSkipped,
      baseline: this.#baseline,
      forced: this.#forced,
      notes: this.#notesOf(msg.quality, placement),
    };
    this.#emit('calibrated', this.#result);
    if (this.#phase === 'calib') this.#showResult();
  }

  #notesOf(q: CalibrationQualityDict, p: PlacementResultDict | null): string[] {
    const notes: string[] = [];
    if (q.status !== 'OK' && q.reason) notes.push(CALIBRATION_HINT[q.reason] ?? q.reason);
    if (q.warnings.some((w) => w.startsWith('SCREEN_MERGED')))
      notes.push(CALIBRATION_HINT.SCREEN_MERGED!);
    if (q.warnings.some((w) => w.startsWith('INVERTED_PITCH')))
      notes.push(CALIBRATION_HINT.INVERTED_PITCH!);
    if (p && !p.supported)
      notes.push(PLACEMENT_HINT[p.placement === 'INCONCLUSIVE' ? p.reason : p.placement] ?? '');
    const sw = this.#sweep;
    if (this.#sweepSkipped || (sw && sw.state !== 'DONE' && sw.missing.length > 4))
      notes.push(
        '고개 원 확인을 건너뛰어, 고개를 돌렸을 때 얼굴을 잘 따라가는지는 확인하지 못했어요.',
      );
    else if (sw && sw.state !== 'DONE' && sw.missing.length)
      notes.push(
        `고개 원 확인에서 ${sw.missing.map((d) => DIRECTION_NAME[d]).join(' · ')} 쪽은 확인하지 못했어요.`,
      );
    return notes.filter(Boolean);
  }

  #showResult(): void {
    const r = this.#result!;
    const q = r.quality;
    const ok = q.status === 'OK';
    // The baseline is set and there is nothing to say: straight to live, no verdict card.
    if (ok && !r.notes.length && this.#opts.autoLive) {
      this.#toast('보정 완료 · 실시간 확인을 시작해요');
      this.goLive();
      return;
    }
    this.#setPhase('result');
    this.element.dataset.ok = ok ? '1' : '0';
    const sep = q.pair_separation['CAMERA-BOTTOM'];
    this.#copy(ok ? '준비 완료' : '보정을 다시 하는 게 좋아요');
    this.#el.sub.innerHTML = `
      <dl class="gzc-metrics">
        <div><dt>렌즈–대본 분리</dt><dd>${sep !== undefined ? sep.toFixed(1) : '–'}<small>σ</small></dd></div>
        <div><dt>자체 검증 정확도</dt><dd>${Math.round(q.loo_accuracy * 100)}<small>%</small></dd></div>
        <div><dt>판정할 수 있는 곳</dt><dd class="is-text">${r.classes.map((c) => STATE_SHORT[c]).join(' · ') || '없음'}</dd></div>
      </dl>
      ${r.notes.map((n) => `<p class="gzc-note">${n}</p>`).join('')}`;
    const goLive = (forced: boolean) => () => {
      if (forced) this.#forced = r.forced = true;
      this.goLive();
    };
    if (ok) {
      this.#setActions([
        { label: '다시 보정', run: () => this.#startCalibration() },
        { label: '바로 시작', primary: true, run: goLive(false) },
      ]);
      if (this.#opts.autoLive || this.#opts.unattended) {
        this.#el.hint.textContent = '잠시 후 실시간 확인으로 넘어가요';
        this.#later(goLive(false), this.#opts.unattended ? 0 : 3200);
      }
      return;
    }
    this.#setActions([
      { label: '다시 보정', primary: true, run: () => this.#startCalibration() },
      ...(r.classes.length ? [{ label: '그래도 시작', run: goLive(true) }] : []),
    ]);
    if (this.#opts.unattended) this.#later(goLive(true), 0);
  }

  // ---------------------------------------------------------------- live

  #onDecision(d: FrameDecision): void {
    const el = this.#el.status;
    el.dataset.state = d.state;
    el.querySelector('b')!.textContent =
      d.state === 'OTHER' && d.direction
        ? `${STATE_SHORT.OTHER} ${DIRECTION_ARROW[d.direction]} ${DIRECTION_NAME[d.direction]}`
        : STATE_NAME[d.state];
    const r = d.uncertain_reason ?? '';
    const cond = d.condition;
    let why = '';
    if (d.state === 'UNCERTAIN') why = UNCERTAIN_REASON[r] ?? GAUGE_REASON[r] ?? '';
    else if (cond && cond.reliability < 0.5 && cond.issues[0])
      why = `측정 신뢰도 ${Math.round(cond.reliability * 100)}% · ${ISSUE[cond.issues[0]]}`;
    el.querySelector('span')!.textContent = why;
    this.#emit('decision', d);
  }

  #onReanchor(st: ReanchorStatus): void {
    if (st.state === this.#reanchorState) return;
    this.#reanchorState = st.state;
    this.#emit('reanchor', st);
    if (st.state === 'DONE') {
      const s = st.shift_deg;
      this.#toast(
        s
          ? `렌즈 기준을 다시 맞췄어요 (${Math.hypot(s[0], s[1]).toFixed(1)}° 이동)`
          : '렌즈 기준을 다시 맞췄어요',
      );
    } else if (st.state === 'REJECTED' || st.state === 'TIMED_OUT' || st.state === 'UNSUPPORTED')
      this.#toast('렌즈 기준을 다시 맞추지 못했어요 · 처음 자리에서 렌즈를 바라봐 주세요');
  }

  // ---------------------------------------------------------------- helpers

  #setBadge(valid: boolean, reason: string | null): void {
    const badge = this.#el.badge;
    badge.dataset.ok = valid ? '1' : '0';
    badge.querySelector('span')!.textContent = valid
      ? '얼굴 인식됨'
      : reason === 'NO_FACE' || !reason
        ? '얼굴을 찾는 중'
        : (GAUGE_REASON[reason] ?? '얼굴 확인 중');
  }

  #toast(text: string): void {
    const t = this.#el.toast;
    t.textContent = text;
    t.classList.add('is-on');
    window.clearTimeout(this.#toastTimer);
    this.#toastTimer = window.setTimeout(() => t.classList.remove('is-on'), 4000);
  }

  #animate = (now: number): void => {
    if (this.#destroyed) return;
    if (this.#phase !== 'result') this.#track.render(now);
    // Before the head moves, the coral tick goes round once to show what to do.
    if (this.#phase === 'sweep' && now < this.#tutorialUntil) {
      const angle = (((now - this.#sweepStarted) / 1800) * 360) % 360;
      this.#ring.ticks(this.#sweep?.ticks ?? [], angle, 0.85);
    }
    this.#raf = requestAnimationFrame(this.#animate);
  };
}
