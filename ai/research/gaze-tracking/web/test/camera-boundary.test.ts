/// <reference types="node" />
/**
 * The camera view (src/camera) is the frontend's from #611: it is copied once more and then
 * changed in the frontend.  So it may reach the engine and the worker only through their public
 * entry points, which stay stable when files behind them change:
 *
 *   ../engine           type-only (index.ts pulls MediaPipe in at run time)
 *   ../engine/contract  types and values (the light entry point)
 *   ../worker/protocol  the messages between the view and its worker
 *
 * Its own files (./...) are free.  The worker itself is started by URL, not imported:
 * `new Worker(new URL('../worker/gaze.worker.ts', import.meta.url))`.  That entry path and the
 * side-by-side camera/ · worker/ layout are the frontend's contract too
 * (docs/ai/features/gaze-tracking.md 5-6), so moving the worker file fails here first.
 */
import { existsSync, readdirSync, readFileSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';
import { describe, expect, it } from 'vitest';

const CAMERA = fileURLToPath(new URL('../src/camera', import.meta.url));
const FILES = readdirSync(CAMERA)
  .filter((name) => name.endsWith('.ts'))
  .map((name) => join(CAMERA, name));
const ANY_USE = new Set(['../engine/contract', '../worker/protocol']);
const TYPE_ONLY = new Set(['../engine']);
const WORKER_ENTRY = '../worker/gaze.worker.ts';

/**
 * Every import · re-export · dynamic `import()` specifier, and whether the compiler erases it.
 * Only `import type` / `export type` count as type-only: with `verbatimModuleSyntax` an
 * `import { type A }` statement stays in the output as `import {}` and still loads the module.
 */
function specifiers(file: string): { spec: string; typeOnly: boolean }[] {
  const src = ts.createSourceFile(file, readFileSync(file, 'utf8'), ts.ScriptTarget.Latest);
  const out: { spec: string; typeOnly: boolean }[] = [];
  const walk = (node: ts.Node): void => {
    if (ts.isImportDeclaration(node)) {
      out.push({
        spec: (node.moduleSpecifier as ts.StringLiteral).text,
        typeOnly: !!node.importClause?.isTypeOnly,
      });
    } else if (ts.isExportDeclaration(node) && node.moduleSpecifier) {
      out.push({
        spec: (node.moduleSpecifier as ts.StringLiteral).text,
        typeOnly: node.isTypeOnly,
      });
    } else if (
      ts.isCallExpression(node) &&
      node.expression.kind === ts.SyntaxKind.ImportKeyword &&
      node.arguments[0] &&
      ts.isStringLiteralLike(node.arguments[0])
    ) {
      out.push({ spec: node.arguments[0].text, typeOnly: false });
    }
    ts.forEachChild(node, walk);
  };
  walk(src);
  return out;
}

/**
 * The target of every `new Worker(...)`.  Only `new Worker(new URL('<literal>', import.meta.url))`
 * names a path the bundler can follow; any other shape is reported as `<not a URL literal>`.
 */
function workerEntries(file: string): string[] {
  const src = ts.createSourceFile(file, readFileSync(file, 'utf8'), ts.ScriptTarget.Latest);
  const out: string[] = [];
  const isImportMetaUrl = (node: ts.Node | undefined): boolean =>
    !!node &&
    ts.isPropertyAccessExpression(node) &&
    ts.isMetaProperty(node.expression) &&
    node.name.text === 'url';
  const walk = (node: ts.Node): void => {
    if (
      ts.isNewExpression(node) &&
      ts.isIdentifier(node.expression) &&
      node.expression.text === 'Worker'
    ) {
      const url = node.arguments?.[0];
      const ok =
        url &&
        ts.isNewExpression(url) &&
        ts.isIdentifier(url.expression) &&
        url.expression.text === 'URL' &&
        url.arguments?.[0] &&
        ts.isStringLiteralLike(url.arguments[0]) &&
        isImportMetaUrl(url.arguments[1]);
      out.push(ok ? (url.arguments![0] as ts.StringLiteralLike).text : '<not a URL literal>');
    }
    ts.forEachChild(node, walk);
  };
  walk(src);
  return out;
}

describe('camera view boundary', () => {
  it('has source files', () => {
    expect(FILES.length).toBeGreaterThan(5);
  });

  it.each(FILES.map((f) => [relative(CAMERA, f), f]))(
    '%s reaches the engine and worker only through their entry points',
    (_, file) => {
      const bad = specifiers(file)
        .filter(({ spec }) => !spec.startsWith('./'))
        .filter(({ spec, typeOnly }) => !(ANY_USE.has(spec) || (TYPE_ONLY.has(spec) && typeOnly)))
        .map(({ spec, typeOnly }) => `${spec}${typeOnly ? ' (type)' : ''}`);
      expect(bad).toEqual([]);
    },
  );

  it(`starts its worker from ${WORKER_ENTRY}, which exists`, () => {
    const entries = FILES.flatMap(workerEntries);
    expect(entries.length).toBeGreaterThan(0);
    expect(entries.filter((spec) => spec !== WORKER_ENTRY)).toEqual([]);
    expect(existsSync(join(CAMERA, WORKER_ENTRY))).toBe(true);
  });
});
