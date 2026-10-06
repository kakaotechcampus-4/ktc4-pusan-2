// 시선 엔진(vendor/gaze) 자산을 public/models/ 에 둡니다 — `npm run models`.
//
// 앱은 실행 중에 CDN 에서 아무것도 받지 않습니다 (CLAUDE.md 개발 중 함정).
// 그래서 이 스크립트가 미리 깔아 둡니다. public/models/* 는 커밋하지 않습니다.
//
//   vision_wasm_*      node_modules/@mediapipe/tasks-vision/wasm/ 에서 복사
//                      (모듈 Worker 는 vision_wasm_module_internal.* 를 씁니다)
//   face_landmarker.task  AI 리그의 ai/models/ 에 있으면 복사, 없으면 받는 명령을 알려 줍니다
import { copyFileSync, existsSync, mkdirSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const out = join(root, 'public', 'models');
mkdirSync(out, { recursive: true });

const wasmDir = join(root, 'node_modules', '@mediapipe', 'tasks-vision', 'wasm');
for (const name of readdirSync(wasmDir)) copyFileSync(join(wasmDir, name), join(out, name));
console.log(`wasm  -> ${out}`);

const TASK = 'face_landmarker.task';
const target = join(out, TASK);
const candidates = [
  resolve(
    root,
    '..',
    'ai',
    'archive',
    'workspaces',
    'jewon-kim',
    'gaze-tracking',
    'v1',
    'local',
    'ai',
    'models',
    TASK,
  ),
];

if (existsSync(target)) {
  console.log(`${TASK} 이미 있음 (${statSync(target).size} B)`);
} else {
  const found = candidates.find((p) => existsSync(p));
  if (found) {
    copyFileSync(found, target);
    console.log(`${TASK} -> ${target}`);
  } else {
    console.error(
      [
        `${TASK} 가 없습니다. 팀 드라이브에서 받거나 아래 명령으로 받은 뒤 다시 실행하세요.`,
        `  curl -L -o public/models/${TASK} \\`,
        `    https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/${TASK}`,
        '없으면 장치 점검은 "시선 분석을 켤 수 없어요"를 띄우고, 소리만으로 연습할 수 있습니다.',
      ].join('\n'),
    );
    process.exitCode = 1;
  }
}
