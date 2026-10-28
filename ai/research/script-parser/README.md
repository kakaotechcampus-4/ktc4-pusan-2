# script-parser

발표 대본을 **코드가 구분자로 장마다 나누고** 정제해 장별 글자 수 · 예상 시간 · 하이라이트를 냅니다. LLM은 장별 `keywords` · 제목 줄과 대본 전체의 `terms`(STT가 틀리기 쉬운 고유명사·전문 용어)만 고릅니다.

원본: [archive/workspaces/seojin-lee/script-parser/v1/local/script_slide_analysis.ipynb](../../archive/workspaces/seojin-lee/script-parser/v1/local/script_slide_analysis.ipynb) (노트북 → 모듈로 옮김)

설계(목표 계약 · 처리 · 배포): [`docs/ai/features/script-parser.md`](../../../docs/ai/features/script-parser.md)

```
ParseRequest {script_text, target_ms}
      │
find_slides()        줄 맨 앞 구분자로 머리글 · 장 후보 (코드)
      │
LLM (SYSTEM_PROMPT + with_structured_output(LLMOutput))
      │              머리글 · 장별 제목 줄, 장별 keywords, terms만 고름
      ▼
판정                 머리글에 제목 줄이 아닌 글이 남으면 fail (코드)
clean_script()       구분자 · 제목 줄 · 마크다운 걷어 냄
count_chars()        script_chars
find_highlights()    keywords 위치 → {start, end}
예상 시간             target_ms 비례(누적 반올림) 또는 기준 말 속도
      │
ParseResponse {status, slides[], terms, meta}
```

---

## 구조

코어(`script_parser`)와 단위 테스트는 AI 서비스로 옮겼습니다: 코어 `ai/service/src/pitch_coach_ai/features/script_parser/`, 테스트 `ai/service/tests/unit/script_parser/`. 원본은 서비스 한 곳이고, 이 폴더는 서비스를 editable로 설치해(`pyproject.toml`의 `[tool.uv.sources]`) `run.py` · 실제 LLM 테스트가 `pitch_coach_ai.features.script_parser`를 import합니다. 코어는 서비스에서 고칩니다. 아래 표의 '서비스 `features/script_parser/…`'는 그 폴더의 파일입니다.

