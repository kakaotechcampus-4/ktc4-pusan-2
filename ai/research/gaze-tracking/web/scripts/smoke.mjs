// Headless end-to-end check in a real browser: the demo page hosting the camera
// view (src/camera), with a fake camera playing tests/fixtures/local/static_face_30fps.mp4
// (converted to .y4m).
//
// Verifies what unit tests cannot: MediaPipe loading inside a module worker
// (wasm via an absolute URL), ImageBitmap frames, OffscreenCanvas brightness,
// crossOriginIsolated under COOP/COEP, per-frame latency, and what happens when
// the camera goes away mid-take.  Screenshots of each stage land in .cache/screens/.
//
//   npm run smoke             with COOP/COEP, like the dev server
//   npm run smoke:prod        without them, as production serves today (Caddy sets none)
//   CHROME=/path/to/msedge npm run smoke   another Chromium browser
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync } from 'node:fs';
import { resolve } from 'node:path';
import puppeteer from 'puppeteer-core';
import { createServer } from 'vite';

const BROWSERS = [
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
];
const executablePath = process.env.CHROME ?? BROWSERS.find((p) => existsSync(p));
if (!executablePath) throw new Error('no Chrome/Edge found; set CHROME=/path/to/chrome');

const y4m = resolve('.cache/static_face.y4m');
if (!existsSync(y4m)) {
  // Chrome's fake camera reads raw 4:2:0 .y4m; build it from the test fixture once.
  const python = resolve('../.venv/Scripts/python.exe');
  execFileSync(existsSync(python) ? python : 'python', [resolve('../tools/make_y4m.py')], {
    stdio: 'inherit',
  });
}
mkdirSync('.cache/screens', { recursive: true });

const isolation = !process.argv.includes('--no-isolation');
const server = await createServer({
  configFile: resolve('vite.config.ts'),
  logLevel: 'warn',
  server: isolation ? {} : { headers: {} },
});
if (!isolation) server.config.server.headers = {};
await server.listen();
console.log(
  `browser: ${executablePath}\nisolation headers: ${isolation ? 'on (COOP/COEP)' : 'off'}`,
);
const browser = await puppeteer.launch({
  executablePath,
  headless: true,
  args: [
    '--use-fake-ui-for-media-stream',
    '--use-fake-device-for-media-stream',
    `--use-file-for-fake-video-capture=${y4m}`,
    // Render with the real GPU when there is one: software rendering of the page
    // competes with the worker for CPU and roughly doubles the measured latency.
    ...(process.platform === 'win32'
      ? ['--use-angle=d3d11', '--enable-gpu', '--ignore-gpu-blocklist']
      : []),
  ],
  defaultViewport: { width: 1440, height: 900, deviceScaleFactor: 1 },
});

