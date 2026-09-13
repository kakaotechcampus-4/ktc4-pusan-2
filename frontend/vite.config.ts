import { fileURLToPath } from 'node:url';
import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import basicSsl from '@vitejs/plugin-basic-ssl';

/**
 * ── @ alias는 fileURLToPath를 쓴다 ─────────────────────────────
 *
 * new URL('./src', import.meta.url).pathname 은 두 가지로 깨집니다.
 *   1. Windows에서 `/C:/...` 로 시작한다
 *   2. 비ASCII 경로가 퍼센트 인코딩된다 → `카테캠` → `%EC%B9%B4...`
 *      → 빌드가 `os error 123`으로 죽는다
 *
 * 리눅스 + ASCII 경로에서는 우연히 동작해서 잡기 어렵습니다.
 * fileURLToPath가 OS별 실제 경로를 돌려줍니다.
 *
 * ── HTTPS가 왜 필요한가 ────────────────────────────────────────
 *
 * getUserMedia(카메라·마이크)는 secure context에서만 동작합니다.
 * localhost는 예외라 혼자 개발할 때는 문제가 없습니다.
 *
 * 그런데 `--host`로 팀원 노트북이나 휴대폰에서 열면 주소가
 * http://192.168.x.x 가 되고, 이건 secure context가 아니라서
 * **카메라가 아예 안 잡힙니다.** 권한 팝업조차 안 뜹니다.
 *
 *   npm run dev:lan   → https://192.168.x.x:3000
 *
 * ★ VITE_HTTPS=true 를 스크립트 앞에 붙이지 않습니다.
 *   그 문법은 Windows(cmd·PowerShell)에서 안 돌아서, 두 사람의 OS가 다르면
 *   한쪽만 카메라 테스트를 못 하게 됩니다. 대신 --mode lan 으로 .env.lan을 읽습니다.
 *
 * 자체 서명 인증서라 브라우저가 경고를 띄웁니다.
 * "고급" → "안전하지 않음(계속)"을 누르면 됩니다. 정상입니다.
 */

// 워커·WASM에 필요한 헤더. dev와 preview **양쪽**에 걸어야 합니다.
// preview에만 빠뜨리면 프로덕션 번들 확인에서 SharedArrayBuffer가 죽습니다.
const COEP_HEADERS = {
  'Cross-Origin-Opener-Policy': 'same-origin',
  'Cross-Origin-Embedder-Policy': 'credentialless',
};

export default defineConfig(({ mode }) => {
  // .env.lan 등 모드별 파일을 읽습니다. 세 번째 인자 ''는 VITE_ 접두사 제한을 푸는 것.
  const env = loadEnv(mode, process.cwd(), '');
  const useHttps = env.VITE_HTTPS === 'true';

  return {
    plugins: [react(), tailwindcss(), ...(useHttps ? [basicSsl()] : [])],
    resolve: {
      alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
    },
    worker: { format: 'es' },

    /**
     * ── 포트는 여기서만 정합니다 ──────────────────────────────────
     *
     * 3000 인 이유는 백엔드가 프론트를 그 주소로 가정하기 때문입니다.
     * `frontend_base_url` 이 CORS 허용 origin 과 OAuth 콜백 리다이렉트
     * 대상으로 동시에 쓰여서, 다른 포트로 띄우면 **로그인이 아예 안 됩니다.**
     * 운영도 같습니다 — Dockerfile 의 Caddy 가 :3000 을 서브하고
     * infra/Caddyfile 이 `reverse_proxy fe:3000` 입니다.
     *
     * ★ strictPort — 3000 이 이미 쓰이고 있으면 **바로 실패**합니다.
     *   없으면 Vite 가 조용히 3001 로 옮겨가고, 그러면 CI 의 헤더 검사가
     *   빈 포트를 60초 재시도한 뒤 죽습니다. 원인이 로그에 안 나옵니다.
     */
    server: { port: 3000, strictPort: true, headers: COEP_HEADERS },
    preview: { port: 3001, strictPort: true, headers: COEP_HEADERS },
  };
});
