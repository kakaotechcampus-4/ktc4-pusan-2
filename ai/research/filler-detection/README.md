# filler-detection

군더더기 판정 모듈입니다. STT 확정 단어마다 "음 · 그 · 그니까" 같은 군더더기인지 층(T1 · T2 · T3)과 함께 판정하고, 최근 빈도로 코치가 1초마다 부르는 판정 결과(영역 `FILLER`)를 냅니다.

- 설계: [docs/ai/features/filler.md](../../../docs/ai/features/filler.md)
- 공통 계약: [docs/ai/architecture.md](../../../docs/ai/architecture.md) 7절
- Issue: #152

## 실행
Python 3.12. LLM을 쓰지 않아 `ai/.env`가 필요 없습니다.
```bash
cd ai/research/filler-detection
uv sync
uv run ruff check . && uv run ruff format --check .
uv run python -m pytest
```

## 폴더 · 파일 지도
| 파일 | 하는 일 | 입력 → 출력 | 부르는 곳 | 고칠 때 |
|---|---|---|---|---|
| `src/filler/schemas.py` | 입출력 pydantic 모델 (= 계약) | `judge` 입력 dict → `FillerInput`, `JudgmentResult` · `FillerSummary` · `IssueCriteria` | `core.py`, 테스트 | 필드 이름 · 뜻이 바뀌면 `schema_version` · filler.md 5절을 함께 고칩니다 |
| `src/filler/config.py` | 사전(T1 · T2 · T3 · keyterm)과 기준값 `FillerConfig`. 값의 해시가 `criteria_version`에 들어갑니다 | — → `FillerConfig` | `core.py`, 테스트 | 단어를 바꾸면 filler.md 5-2의 표와 BE keyterm 목록도 맞춥니다 |
| `src/filler/version.py` | 기능 버전 `filler-1.0` | — → 문자열 | `core.py` | 판정 규칙 · 출력의 뜻이 바뀔 때만 올립니다 |
| `tests/test_contract.py` | 입출력 모델 · 사전 검증 테스트 | — | pytest | — |
| `tests/test_core_boundary.py` | 코어가 상대 import와 표준 모듈 · pydantic만 쓰고 파일 · 출력을 다루지 않는지 검사 | — | pytest | 코어에 새 표준 모듈을 쓰면 허용 목록을 확인합니다 |

`src/filler/`는 나중에 AI 서비스의 `features/filler/`로 폴더째 옮깁니다. 코어는 pydantic 말고는 외부 패키지를 쓰지 않고, 다른 판정 모듈을 import하지 않습니다.