| 위치 | 내용 |
|---|---|
| 서비스 `features/script_parser/schemas.py` | `ParseRequest` · `ParseResponse` (요청 · 응답 계약), `LLMOutput` (LLM Structured Output 스키마) |
| 서비스 `features/script_parser/prompts/parse.py` | `SYSTEM_PROMPT` — 제목 줄 · keywords · terms 고르기 규칙 |
| 서비스 `features/script_parser/splitter.py` | `find_slides()` — 줄 맨 앞 구분자(`[N]` · `[슬라이드 N]` · `슬라이드 N:`)로 머리글 · 장 후보를 나눔 (LLM 없음) |
| 서비스 `features/script_parser/cleaner.py` | `clean_script()`, `count_chars()`, `find_highlights()` — LLM 없이 도는 순수 함수 |
| 서비스 `features/script_parser/core.py` | `parse_script(request, llm)` — 파이프라인, `EmptyScriptError`(서비스에서 422 `EMPTY_SCRIPT`) |
| 서비스 `features/script_parser/config.py` | 기준 말 속도 `BASE_CPM`(잠정 264), 키워드 상한(장마다 7 · fail 15), 용어 상한 50 |
| 서비스 `features/script_parser/version.py` | `SCHEMA_VERSION` · `FEATURE_VERSION` |
| 서비스 `features/script_parser/examples/` | 요청 · 응답 예시 `success` · `no_target` · `fail` (`request.json` · 가짜 LLM 출력 `llm_output.json` · `response.json`) |
| `run.py` | `build_llm()` (`ai/.env`로 LLM 생성), `data/scripts/*.txt` 실행 → `results/<이름>.json`, `--eval`로 라벨과 비교 → `reports/results/` |
| 서비스 `tests/unit/script_parser/test_cleaner.py` | 정제 · 글자 수 · 하이라이트 테스트 (LLM 호출 없음) |
| 서비스 `tests/unit/script_parser/test_splitter.py` | 구분자 · 번호 검사 테스트 (LLM 호출 없음) |
| 서비스 `tests/unit/script_parser/test_core.py` | `parse_script` 테스트 (가짜 LLM, 과금 없음) |
| 서비스 `tests/unit/script_parser/test_core_boundary.py` | 코어가 상대 import와 표준 계산 모듈 · pydantic만 쓰고 파일 · 출력을 다루지 않음 |
| 서비스 `tests/unit/script_parser/test_examples.py` | 계약 테스트: `examples/`의 요청을 돌린 응답이 `response.json`과 같은지, 예시가 계약(장 번호 · fail 1장 · 예상 시간 합 · 하이라이트 위치)을 지키는지 |
| `data/scripts/` | 평가 대본 15개. `script1`–`5`는 원래 대본(`script1`만 구분자 형식, `2`–`5`는 형식이 다른 `fail` 경우), `script6`–`15`는 평가 경우마다 새로 쓴 대본으로, 분야를 나눴다: `[N]` 수학 · `[슬라이드 N]` 화학 · 머리글 제목만 법률 · 머리글 본문 기계공학 · 일부만 구분자 생명과학 · 마크다운 컴퓨터공학 · 번호 겹침 물리 · 구분자 없음 금융공학 · 2,000자대(2,052자) 전자공학 · 4,000자대(4,198자) 토목공학 |
| `data/labels/` | 대본마다 정답 라벨: `status`, 장마다 첫 줄 · 마지막 줄, 남으면 안 되는 제목 줄, 장별 실제 시간(`actual_slide_ms`, 아직 없음) |
| `tests/live/` | 실제 LLM 테스트 (`live` 표시, 과금). `run.build_llm`을 써서 연구 폴더에 남겼습니다. 이 폴더의 기본 실행(`pytest`)에는 고를 테스트가 없습니다 |
| `reports/results/` | 평가 결과 JSON (`run.py --eval`) |
| `results/` | 실행 결과 (gitignore) |

---

## 실행

```bash
# ai/research/script-parser/ 에서 (Python 3.12, uv)
uv sync
```

`ai/.env`에 `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL`을 채웁니다(`ai/.env.example` 복사). 실제 LLM을 부르는 일에만 필요합니다.
경로가 코드에 고정되어 있어서 어느 디렉터리에서 실행하든 같은 파일을 읽습니다. 키나 모델이 비어 있으면 바로 에러가 납니다.

```bash
# 코어 단위 테스트는 서비스에서: cd ai/service && uv run python -m pytest tests/unit/script_parser  (가짜 LLM, 과금 없음)
uv run python -m pytest -m live                   # 실제 LLM 테스트 (과금)
uv run python run.py --file script3.txt           # 대본 하나 (과금)
uv run python run.py --all                        # 전체 (순차 호출이라 시간이 걸림, 과금)
uv run python run.py --all --target-ms 300000     # 발표 제한 시간에 맞춰 예상 시간 나누기
uv run python run.py --eval --reasoning-effort low   # 라벨과 비교해 평가 (과금)
```

Windows에서는 실행 파일이 막힐 수 있어 `uv run python -m …`으로 부릅니다.

코드에서 직접 쓸 때:

```python
from run import build_llm
from pitch_coach_ai.features.script_parser import parse_script
from pitch_coach_ai.features.script_parser.schemas import ParseRequest

llm = build_llm()                    # 여러 대본을 돌릴 때는 재사용
response = parse_script(ParseRequest(script_text=text, target_ms=300000), llm)
```

---

## 출력

```json
{
  "status": "success",
  "slides": [
    {"slide_number": 1, "script": "...", "script_chars": 24, "keywords": ["..."],
     "highlights": [{"start": 7, "end": 9}], "expected_ms": 160000}
  ],
  "terms": ["..."],
  "meta": {"schema_version": "1.0", "feature_version": "script_parser-1.1", "model": null}
}
```
전체 계약 · 예시는 설계 문서 5절입니다. `meta.model`은 서비스 라우터가 채웁니다.

