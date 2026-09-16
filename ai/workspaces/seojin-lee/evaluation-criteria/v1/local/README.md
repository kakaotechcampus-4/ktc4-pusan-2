# v1 / local — 개발·실험 리그

evaluation-criteria **v1 모델을 노트북에서 돌리고 확인하는 리그**입니다.
사용자가 자연어로 작성한 발표 평가 기준을 LLM으로 분류합니다
(판단 가능 / 정보 부족 / 판단 불가).

v1의 범위와 출력 계약은 [../README.md](../README.md)를 보세요.

> 이 문서는 **작업용 README**입니다 — 개발할 때 필요한 것만 담았습니다.
> 분류 규칙 정의, 출력 계약, 알려진 한계는 **[../README.md](../README.md)** 에 있습니다.

> ⚠️ **모든 명령은 이 디렉터리(`v1/local/`)에서 실행하세요.**
> `.env`를 이 디렉터리에 두면 `load_dotenv()`가 별도 경로 지정 없이 찾습니다.

---

## 한눈에 보기

```
.env (OPENAI_*) ──▶ ChatOpenAI ──▶ with_structured_output(EvaluationCriteriaAnalysis)
                                          │
자연어 평가 기준 문장 ──▶ [system prompt + user input] ──▶ invoke()
                                          │
                                          ▼
                     {evaluable[], need_more_info[], not_evaluable[]}
```

| 항목 | 값 |
|---|---|
| 소스 | `evaluation_criteria_analysis.ipynb` (노트북 하나) |
| 테스트 데이터 | 노트북 셀에 직접 적힌 예시 평가 기준 문장 5~6개 |
| 필요한 패키지 | `python-dotenv`, `pydantic`, `langchain-openai` |
| 모델 | `.env`의 `OPENAI_MODEL` (검증 시점: `openai/gpt-5.6-luna`) |

---

## 셋업

```bash
# 0) 가상환경 (v1/local 에서)
python -m venv .venv
./.venv/Scripts/python.exe -m pip install python-dotenv pydantic langchain-openai jupyter
```

```bash
# 1) 환경 변수 — 루트 ai/.env.example을 복사해 이 디렉터리(v1/local/)에 .env로 둡니다
cp ../../../../.env.example .env
# .env 안 OPENAI_API_KEY 채우기
```

| 변수 | 효과 |
|---|---|
| `OPENAI_BASE_URL` | 호출할 LLM 엔드포인트 |
| `OPENAI_API_KEY` | 해당 엔드포인트 인증 키 |
| `OPENAI_MODEL` | 사용할 모델 이름 |

> `load_dotenv()`는 **현재 작업 디렉터리**부터 상위로 `.env`를 탐색합니다.
> 노트북을 `v1/local/`에서 열고 커널을 그 디렉터리에서 띄우면 별도 경로 지정 없이 잡힙니다.

---

## 자주 쓰는 명령

```bash
PY=./.venv/Scripts/python.exe

# 노트북 실행 (Jupyter)
$PY -m jupyter notebook evaluation_criteria_analysis.ipynb

# 또는 CLI에서 전체 셀 실행
$PY -m jupyter nbconvert --to notebook --execute evaluation_criteria_analysis.ipynb
```

노트북 셀 순서 그대로 따라가면 됩니다:

1. **환경 설정** — `.env` 로드, `ChatOpenAI` 생성
2. **Structured Output 정의** — `EvaluableItem`, `NeedMoreInfoItem`, `EvaluationCriteriaAnalysis` Pydantic 모델
3. **System Prompt 작성** — 판단 가능 요소 목록 + 3분류 규칙 + 예시
4. **LLM 모델 불러오기** — 간단한 단일 입력으로 호출 확인
5. **단일 평가 기준 test** → **여러 평가 기준 test** — 예시 문장 리스트를 순회 호출해 출력 비교

---

## 모듈 지도

**"무엇이 어디 있나"** 기준입니다. 별도 소스 패키지 없이 노트북 하나가 전부입니다.

| 위치 | 무엇을 소유하는가 |
|---|---|
| `evaluation_criteria_analysis.ipynb` | 스키마 정의, 시스템 프롬프트, 호출 로직, 테스트 루프 전부 |
| `../README.md` | v1 전체 기술 레퍼런스 (출력 계약, 분류 규칙 상세) |

---

## 반드시 알아야 할 함정

| 함정 | 내용 |
|---|---|
| 자유 키 dict를 쓰지 않는 이유 | OpenAI structured output의 `json_schema` strict 모드는 키가 고정되지 않은 필드를 지원하지 않습니다. `evaluable`/`need_more_info`를 `dict`가 아니라 `{key, value}` 리스트로 정의한 것은 의도된 설계입니다 — dict로 되돌리면 strict 모드가 깨집니다 |
| `not_evaluable`에는 값을 담지 않음 | 판단하지 않는 요소는 이름만 리스트에 넣습니다. 값이나 세부 정보를 억지로 채우지 마세요 |
| `need_more_info`에 수치를 만들어내지 말 것 | 입력에 없는 구체적 기준(예: "5분 이내")을 임의로 생성하면 안 됩니다 — 현재까지 확인된 값만 그대로 담습니다 |
| 세 필드는 항상 반환 | 해당 사항이 없어도 `[]`로 채우고 필드 자체를 생략하지 않습니다. 프롬프트에서 이 지시를 지우면 스키마 검증이 실패할 수 있습니다 |
| `.env` 탐색 경로 | 노트북을 다른 디렉터리(예: 리포 루트)에서 열면 `load_dotenv()`가 `.env`를 못 찾아 `OPENAI_API_KEY=None`으로 조용히 넘어갑니다 |

---

## 현재 상태

| | |
|---|---|
| ✅ | 예시 평가 기준 5~6건에 대해 파이프라인 동작 확인 |
| ⚠️ | **정답 라벨이 있는 평가셋 없음** — 분류 정확도는 사람이 눈으로 확인한 것이 전부 |
| ⚠️ | CI 없음 · 자동화된 테스트 없음 |
| ⬜ | 전송 계층 없음 — 노트북 인프로세스 호출뿐 |

가장 큰 공백은 **정량 평가셋**입니다. 그전까지 모든 분류 정확도 수치는 미측정입니다.

자세한 기술 문서 · 출력 계약 · 알려진 한계 → **[../README.md](../README.md)**
