/**
 * Local demo page: a stand-in for the frontend page that will host the camera
 * view.  The camera view (`src/camera`, the AI side's part) draws everything
 * inside the camera picture; this page does only what the frontend will do
 * around it:
 *
 *   - the box the view fills (#stage: the whole window, or a 16:9 card with ?box),
 *   - the start card, the camera permission and going full screen,
 *   - a dev panel next to the picture in live, built only from the view's events:
 *     the frontend's own 1 s vote (`TemporalVoter`, imported read-only) and the
 *     agent evidence (`engine/evidence.ts`) the coach and review agents read.
 *
 * So everything this page shows is what the frontend can get from the view.
 *
 * `?smoke` runs the whole flow unattended and leaves a summary in
 * `window.__smoke` (scripts/smoke.mjs drives it in headless Chrome).
 */
import './styles.css';
import { TemporalVoter } from '@fe/workers/temporalVoter';
import { GazeCameraView, type CameraPhase, type SetupResult } from '../camera';
import {
  DIRECTION_ARROW,
  DIRECTION_NAME,
  GAUGE_REASON,
  ISSUE,
  STATE_NAME,
  UNCERTAIN_REASON,
  ZONE_NAME,
} from '../camera/text';
import type { CalibrationQualityDict } from '../engine';
import { makeConfig } from '../engine/config';
import type { ConditionState } from '../engine/condition';
import type { FrameDecision } from '../engine/contract';
import { GazeEvidenceRecorder, sampleToDict, type GazeSample } from '../engine/evidence';
import type { GaugeStatus } from '../engine/gauge';
import type { PlacementResultDict } from '../engine/placement';
import type { PreconditionReport } from '../engine/preconditions';
import { aimOffset, screenRegion, type ReferenceModelData } from '../engine/reference';
import type { SweepStatus } from '../engine/sweep';
import { GAZE_DIRECTIONS, type GazeDirection, type StateClass } from '../engine/types';
import {
  GAZE_ISSUE_NAME,
  GazeTimeline,
  issuesNow,
  takeSummary,
  type GazeIssue,
} from './evidence-preview';

type Stage = 'intro' | 'flow' | 'live';

const PARAMS = new URLSearchParams(location.search);
const SMOKE = PARAMS.has('smoke');
const CFG = makeConfig();
const EVIDENCE = CFG.evidence;
const secs = (ms: number) => `${ms / 1000}`;

const $ = <T extends HTMLElement = HTMLElement>(sel: string) => document.querySelector<T>(sel)!;
const stageBox = $('#stage');
const panel = $('#panel');
stageBox.classList.toggle('boxed', PARAMS.has('box'));

const view = new GazeCameraView(stageBox, { unattended: SMOKE });

const state = {
  stage: 'intro' as Stage,
  ready: false,
  version: '',
  isolated: false,
  processMs: [] as number[],
  detectMs: [] as number[],
  /** Why recent frames were "판정 보류" (t_ms, reason), for the dev panel. */
  uncertain: [] as [number, string][],
  result: null as SetupResult | null,
  voter: new TemporalVoter(),
  zone: null as { zone: string; confidence: number; sampleCount: number } | null,
  evidence: new GazeEvidenceRecorder(EVIDENCE),
};

const smoke = {
  done: false,
  error: null as string | null,
  errorReason: null as string | null,
  ready: false,
  isolated: false,
  version: '',
  frames: 0,
  faces: 0,
  phases: [] as string[],
  check: null as PreconditionReport | null,
  sweep: null as SweepStatus | null,
  gauges: [] as GaugeStatus[],
  placement: null as PlacementResultDict | null,
  quality: null as CalibrationQualityDict | null,
  modelCloneOk: false,
  decisions: [] as FrameDecision[],
  zones: [] as unknown[],
  processMsP95: 0,
  processMsMedian: 0,
  processMsWarm: [] as number[],
  detectMsMedian: 0,
  headSamples: [] as [number, number][],
  evidence: { samples: [] as GazeSample[], issues: [] as GazeIssue[], summary: {} as unknown },
};
(window as unknown as { __smoke: typeof smoke }).__smoke = smoke;

