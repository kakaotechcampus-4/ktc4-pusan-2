# 모델 가중치는 여기에 둡니다

`public/models/` 에 두고 Cache API로 캐싱합니다. **CDN에서 받지 않습니다.**
시연장 와이파이가 느리면 발표가 안 됩니다.

시선 엔진 v1.1(`src/vendor/gaze`)이 읽는 파일입니다. `npm run models` 가 채웁니다.

`npm run models` 는 `face_landmarker.task` 의 크기·sha256(`64184e22…`)과 `@mediapipe/tasks-vision` 버전(`1.0.1`)이
AI 가 검증한 값과 같은지 확인하고, 다르면 복사하지 않고 실패합니다. 다른 모델·런타임이면 에러 없이 판정만 틀어지기 때문입니다.
`package.json` 의 `@mediapipe/tasks-vision` 을 `^` 없이 고정해 둔 것도 같은 이유입니다.

| 파일 | 무엇 | 어디서 |
| --- | --- | --- |
| `face_landmarker.task` | MediaPipe Face Landmarker (3.6 MB) | AI 리그 `ai/models/` · 팀 드라이브 |
| `vision_wasm_module_internal.wasm` · `.js` | MediaPipe WASM 런타임 — **모듈 Worker용** | `node_modules/@mediapipe/tasks-vision/wasm/` |

`vision_wasm_internal.*` 이 아니라 `vision_wasm_module_internal.*` 입니다 — 시선 워커가
`type: 'module'` 이라서입니다. 스크립트는 wasm 폴더를 통째로 복사하므로 나머지가 같이 있어도 됩니다.
시선 백본은 가중치가 없습니다(고개 방향 기하 계산, I-03) — `.onnx` 는 들어오지 않습니다.

파일이 없으면 앱은 죽지 않습니다. 장치 점검은 "시선 분석을 켤 수 없어요"를 띄우고
'소리만으로 계속하기'만 열리며, 리허설은 시선을 `ENGINE_UNAVAILABLE` 로 제외합니다.

가중치는 커밋하지 않습니다 (`.gitignore`).
