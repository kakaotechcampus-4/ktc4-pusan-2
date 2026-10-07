// Copies the MediaPipe runtime and the face landmarker model into public/models/,
// the same layout the frontend expects (frontend/public/models/README.md).
// Nothing is fetched from a CDN.
import { copyFileSync, existsSync, mkdirSync, readdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');
const out = join(root, 'public', 'models');
mkdirSync(out, { recursive: true });

const wasmDir = join(root, 'node_modules', '@mediapipe', 'tasks-vision', 'wasm');
for (const name of readdirSync(wasmDir)) copyFileSync(join(wasmDir, name), join(out, name));

const task = resolve(root, '..', 'artifacts', 'face_landmarker.task');
if (!existsSync(task)) {
  console.error(
    `missing ${task}\nDownload it first: run "uv run python tools/fetch_assets.py" (from gaze-tracking/).`,
  );
  process.exit(1);
}
copyFileSync(task, join(out, 'face_landmarker.task'));
console.log(`assets -> ${out}`);
