import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

// 화면 설계서는 이 폴더 안에서만 돈다. 제품(frontend/) 코드를 import하지 않는다.
// 포트는 제품 개발 서버(3000)와 겹치지 않게 따로 둔다.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: { port: 3100, strictPort: true },
  preview: { port: 3101, strictPort: true },
  test: { environment: 'jsdom' },
});
