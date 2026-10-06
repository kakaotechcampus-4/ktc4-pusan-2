# 배포 가이드 — 대본 전달도

이 기능을 AI 서버(`ai/service`)에 올려 BE가 호출하게 하려면 무엇이 필요한지 정리합니다.
아직 서버는 없고, 코어(`src/script_coverage/`)를 그대로 옮겨 쓰는 것을 전제로 합니다.

## 배포 구성

```
FE ──▶ BE ──HTTP──▶ AI 서버 (상태 없음) ──▶ LLM API
        │
        └─ DB: 평가 기준 · 평가 결과 · 비슷한 말 · 사용자 확인 저장
```

- AI 서버는 DB를 쓰지 않습니다. 필요한 데이터(평가 기준 등)는 요청으로 받고, 결과는 응답으로 돌려줍니다.
- 저장과 재호출 판단은 BE가 합니다.
- LLM 응답 캐시는 배포에서 쓰지 않습니다(`cache=None`). BE가 평가 기준을 저장해 두므로 같은 대본을 다시 분석할 일이 없습니다.

## 필요한 작업

### AI 서버 (`ai/service/`)

- [ ] 서버 뼈대: FastAPI 앱, `/health` · `/ready`, 설정(환경변수), Python 3.12
- [ ] 코어 옮기기: `src/script_coverage/` → `src/pitch_coach_ai/features/script_coverage/`
  - 코어 안은 상대 import라 코드 변경이 없습니다.
  - 코어 의존성 세 개를 service `pyproject.toml`에 더합니다: `pydantic`, `kiwipiepy>=0.23,<0.24`, `scikit-learn>=1.8,<1.9`
- [ ] 테스트 옮기기: `tests/unit/` → `tests/unit/script_coverage/`
  - import 접두사만 `script_coverage.` → `pitch_coach_ai.features.script_coverage.`로 일괄 치환합니다.
  - `test_core_boundary.py`는 코어 폴더 위치를 상대 경로로 찾으므로 그 경로 계산을 새 위치에 맞춥니다.
- [ ] LLM 클라이언트: 출력 스키마별 클라이언트 5개(대본 분석 3 · STT 평가 2), 타임아웃 · 재시도 · 토큰 로그
  - 어떤 스키마로 몇 개를 만드는지는 `coverage_lab/llm.py`의 `script_llms` · `stt_llms`를 참고합니다.
- [ ] 시작할 때 Kiwi 미리 로딩: 처음 로딩이 느리므로 `/ready` 전에 끝냄
- [ ] API 3개 (경로는 BE와 정함, 버전 표기 없음)
- [ ] 응답에 기능 버전(`FEATURE_VERSION`)과 평가 기준 스키마 버전(`RUBRIC_SCHEMA_VERSION`) 싣기
- [ ] 계약 파일(OpenAPI · 요청/응답 예시)과 계약 테스트
- [ ] Dockerfile, CI (ruff · pytest)

### 인프라

- [ ] `infra/docker-compose.yml`에 `ai` 서비스 추가
- [ ] BE 환경변수 `AI_BASE_URL=http://ai:8000`
- [ ] AI 서버 환경변수 `OPENAI_BASE_URL` · `OPENAI_API_KEY` · `OPENAI_MODEL` (비밀값은 배포 환경에서 주입)

### BE (BE와 협의)

- [ ] AI 호출 함수 3개
- [ ] 저장 테이블: 평가 기준(대본 버전 · 슬라이드별), 평가 결과(연습 · 슬라이드별), 비슷한 말, 사용자 확인, 확인 뒤 평가
- [ ] 호출을 백그라운드 작업으로 실행 (대본 파싱처럼). LLM 호출이 많아 동기 요청으로는 오래 걸림
- [ ] 실패한 슬라이드만 다시 요청하는 재시도
- [ ] 비슷한 말 위치(`stt_raw_start` · `stt_raw_end`)를 Deepgram 단어 타임스탬프에 맞춰 녹음 구간 찾기

## API

| API | 언제 | 요청 | 응답 | 코어 함수 |
|---|---|---|---|---|
| 평가 기준 만들기 | 대본 등록 · 수정 | 슬라이드별 대본 | 슬라이드별 평가 기준, 실패한 슬라이드 | `script_analysis.core.analyze_script` |
| 채점 | 연습 종료 | 슬라이드별 STT + 그 대본의 평가 기준 | 슬라이드별 평가 결과, 실패한 슬라이드 | `stt_evaluation.core.evaluate_take` |
| 다시 계산 | 사용자 확인 | 평가 결과 + 평가 기준 + 사용자 답 | 다시 계산한 평가 결과 | `stt_evaluation.confirm.rescore_evaluation` |

- 대본을 고쳤을 때는 평가 기준의 `meta.content_hash`로 바뀐 슬라이드만 골라 보내면 비용이 줄어듭니다.
- 평가 결과에는 채점에 쓴 평가 기준의 `rubric_id`가 들어 있습니다. 평가 기준이 바뀌면 다시 계산은 거절되고, 다시 채점해야 합니다.
- 요청 · 응답 모양의 기준은 코어의 pydantic 모델입니다: 평가 기준 `shared/rubric.py`, 평가 결과 `stt_evaluation/schemas.py`.

## 비용과 시간 (v1 가상 데이터 기준)

| 작업 | LLM 호출 |
|---|---|
| 평가 기준 만들기 | 슬라이드당 3회 (20장이면 약 60회, 동시 4개) |
| 채점 | 슬라이드당 1회 + 충돌한 슬라이드만 1회 더 (약 25%) |
| 다시 계산 | 0회 |

## 결정해야 할 것

- API 경로와 요청 · 응답 필드 (BE와)
- 호출 방식: 백그라운드 작업 + 결과 조회인지, 콜백인지
- 평가 기준을 대본 버전마다 새로 만들지, 바뀐 슬라이드만 갱신할지
- 사용할 LLM 모델과 타임아웃

## 옮긴 뒤

- [ ] 단위 테스트 통과
- [ ] research의 캐시로 같은 입력을 넣어, 옮기기 전과 평가 기준 · 평가 결과가 같은지 비교
- [ ] 실제 API로 슬라이드 1장 끝까지 (live 테스트)
- [ ] research는 `coverage_lab`만 남기고 코어를 service에서 import하도록 바꿈
  - `pyproject.toml`에 `pitch-coach-ai`를 editable로 추가하고, `src/script_coverage/`를 지웁니다.
  - 이후 코어는 service에서만 고칩니다.
