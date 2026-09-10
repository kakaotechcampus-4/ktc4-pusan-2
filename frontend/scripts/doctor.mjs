/**
 * npm run doctor — 세팅이 끝났는지, 두 사람이 같은지 한 명령으로 대조합니다.
 *
 * SETUP.md §3 체크리스트와 §4 "반드시 같아야 하는 여섯 값"을 확인합니다.
 * 세팅 직후에 한 번, 그리고 "제 컴퓨터에서는 되는데요"가 나올 때마다 돌리세요.
 *
 * 왜 필요한가 — §3의 5·7·10은 사람이 눌러야 확인되는 항목입니다.
 * 그중 5번(에디터 TS 버전)은 틀려도 아무 에러가 안 나고 npm run build는 통과해서,
 * 한 사람 화면에만 빨간 줄이 뜨는 형태로 며칠 뒤에 드러납니다.
 * 그걸 명령으로 끌어내는 게 이 스크립트의 요점입니다.
 */
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { join } from 'node:path';

const require = createRequire(import.meta.url);
const results = [];
const add = (level, item, detail) => results.push({ level, item, detail });

// git 만 프로세스로 부릅니다 — 실제 실행 파일이라 shell 이 필요 없습니다.
//
// npm·code 는 Windows 에서 .cmd 셔임이고, 셔임을 실행하려면 shell:true 가 필요한데
// 그건 Node 가 DEP0190 으로 경고합니다(인자가 이스케이프되지 않음). 그래서
// 의존성과 확장 확인은 프로세스를 띄우지 않고 파일 시스템에서 직접 읽습니다.
// 더 빠르고, 셔임이 조용히 실패해서 "확인 못 함"을 "틀렸다"로 오보하는 일도 없습니다.
const run = (cmd, args) => {
  try {
    return execFileSync(cmd, args, {
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'ignore'],
    }).trim();
  } catch {
    return null;
  }
};

/** 한글·CJK 는 터미널에서 두 칸을 차지합니다. 표를 맞추려면 세어 줘야 합니다. */
const displayWidth = (s) =>
  [...s].reduce((n, ch) => {
    const c = ch.codePointAt(0);
    const wide =
      (c >= 0x1100 && c <= 0x115f) ||
      (c >= 0x2e80 && c <= 0xa4cf) ||
      (c >= 0xac00 && c <= 0xd7a3) ||
      (c >= 0xf900 && c <= 0xfaff) ||
      (c >= 0xfe30 && c <= 0xfe6f) ||
      (c >= 0xff00 && c <= 0xff60) ||
      (c >= 0xffe0 && c <= 0xffe6);
    return n + (wide ? 2 : 1);
  }, 0);

// ── 1. Node 버전 == .nvmrc ────────────────────────────────────────────
const wanted = existsSync('.nvmrc') ? readFileSync('.nvmrc', 'utf8').trim() : null;
const major = process.versions.node.split('.')[0];
if (!wanted) {
  add('FAIL', 'Node 버전', '.nvmrc가 없다 — 두 사람이 볼 기준 숫자가 없다');
} else if (major === wanted) {
  add('OK', 'Node 버전', `v${process.versions.node} (.nvmrc ${wanted})`);
} else {
  add('FAIL', 'Node 버전', `v${process.versions.node}인데 .nvmrc는 ${wanted} — nvm use ${wanted}`);
}

// ── 2. 프로젝트 TypeScript ────────────────────────────────────────────
let projectTs = null;
try {
  projectTs = require('typescript/package.json').version;
  add('OK', '프로젝트 TS', projectTs);
} catch {
  add('FAIL', '프로젝트 TS', 'typescript가 설치되지 않았다 — npm ci');
}

