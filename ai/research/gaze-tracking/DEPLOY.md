# 배포 가이드 — 시선

시선 기능을 실제 서비스에 붙이려면 무엇이 필요한지 정리합니다. 이 기능은 두 곳에 배포됩니다.

- **TS 엔진** (`web/src/engine/`) → 사용자 브라우저. frontend에 폴더째 넣습니다.
- **서버 코어** (`src/gaze/`) → AI 서버(`ai/service`). 폴더째 옮깁니다.

아직 AI 서버 뼈대와 frontend 연결은 없습니다. 이 문서의 FE · BE 항목은 **제안**이고, 각 팀과 협의해서 정합니다.
입력 · 출력의 필드 단위 설명은 [INTERFACE.md](INTERFACE.md)에 있습니다.

## 배포 구성

```
[사용자 브라우저]
 카메라 ─▶ frontend 카메라 화면 ─▶ Worker ─▶ TS 엔진 (frontend/src/workers/gaze/engine/)
                                              │ 프레임 · 랜드마크 · 얼굴 측정값 · 보정 모델: 메모리에만
                                              ▼
                                        1초 기록 (필드 8개, 약 150 B/초)
                                              │
 ─────────────────────────────────────────────┼──────────────── 기기 밖으로 나가는 것은 이것뿐
                                              ▼
 BE ── 저장(테이크별 1초 기록 + 엔진 버전) ──HTTP──▶ AI 서버 (상태 없음)
                                                      parse_records → normalize_samples
                                                      → evaluate_gaze / take_summary / compare_summaries / intervention_outcome
```

- AI 서버는 DB를 쓰지 않습니다. 1초 기록은 요청으로 받고, 결과는 응답으로 돌려줍니다. 저장은 BE가 합니다.
- 브라우저에서 모델 · 런타임을 받는 첫 로딩은 약 16 MB(압축 시 약 7 MB)이고, 그 뒤는 브라우저 캐시를 씁니다.

---

## 필요한 작업

### AI 서버 (`ai/service/`)

- [ ] 코어 옮기기: `src/gaze/` → `src/pitch_coach_ai/features/gaze/`
  - 코어 안은 상대 import라 코드 변경이 없습니다. 의존성은 `pydantic`뿐입니다.
- [ ] 테스트 옮기기: `tests/unit/` → `tests/unit/gaze/`
  - import 접두어만 `gaze.` → `pitch_coach_ai.features.gaze.`로 바꿉니다.
  - `test_core_boundary.py`는 코어 폴더를 상대 경로로 찾으므로 그 경로 계산을 새 위치에 맞춥니다.
- [ ] API (경로는 BE와 정함, 버전 표기 없음). 두 요청 모두 처음에 `parse_records` → `normalize_samples`를 거칩니다.

  | API | 언제 | 요청 | 응답 | 코어 |
  |---|---|---|---|---|
  | 시선 이슈 | 연습 중 (코치가 판단할 때마다) | 이번 테이크의 1초 기록, 테이크 시작 시각, **지금 시각** | 지금의 시선 이슈 (+ 직전 피드백의 효과. 피드백 10초 뒤부터) | `evaluate_gaze`, `intervention_outcome` |
  | 시선 요약 | 테이크 종료 | 이번 테이크의 1초 기록, 테이크 시작 · 끝, (이전 테이크 요약) | 테이크 요약, 이전 테이크와 차이 | `take_summary`, `compare_summaries` |

  - `normalize_samples`에 **지금 시각 · 테이크 끝을 꼭 줍니다.** 기록이 끊긴 시간(카메라 끊김 · 탭 숨김 · 유실)을 측정 못 함으로 채워서,
    오래된 마지막 상태로 피드백하지 않게 하는 장치입니다.
  - `parse_records`가 버린 기록 수를 로그 · 응답 메타에 남깁니다. 버려도 요청은 실패시키지 않습니다.
