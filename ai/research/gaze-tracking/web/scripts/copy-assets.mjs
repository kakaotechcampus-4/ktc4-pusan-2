// Copies the MediaPipe runtime and the face landmarker model into public/models/,
// the same layout the frontend expects (frontend/public/models/README.md).
// Nothing is fetched from a CDN. The model's sha256 and the runtime's version are
// checked against artifacts/face_landmarker.task.json first; a mismatch stops here.
import { copyFileSync, existsSync, mkdirSync, readdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { verifyAssets } from './verify-assets.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..');
const out = join(root, 'public', 'models');
mkdirSync(out, { recursive: true });

const runtime = join(root, 'node_modules', '@mediapipe', 'tasks-vision');
const task = resolve(root, '..', 'artifacts', 'face_landmarker.task');
if (!existsSync(task)) {
  console.error(
    `missing ${task}\nDownload it first: run "uv run python tools/fetch_assets.py" (from gaze-tracking/).`,
  );
  process.exit(1);
}
const problems = verifyAssets({
  taskPath: task,
  manifestPath: resolve(root, '..', 'artifacts', 'face_landmarker.task.json'),
  runtimePackageJson: join(runtime, 'package.json'),
});
if (problems.length) {
  console.error(`assets do not match the manifest:\n  ${problems.join('\n  ')}`);
  process.exit(1);
}

const wasmDir = join(runtime, 'wasm');
for (const name of readdirSync(wasmDir)) copyFileSync(join(wasmDir, name), join(out, name));
copyFileSync(task, join(out, 'face_landmarker.task'));
console.log(`assets -> ${out}`);