// ── 3. 에디터(VS Code) 내장 TS vs 프로젝트 TS  ★ §3-5 ─────────────────
// 이 둘이 다르면 S5(Use Workspace Version)를 눌러야 합니다. 누르지 않으면
// 에디터만 낮은 버전으로 검사해서, 화면의 빨간 줄과 CI 결과가 갈립니다.
// 승인 여부 자체는 VS Code 내부 상태라 명령으로 읽을 수 없습니다.
// 여기서는 "차이가 있으니 눌러야 한다"까지만 알려줍니다.
const VSCODE_TS_PATHS = [
  join(
    homedir(),
    'AppData/Local/Programs/Microsoft VS Code/resources/app/extensions/node_modules/typescript/package.json',
  ),
  '/Applications/Visual Studio Code.app/Contents/Resources/app/extensions/node_modules/typescript/package.json',
  '/usr/share/code/resources/app/extensions/node_modules/typescript/package.json',
];
const vscodeTs = VSCODE_TS_PATHS.filter((p) => existsSync(p)).map(
  (p) => JSON.parse(readFileSync(p, 'utf8')).version,
)[0];

if (!vscodeTs) {
  add('INFO', '에디터 TS', 'VS Code 설치를 찾지 못했다 — 상태 바에서 직접 확인');
} else if (projectTs && vscodeTs === projectTs) {
  add('OK', '에디터 TS', `내장 ${vscodeTs} == 프로젝트 ${projectTs}`);
} else {
  add(
    'WARN',
    '에디터 TS',
    `VS Code 내장 ${vscodeTs} != 프로젝트 ${projectTs} — S5 필요: ` +
      `Ctrl+Shift+P → TypeScript: Select TypeScript Version → Use Workspace Version ` +
      `(상태 바가 ${projectTs}여야 한다. 명령으로는 확인 불가)`,
  );
}

// ── 4. 설치된 것이 lock과 맞는지  ★ §4-2 ──────────────────────────────
// 반쯤 지워진 node_modules 를 잡아내는 게 목적입니다. npm ci 를 두 번 돌리다
// 중간에 멈추면 .bin 이 비고 패키지 몇 개가 사라지는데, 그 상태에서 나는
// 에러(tsc를 찾을 수 없음 등)는 원인을 가리키지 않습니다.
if (!existsSync('node_modules')) {
  add('FAIL', '의존성', 'node_modules가 없다 — npm ci');
} else if (!existsSync('package-lock.json')) {
  add('FAIL', '의존성', 'package-lock.json이 없다 — 두 사람의 버전을 고정할 근거가 없다');
} else {
  const lock = JSON.parse(readFileSync('package-lock.json', 'utf8'));
  const pkg = JSON.parse(readFileSync('package.json', 'utf8'));
  const names = Object.keys({ ...pkg.dependencies, ...pkg.devDependencies });
  const broken = [];
  for (const name of names) {
    const manifest = join('node_modules', name, 'package.json');
    if (!existsSync(manifest)) {
      broken.push(`${name}(없음)`);
      continue;
    }
    const want = lock.packages?.[`node_modules/${name}`]?.version;
    const got = JSON.parse(readFileSync(manifest, 'utf8')).version;
    if (want && want !== got) broken.push(`${name}(${got}≠lock ${want})`);
  }
  if (broken.length) {
    add('FAIL', '의존성', `${broken.join(', ')} — rm -rf node_modules && npm ci`);
  } else {
    add('OK', '의존성', `${names.length}개 전부 lock과 일치`);
  }
}

// ── 5. .env.local ─────────────────────────────────────────────────────
if (!existsSync('.env.local')) {
  add('FAIL', '.env.local', '없다 — cp .env.example .env.local');
} else if (!readFileSync('.env.local', 'utf8').includes('VITE_USE_MOCK')) {
  add('FAIL', '.env.local', 'VITE_USE_MOCK이 없다');
} else {
  add('OK', '.env.local', 'VITE_USE_MOCK 있음');
}

