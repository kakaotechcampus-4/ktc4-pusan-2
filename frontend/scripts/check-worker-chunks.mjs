// 운영 빌드에서 워커 시작 파일을 다른 조각이 import 하지 않는지 봅니다 — `npm run build` 끝에 돕니다.
//
// 리허설 시선 워커는 설정을 주소 쿼리(`?load=…&impl=model`)로 받아 뜹니다. 다른 조각이 워커 시작 파일을
// 쿼리 없이 다시 import 하면 브라우저는 다른 모듈로 보고 워커 코드를 한 번 더 실행합니다.
// 두 번째 실행이 onmessage 를 덮어써, 발표 중 프레임을 하나도 처리하지 않습니다 (#779).
// 에러도 나지 않고 개발 서버에서는 생기지 않아서, 빌드 결과를 직접 봐야 잡힙니다.
//
// 대표 원인은 워커 안에서 엔진을 동적 import(`await import(...)`)로 부르는 것입니다 —
// 엔진이 조각으로 나뉘고, 함께 쓰는 코드는 워커 시작 파일에 남아 조각이 그것을 불러옵니다.
//
//   node scripts/check-worker-chunks.mjs [빌드 폴더]   (기본 dist)
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { join, resolve } from 'node:path';

const assets = join(resolve(process.argv[2] ?? 'dist'), 'assets');
if (!existsSync(assets)) {
  console.error(`빌드 결과가 없습니다: ${assets}`);
  process.exit(1);
}

const scripts = readdirSync(assets).filter((name) => name.endsWith('.js'));
const workers = scripts.filter((name) => /\.worker-[^/]*\.js$/.test(name));
if (workers.length === 0) {
  console.error(`워커 시작 파일(*.worker-*.js)을 찾지 못했습니다: ${assets}`);
  process.exit(1);
}

const escape = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const problems = [];
for (const worker of workers) {
  // `from"./x.js"` · `import"./x.js"` · `import("./x.js")` — 워커 주소를 만드는 `new URL(...)` 은 import 가 아닙니다
  const imports = new RegExp(`(?:\\bfrom|\\bimport)\\s*\\(?\\s*["']\\./${escape(worker)}["']`);
  for (const chunk of scripts) {
    if (chunk === worker) continue;
    if (imports.test(readFileSync(join(assets, chunk), 'utf8'))) problems.push({ chunk, worker });
  }
}

if (problems.length > 0) {
  for (const { chunk, worker } of problems) {
    console.error(`${chunk} 가 워커 시작 파일 ${worker} 를 import 합니다.`);
  }
  console.error(
    '쿼리를 붙여 띄운 워커는 이 import 로 한 번 더 실행되어 프레임을 처리하지 못합니다 (#779).\n' +
      '워커 안에서 동적 import 로 나뉜 조각이 없는지 보세요 (frontend/src/workers/modelClassifier.ts).',
  );
  process.exit(1);
}

console.log(`워커 시작 파일 ${workers.length}개를 다른 조각이 import 하지 않습니다.`);
