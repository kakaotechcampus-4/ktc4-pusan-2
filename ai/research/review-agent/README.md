# review-agent (리뷰)

Take가 끝난 뒤 코치의 **Take 결과**와 **대본 전달도 채점**을 읽어 차원 상태 · 점수 · 미션 판정과 다음 미션 · 반복 문제를 정합니다.

| | |
|---|---|
| 담당 | seojin-lee |
| 기능 버전 | `review-1.0`, 요청 · 응답 스키마 `1.0` (`src/review/version.py`) |
| 상태 | 패키지 + 테스트 (규칙 단계만. LLM 단계 · 숫자 검사 · 이전 Take 비교는 아직 없음) |
| 설계 | 목적 · BE ↔ 리뷰 계약 · 처리 · 배포 → [`docs/ai/features/review-agent.md`](../../../docs/ai/features/review-agent.md) |

## 1. 폴더 · 파일 지도

```
review-agent/
├─ README.md
├─ pyproject.toml · uv.lock
├─ src/review/            리뷰 코어 → 서비스 features/review/ 로 폴더째 옮긴다
│   └─ examples/review/   요청 · 응답 예시 (계약 테스트가 응답과 맞는지 본다)
└─ tests/unit/            코어 경계 · 단계별 규칙 · 예시 계약
```

| 파일 | 하는 일 | 설계 |
|---|---|---|
| `core.py` | 진입점 `apply_rules`(규칙 단계, 문장 없음) · `review_take`(최종) | 4-2 |
| `schemas.py` | 요청 · 응답 pydantic 모델 = 계약 | 5 |
| `config.py` | 모든 기준값 · 가중치 · 다음 미션 지표. `load_config()`로 덮어쓰기, `criteria_version()` | — |
| `vocab.py` | 고정 어휘와 차원 표 | 5-3 |
| `version.py` | 스키마 · 기능 버전 | 9 |
| `metrics.py` | 지표 이름으로 값 찾기(`find_metric`), 비교(`compare`) | — |
| `evaluable.py` | 판단 가능 여부, 잰 영역(`measured_areas`) | 6-1 1번 |
| `missions.py` | 미션 · 사용자 기준 판정 | 6-1 2 · 3번 |
| `burden.py` | 차원 · 장 · 영역별 부담 | 6-1 4번 |
| `dimensions.py` | 차원 상태 | 6-1 5번 |
| `next_missions.py` | 다음 미션 후보 · 선택 | 6-1 7번 |
| `recurring.py` | 반복 문제 | 6-1 8번 |
| `score.py` | 점수 | 6-1 9번 |
| `text.py` | 기본 문장 (요약 · 강점 · 개선점) | 6-2 |

## 2. 실행법

```bash
cd ai/research/review-agent
uv sync                                   # Python 3.12, pydantic + 개발 도구 설치
```

LLM을 쓰지 않아 `ai/.env`가 필요 없습니다. Windows에서는 `uv run python -m …` 형태로 부르고, 콘솔 한글이 깨지면 `PYTHONIOENCODING=utf-8`을 붙입니다.

| 하는 일 | 명령 |
|---|---|
| 테스트 | `uv run python -m pytest` |
| 린트 | `uv run ruff check . && uv run ruff format --check .` |

파이썬에서 직접

```python
from review import apply_rules, load_config, review_take

# 최종 응답 ReviewResponse. dict 로: resp.model_dump(mode="json")
resp = review_take(request_dict)
# 규칙 단계만 (문장 없음): rules.evaluable · rules.burden · rules.next_mission_candidates …
rules = apply_rules(request_dict)
# 설정 덮어쓰기
resp = review_take(request_dict, config=load_config(dimension={"priority_max_count": 2}))
```

설정은 `config.py`에만 있습니다. 코드를 고치지 말고 바꿀 값만 `load_config(...)`로 덮어씁니다.

## 3. 실험 결과

아직 없습니다. 공통 기준 Take와 정답 리뷰로 평가한 결과(설계 13절)를 여기에 둡니다.
