# v1 / local — 개발·실험 리그

script-parser **v1 모델을 노트북에서 돌리고 확인하는 리그**입니다.
발표 대본 전체 텍스트를 LLM에 넣어 슬라이드 단위로 분리하고 키워드를 추출합니다.

v1의 범위와 출력 계약은 [../README.md](../README.md)를 보세요.

> 이 문서는 **작업용 README**입니다 — 개발할 때 필요한 것만 담았습니다.
> 프롬프트 설계 근거, 출력 계약, 알려진 한계는 **[../README.md](../README.md)** 에 있습니다.

> ⚠️ **모든 명령은 이 디렉터리(`v1/local/`)에서 실행하세요.**
> 노트북이 `대본/` 폴더를 `os.getcwd()` 기준 상대 경로로 읽습니다.

---

## 한눈에 보기

```
.env (OPENAI_*) ──▶ ChatOpenAI ──▶ with_structured_output(SeperatedSlides)
                                          │
대본/*.txt ──▶ scripts[] ──▶ [system prompt + user script] ──▶ invoke()
                                          │
                                          ▼
                                   {status, slides[]}
                                          │
                                   clean_script() 후처리
```

| 항목 | 값 |
|---|---|
| 소스 | `script_slide_analysis.ipynb` (노트북 하나) |
| 테스트 데이터 | `대본/script1.txt` ~ `script15.txt` |
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
$PY -m jupyter notebook script_slide_analysis.ipynb

# 또는 CLI에서 전체 셀 실행
$PY -m jupyter nbconvert --to notebook --execute script_slide_analysis.ipynb
```

노트북 셀 순서 그대로 따라가면 됩니다:

1. **환경 설정** — `.env` 로드, `ChatOpenAI` 생성
2. **Structured Output 정의** — `Slide`, `SeperatedSlides` Pydantic 모델
3. **System Prompt 작성** — 슬라이드 구분·키워드 규칙
4. **테스트용 대본 파일 import** — `대본/*.txt` 전부 읽어 `scripts[]`에 적재
5. **전처리 함수 정의** — `clean_script()` (마크다운 문법 제거)
6. **단일 대본 test** → **전체 대본 test** — `scripts` 전체에 대해 순회 호출

---

## 모듈 지도

**"무엇이 어디 있나"** 기준입니다. 별도 소스 패키지 없이 노트북 하나가 전부입니다.

| 위치 | 무엇을 소유하는가 |
|---|---|
| `script_slide_analysis.ipynb` | 스키마 정의, 시스템 프롬프트, 호출 로직, 후처리, 테스트 루프 전부 |
| `대본/*.txt` | 테스트용 발표 대본 15개. 슬라이드 구분자가 있는 것/없는 것이 섞여 있음 |
| `../README.md` | v1 전체 기술 레퍼런스 (출력 계약, 분류 규칙 상세) |

---

## 반드시 알아야 할 함정

| 함정 | 내용 |
|---|---|
| `status="fail"` 처리 | 대본 일부만 구분자가 있어도 **전체가 fail**입니다. 슬라이드 배열에 임의 번호를 채우지 않습니다 — 다운스트림에서 `success`만 신뢰하고 소비하세요 |
| `keywords` 원문 그대로 | 요약·의역이 아니라 대본에 있는 단어·어절 그대로입니다. 후처리에서 유의어로 바꾸지 마세요 |
| `clean_script`는 `script` 필드에만 | LLM이 마크다운 문법을 섞어 낼 수 있어 후처리가 필요합니다. `keywords`에는 적용하지 않습니다 |
| `.env` 탐색 경로 | 노트북을 다른 디렉터리(예: 리포 루트)에서 열면 `load_dotenv()`가 `.env`를 못 찾아 `OPENAI_API_KEY=None`으로 조용히 넘어갑니다 |
| 대량 출력 셀 | "전체 대본 test" 셀은 15개 대본을 순차 호출하므로 시간이 걸리고, 출력이 매우 깁니다 |

---

## 현재 상태

| | |
|---|---|
| ✅ | 15개 테스트 대본 전체에 대해 파이프라인 동작 확인 |
| ⚠️ | **정답 라벨이 있는 평가셋 없음** — 정확도는 사람이 눈으로 확인한 것이 전부 |
| ⚠️ | CI 없음 · 자동화된 테스트 없음 |
| ⬜ | 전송 계층 없음 — 노트북 인프로세스 호출뿐 |

가장 큰 공백은 **정량 평가셋**입니다. 그전까지 모든 분리 정확도 수치는 미측정입니다.

자세한 기술 문서 · 출력 계약 · 알려진 한계 → **[../README.md](../README.md)**