// ---------------------------------------------------------------- the view's events

view.on('ready', ({ version, isolated }) => {
  Object.assign(state, { ready: true, version, isolated });
  Object.assign(smoke, { ready: true, version, isolated });
  renderPanel();
});
view.on('error', ({ message, reason }) => {
  smoke.error = message;
  smoke.errorReason = reason ?? null;
});
view.on('frame', (f) => {
  state.processMs.push(f.processMs);
  state.detectMs.push(f.detectMs);
  if (state.processMs.length > 400) state.processMs.shift();
  if (state.detectMs.length > 400) state.detectMs.shift();
  smoke.frames += 1;
  if (f.faceValid) {
    smoke.faces += 1;
    if (smoke.headSamples.length < 50) smoke.headSamples.push([f.headYawDeg, f.headPitchDeg]);
  }
});
view.on('phase', ({ phase }) => {
  if (phase !== 'idle' && phase !== 'live') smoke.phases.push(phase);
  go(stageOf(phase));
});
view.on('check', (report) => (smoke.check = report));
view.on('sweep', (status) => (smoke.sweep = status));
view.on('gauge', ({ status }) => {
  if (status.finished) smoke.gauges.push(status);
});
view.on('placement', (result) => (smoke.placement = result));
view.on('calibrated', (result) => {
  state.result = result;
  smoke.quality = result.quality;
  smoke.placement = result.placement ?? smoke.placement;
  try {
    smoke.modelCloneOk =
      result.model === null ||
      JSON.stringify(structuredClone(result.model)) === JSON.stringify(result.model);
  } catch {
    smoke.modelCloneOk = false;
  }
});
view.on('decision', onDecision);

const stageOf = (phase: CameraPhase): Stage =>
  phase === 'idle' ? 'intro' : phase === 'live' ? 'live' : 'flow';

// ---------------------------------------------------------------- page

function go(stage: Stage): void {
  const entering = stage !== state.stage;
  state.stage = stage;
  document.body.dataset.stage = stage;
  if (stage === 'live' && entering) {
    state.voter = new TemporalVoter();
    state.zone = null;
    state.uncertain = [];
    state.evidence.reset();
  }
  if (entering) renderPanel();
}

function renderPanel(): void {
  panel.classList.remove('enter');
  panel.getBoundingClientRect(); // restart the entrance animation
  panel.classList.add('enter');
  if (state.stage === 'live') {
    panel.innerHTML = liveHtml();
    $('#live-reanchor').addEventListener('click', () => view.reanchor());
    $('#live-restart').addEventListener('click', () => view.startSetup('align'));
    return;
  }
  panel.innerHTML = introHtml();
  $('#start').addEventListener('click', () => {
    // The click is the user gesture full screen needs: ask before awaiting the camera.
    requestFullscreen();
    startCamera()
      .then(() => view.startSetup())
      .catch((err) => {
        exitFullscreen();
        showToast(`카메라를 켜지 못했어요: ${err instanceof Error ? err.message : err}`);
      });
  });
}

/** The frontend's camera settings (useCameraStream): 640x480, video only here. */
async function startCamera(): Promise<void> {
  const stream = await navigator.mediaDevices.getUserMedia({
    video: { width: 640, height: 480, frameRate: { ideal: 15 } },
    audio: false,
  });
  await view.attach(stream);
}

