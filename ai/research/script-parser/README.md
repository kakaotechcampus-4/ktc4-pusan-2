# script-parser

발표 대본 전체 텍스트를 LLM에 넣어 **슬라이드 단위로 분리**하고, 슬라이드별 `keywords`와 대본 전체의 `terms`(STT가 틀리기 쉬운 고유명사·전문 용어)를 추출합니다.

원본: [archive/workspaces/seojin-lee/script-parser/v1/local/script_slide_analysis.ipynb](../../archive/workspaces/seojin-lee/script-parser/v1/local/script_slide_analysis.ipynb) (노트북 → 모듈로 옮김)

```
대본 텍스트 ──▶ LLM (SYSTEM_PROMPT + with_structured_output(SeparatedSlides))
                 │
                 ▼
          {status, slides[], terms[]}
                 │
          clean_script()     slides[].script의 마크다운 문법 제거
                 │
          find_highlights()  slides[].keywords 위치 → "start:end"
```

---

## 구조

| 위치 | 내용 |
|---|---|
| `script_parser/schema.py` | `Slide`, `SeparatedSlides` (Structured Output 스키마) |
| `script_parser/prompt.py` | `SYSTEM_PROMPT` — 슬라이드 구분 · keywords · terms 규칙 |
| `script_parser/postprocess.py` | `clean_script()`, `find_highlights()` — LLM 없이 도는 순수 함수 |
| `script_parser/parser.py` | `build_llm()`, `parse_script(text)` — 파이프라인 |
| `run.py` | `data/scripts/*.txt` 실행 → `results/<이름>.json` |
| `tests/test_postprocess.py` | 후처리 테스트 (LLM 호출 없음) |
| `data/scripts/` | 테스트 대본 10개. 슬라이드 구분자가 있는 것과 없는 것이 섞여 있음 |
| `results/` | 실행 결과 (gitignore) |

---

## 실행

```bash
# research/script-parser/ 에서
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev]"
```

`ai/.env`에 `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL`을 채웁니다(`ai/.env.example` 복사).
경로가 코드에 고정되어 있어서 어느 디렉터리에서 실행하든 같은 파일을 읽습니다. 키나 모델이 비어 있으면 바로 에러가 납니다.

```bash
python run.py --file script3.txt     # 대본 하나
python run.py --all                  # 전체 (순차 호출이라 시간이 걸림)
python -m pytest                     # 후처리 테스트
```

코드에서 직접 쓸 때:

```python
from script_parser import build_llm, parse_script

llm = build_llm()                    # 여러 대본을 돌릴 때는 재사용
data = parse_script(text, llm=llm)
```

---

## 출력

```json
{
  "status": "success",
  "slides": [
    {"slide_number": 1, "script": "...", "keywords": ["..."], "highlights": ["0:4", "..."]}
  ],
  "terms": ["..."]
}
```

---

## 알아둘 것

| 항목 | 내용 |
|---|---|
| `status="fail"` | 대본 일부에만 구분자가 있어도 **전체가 fail**입니다. 이때 슬라이드는 1개(`slide_number=None`)이고, 대본 전체와 키워드 약 15개가 들어갑니다 |
| `keywords` | 요약이 아니라 대본에 있는 표현 그대로입니다. `clean_script`는 `script`에만 적용합니다 |
| `highlights` | **정제된 script 기준** 오프셋이고, 첫 등장 위치만 잡습니다. script에서 못 찾은 keyword는 경고 로그를 남기고 건너뜁니다 |
| `terms` | 슬라이드 단위가 아니라 대본 전체에서 한 번만, 최대 100개까지 추출합니다 |
| 평가 | 정답 라벨이 있는 평가셋이 없습니다. 지금까지의 정확도는 눈으로 확인한 것이 전부입니다 |
