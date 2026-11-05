/// <reference types="node" />
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import prettier from 'prettier';
import { it, expect } from 'vitest';

it('prints the formatter output for the new UI file', async () => {
  const path = resolve(process.cwd(), 'src/features/chat/ChatAgentHost.tsx');
  const content = readFileSync(path, 'utf8');
  const options = (await prettier.resolveConfig(path)) ?? {};
  const formatted = await prettier.format(content, { ...options, filepath: path });
  console.log('CHAT_FORMAT_RESULT=' + JSON.stringify(formatted));
  expect(formatted.length).toBeGreaterThan(0);
});
