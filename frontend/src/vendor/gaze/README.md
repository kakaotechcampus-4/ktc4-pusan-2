# vendor/gaze — AI 시선 엔진 v1.1 (복사본) + 카메라 화면 (FE 원본)

AI 파트(제원)가 만든 브라우저용 시선 엔진과, 그 엔진을 쓰는 장치 점검의 카메라 화면 모듈이 있는 폴더입니다.
**폴더마다 주인이 다릅니다** (#611).

| 폴더 | 주인 | 고치는 곳 |
| --- | --- | --- |
| `engine/` · `worker/` | AI | **여기서 고치지 않습니다.** AI 쪽에서 고치고 다시 복사합니다 |
| `camera/` | **FE** | 여기서 고칩니다. AI 쪽 `camera/` 는 연구 데모용으로만 남고, 다시 복사하지 않습니다 |

| 항목 | 값 |
| --- | --- |
| 원본 | `ai/research/gaze-tracking/web/src/{engine,worker}` (`engine/__tests__` 포함) |
| 기준 커밋 | `11db0f7d` (docs(ai): 검증 반영 — #611) — PR #612 브랜치, 머지 전. 머지되면 머지 커밋으로 고칩니다 |
| `camera/` 넘겨받은 판 | 같은 `11db0f7d`. 이 뒤로는 FE 원본입니다 |
| 모델 버전 | `gaze_v1.1.0+head_pose+reference_anchor_v1` · 설정 해시 `32ad2751de59` |
| 사용법 원문 | `ai/research/gaze-tracking/web/README.md` (FE에 붙이는 법 · 카메라 화면 붙이기) · `ai/research/gaze-tracking/DEPLOY.md` |
| 화면이 쓰는 엔진 코드 | `docs/ai/features/gaze-tracking.md` 5-6 '화면이 쓰는 엔진 코드' (엔진은 코드만 내고 문구는 `camera/text.ts` 가 정합니다) |

## 누가 무엇을 쓰나

| 폴더 | 쓰는 곳 | 역할 |
| --- | --- | --- |
| `engine/` | `workers/modelClassifier.ts` | 리허설 워커의 `GazeClassifier` 구현 (프레임 판정만, 1초 다수결은 FE) |
| `camera/` + `worker/` | `features/rehearsal/prepare/useGazeSetup.ts` | 장치 점검의 준비 점검 → 고개 원 → 3점 보정 화면 (상자 안은 모듈이 그림) |

엔진은 두 입구로만 부릅니다(원문 README 'FE로 복사하기'). 안쪽 파일(`config` · `evidence` · `reference`)을 바로 부르지 않습니다.

- `engine/index.ts` — `createClassifier`. MediaPipe 를 불러오므로 워커에서 동적 `import()` 로 부릅니다. 타입만 가져오는 `import type` 은 괜찮습니다
- `engine/contract.ts` — MediaPipe 없이 쓰는 값 (`CONFIG_HASH` · `makeConfig` · `GazeSlicer` · `sampleToDict` · `frameFromDecision` · `SCREEN_MERGED_WARNING`, 화면이 쓰는 `GAZE_DIRECTIONS` · `Cue` · `FaceGuide` · `State` …)

`camera/` 도 같은 입구로만 부릅니다 — 엔진은 `../engine`(타입만) · `../engine/contract`, Worker 는 `../worker/protocol` 입니다.
`.oxlintrc.json` 이 이것을 검사합니다. 그래야 엔진을 다시 복사해도 화면이 엔진 안쪽 파일에 묶이지 않습니다.
Worker 는 `camera/view.ts` 가 `../worker/gaze.worker.ts` 를 경로로 띄우므로 `camera/` 와 `worker/` 는 이 폴더 아래 나란히 둡니다.

## 자산

`public/models/`에 아래가 있어야 엔진이 뜹니다. 없으면 장치 점검은 "시선 분석을 켤 수 없어요"를 띄우고,
리허설은 시선을 `ENGINE_UNAVAILABLE`로 제외합니다. `npm run models`가 wasm을 복사하고 `face_landmarker.task`를 찾아 둡니다.

| 파일 | 출처 |
| --- | --- |
| `face_landmarker.task` | AI `ai/research/gaze-tracking/artifacts/` 또는 팀 드라이브 (sha256 은 `npm run models` 가 확인) |
| `vision_wasm_module_internal.js` · `.wasm` | `node_modules/@mediapipe/tasks-vision/wasm/` |

## 다시 복사할 때

1. 위 원본 경로의 **`engine` · `worker` 두 폴더만** 통째로 덮어씁니다. `camera/` 는 덮어쓰지 않습니다 — FE 가 고친 것이 사라집니다.
2. 이 문서의 기준 커밋과 모델 버전을 고칩니다.
3. `npm run typecheck` 가 `camera/` 에서 깨지면 엔진의 공개 입구가 바뀐 것입니다. 화면 쪽을 맞춥니다.
   엔진에 새 코드가 생겼으면(`gaze-tracking.md` 5-6) `camera/text.ts` 에 문구를 더합니다.
4. 옛 보정 기준은 쓰이지 않습니다 — 기준은 탭 메모리에만 두어(`features/rehearsal/lib/zoneRefMemory.ts`) 새로고침하면 사라지고,
   탭에 남아 있어도 엔진 버전이 다르면 `takeZoneRef` 가, 버전은 같아도 설정이 바뀌면 `configHash` 가 달라져
   `workers/calibrationModel.ts` 가 거릅니다.
   엔진의 `calibrate()` 는 모양(schema)만 보고 설정은 확인하지 않으니, 이 검사를 지우지 마세요.
5. `npm run typecheck && npm run lint && npm run test`
