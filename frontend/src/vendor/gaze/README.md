# vendor/gaze — AI 시선 엔진 v1.1 (복사본)

AI 파트(제원)가 만든 브라우저용 시선 엔진과 카메라 화면 모듈을 **그대로 복사**해 둔 폴더입니다.
**여기서 고치지 않습니다.** 고칠 것이 생기면 AI 쪽에서 고치고 다시 복사합니다.

| 항목 | 값 |
| --- | --- |
| 원본 | `ai/research/gaze-tracking/web/src/{engine,camera,worker}` (`engine/__tests__` 포함) |
| 기준 커밋 | `1f92351` (fix(ai): 프레임 예외 · Worker 종료 · 카메라 끊김에서 시선 분석이 조용히 멈추지 않게 함) — develop `a315a0d` 시점 (AI #104~#108) |
| 모델 버전 | `gaze_v1.1.0+head_pose+reference_anchor_v1` · 설정 해시 `14ed457ab13b` |
| 사용법 원문 | `ai/research/gaze-tracking/web/README.md` (FE에 붙이는 법 · 카메라 화면 붙이기) · `ai/research/gaze-tracking/DEPLOY.md` |

## 누가 무엇을 쓰나

| 폴더 | 쓰는 곳 | 역할 |
| --- | --- | --- |
| `engine/` | `workers/modelClassifier.ts` | 리허설 워커의 `GazeClassifier` 구현 (프레임 판정만, 1초 다수결은 FE) |
| `camera/` + `worker/` | `features/rehearsal/prepare/GazeSetup.tsx` | 장치 점검의 준비 점검 → 고개 원 → 3점 보정 화면 (상자 안은 모듈이 그림) |

## 자산

`public/models/`에 아래가 있어야 엔진이 뜹니다. 없으면 장치 점검은 "시선 분석을 켤 수 없어요"를 띄우고,
리허설은 시선을 `ENGINE_UNAVAILABLE`로 제외합니다. `npm run models`가 wasm을 복사하고 `face_landmarker.task`를 찾아 둡니다.

| 파일 | 출처 |
| --- | --- |
| `face_landmarker.task` | AI `ai/research/gaze-tracking/artifacts/` 또는 팀 드라이브 (sha256 은 `npm run models` 가 확인) |
| `vision_wasm_module_internal.js` · `.wasm` | `node_modules/@mediapipe/tasks-vision/wasm/` |

## 다시 복사할 때

1. 위 원본 경로의 세 폴더를 통째로 덮어씁니다.
2. 이 문서의 기준 커밋과 모델 버전을 고칩니다.
3. 저장된 기준(IndexedDB)은 자동으로 버려집니다 — 모델 버전이 바뀌면 저장 키(엔진 버전)가 달라지고,
   버전은 같아도 설정이 바뀌면 `configHash` 가 달라져 `workers/calibrationModel.ts` 가 거릅니다.
   엔진의 `calibrate()` 는 모양(schema)만 보고 설정은 확인하지 않으니, 이 검사를 지우지 마세요.
4. `npm run typecheck && npm run test`