- [ ] 응답 메타에 기능 버전(`FEATURE_VERSION`)과 요청에 실려 온 엔진 버전을 싣습니다.
- [ ] 계약 파일: `GazeSampleRecord`의 JSON Schema(FE 타입 생성용)와 요청 · 응답 예시, 계약 테스트
- [ ] 실시간 코치 연결: 지금 코치 에이전트는 시선을 라벨별 비율로 따로 받습니다. 시선 이슈 형식(`evaluator: "gaze"` …)으로 받도록 맞출지 코치와 함께 정합니다.

### BE (BE와 협의)

- [ ] 테이크마다 1초 기록을 저장 (예: `takes.event_logs` JSONB). 30분 테이크면 약 1,800개 · 270 KB입니다.
  - 엔진 버전과 기록 형식 버전을 같이 저장합니다. 나중에 형식이 바뀌어도 어느 버전 기록인지 알 수 있게 합니다.
- [ ] 연습 중에는 받은 기록으로 시선 이슈 API를 부르고, 테이크가 끝나면 시선 요약 API를 불러 저장합니다.
- [ ] 지금 FE가 보내는 시선 값(`GazePayload`: 3구역 구간 목록)에서 1초 기록 전송으로 바꾸는 계약 변경이 필요합니다 (FE와 함께).

### FE (제안 — FE와 협의)

| 무엇 | 제안 |
|---|---|
| 엔진 위치 | `web/src/engine/`을 `frontend/src/workers/gaze/engine/`으로 폴더째 (`__tests__` · `defaults.generated.ts` 포함). FE는 `index.ts` · `contract.ts`만 import. CODEOWNERS로 AI 소유 표시 |
| 연결 | `frontend/src/workers/modelClassifier.ts`(지금 stub)를 `web/test/fe-contract/conformance.ts`의 `EngineBackedClassifier` 내용으로 채운다 |
| 지원 확인 | 카메라 권한을 묻기 전에 `checkSupport()`. 실패하면 시선만 빼고 발표는 진행 (`USER_DECLINED`와 같은 흐름) |
| 실패 처리 | `EngineInitError`의 사유(`UNSUPPORTED_BROWSER` · `TIMEOUT` · `INIT_FAILED`)는 모두 기존 `ENGINE_UNAVAILABLE`로. 사유는 로그용 |
| **보정 모델 저장 중단** | 지금 IndexedDB `zoneRefs`에 보정 모델을 저장한다. 모델 안에 얼굴 측정값(얼굴 위치 · 면적 · 홍채 픽셀 · 거리)이 있어 **저장하지 않는다.** 세션 메모리에만 두고 다음 세션은 다시 보정(지점마다 약 2초). DB 버전을 올리며 기존 `zoneRefs`를 지운다 |
| 1초 기록 | Worker가 `GazeEvidenceRecorder`의 1초 기록을 내보내고(`sampleToDict`), 그 기록을 BE로 보낸다. 테이크마다 새 기록기를 쓰고, `classify`의 `tMs`는 테이크 시작 기준으로 넘기며, 테이크가 끝나면 `flush(테이크 길이)`를 부른다 |
| 카메라 끊김 | 지금은 끊기면 테이크 전체의 시선을 측정 제외한다. 서버가 빈 시간을 측정 못 함으로 처리하므로, 끊기기 전까지 잘 잰 기록은 살리는 방식을 검토 |
| 탭 숨김 · 저사양 | 탭이 숨겨지면 시선 측정이 멈춘다는 표시. `LIGHT` 모드를 만들 때 초당 5프레임 이상 유지 (1초 기록은 프레임 4개 이상이어야 판정한다) |
| 자산 | 빌드 단계에서 `web/scripts/verify-assets.mjs`와 같은 해시 확인 후 `public/models/`에 넣는다. 파일 이름이나 경로에 버전을 붙여 캐시를 바꾼다. CDN은 쓰지 않는다 |
| 버전 고정 | `@mediapipe/tasks-vision`을 `^1.0.1`에서 `1.0.1`로 정확히 고정 |
| 생성 파일 | `defaults.generated.ts` · `__tests__/fixtures/`는 prettier · oxlint 대상에서 뺀다. 생성은 research의 `tools/`가 계속 한다 (출력 경로만 frontend로) |
| 카메라 화면 | 지금 화면(`web/src/camera`)을 그대로 붙이고 FE가 점진적으로 고친다. `camera/` · `worker/`는 엔진 옆(`frontend/src/workers/gaze/`)에 두면 서로 부르는 상대 경로를 고치지 않아도 된다. FE oxlint의 `no-nested-ternary`에 8곳 걸린다(`view.ts`). 프레임 예외 · Worker 종료 · 카메라 끊김은 이미 `error` 이벤트(`reason`)로 알린다 |
| 계약 | FE 구역은 3개(청중 · 화면/대본 · 판정 불가), 엔진은 4상태 + OTHER 방향. OTHER를 어디로 셀지, 보정 실패 사유 `ANCHOR_AMBIGUOUS`를 FE 목록에 넣을지 정한다 |