---

## 알아둘 것

| 항목 | 내용 |
|---|---|
| 구분자 | 줄 맨 앞의 `[N]` · `[슬라이드 N]` · `슬라이드 N:`만 인정합니다. `script2`–`5`처럼 `## Slide N` · `1.` · `[Slide N]` 같은 다른 표시는 구분자가 아니라 `fail`입니다 |
| `status="fail"` | 구분자가 없거나, 번호가 0 · 겹침 · 거꾸로이거나, 첫 구분자 앞에 제목 줄이 아닌 글이 있으면 fail입니다. 장은 1개(`slide_number=1`)이고 키워드는 대본 전체에서 15개까지입니다 |
| `keywords` · 제목 줄 · `terms` | LLM이 고른 것 중 대본에 그대로 있는 것만 씁니다(중복 · 빈 값 버림) |
| `highlights` | **정제된 script 기준** 코드 포인트 위치 `{start, end}`(end 미포함)이고, 첫 등장 위치만 잡습니다 |
| `terms` | 장 단위가 아니라 대본 전체에서 한 번만, 최대 50개까지 추출합니다 |
| 평가 | 장별 실제 발표 시간이 없어 예상 시간 오차는 아직 잴 수 없습니다 |

---

## 평가 결과

2026-10-24, `openai/gpt-6-luna` · 추론 `low`, `uv run python run.py --eval --reasoning-effort low` (대본 15개, 대본마다 LLM 1번, 목표 시간 없음, 출력 상한은 정하지 않음 = 게이트웨이 기본 2,000토큰) → [`reports/results/script_parser_eval.json`](reports/results/script_parser_eval.json). 결과 JSON에는 대본마다 거른 뒤의 키워드(`result_keywords`)와 용어(`result_terms`)도 있다.

| 지표 | 지금 | 프롬프트 · 코어 고치기 전 (같은 15개) |
|---|---|---|
| 실패 | 0개 | 0개 |
| 장 나눔 정확도 | **1.00** (15/15) | 0.93 (`script5`에 `[Slide N]` 줄이 남음) |
| 키워드 · 용어가 대본에 그대로 있는 비율 (거르기 전) | 0.998 · 1.00 | 0.996 · 1.00 |
| 장 대본에 남은 제목 줄 | **0줄** | 3줄 |
| LLM 호출 시간 | 1,000자 미만 13개 중앙값 6.2초 · 최대 7.9초, 2,052자 7.5초, 4,198자 9.9초. 12초 넘은 것 없음 | 중앙값 6.3초 · 최대 10.1초 |
| 최대 출력 토큰 | 1,283 | 1,261 |

고친 것
- 프롬프트: 장 번호 · 순서만 표시한 줄(`[Slide 1]` · `슬라이드 4` · `1. 논의의 개요` · 숫자만 있는 줄)도 제목 줄로 고르게 했다. 키워드는 명사 · 명사구만, 조사 · 어미는 떼고, 서술어가 든 어구와 인사말 · 맺음말은 빼게 했다.
- 코어: 머리글 때문에 `fail`로 바뀔 때 장별 키워드를 장마다 번갈아 합친 뒤 15개로 자른다(전에는 앞 장부터 이어 붙여 앞쪽 장에 몰렸다).

