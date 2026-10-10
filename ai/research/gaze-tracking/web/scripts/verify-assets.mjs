// Checks that the assets the browser will load are the ones the engine was built and
// tested with: the face landmarker model by sha256 (artifacts/face_landmarker.task.json),
// the MediaPipe wasm runtime by its exact npm version. A different model or runtime moves
// the landmarks slightly, which shifts calibration and the Python parity answers.
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';

/** sha256 hex of a file. */
export function sha256(path) {
  return createHash('sha256').update(readFileSync(path)).digest('hex');
}

/**
 * Compares the model file and the installed runtime against the manifest.
 * Returns a list of problems (empty when everything matches).
 */
export function verifyAssets({ taskPath, manifestPath, runtimePackageJson }) {
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
  const problems = [];

  const actual = sha256(taskPath);
  if (actual !== manifest.sha256) {
    problems.push(
      `${manifest.name}: sha256 ${actual.slice(0, 12)}… differs from the manifest ${manifest.sha256.slice(0, 12)}…`,
    );
  }

  const wanted = manifest.runtime?.web; // "@mediapipe/tasks-vision@1.0.1"
  if (wanted) {
    const version = JSON.parse(readFileSync(runtimePackageJson, 'utf8')).version;
    const expected = wanted.slice(wanted.lastIndexOf('@') + 1);
    if (version !== expected) {
      problems.push(`@mediapipe/tasks-vision ${version} installed, the manifest pins ${expected}`);
    }
  }
  return problems;
}