function introHtml(): string {
  return `
    <p class="eyebrow">시선 인식 준비</p>
    <h1 class="panel-title big">발표 연습 전에<br/>얼굴과 시선을 맞춰 볼게요</h1>
    <ol class="intro-steps">
      <li><span>1</span><div><b>얼굴 확인</b><p>원 안에 얼굴을 맞추고, 고개를 천천히 돌려 원을 채워요.</p></div></li>
      <li><span>2</span><div><b>시선 보정</b><p>화면 가운데 → 렌즈 → 대본 자리를 차례로 바라봐요.</p></div></li>
      <li><span>3</span><div><b>실시간 확인</b><p>지금 어디를 보고 있는지 바로 보여 드려요.</p></div></li>
    </ol>
    <button id="start" class="btn primary wide" ${state.ready ? '' : 'disabled'}>
      ${state.ready ? '카메라 켜고 시작하기' : '엔진 준비 중…'}
    </button>
    <p class="fineprint">1분 정도 걸려요. 영상은 이 기기 밖으로 나가지 않고, 아무것도 저장하지 않아요.</p>
    <p class="fineprint host-note">이 카드와 오른쪽 패널은 데모 페이지 몫이에요. 실제 서비스에서는 프론트엔드 화면이 이 자리를 맡고, 카메라 화면 안쪽만 시선 모듈이 그려요.</p>`;
}

// ---------------------------------------------------------------- live dev panel

function liveHtml(): string {
  const bars = (['CAMERA', 'SCREEN', 'BOTTOM', 'OTHER'] as StateClass[])
    .map(
      (c) =>
        `<li class="bar" data-class="${c}"><span>${STATE_NAME[c]}</span><b><i></i></b><em>0%</em></li>`,
    )
    .join('');
  return `
    <p class="eyebrow">실시간 확인 · 카메라 화면이 내는 값</p>
    <div class="now">
      <p class="now-label">지금 보는 곳</p>
      <p class="now-state" id="now-state">–</p>
      <p class="now-zone" id="now-zone">1초 판정 대기 중</p>
      <p class="now-reason" id="now-reason"></p>
    </div>
    <div class="aim">
      <svg class="aim-map" id="aim-map" viewBox="-40 -30 80 60" aria-hidden="true"></svg>
      <dl class="aim-vals">
        <div><dt>좌우</dt><dd id="aim-x">–</dd></div>
        <div><dt>상하</dt><dd id="aim-y">–</dd></div>
      </dl>
      <p class="aim-note">화면 가운데를 볼 때가 0° · 점선 밖으로 나가면 '다른 곳'</p>
    </div>
    <ul class="bars">${bars}</ul>
    <div class="reliability">
      <div class="rel-head"><span>측정 신뢰도</span><b id="rel-value">–</b></div>
      <div class="rel-track"><div class="rel-fill" id="rel-fill"></div></div>
      <ul class="issues" id="issues"></ul>
      <p class="jitter" id="jitter"></p>
      <div class="drift" id="drift" data-level="ok">
        <div class="drift-head"><span>처음 위치에서</span><b id="drift-text">–</b></div>
        <div class="drift-track"><div class="drift-fill" id="drift-fill"></div><i></i></div>
        <p class="drift-note" id="drift-note">보정할 때 앉은 자리가 기준이에요</p>
      </div>
    </div>
    <section class="agent">
      <div class="agent-head"><span>코치에게 가는 시선 신호</span><small>1초 기록 · 최근 ${secs(EVIDENCE.short_window_ms)}–${secs(EVIDENCE.long_window_ms)}초</small></div>
      <ul class="agent-issues" id="agent-issues"><li class="empty">기록을 모으는 중이에요</li></ul>
      <dl class="take">
        <div><dt>청중 응시</dt><dd id="take-eye">–</dd></div>
        <div><dt>측정된 시간</dt><dd id="take-cov">–</dd></div>
        <div><dt>문제 구간</dt><dd id="take-prob">–</dd></div>
      </dl>
      <p class="take-dirs" id="take-dirs"></p>
    </section>
    <div class="actions">
      <button id="live-reanchor" class="btn ghost">렌즈 다시 맞추기</button>
      <button id="live-restart" class="btn ghost">보정 다시 하기</button>
    </div>
    <details class="dev"><summary>개발 정보</summary><pre id="dev"></pre></details>
    <details class="dev"><summary>보정 결과 (calibrated 이벤트)</summary><pre class="json" id="setup-json"></pre></details>
    <details class="dev"><summary>에이전트 입력 (JSON)</summary><pre class="json" id="agent-json"></pre></details>`;
}

