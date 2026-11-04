// 설계서 디자인 값(src/styles.css)과 제품(frontend/src/index.css)의 CSS 변수를 비교해 다른 것만 출력한다.
// 제품 파일은 읽기만 한다. 차이는 오류가 아니다 — 설계서가 앞서 있거나 제품이 바뀐 것이니 README 절차로 정리한다.
import { existsSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const designPath = fileURLToPath(new URL('../src/styles.css', import.meta.url));
const productPath = fileURLToPath(new URL('../../../../frontend/src/index.css', import.meta.url));

/** 선택자 범위(예: :root, .pitch-create)별 `--이름: 값` 목록 */
function variables(css) {
  const result = new Map();
  const text = css.replace(/\/\*[\s\S]*?\*\//g, '');
  for (const block of text.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    const scope = block[1].trim().replace(/\s+/g, ' ');
    for (const match of block[2].matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) {
      result.set(`${scope} ${match[1]}`, match[2].trim());
    }
  }
  return result;
}

if (!existsSync(productPath)) {
  console.log('제품 index.css 를 찾지 못했습니다. 저장소 전체를 받은 곳에서 실행하세요.');
  process.exit(0);
}

const design = variables(readFileSync(designPath, 'utf8'));
const product = variables(readFileSync(productPath, 'utf8'));
const rows = [];
for (const key of new Set([...design.keys(), ...product.keys()])) {
  const a = design.get(key) ?? '(없음)';
  const b = product.get(key) ?? '(없음)';
  if (a.toLowerCase() !== b.toLowerCase()) rows.push({ 변수: key, 설계서: a, 제품: b });
}

if (rows.length === 0) console.log('설계서와 제품의 디자인 값이 같습니다.');
else {
  console.log(`다른 값 ${rows.length}개`);
  console.table(rows);
}