// ── 6. git — 커밋 대조와 줄바꿈 고정  ★ §4-5 ──────────────────────────
const head = run('git', ['rev-parse', 'HEAD']);
if (!head) {
  add('FAIL', 'git', '저장소가 아니다 — git init (그래야 .gitattributes의 eol=lf가 동작한다)');
} else {
  add('OK', 'git HEAD', `${head.slice(0, 7)} — 상대와 이 값을 대조한다`);
  const eol = run('git', ['ls-files', '--eol', 'src/main.tsx']) ?? '';
  if (eol.includes('i/lf') && eol.includes('w/lf')) {
    add('OK', '줄바꿈', 'i/lf w/lf (eol=lf 적용됨)');
  } else {
    add('WARN', '줄바꿈', `eol=lf가 안 먹었다: ${eol || '(확인 불가)'}`);
  }
}

// ── 7. COOP/COEP  ★ §3-7 — 오타는 에러를 내지 않는다 ──────────────────
// 워커에서 SharedArrayBuffer를 쓰려면 이 두 줄이 필요합니다.
// 지금 없어도 아무 에러가 안 나고, 몇 주 뒤 MediaPipe를 붙일 때 터집니다.
const port = process.env.PORT ?? '3000';
let res = null;
for (const scheme of ['http', 'https']) {
  try {
    res = await fetch(`${scheme}://localhost:${port}/`, {
      method: 'HEAD',
      signal: AbortSignal.timeout(1500),
    });
    break;
  } catch {
    /* 다음 스킴으로 */
  }
}
if (!res) {
  add('INFO', 'COOP/COEP', `개발 서버가 없어 확인 못 함 — npm run dev 후 다시 (PORT=${port})`);
} else {
  const coop = res.headers.get('cross-origin-opener-policy');
  const coep = res.headers.get('cross-origin-embedder-policy');
  if (coop === 'same-origin' && coep) {
    add('OK', 'COOP/COEP', `${coop} / ${coep}`);
  } else {
    add('FAIL', 'COOP/COEP', `COOP=${coop} COEP=${coep} — vite.config.ts의 headers 확인`);
  }
}

// ── 8. VS Code 확장 다섯 ──────────────────────────────────────────────
const REQUIRED = [
  'oxc.oxc-vscode',
  'esbenp.prettier-vscode',
  'bradlc.vscode-tailwindcss',
  'usernamehw.errorlens',
  'vitest.explorer',
];
// ~/.vscode/extensions 의 폴더 이름이 publisher.name-version 형태입니다.
const extDir = join(homedir(), '.vscode', 'extensions');
if (!existsSync(extDir)) {
  add('INFO', '확장 5개', 'VS Code 확장 폴더를 못 찾았다 — 에디터에서 직접 확인');
} else {
  const dirs = readdirSync(extDir);
  const missing = REQUIRED.filter((id) => !dirs.some((d) => d.startsWith(`${id}-`)));
  if (missing.length === 0) {
    add('OK', '확장 5개', '전부 설치됨');
  } else {
    add('WARN', '확장 5개', `없음: ${missing.join(', ')} — code --install-extension <id>`);
  }
}

// ── 출력 ──────────────────────────────────────────────────────────────
const MARK = { OK: '  ok  ', FAIL: ' FAIL ', WARN: ' warn ', INFO: ' .... ' };
const width = Math.max(...results.map((r) => displayWidth(r.item)));
console.log('');
for (const r of results) {
  const pad = ' '.repeat(width - displayWidth(r.item));
  console.log(`[${MARK[r.level]}] ${r.item}${pad}  ${r.detail}`);
}

const failed = results.filter((r) => r.level === 'FAIL').length;
const warned = results.filter((r) => r.level === 'WARN').length;
console.log('');
if (failed) {
  console.log(`${failed}개 실패${warned ? `, 경고 ${warned}개` : ''}. 위의 조치를 먼저 하세요.`);
  process.exitCode = 1;
} else {
  console.log(
    `실패 없음${warned ? `, 경고 ${warned}개` : ''}. ` +
      '남은 것은 사람이 눌러야 확인되는 둘 — /dev/stage 렌더 카운터(§3-9), 폰 카메라 팝업(§3-10).',
  );
}