function onDecision(d: FrameDecision): void {
  if (state.stage !== 'live') return;
  if (SMOKE && smoke.decisions.length < 40) smoke.decisions.push(d);
  // The frontend's own adapter rule: no face -> no vote.
  if (d.face_valid)
    state.voter.push({ zone: d.label, confidence: Math.max(d.p_camera, d.p_bottom) }, d.t_ms);
  const z = state.voter.decide(d.t_ms);
  if (z) {
    state.zone = z;
    smoke.zones.push(z);
    // The 1 s decision goes back to the view as the rehearsal border.
    view.setZone(z.zone as 'CAMERA' | 'BOTTOM' | 'UNCERTAIN');
    $('#now-zone').textContent =
      `1초 판정 · ${ZONE_NAME[z.zone as keyof typeof ZONE_NAME]} (${Math.round(z.confidence * 100)}%)`;
  }
  renderReason(d);
  renderAim(d);
  const nowState = $('#now-state');
  nowState.innerHTML = d.direction
    ? `${STATE_NAME[d.state]} <span class="dir">${DIRECTION_ARROW[d.direction]} ${DIRECTION_NAME[d.direction]}</span>`
    : STATE_NAME[d.state];
  nowState.dataset.state = d.state;
  if (state.evidence.record(d).length) renderEvidence();
  for (const li of document.querySelectorAll<HTMLElement>('.bar')) {
    const c = li.dataset.class as StateClass;
    const p = d.probs?.[c];
    li.classList.toggle('off', p === undefined);
    const v = p ?? 0;
    li.querySelector<HTMLElement>('i')!.style.transform = `scaleX(${v})`;
    li.querySelector('em')!.textContent = p === undefined ? '–' : `${Math.round(v * 100)}%`;
    li.classList.toggle('lead', d.state === c);
  }
  const cond = d.condition;
  if (cond) {
    $('#rel-value').textContent = `${Math.round(cond.reliability * 100)}%`;
    const fill = $('#rel-fill');
    fill.style.transform = `scaleX(${cond.reliability})`;
    fill.dataset.level = cond.reliability >= 0.8 ? 'high' : cond.reliability >= 0.5 ? 'mid' : 'low';
    $('#issues').innerHTML = [
      ...cond.issues.map((i) => `<li>${ISSUE[i]}</li>`),
      ...cond.notices.map((i) => `<li class="notice">참고 · ${ISSUE[i]} (신뢰도 영향 없음)</li>`),
    ].join('');
    $('#jitter').textContent =
      cond.jitter_deg === null ? '' : `고개 방향 흔들림 ${cond.jitter_deg.toFixed(1)}°`;
    renderDrift(cond.drift, cond.issues.includes('MOVED_TOO_FAR'));
  }
  const p95 = percentile(state.processMs, 95);
  $('#dev').textContent = [
    `engine     ${state.version}${state.isolated ? '  (crossOriginIsolated)' : ''}`,
    `frame      ${d.state} -> FE zone ${d.face_valid ? d.label : 'null (no sample)'}`,
    `head       yaw ${d.head_yaw_deg.toFixed(1)}°  pitch ${d.head_pitch_deg.toFixed(1)}°`,
    `outside    ${d.offset_deg ? `right ${d.offset_deg[0].toFixed(1)}°  up ${d.offset_deg[1].toFixed(1)}°` : '–'}${d.direction ? `  -> ${d.direction}` : ''}`,
    `latency    p95 ${p95.toFixed(1)} ms (worker) · detect median ${percentile(state.detectMs, 50).toFixed(1)} ms`,
    `보류 이유  ${reasonCounts(d.t_ms) || '최근 30초 없음'}`,
  ].join('\n');
  const setup = $('#setup-json');
  if (!setup.textContent && state.result) setup.textContent = setupJson(state.result);
  if (SMOKE && smoke.decisions.length >= 24) finishSmoke();
}

