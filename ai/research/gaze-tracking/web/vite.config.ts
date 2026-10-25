import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';

// The frontend's src, read-only: test/fe-contract runs the frontend's own gaze code. Its
// `@/vendor/gaze/*` (the frontend's copy of this folder) resolves to this folder's src instead.
const frontendSrc = fileURLToPath(new URL('../../../../frontend/src', import.meta.url));
const engineSrc = fileURLToPath(new URL('./src', import.meta.url));

// Same isolation headers as the frontend dev server (frontend/vite.config.ts):
// the engine has to behave identically under COOP/COEP, which is where it will run.
const isolation = {
  'Cross-Origin-Opener-Policy': 'same-origin',
  'Cross-Origin-Embedder-Policy': 'credentialless',
};

export default defineConfig({
  server: { headers: isolation, port: 5180, strictPort: true },
  preview: { headers: isolation, port: 5180, strictPort: true },
  worker: { format: 'es' },
  resolve: {
    alias: [
      { find: /^@\/vendor\/gaze\//, replacement: `${engineSrc}/` },
      { find: /^@fe\//, replacement: `${frontendSrc}/` },
      { find: /^@\//, replacement: `${frontendSrc}/` },
    ],
  },
  test: { include: ['src/**/__tests__/**/*.test.ts', 'test/**/*.test.ts'] },
});
