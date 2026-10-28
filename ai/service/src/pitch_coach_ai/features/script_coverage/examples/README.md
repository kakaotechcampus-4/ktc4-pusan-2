# API 예시

대본 전달도 API의 요청 · 응답 예시다. 실제 LLM 출력으로 만들었고, 코어(`script_coverage.core`)의 계약 테스트가 이 파일을 읽는다.

## 폴더

| 폴더 | API | 요청 | 응답에 보이는 것 |
|---|---|---|---|
| `rubric/` | `POST /scripts/rubric` | 가상대본1의 모든 9장 + `slide_numbers` [6, 7] | 6번 장 평가 기준 1개 (성공) + 7번 장 `failed` 1개 (`LLM_TIMEOUT`) |
| `analyze/` | `POST /takes/analyze` | 가상대본1_take9의 모든 9장 STT + 2 · 4번 장 평가 기준 + `slide_numbers` [2, 4, 6] | 2번 장 채점 1개 (비슷한 말 R1 · R2 → `confirmations`) + `failed` 2개 (4번 `LLM_TIMEOUT`, 6번 `RUBRIC_MISSING`), `scored_ratio` 0.333 |
| `rescore/` | `POST /takes/rescore` | analyze 응답의 채점 · `failed` + 2번 장 평가 기준 + 답 하나 (2번 장 R1 `as_script`) | R1이 `applied_answers`에 들고 `confirmations`에는 R2만 남음, `model`은 null |

`rescore/`는 LLM이 없어 `llm_cache.json`이 없고(파일이 둘), 나머지는 파일이 셋이다.

- `request.json`: 진입 함수에 넣는 요청
- `response.json`: 그 요청에 나오는 응답 (`model_dump(mode="json")`)
- `llm_cache.json`: 응답을 만드는 데 읽은 LLM 출력 `[{kind, key, config_hash, output}]` (정렬). `kind` · `key` · `config_hash`는 코어의 `LLMCache.get`이 받는 값이고 `output`은 LLM이 돌려준 JSON이다.

## 어떻게 만들었나

1. 연구 캐시(`outputs/rubrics.sqlite`)에 있는 가상대본1의 실제 LLM 출력(모델 `openai/gpt-5.6-luna`)을 쓴다. LLM은 한 번도 부르지 않는다 (과금 없음).
2. 캐시에 없는 호출은 시간 초과(`TimeoutError`)가 되고 그 장이 `failed`에 든다. `rubric`의 7번 장은 전달 단위 호출의, `analyze`의 4번 장은 의미 평가 호출의 캐시 항목을 일부러 빼서 `LLM_TIMEOUT`이 되게 했다 (성공과 실패를 한 응답에서 보이기 위해).
3. `analyze`의 평가 기준은 `rubric`과 같은 방식으로 코어에서 새로 만든다 (평가 기준 스키마 버전이 올라 `rubric_id`가 바뀌어서, 연구 DB에 저장된 예전 채점은 쓰지 않는다). 어느 장에 비슷한 말이 있는지는 연구 DB의 저장된 채점을 보고 사람이 골랐다 (`coverage_lab/examples.py`의 상수). 6번 장은 평가 기준을 싣지 않아 `RUBRIC_MISSING`이 된다.
4. `rescore`는 analyze 응답의 채점 · `failed`를 그대로 요청에 넣고 답 하나를 더해 코어를 돌린 것이다.
5. 읽은 캐시 항목을 `llm_cache.json`에 남긴다. 이 파일만 있으면 LLM 없이 같은 응답이 나온다.

## 다시 만들기

`ai/research/script-coverage-evaluation`에서:

```
uv run python -m coverage_lab.examples
```

연구 캐시가 있어야 한다. 다시 만들어도 내용이 같아야 하고(`tests/lab/test_examples_build.py`가 확인), 응답이 바뀌면 계약이 바뀐 것이므로 변경 내용을 보고 의도한 것인지 확인한다. `analyze` · `rescore` 응답의 `meta.criteria_versions`에는 기준값 해시가 들어 있어, `config.py` 기준값을 바꾸면 이 예시도 다시 만들어야 한다.

## 계약 테스트

서비스 `tests/unit/script_coverage/test_examples.py`가 `llm_cache.json`으로 캐시를 만들고(`rescore`는 LLM 없이), 부르면 시간 초과가 되는 LLM으로 `request.json`을 코어에 넣어 `response.json`과 같은 응답이 나오는지 본다 (실수는 약간의 오차를 허용). 이 테스트는 `coverage_lab`을 쓰지 않아 연구 폴더 없이 서비스만으로 돈다.