/** The calibration as the view hands it over, minus the bulky model numbers. */
function setupJson(r: SetupResult): string {
  const { model, ...rest } = r;
  return JSON.stringify(
    { ...rest, model: model ? { classes: model.classes, anchors: model.anchors } : null },
    null,
    2,
  );
}

/** The calibrated regions in "head from the screen centre" degrees, once per calibration. */
function drawAimMap(m: ReferenceModelData): void {
  const svg = document.querySelector<SVGSVGElement>('#aim-map');
  if (!svg) return;
  const cal = CFG.calibration;
  // A model box as an SVG rect: x = right, y = -up.
  const rect = (yawLo: number, yawHi: number, pitchLo: number, pitchHi: number) => {
    const [x1, y1] = aimOffset(m, yawHi, pitchHi);
    const [x2, y2] = aimOffset(m, yawLo, pitchLo);
    return `x="${Math.min(x1, x2)}" y="${-Math.max(y1, y2)}" width="${Math.abs(x2 - x1)}" height="${Math.abs(y2 - y1)}"`;
  };
  const parts = [
    '<line class="axis" x1="-40" y1="0" x2="40" y2="0"/><line class="axis" x1="0" y1="-30" x2="0" y2="30"/>',
  ];
  const r = screenRegion(m, cal.screen_min_halfwidth_deg);
  const g = cal.other_margin_deg;
  parts.push(
    `<rect class="limit" ${rect(r.yawLo - g, r.yawHi + g, r.pitchLo - g, r.pitchHi + g)}/>`,
  );
  for (const cue of ['SCREEN', 'BOTTOM'] as const) {
    const b = m.boxes[cue];
    if (b && b.yawHi - b.yawLo > 0.01)
      parts.push(
        `<rect class="zone zone-${cue}" ${rect(b.yawLo, b.yawHi, b.pitchLo, b.pitchHi)}/>`,
      );
  }
  const lens = m.anchors.CAMERA;
  if (lens) {
    const [x, y] = aimOffset(m, lens[0], lens[1]);
    parts.push(`<circle class="zone zone-CAMERA" cx="${x}" cy="${-y}" r="1.6"/>`);
  }
  parts.push(
    '<text x="-39" y="-26">← 왼쪽</text><text x="39" y="-26" text-anchor="end">오른쪽 →</text>',
    '<circle class="me" id="aim-me" r="2.4" cx="0" cy="0" visibility="hidden"/>',
  );
  svg.innerHTML = parts.join('');
  svg.dataset.drawn = '1';
}

function renderAim(d: FrameDecision): void {
  const svg = document.querySelector<SVGSVGElement>('#aim-map');
  if (!svg) return;
  const model = state.result?.model;
  if (model && svg.dataset.drawn !== '1') drawAimMap(model);
  const me = document.querySelector<SVGCircleElement>('#aim-me');
  const aim = d.aim_deg;
  svg.dataset.state = d.state;
  if (!aim) {
    me?.setAttribute('visibility', 'hidden');
    $('#aim-x').textContent = '–';
    $('#aim-y').textContent = '–';
    return;
  }
  const [x, y] = aim;
  if (me) {
    me.setAttribute('visibility', 'visible');
    me.setAttribute('cx', `${Math.max(-38, Math.min(38, x))}`);
    me.setAttribute('cy', `${Math.max(-28, Math.min(28, -y))}`);
  }
  const deg = (v: number, pos: string, neg: string) =>
    Math.abs(v) < 0.5 ? '가운데' : `${v > 0 ? pos : neg} ${Math.abs(v).toFixed(0)}°`;
  $('#aim-x').textContent = deg(x, '오른쪽', '왼쪽');
  $('#aim-y').textContent = deg(y, '위', '아래');
}

