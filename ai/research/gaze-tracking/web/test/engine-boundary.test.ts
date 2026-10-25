/// <reference types="node" />
/**
 * The engine folder moves to the frontend as is (src/engine -> frontend/src/vendor/gaze/engine),
 * so it must stand alone: it imports only its own files and MediaPipe. Nothing from the camera
 * view, the worker, the demo page or the frontend (@/..., @fe/...). The frontend imports
 * `index.ts` and `contract.ts`; everything else stays behind that boundary.  `contract.ts` is
 * the light entry point: nothing it loads at run time may reach MediaPipe.
 *
 * DOM use is caught by the compiler instead (tsconfig.engine.json: worker globals only).
 *
 * Nothing face-derived may be stored or sent from here either: the engine uses no storage
 * (localStorage, IndexedDB, Cache API), no network (fetch, XHR, WebSocket, beacons) and no
 * console.  What leaves the device is the 1 s record the host posts on, nothing else.
 * (MediaPipe loads its own model and wasm files; that happens inside the library.)
 */
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
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
const FORBIDDEN =
  /\b(localStorage|sessionStorage|indexedDB|caches|fetch|XMLHttpRequest|WebSocket|EventSource|sendBeacon)\b|\bconsole\./g;
const code = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
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

  it('contract.ts (the light entry point) loads no MediaPipe at run time', () => {
    // Value imports, re-exports, side-effect and dynamic imports; `import type` / `export type`
    // are erased and not followed.
    const RUNTIME_SPECIFIERS = [
      /\b(?:import|export)\s+(?!type\s)[^;]*?\bfrom\s+['"]([^'"]+)['"]/g,
      /^\s*import\s+['"]([^'"]+)['"]/gm,
      /\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)/g,
    ];
    const resolveLocal = (from: string, spec: string): string => {
      const base = join(dirname(from), spec);
      return existsSync(`${base}.ts`) ? `${base}.ts` : join(base, 'index.ts');
    };
    const seen = new Set<string>();
    const reached: string[] = [];
    const visit = (file: string): void => {
      if (seen.has(file)) return;
      seen.add(file);
      const text = code(readFileSync(file, 'utf8'));
      for (const pattern of RUNTIME_SPECIFIERS)
        for (const m of text.matchAll(pattern)) {
          const spec = m[1]!;
          if (spec.startsWith('.')) visit(resolveLocal(file, spec));
          else reached.push(`${relative(ENGINE, file)} -> ${spec}`);
        }
    };
    visit(join(ENGINE, 'contract.ts'));
    expect(seen.size).toBeGreaterThan(3); // config, evidence, reference, ... are reached
    expect(reached).toEqual([]);
    expect([...seen].map((f) => relative(ENGINE, f))).not.toContain('landmarker.ts');
  });

  it.each(FILES.map((f) => [relative(ENGINE, f), f]))(
    '%s stores, sends and logs nothing',
    (_, file) => {
      expect(code(readFileSync(file, 'utf8')).match(FORBIDDEN) ?? []).toEqual([]);
    },
  );
});