### 인프라

- [ ] 운영 Caddy에 정적 파일 압축(`encode`)을 켠다. wasm 11.8 MB → 압축 시 3.4 MB
- [ ] `/models/` 파일에 긴 캐시 헤더 (경로에 버전이 있으므로)
- [ ] **COOP/COEP는 넣지 않는다.** 엔진은 교차 출처 격리 없이 동작하고(`npm run smoke:prod`로 확인), COEP는 Safari 미지원 · OAuth 팝업 등에 영향을 준다
- [ ] CSP를 쓰게 되면 `script-src 'wasm-unsafe-eval'`, `worker-src 'self'`가 필요하다

---

## 비용과 시간 (개발 PC, Chrome, 640×480)

| 항목 | 값 |
|---|---|
| 첫 로딩 | 약 16 MB (압축 시 약 7 MB). 초기화 제한 시간 기본 60초 |
| Worker 한 프레임 | 중앙값 약 22 ms (MediaPipe 약 19 ms), p95 약 30 ~ 38 ms / 예산 125 ms (초당 8프레임) |
| 같은 PC, 배터리(절전) | 중앙값 약 100 ~ 113 ms (MediaPipe 약 89 ~ 101 ms), p95 약 142 ~ 147 ms. 예산을 넘는다 |
| 1초 기록 | 약 150 B. 30분 테이크 약 270 KB |
| 서버 계산 | 테이크 하나를 읽는 데 밀리초 단위 (Python 표준 라이브러리 + pydantic) |

저사양 노트북에서는 `cd web && npm run bench`로 다시 재야 합니다. 같은 PC도 배터리로 돌면 약 5배 느려져서, 저사양 대응(`LIGHT` 모드)이 필요한지는 실제 사용자 기기로 정합니다.

브라우저는 Chrome에서만 확인했습니다(`smoke` · `smoke:prod`). Edge · Safari · Firefox는 확인하지 못했습니다.

## 결정해야 할 것

1. 1초 기록 전송 경로와 형식 (FE → BE → AI). `GazeSampleRecord`를 그대로 쓸지
2. 실시간 시선 이슈를 몇 초마다 부를지, 코치 에이전트가 시선 이슈 형식을 그대로 받을지
3. FE 구역(3개)과 엔진 상태(4개 + 방향)의 대응, OTHER 처리
4. 보정 모델 저장 중단에 따른 보정 흐름 (세션마다 다시 보정)
5. 실측 평가 데이터 수집 정책 (동의 · 보관 기간 · 삭제). 정하기 전에는 얼굴 영상을 녹화 · 저장하지 않는다

## 옮긴 뒤 확인

- [ ] AI 서버: 옮긴 `tests/unit/gaze/`가 그대로 통과하고, 코어 경계 테스트가 새 위치를 본다
- [ ] frontend: `tsc -b`, `oxlint --max-warnings=0`, `vitest`(엔진 `__tests__` 포함)가 통과한다
- [ ] research: `tools/export_config.py` · `make_fixtures.py`의 출력 경로를 frontend로 바꾸고, `tests/lab/test_web_engine_sources.py`가 그 파일을 보게 한다
- [ ] research의 `web/`은 엔진 사본을 지우고 데모 · bench만 남긴다 (`web-demo/`, frontend 엔진을 vite alias로 import)
- [ ] frontend 빌드로 `smoke:prod`와 같은 확인 (격리 없이 준비 점검 → 실시간 → 카메라 끊김). Chrome 말고 지원할 브라우저에서도