/** Why this frame is not a plain answer: "판정 보류" with its reason, or OTHER's meaning. */
function renderReason(d: FrameDecision): void {
  const el = $('#now-reason');
  if (d.state === 'UNCERTAIN') {
    const r = d.uncertain_reason ?? '';
    if (r) state.uncertain.push([d.t_ms, r]);
    el.textContent = `이유 · ${UNCERTAIN_REASON[r] ?? GAUGE_REASON[r] ?? r ?? '알 수 없음'}`;
  } else if (d.state === 'OTHER') {
    el.textContent = '보정한 화면·렌즈·대본 영역 밖을 보고 있어요';
  } else el.textContent = '';
  // The frontend's 3 zones have no "elsewhere": OTHER counts there as 판정 불가.
  if (state.zone?.zone === 'UNCERTAIN' && d.state === 'OTHER')
    $('#now-zone').textContent =
      `1초 판정 · 판정 불가 (프론트엔드 3구역에서는 '다른 곳'도 판정 불가로 셉니다)`;
  while (state.uncertain.length && d.t_ms - state.uncertain[0]![0] > 30_000)
    state.uncertain.shift();
}

function reasonCounts(tMs: number): string {
  const counts = new Map<string, number>();
  for (const [t, r] of state.uncertain)
    if (tMs - t <= 30_000) counts.set(r, (counts.get(r) ?? 0) + 1);
  return [...counts]
    .sort((a, b) => b[1] - a[1])
    .map(([r, n]) => `${r} ${n}`)
    .join(' · ');
}

/** How far the presenter moved from where they sat while calibrating, and what it costs. */
function renderDrift(drift: ConditionState['drift'], unusable: boolean): void {
  const box = $('#drift');
  if (!drift) {
    box.dataset.level = 'ok';
    $('#drift-text').textContent = '–';
    return;
  }
  const parts: string[] = [];
  const side = (v: number, pos: string, neg: string) =>
    Math.abs(v) >= 0.5 ? `${v > 0 ? pos : neg} ${Math.abs(v).toFixed(1)}cm` : '';
  parts.push(side(drift.right_cm, '오른쪽', '왼쪽'), side(drift.up_cm, '위', '아래'));
  if (Math.abs(drift.closer_cm) >= 1)
    parts.push(
      `${Math.abs(drift.closer_cm).toFixed(0)}cm ${drift.closer_cm > 0 ? '가까워짐' : '멀어짐'}`,
    );
  $('#drift-text').textContent = parts.filter(Boolean).join(' · ') || '그대로';
  const share = Math.min(1, drift.deg / drift.fail_deg);
  $('#drift-fill').style.transform = `scaleX(${share})`;
  const level = unusable
    ? 'fail'
    : drift.deg >= drift.fail_deg
      ? 'over'
      : drift.deg > drift.warn_deg
        ? 'warn'
        : 'ok';
  box.dataset.level = level;
  $('#drift-note').textContent = unusable
    ? '측정 불가 · 처음 자리로 돌아오거나 "렌즈 다시 맞추기"를 눌러 주세요'
    : `판정 오차 약 ${drift.deg.toFixed(1)}° · 한계 ${drift.fail_deg.toFixed(1)}° (보정 지점 간격)`;
}

