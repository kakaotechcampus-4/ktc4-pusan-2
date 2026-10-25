# API 예시

대본 전달도 API의 요청 · 응답 예시다. 실제 LLM 출력으로 만들었고, 코어(`script_coverage.core`)의 계약 테스트가 이 파일을 읽는다.

## 폴더

| 폴더 | API | 요청 | 응답에 보이는 것 |
|---|---|---|---|
| `rubric/` | `POST /scripts/rubric` | 가상대본1의 모든 9장 + `slide_numbers` [6, 7] | 6번 장 평가 기준 1개 (성공) + 7번 장 `failed` 1개 (`LLM_TIMEOUT`) |

폴더마다 파일이 셋이다.

- `request.json`: 진입 함수에 넣는 요청
- `response.json`: 그 요청에 나오는 응답 (`model_dump(mode="json")`)
- `llm_cache.json`: 응답을 만드는 데 읽은 LLM 출력 `[{kind, key, config_hash, output}]` (정렬). `kind` · `key` · `config_hash`는 코어의 `LLMCache.get`이 받는 값이고 `output`은 LLM이 돌려준 JSON이다.

## 어떻게 만들었나

1. 연구 캐시(`outputs/rubrics.sqlite`)에 있는 가상대본1의 실제 LLM 출력(모델 `openai/gpt-5.6-luna`)을 쓴다. LLM은 한 번도 부르지 않는다 (과금 없음).
2. 캐시에 없는 호출은 시간 초과(`TimeoutError`)가 되고 그 장이 `failed`에 든다. 7번 장은 전달 단위 호출의 캐시 항목을 일부러 빼서 `LLM_TIMEOUT`이 되게 했다 (성공 1 · 실패 1을 한 응답에서 보이기 위해).
3. 읽은 캐시 항목을 `llm_cache.json`에 남긴다. 이 파일만 있으면 LLM 없이 같은 응답이 나온다.

## 다시 만들기

`ai/research/script-coverage-evaluation`에서:

```
python -m coverage_lab.examples
```

연구 캐시가 있어야 한다. 다시 만들어도 내용이 같아야 하고(`tests/lab/test_examples_build.py`가 확인), 응답이 바뀌면 계약이 바뀐 것이므로 변경 내용을 보고 의도한 것인지 확인한다.

## 계약 테스트

`tests/unit/test_examples.py`가 `llm_cache.json`으로 캐시를 만들고, 부르면 시간 초과가 되는 LLM으로 `request.json`을 코어에 넣어 `response.json`과 같은 응답이 나오는지 본다 (실수는 약간의 오차를 허용). 이 테스트는 `coverage_lab`을 쓰지 않으므로 코어를 서비스로 옮길 때 예시 폴더와 함께 가져간다.
