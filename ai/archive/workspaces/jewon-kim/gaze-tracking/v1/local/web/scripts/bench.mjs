// Per-frame cost of the engine in a real browser worker (frontend open item
// I-03, "프레임당 ms"): the bare bench page, no UI, a fake camera playing the
// test fixture.  Prints median / p95 after warm-up for each configuration.
//
//   npm run bench                      CPU and GPU delegates, with isolation headers
//   npm run bench -- --no-isolation    as production serves today (no COOP/COEP)
import { existsSync } from 'node:fs';
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
const isolation = !process.argv.includes('--no-isolation');
const y4m = resolve('.cache/static_face.y4m');
if (!existsSync(y4m))
  throw new Error('run `npm run smoke` once first (it builds .cache/static_face.y4m)');

const server = await createServer({
  configFile: resolve('vite.config.ts'),
  logLevel: 'error',
  server: isolation ? {} : { headers: {} },
});
if (!isolation) server.config.server.headers = {};
await server.listen();
const stat = (a, q) =>
  [...a].sort((x, y) => x - y)[Math.min(a.length - 1, Math.floor(q * a.length))];

console.log(`isolation headers: ${isolation ? 'on (COOP/COEP)' : 'off'}`);
for (const delegate of ['CPU', 'GPU']) {
  const browser = await puppeteer.launch({
    executablePath,
    headless: true,
    args: [
      '--use-fake-ui-for-media-stream',
      '--use-fake-device-for-media-stream',
      `--use-file-for-fake-video-capture=${y4m}`,
    ],
  });
  try {
    const page = await browser.newPage();
    await page.goto(`http://localhost:5180/bench.html?delegate=${delegate}`);
    await page.waitForFunction(
      () => window.__bench && (window.__bench.done || window.__bench.error),
      { timeout: 120000 },
    );
    const b = await page.evaluate(() => window.__bench);
    if (b.error) {
      console.log(`${delegate}  FAILED  ${b.error}`);
      continue;
    }
    const ms = b.ms.slice(8);
    const det = b.det.slice(8);
    console.log(
      `${delegate}  isolated=${b.isolated}  frame median ${stat(ms, 0.5).toFixed(1)} ms · p95 ${stat(ms, 0.95).toFixed(1)} ms ` +
        `(MediaPipe ${stat(det, 0.5).toFixed(1)} ms) over ${ms.length} frames`,
    );
  } finally {
    await browser.close();
  }
}
await server.close();