/** Once per completed 1 s record: the coach's issues now and the take so far. */
function renderEvidence(): void {
  const rec = state.evidence;
  const issues = issuesNow(rec.samples, EVIDENCE);
  const summary = takeSummary(new GazeTimeline(rec.samples), EVIDENCE) as {
    eye_contact_ratio?: number | null;
    coverage?: number;
    measured_ms?: number;
    problem_segments: unknown[];
    other_direction_ms?: Record<GazeDirection, number>;
  };
  $('#agent-issues').innerHTML = issues.length
    ? issues.map(issueHtml).join('')
    : '<li class="empty">지금은 지적할 시선 문제가 없어요</li>';
  const pct = (v: number | null | undefined) => (v == null ? '–' : `${Math.round(v * 100)}%`);
  $('#take-eye').textContent = pct(summary.eye_contact_ratio);
  $('#take-cov').textContent = pct(summary.coverage);
  $('#take-prob').textContent = `${summary.problem_segments.length}개`;
  const dirs = GAZE_DIRECTIONS.map((g) => [g, summary.other_direction_ms?.[g] ?? 0] as const)
    .filter(([, ms]) => ms > 0)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3);
  $('#take-dirs').textContent = dirs.length
    ? `다른 곳을 본 방향 · ${dirs.map(([g, ms]) => `${DIRECTION_NAME[g]} ${Math.round(ms / 1000)}초`).join(' · ')}`
    : '';
  const last = rec.samples.at(-1);
  $('#agent-json').textContent = JSON.stringify(
    {
      last_sample: last ? sampleToDict(last) : null,
      coach_issues: issues,
      review_summary: summary,
    },
    null,
    2,
  );
  if (SMOKE) {
    smoke.evidence = {
      samples: rec.samples.map(sampleToDict),
      issues,
      summary,
    };
  }
}

function issueHtml(i: GazeIssue): string {
  const longS = secs(EVIDENCE.long_window_ms);
  const shortS = secs(EVIDENCE.short_window_ms);
  const ev = i.evidence as Record<string, number | string | null>;
  const pct = (v: unknown) => `${Math.round(Number(v ?? 0) * 100)}%`;
  let title: string = GAZE_ISSUE_NAME[i.issue_type];
  let detail = `${i.persistence_sec.toFixed(0)}초째 계속`;
  if (i.issue_type === 'GAZE_AWAY' && ev.direction)
    title = `${DIRECTION_NAME[ev.direction as GazeDirection]} 쪽을 오래 보고 있어요`;
  if (i.issue_type === 'GAZE_LOW_EYE_CONTACT')
    detail = `최근 ${longS}초 중 청중 ${pct(ev[`camera_ratio_${longS}s`])}`;
  if (i.issue_type === 'GAZE_UNMEASURABLE')
    detail = `최근 ${shortS}초 측정 ${pct(ev[`coverage_${shortS}s`])} · 시선 피드백 보류`;
  return `
    <li class="${i.actionable ? '' : 'passive'}">
      <div><b>${title}</b><span>${detail}</span></div>
      <em><small>심각도</small>${Math.round(i.severity * 100)}</em>
    </li>`;
}

// ---------------------------------------------------------------- helpers

function percentile(values: number[], p: number): number {
  if (!values.length) return 0;
  const s = [...values].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.floor((p / 100) * s.length))]!;
}

let toastTimer = 0;
function showToast(text: string): void {
  const t = $('#toast');
  t.textContent = text;
  t.classList.add('show');
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => t.classList.remove('show'), 4000);
}

/** The page's job, not the view's: the whole page goes full screen, the view's box with it. */
function requestFullscreen(): void {
  if (SMOKE || PARAMS.has('box') || document.fullscreenElement) return;
  document.documentElement.requestFullscreen?.().catch(() => undefined);
}

function exitFullscreen(): void {
  if (document.fullscreenElement) document.exitFullscreen().catch(() => undefined);
}

function finishSmoke(): void {
  if (smoke.done) return;
  // Steady state: the first frames still carry one-off costs (landmark model first use).
  const steady = state.processMs.slice(8);
  smoke.processMsP95 = percentile(steady, 95);
  smoke.processMsMedian = percentile(steady, 50);
  smoke.detectMsMedian = percentile(state.detectMs.slice(8), 50);
  smoke.processMsWarm = state.processMs.slice(0, 12).map((v) => Math.round(v));
  smoke.done = true;
}

renderPanel();
if (SMOKE) {
  const auto = window.setInterval(() => {
    if (!state.ready) return;
    window.clearInterval(auto);
    startCamera()
      .then(() => view.startSetup())
      .catch((err) => (smoke.error = String(err)));
  }, 50);
}
