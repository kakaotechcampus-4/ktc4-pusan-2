/// <reference types="node" />
/**
 * `npm run assets` copies the model and the wasm runtime only when they are the ones the
 * engine was built and tested with (artifacts/face_landmarker.task.json).
 */
import { mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { sha256, verifyAssets } from '../scripts/verify-assets.mjs';

function fixture(model: string, manifestSha: string | null, runtimeVersion: string) {
  const dir = mkdtempSync(join(tmpdir(), 'gaze-assets-'));
  const taskPath = join(dir, 'face_landmarker.task');
  writeFileSync(taskPath, model);
  const manifestPath = join(dir, 'face_landmarker.task.json');
  writeFileSync(
    manifestPath,
    JSON.stringify({
      name: 'face_landmarker.task',
      sha256: manifestSha ?? sha256(taskPath),
      runtime: { web: '@mediapipe/tasks-vision@1.0.1' },
    }),
  );
  const runtimePackageJson = join(dir, 'package.json');
  writeFileSync(runtimePackageJson, JSON.stringify({ version: runtimeVersion }));
  return { taskPath, manifestPath, runtimePackageJson };
}

describe('asset check', () => {
  it('passes the pinned model and runtime', () => {
    expect(verifyAssets(fixture('model', null, '1.0.1'))).toEqual([]);
  });

  it('stops on a different model', () => {
    const problems = verifyAssets(fixture('model', '0'.repeat(64), '1.0.1'));
    expect(problems).toHaveLength(1);
    expect(problems[0]).toContain('face_landmarker.task: sha256');
  });

  it('stops on a different runtime version', () => {
    const problems = verifyAssets(fixture('model', null, '1.0.2'));
    expect(problems).toEqual(['@mediapipe/tasks-vision 1.0.2 installed, the manifest pins 1.0.1']);
  });
});
