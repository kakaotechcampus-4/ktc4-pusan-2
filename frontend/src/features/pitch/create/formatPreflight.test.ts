import { spawnSync } from 'node:child_process';
import { readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import * as prettier from 'prettier';
import { it } from 'vitest';

it('temporary formatting diagnostics', async () => {
  for (const filepath of [
    'src/features/pitch/create/CriteriaPane.test.tsx',
    'src/features/pitch/create/steps/CriteriaPane.tsx',
  ]) {
    const input = readFileSync(filepath, 'utf8');
    const output = await prettier.format(input, {
      ...(await prettier.resolveConfig(filepath)),
      filepath,
    });
    const target = join(tmpdir(), filepath.split('/').at(-1)!);
    writeFileSync(target, output);
    const { stdout } = spawnSync('diff', ['-u', filepath, target], { encoding: 'utf8' });
    console.log(`FORMAT_DIFF_START ${filepath}\n${stdout}\nFORMAT_DIFF_END`);
  }
});
