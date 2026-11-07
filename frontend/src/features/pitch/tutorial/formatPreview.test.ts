import { it } from 'vitest';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import * as prettier from 'prettier';

it('prints Prettier output for the new First Take files', async () => {
  const paths = [
    'src/features/pitch/tutorial/firstTakeMaterials.ts',
    'src/features/pitch/tutorial/FirstTakePage.tsx',
    'src/features/pitch/tutorial/firstTakePdf.ts',
    'src/features/pitch/tutorial/firstTakeSetup.test.ts',
    'src/features/pitch/tutorial/firstTakeSetup.ts',
  ];
  for (let index = 0; index < paths.length; index += 1) {
    const filePath = resolve(paths[index]);
    const configuration = await prettier.resolveConfig(filePath);
    const source = await readFile(filePath, 'utf8');
    const formatted = await prettier.format(source, { ...configuration, filepath: filePath });
    console.log('FORMAT_DATA_' + index + ':' + Buffer.from(formatted, 'utf8').toString('base64'));
  }
});