const failures = [];
const check = (ok, what) => {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${what}`);
  if (!ok) failures.push(what);
};

try {
  const page = await browser.newPage();
  page.on('pageerror', (e) => console.log('[pageerror]', e.message));
  page.on('console', (m) => m.type() === 'error' && console.log('[console]', m.text()));

  // A slow, real-speed pass first for the screenshots (no ?smoke shortcuts).
  await page.goto('http://localhost:5180/');
  await page.waitForFunction(() => !document.querySelector('#start')?.hasAttribute('disabled'), {
    timeout: 60000,
  });
  await page.screenshot({ path: '.cache/screens/1-intro.png' });
  await page.click('#start');
  await page.waitForFunction(() => document.body.dataset.stage === 'flow', { timeout: 20000 });
  await new Promise((r) => setTimeout(r, 1500));
  await page.screenshot({ path: '.cache/screens/2-align.png' });
  // The set-up check passes on the test video; if it rejects, take the escape hatch.
  await page
    .waitForFunction(() => document.querySelector('.gzc')?.dataset.phase === 'sweep', {
      timeout: 20000,
    })
    .catch(() => page.click('.gzc-skip'));
  await new Promise((r) => setTimeout(r, 1200));
  await page.screenshot({ path: '.cache/screens/3-sweep.png' });
  // A still video cannot turn its head: skip the ring like a user who cannot.
  await page.click('.gzc-skip');
  for (const [i, cue] of ['SCREEN', 'CAMERA', 'BOTTOM'].entries()) {
    await page.waitForFunction(
      (c) => {
        const f = document.querySelector('.gzc');
        return f?.dataset.phase === 'calib' && f.dataset.cue === c;
      },
      // A still face cannot lift or lower its head: each direction-checked look
      // times out twice before it is taken without the check.
      { timeout: 60000 },
      cue,
    );
    await new Promise((r) => setTimeout(r, 2300));
    await page.screenshot({ path: `.cache/screens/4-calib-${i + 1}-${cue.toLowerCase()}.png` });
  }
  await page.waitForFunction(() => document.querySelector('.gzc')?.dataset.phase === 'result', {
    timeout: 30000,
  });
  await new Promise((r) => setTimeout(r, 800));
  await page.screenshot({ path: '.cache/screens/5-result.png' });
  await page.evaluate(() => {
    const buttons = [...document.querySelectorAll('.gzc-actions button')];
    buttons.find((b) => b.textContent?.includes('시작'))?.click();
  });
  await page.waitForFunction(() => document.body.dataset.stage === 'live', { timeout: 10000 });
  await new Promise((r) => setTimeout(r, 3000));
  await page.screenshot({ path: '.cache/screens/6-live.png' });

  // The same view in a 16:9 card (?box): it fills the host's box, not the window.
  await page.goto('http://localhost:5180/?box');
  await page.waitForFunction(() => !document.querySelector('#start')?.hasAttribute('disabled'), {
    timeout: 60000,
  });
  await page.click('#start');
  await page.waitForFunction(() => document.querySelector('.gzc')?.dataset.phase === 'align', {
    timeout: 20000,
  });
  await new Promise((r) => setTimeout(r, 1500));
  await page.screenshot({ path: '.cache/screens/7-box.png' });
  const fit = await page.evaluate(() => {
    const r = (sel) => document.querySelector(sel).getBoundingClientRect().toJSON();
    return { view: r('.gzc'), box: r('#stage'), window: [innerWidth, innerHeight] };
  });

  // Then the unattended run, which collects numbers.
  await page.goto('http://localhost:5180/?smoke');
  await page.waitForFunction(
    () => window.__smoke && (window.__smoke.done || window.__smoke.error),
    { timeout: 120000 },
  );
  const s = await page.evaluate(() => window.__smoke);

  const same = ['x', 'y', 'width', 'height'].every((k) => Math.abs(fit.view[k] - fit.box[k]) < 1);
  check(
    same && fit.view.width < fit.window[0] && fit.view.height < fit.window[1],
    `camera view fills the host's box (${Math.round(fit.view.width)}x${Math.round(fit.view.height)} in a ${fit.window.join('x')} window)`,
  );
  check(!s.error, `no engine error (${s.error ?? 'none'})`);
  check(s.ready, `worker ready: ${s.version}`);
  check(
    s.isolated === isolation,
    isolation
      ? 'crossOriginIsolated inside the worker (COOP/COEP)'
      : 'runs without cross-origin isolation, as production serves today',
  );
  check(
    s.frames > 40 && s.faces / s.frames > 0.9,
    `frames analysed ${s.frames}, face found in ${s.faces}`,
  );
  const heads = s.headSamples;
  const finite =
    heads.length > 0 && heads.every(([y, p]) => Number.isFinite(y) && Number.isFinite(p));
  const meanYaw = heads.reduce((a, h) => a + h[0], 0) / Math.max(heads.length, 1);
  const meanPitch = heads.reduce((a, h) => a + h[1], 0) / Math.max(heads.length, 1);
  check(
    finite,
    `head pose finite (mean yaw ${meanYaw.toFixed(1)}°, pitch ${meanPitch.toFixed(1)}°)`,
  );
  // The Python pipeline (gaze_lab PreprocessPipeline) measures this fixture video
  // at yaw +0.53°, pitch +1.52°.  The synthetic face looks straight into the lens,
  // so a sign error would not show here -- the parity tests pin the matrix layout
  // and the signs on known rotations -- but the browser path must land on the
  // same pose end to end.
  check(
    Math.abs(meanYaw - 0.53) < 1.5 && Math.abs(meanPitch - 1.52) < 1.5,
    'head pose matches the Python measurement of the fixture (yaw +0.5°, pitch +1.5°)',
  );
  const m = s.check?.measurements ?? {};
  check(m.face_brightness > 60, `brightness measured in the worker (${m.face_brightness})`);
  check(s.check !== null, `set-up check reported (${s.check?.status} ${s.check?.reason})`);
  check(
    ['align', 'sweep', 'calib', 'result'].every((p) => s.phases.includes(p)),
    `one flow: ${[...new Set(s.phases)].join(' -> ')}`,
  );
  check(
    s.sweep !== null && s.sweep.neutral_deg !== null && s.sweep.filled === 0,
    `head circle: centre measured (${s.sweep?.neutral_deg?.map((v) => v.toFixed(1)).join(', ')}), a still face lights nothing (${s.sweep?.state})`,
  );
  const last = (cue) => s.gauges.filter((g) => g.cue === cue).at(-1);
  check(
    ['SCREEN', 'CAMERA', 'BOTTOM'].every((c) => last(c)?.state === 'DONE'),
    `three looks filled in the end (${s.gauges.map((g) => `${g.cue} ${g.state} ${g.good}`).join(', ')})`,
  );
  const held = (cue, reason) =>
    s.gauges.some(
      (g) =>
        g.cue === cue && g.state === 'TIMED_OUT' && g.good === 0 && g.dominant_reason === reason,
    );
  check(
    held('CAMERA', 'LOOK_HIGHER') && held('BOTTOM', 'LOOK_LOWER'),
    'a still head does not fill the lens or script look (read from the circle centre)',
  );
  check(
    s.placement?.placement === 'INCONCLUSIVE',
    `placement unreadable on a still face (${s.placement?.reason})`,
  );
  check(
    s.quality?.reason === 'CLASS_NOT_SEPARABLE',
    `still face -> ${s.quality?.status} ${s.quality?.reason}`,
  );
  check(s.modelCloneOk, 'calibration model survives structuredClone');
  check(s.decisions.length >= 24, `live decisions ${s.decisions.length}`);
  const drift = s.decisions.at(-1)?.condition?.drift;
  check(
    drift && drift.deg < drift.warn_deg,
    `position baseline: a still face has not moved (${drift ? `${drift.deg.toFixed(2)}° of ${drift.fail_deg.toFixed(1)}°, ${drift.distance_cm.toFixed(0)} cm away` : 'no drift'})`,
  );
  const jitter = s.decisions.at(-1)?.condition?.jitter_deg;
  check(
    typeof jitter === 'number' && jitter < 1.5,
    `head-direction jitter of real MediaPipe on a still face (${typeof jitter === 'number' ? `${jitter.toFixed(2)}°` : 'none'})`,
  );
  check(s.zones.length >= 1, `1-second votes from the frontend TemporalVoter (${s.zones.length})`);
  const ev = s.evidence;
  const wire = [
    't_ms',
    'duration_ms',
    'state',
    'direction',
    'confidence',
    'reliability',
    'issues',
    'frames',
  ];
  check(
    ev.samples.length >= 1 && ev.samples.every((x) => wire.every((k) => k in x)),
    `1 s agent records from live decisions (${ev.samples.map((x) => x.state).join(' ')})`,
  );
  check(
    ev.issues.every((i) => i.evaluator === 'gaze' && 'severity' in i && 'persistence_sec' in i) &&
      'problem_segments' in ev.summary,
    `coach issues and review summary in the agents' format (${ev.issues.map((i) => i.issue_type).join(', ') || 'none'})`,
  );
  console.log(
    `      worker ms: median ${s.processMsMedian.toFixed(1)} (MediaPipe ${s.detectMsMedian.toFixed(1)}), p95 ${s.processMsP95.toFixed(1)}, first frames ${s.processMsWarm.join(' ')}`,
  );
  check(
    s.processMsP95 > 0 && s.processMsP95 < 125,
    `worker p95 ${s.processMsP95.toFixed(1)} ms < 125 ms budget`,
  );

  // The camera goes away mid-take (unplugged, taken by another app): the view reports
  // CAMERA_LOST once and stops grabbing frames instead of retrying silently.
  await page.evaluate(() => {
    for (const t of document.querySelector('video').srcObject.getTracks()) t.stop();
  });
  await new Promise((r) => setTimeout(r, 1000));
  const lost = await page.evaluate(() => ({
    reason: window.__smoke.errorReason,
    frames: window.__smoke.frames,
  }));
  await new Promise((r) => setTimeout(r, 1000));
  const later = await page.evaluate(() => window.__smoke.frames);
  check(
    lost.reason === 'CAMERA_LOST' && later === lost.frames,
    `camera lost mid-take: reported (${lost.reason}) and frame grabbing stopped (${lost.frames} -> ${later})`,
  );
} finally {
  await browser.close();
  await server.close();
}

if (failures.length) {
  console.log(`\n${failures.length} check(s) failed`);
  process.exit(1);
}
console.log('\nall smoke checks passed · screenshots in .cache/screens/');
