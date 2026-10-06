/// <reference types="node" />
/**
 * The engine folder moves to the frontend as is (src/engine -> frontend/src/workers/gaze/engine),
 * so it must stand alone: it imports only its own files and MediaPipe. Nothing from the camera
 * view, the worker, the demo page or the frontend (@/..., @fe/...). The frontend imports
 * `index.ts` and `contract.ts`; everything else stays behind that boundary.
 *
 * DOM use is caught by the compiler instead (tsconfig.engine.json: worker globals only).
 */
import { readdirSync, readFileSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const ENGINE = fileURLToPath(new URL('../src/engine', import.meta.url));
const ALLOWED_PACKAGES = new Set(['@mediapipe/tasks-vision']);

function sources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === '__tests__' ? [] : sources(path);
    return entry.name.endsWith('.ts') ? [path] : [];
  });
}

const FILES = sources(ENGINE);
const SPECIFIER = /(?:\bfrom\s+|\bimport\s*\(\s*|^\s*import\s+)['"]([^'"]+)['"]/gm;

describe('engine boundary', () => {
  it('has source files', () => {
    expect(FILES.length).toBeGreaterThan(10);
  });

  it.each(FILES.map((f) => [relative(ENGINE, f), f]))(
    '%s imports only the engine and MediaPipe',
    (_, file) => {
      const text = readFileSync(file, 'utf8');
      const bad = [...text.matchAll(SPECIFIER)]
        .map((m) => m[1]!)
        .filter((spec) => !(spec.startsWith('./') || ALLOWED_PACKAGES.has(spec)));
      expect(bad).toEqual([]);
    },
  );
});