눈으로 본 키워드 · 용어 품질 (사람 확인 필요)
- 서술형 어구(`응답이 느려지고` · `편하게 해 주세요` · `감사합니다` 등)가 사라지고 명사 · 명사구만 남았다. `script9_preface_body`의 키워드가 1–5장에 고르게 퍼졌다.
- 용어는 STT가 틀리기 쉬운 전문 용어 · 약어다(예: `에일리어싱` · `미카엘리스-멘텐 식` · `CNOT 게이트` · `HBM` · `동조 질량 감쇠기`).
- 남은 것: `물질` · `속도` · `법` · `개인` · `K` · `탁자` · `머리`처럼 너무 일반적이거나 한 글자인 키워드가 가끔 있다. `script10_partial`처럼 첫 구분자 앞(머리글)에 본문이 있는 대본은 그 부분에서 키워드를 고르지 않는다(LLM 출력 스키마가 장 후보마다만 키워드를 받는다. 설계 문서 6-1을 바꿔야 고칠 수 있다).
- 예상 시간 오차는 장별 실제 발표 시간 라벨이 없어 재지 않았다.

### 이전 측정 (옛 평가 대본 20개, 추론 강도 비교)

옛 대본 이름이고, 옛 `script6`–`20`은 지금 대본과 내용이 다르다.

2026-10-24, `uv run python run.py --eval [--reasoning-effort low]` (옛 대본 20개, 대본마다 LLM 1번, 목표 시간 없음, 출력 상한은 정하지 않음 = 게이트웨이 기본 2,000토큰). 결과 JSON은 마지막에 돌린 `gpt-6-luna` · `low` 것이다 → [`reports/results/script_parser_eval.json`](reports/results/script_parser_eval.json)

| 지표 | `gpt-6-luna` · 추론 `low` | `gpt-6-luna` · 추론 기본값 | `gpt-5.6-luna` · 추론 기본값 (참고) |
|---|---|---|---|
| 실패 (`LengthFinishReasonError`) | **0개** | 3개: `script2`(922자) · `script19`(2,455자) · `script20`(4,440자) | 1개: `script19` |
| 장 나눔 정확도 | **0.85** (17/20) | 0.70 (14/20) | 0.80 (16/20) |
| 키워드 · 용어가 대본에 그대로 있는 비율 (거르기 전) | 1.00 · 1.00 | 1.00 · 1.00 | 1.00 · 1.00 |
| 장 대본에 남은 제목 줄 | 15줄 | 13줄 | 15줄 |
| LLM 호출 시간 (1,000자 미만) | 중앙값 **6.5초**, 최대 7.8초 | 중앙값 13.2초, 최대 20.6초 | 중앙값 10.2초, 최대 16.0초 |
| LLM 호출 시간 (긴 대본) | 2,455자 12.2초 · 4,440자 12.5초 | 실패 (1,125자는 17.5초) | 1,125자 10.1초 · 4,440자 10.6초 |
| 12초 넘음 | 2/20 (긴 대본 2개) | 11/17 | 5/19 |
| 출력 토큰 · 그중 추론 | 최대 1,239 · 추론 168~472 | 최대 1,946 · 추론 425~1,592 | 최대 1,680 · 추론 276~1,314 |

- 기본 추론 강도에서의 실패는 모두 출력 2,000토큰을 추론이 다 써서 답이 비어 나온 것이다(`reasoning_tokens=2000`). 추론 토큰은 대본 길이와 관계없이 호출마다 달라서(같은 본문인 `script1` · `11` · `12` · `13`이 448~1,592) 짧은 대본도 실패했다.
- 답 자체(출력 − 추론)는 6장 대본에서 300토큰 안팎, 16장에서 약 1,000토큰이다. `low`에서는 추론이 최대 472토큰이라 2,000 안에 여유가 있다.
- 장 나눔이 틀린 것은 세 설정 모두 `script5` · `6` · `9`로, 장 나눔은 맞고 `[Slide N]` · `슬라이드 N` · `1` 같은 장 번호 줄이 장 대본에 남은 것이다.
- 2,000자 넘는 대본은 `low`에서도 12초를 조금 넘는다. 서비스 시간 제한(`SCRIPT_PARSER_LLM_TIMEOUT_SECONDS`, 문서 11절)을 정할 근거다.
- 키워드 · 용어의 품질(중요한 단어를 골랐는가)은 대본에 있는지만 보고 따로 재지 않았다.
- 예상 시간 오차는 장별 실제 발표 시간 라벨이 없어 재지 않았다.
