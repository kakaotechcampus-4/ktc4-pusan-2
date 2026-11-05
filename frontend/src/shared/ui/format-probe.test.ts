// @ts-nocheck
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import * as prettier from 'prettier';
import { it } from 'vitest';

it('format probe for #930 (temporary)', async () => {
  for (const path of [
    'src/shared/ui/HomeHelpDialog.tsx',
    'src/shared/ui/HomeHelpDialog.test.tsx',
  ]) {
    const filepath = resolve(process.cwd(), path);
    const config = await prettier.resolveConfig(filepath);
    const formatted = await prettier.format(readFileSync(filepath, 'utf8'), {
      ...config,
      filepath,
    });
    console.log('__FORMAT_PROBE__' + path + '__' + JSON.stringify(formatted));
  }
});
