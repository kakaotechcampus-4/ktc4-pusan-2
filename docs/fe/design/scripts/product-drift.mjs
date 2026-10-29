// 화면 목록(src/catalog.ts)이 마지막으로 대조한 제품 커밋 뒤에 각 화면의 제품 코드(sources)가
// 바뀌었거나 없어졌는지 화면별로 출력한다. 제품 파일은 git으로 읽기만 한다.
// 바뀐 것은 오류가 아니다 — 그 화면의 설명을 제품 코드와 다시 맞출 차례라는 뜻이다(README "제품이 바뀌었을 때").
//
// 사용: npm run drift [-- <비교할 ref>]   (기본 origin/develop-next, 먼저 git fetch)
// catalog.ts는 import type만 쓰므로 Node가 타입을 지우고 바로 읽는다.
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { PRODUCT_CHECKED, screens } from '../src/catalog.ts';

const repoRoot = fileURLToPath(new URL('../../../../', import.meta.url));
const target = process.argv[2] ?? 'origin/develop-next';

function git(...args) {
  // git의 오류 출력은 삼킨다 — 없는 경로 · ref는 아래에서 문장으로 알린다
  return execFileSync('git', ['-C', repoRoot, ...args], {
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
  }).trim();
}

/** ref에 그 경로(파일 · 폴더)가 있나 */
function exists(ref, path) {
  try {
    git('cat-file', '-e', `${ref}:${path.replace(/\/$/, '')}`);
    return true;
  } catch {
    return false;
  }
}

try {
  git('rev-parse', '--verify', '--quiet', `${PRODUCT_CHECKED.commit}^{commit}`);
  git('rev-parse', '--verify', '--quiet', `${target}^{commit}`);
} catch {
  console.log(
    `커밋을 찾지 못했습니다(${PRODUCT_CHECKED.commit} · ${target}). 저장소에서 git fetch 뒤 실행하세요.`,
  );
  process.exit(0);
}

const targetCommit = git('rev-parse', '--short', target);
console.log(
  `대조 기준 ${PRODUCT_CHECKED.commit.slice(0, 8)}(${PRODUCT_CHECKED.date}) → ${target}(${targetCommit})`,
);

let stale = 0;
for (const screen of screens) {
  // 옛 시안의 sources에는 설계서 파일도 있다. 제품 코드만 본다
  const sources = screen.sources.filter((path) => path.startsWith('frontend/'));
  if (sources.length === 0) continue;
  const changed = git('diff', '--name-only', PRODUCT_CHECKED.commit, target, '--', ...sources)
    .split('\n')
    .filter(Boolean);
  const missing = sources.filter((path) => !exists(target, path));
  if (changed.length === 0 && missing.length === 0) continue;
  stale += 1;
  console.log(`\n[${screen.id}] ${screen.title}`);
  for (const path of missing) console.log(`  없어짐  ${path}`);
  for (const path of changed.filter((path) => !missing.includes(path)))
    console.log(`  바뀜    ${path}`);
}

if (stale === 0) console.log('대조 기준 뒤로 바뀐 제품 코드가 없습니다.');
else
  console.log(
    `\n${stale}개 화면의 제품 코드가 바뀌었습니다. 설명을 맞춘 뒤 catalog.ts의 PRODUCT_CHECKED를 ${targetCommit}로 올립니다.`,
  );
