# evaluation-criteria

사용자가 자유 문장으로 작성한 발표 평가 기준을 LLM에 넣어, 발표 분석 시스템이 **측정할 수 있는 8개 평가 요소의 기준 값**(`values`)과 사용자에게 보여줄 **짧은 평가 기준 문장**(`display_criteria`)을 추출합니다. 측정할 수 없는 기준은 항목 이름만 `excluded`에 모읍니다.

원본: [archive/workspaces/seojin-lee/evaluation-criteria/v1/local/evaluation_criteria_analysis.ipynb](../../archive/workspaces/seojin-lee/evaluation-criteria/v1/local/evaluation_criteria_analysis.ipynb) (노트북 → 모듈로 옮김)

```
평가 기준 문장 ──▶ LLM (SYSTEM_PROMPT + with_structured_output(EvaluationCriteriaAnalysis))
                    │
                    ▼
         {display_criteria[], values{8개 필드}, excluded[]}
```

---

## 구조

| 위치 | 내용 |
|---|---|
| `evaluation_criteria/schema.py` | `Criteria`, `EvaluationCriteriaAnalysis` (Structured Output 스키마) |
| `evaluation_criteria/prompt.py` | `SYSTEM_PROMPT` — 평가 요소 매핑 · values · display_criteria · excluded 규칙 |
| `evaluation_criteria/analyzer.py` | `build_llm()`, `analyze_criteria(text)` — 파이프라인 |
| `run.py` | 직접 입력 또는 `data/examples.json` 실행 → 콘솔 출력 (`--all`은 `results/examples.json` 저장) |
| `tests/test_schema.py` | 스키마 테스트 (LLM 호출 없음) |
| `data/examples.json` | 테스트용 평가 기준 문장 5개 |
| `results/` | 실행 결과 (gitignore) |

---

## 실행

```bash
# research/evaluation-criteria/ 에서
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev]"
```

`ai/.env`에 `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL`을 채웁니다(`ai/.env.example` 복사).
경로가 코드에 고정되어 있어서 어느 디렉터리에서 실행하든 같은 파일을 읽습니다. 키나 모델이 비어 있으면 바로 에러가 납니다.

```bash
python run.py --text "추임새는 5번 이하로 해주세요."   # 직접 입력
python run.py --index 1 3                            # examples.json의 1, 3번째
python run.py --all                                  # 전체 (순차 호출)
python -m pytest                                     # 스키마 테스트
```

코드에서 직접 쓸 때:

```python
from evaluation_criteria import analyze_criteria, build_llm

llm = build_llm()                    # 여러 입력을 돌릴 때는 재사용
data = analyze_criteria(text, llm=llm)
```

---

## 출력

입력: `"추임새는 5번 이하로 해주시고, 대본 없이 발표해야 합니다. 발표 자료 디자인도 깔끔해야 합니다."`

```json
{
  "display_criteria": ["추임새 5번 이하", "대본 없이 발표하기"],
  "values": {
    "speed": null, "pause": null, "volume": null, "filler": "5회 이하", "gaze": null,
    "script_used": false, "script_dependency": null, "script_similarity": null
  },
  "excluded": ["발표 자료 디자인"]
}
```

| 필드 | 의미 |
|---|---|
| `values` | 8개 평가 요소의 기준 값. 입력에 언급이 없으면 `null`. `script_used`만 `bool`, 나머지는 짧은 명사구 `str` |
| `display_criteria` | `values`에서 null이 아닌 필드마다 한 문장. 사용자 입력 표현을 살려 20자 이내로 다듬음 |
| `excluded` | `values`에 반영하지 않은 기준의 항목 이름(명사구). 값 · 조건은 담지 않음 |

| 필드 | 평가 요소 |
|---|---|
| `speed` | 말하기 속도 |
| `pause` | 지나치게 긴 침묵 |
| `volume` | 목소리 크기 |
| `filler` | 채움말(추임새) 사용 |
| `gaze` | 시선 처리 |
| `script_used` | 대본 사용 허용 여부 |
| `script_dependency` | 발표 중 대본을 보는 정도 |
| `script_similarity` | 실제 발화와 대본 내용의 일치도 |

---

## 알아둘 것

| 항목 | 내용 |
|---|---|
| 고정 필드 스키마 | OpenAI Structured Output의 `json_schema` strict 모드는 자유 키 dict(`dict[str, ...]`)를 지원하지 않습니다. 평가 요소를 `Criteria`의 고정 필드로 둔 것은 의도된 설계입니다 |
| 모든 필드 required | 언급이 없는 요소도 필드를 생략하지 않고 `null`로 반환합니다. `tests/test_schema.py`가 이를 확인합니다 |
| 평가 요소 추가 | 발표 분석 시스템에 지표가 추가되면 `schema.py`의 `Criteria`와 `prompt.py`의 평가 요소 표를 함께 고쳐야 합니다 |
| 수치 생성 금지 | 입력에 숫자가 없는 모호한 기준("너무 빠르지 않게")은 숫자를 만들어내지 않고 입력 표현을 그대로 남깁니다 |
| 발표 시간 | 현재 8개 요소에 없어 `excluded`로 갑니다 |
| 평가 | 정답 라벨이 있는 평가셋이 없습니다. 지금까지의 정확도는 눈으로 확인한 것이 전부입니다 |
