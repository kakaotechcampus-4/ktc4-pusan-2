// 시선 엔진(vendor/gaze) 자산을 public/models/ 에 둡니다 — `npm run models`.
//
// 앱은 실행 중에 CDN 에서 아무것도 받지 않습니다 (CLAUDE.md 개발 중 함정).
// 그래서 이 스크립트가 미리 깔아 둡니다. public/models/* 는 커밋하지 않습니다.
//
//   vision_wasm_*         node_modules/@mediapipe/tasks-vision/wasm/ 에서 복사
//                         (모듈 Worker 는 vision_wasm_module_internal.* 를 씁니다)
//   face_landmarker.task  AI 리그에 있으면 복사, 없으면 받는 명령을 알려 줍니다
//
// ── 왜 해시와 버전을 확인하나 ──────────────────────────────────────────
// 모델이나 런타임이 다르면 얼굴 랜드마크가 조금씩 달라져, 같은 사람의 보정값과 판정이
// AI 가 Python 으로 검증한 기준 답과 어긋납니다. 에러는 나지 않고 숫자만 조용히 틀립니다.
// AI 의 verify-assets 와 같은 값으로 확인하고, 다르면 복사하지 않고 실패합니다.
// 기준 값의 출처: ai/research/gaze-tracking/artifacts/face_landmarker.task.json
import { createHash } from 'node:crypto';
import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const TASK = 'face_landmarker.task';
const TASK_SHA256 = '64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff';
const TASK_SIZE = 3_758_596;
const RUNTIME_VERSION = '1.0.1';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const out = join(root, 'public', 'models');
mkdirSync(out, { recursive: true });

const fail = (lines) => {
  console.error(lines.join('\n'));
  process.exitCode = 1;
};

// ── 런타임(wasm) ────────────────────────────────────────────────────
const runtimeDir = join(root, 'node_modules', '@mediapipe', 'tasks-vision');
const runtimeVersion = JSON.parse(readFileSync(join(runtimeDir, 'package.json'), 'utf8')).version;
if (runtimeVersion !== RUNTIME_VERSION) {
  fail([
    `@mediapipe/tasks-vision 이 ${runtimeVersion} 입니다. 시선 엔진은 ${RUNTIME_VERSION} 으로만 검증됐습니다.`,
    '  npm install 로 package-lock.json 의 버전을 다시 받으세요. wasm 은 복사하지 않았습니다.',
  ]);
} else {
  const wasmDir = join(runtimeDir, 'wasm');
  for (const name of readdirSync(wasmDir)) copyFileSync(join(wasmDir, name), join(out, name));
  console.log(`wasm (${RUNTIME_VERSION}) -> ${out}`);
}

// ── 얼굴 랜드마크 모델 ──────────────────────────────────────────────
const sha256 = (path) => createHash('sha256').update(readFileSync(path)).digest('hex');
const isExpected = (path) =>
  readFileSync(path).length === TASK_SIZE && sha256(path) === TASK_SHA256;

const target = join(out, TASK);
const ai = (...parts) => resolve(root, '..', 'ai', ...parts);
const candidates = [
  // 지금 AI 위치 (research 로 옮긴 뒤)
  ai('research', 'gaze-tracking', 'artifacts', TASK),
  // 옮기기 전 위치
  ai('archive', 'workspaces', 'jewon-kim', 'gaze-tracking', 'v1', 'local', 'ai', 'models', TASK),
];

const download = [
  `  curl -L -o public/models/${TASK} \\`,
  `    https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/${TASK}`,
];

if (existsSync(target)) {
  if (isExpected(target)) {
    console.log(`${TASK} 확인됨 (sha256 ${TASK_SHA256.slice(0, 8)}…)`);
  } else {
    fail([
      `public/models/${TASK} 가 시선 엔진이 검증한 파일과 다릅니다 (크기 또는 sha256 불일치).`,
      '  지우고 아래 명령으로 다시 받은 뒤 npm run models 를 다시 실행하세요.',
      ...download,
    ]);
  }
} else {
  const found = candidates.find((p) => existsSync(p) && isExpected(p));
  if (found) {
    copyFileSync(found, target);
    console.log(`${TASK} -> ${target} (sha256 확인됨)`);
  } else {
    fail([
      `${TASK} 가 없습니다. 팀 드라이브에서 받거나 아래 명령으로 받은 뒤 다시 실행하세요.`,
      ...download,
      '없으면 장치 점검은 "시선 분석을 켤 수 없어요"를 띄웁니다.',
    ]);
  }
}
