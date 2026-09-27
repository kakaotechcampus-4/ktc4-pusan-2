# v1 / local — 개발·실험 리그

script-coverage-evaluation **v1 모델을 로컬에서 돌리고 평가하는 리그**입니다.
Jupyter 노트북 2개가 코드 전부입니다 — 대본을 **평가 기준**으로 만들고, 발표 연습 STT 를 그 기준으로 **채점**합니다.

v1의 범위와 출력 계약은 [../README.md](../README.md)를 보세요.

> 이 문서는 **작업용 README**입니다 — 개발할 때 필요한 것만 담았습니다.
> 단계별 동작, 설계 근거, 측정 결과, 한계는 **[../README.md](../README.md)** 에 있습니다.

> ⚠️ **노트북은 이 디렉터리(`v1/local/`)에서 실행하세요.**
> 경로(`data/`, `outputs/`)가 작업 디렉터리 기준이고, STT 노트북은 `%run ./script_analysis_pipeline.ipynb` 로 대본 분석 노트북을 불러옵니다.

> ⚠️ **노트북을 실행하면 실제 LLM API 가 호출되고 과금됩니다.** 캐시가 있으면 호출하지 않지만,
> 캐시가 없거나 프롬프트·모델을 바꾸면 전부 다시 부릅니다 ([캐시와 비용](#캐시와-비용)).

---

## 한눈에 보기

```
 data/scripts/*.json ──▶ script_analysis_pipeline.ipynb ──▶ outputs/rubrics.sqlite
   (대본)                  ① 대본 → 평가 기준                  evaluation_rubrics
                           슬라이드당 LLM 2회                          │
                                                                       ▼
 data/stt/*.json ──────▶ stt_evaluation_pipeline.ipynb ──▶ outputs/rubrics.sqlite
   (슬라이드별 STT)        ② STT → 채점 (①을 %run)            slide_evaluations
 data/stt_labels/*.json    슬라이드당 LLM 1회 + 필요할 때 1회    similar_items
   (정답 — 11장만 읽음)
```

| 항목 | 값 |
|---|---|
| 검증 환경 | CPython 3.11.9 · Windows 11 · kiwipiepy 0.23.2 · pydantic 2 · langchain-openai |
| LLM | OpenAI 호환 API, 검증한 모델 `gpt-5.6-luna` |
| 데이터 | 가상 대본 2개(슬라이드 20장), 가상 STT 18개 + 정답 라벨 |
| 실행 시간 (캐시 있음) | 대본 분석 약 25초, STT 평가 약 40초 — API 호출 0회 |
| 처음부터 (캐시 없음) | 대본 분석 약 40회 + 일관성 측정 약 80회, STT 평가 약 220회 + 반복 채점 약 440회 |

---

## 셋업

```bash
# 0) 가상환경 (v1/local 에서)
python -m venv .venv

# 1) 의존성 (jupyter 포함)
./.venv/Scripts/python.exe -m pip install -r requirements.txt

# 2) .env — OpenAI 호환 API 설정 (아래 참고)

# 3) 확인 — 캐시가 없으면 이 한 번이 대본 분석 LLM 호출을 합니다
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m nbconvert --to notebook --execute --inplace \
    --ExecutePreprocessor.timeout=3600 script_analysis_pipeline.ipynb
```

> macOS/Linux에서는 `.venv/bin/python`입니다. **`.venv`는 저장소에 포함되지 않습니다** — 각자 만드세요.

**`.env`** — 노트북은 **현재 디렉터리부터 위로 올라가며 처음 만나는 `.env`** 를 읽습니다 (`v1/local/.env` → … → `ai/.env`).
저장소 상위에 공용 `.env` 가 있으면 그 키로 바로 과금되니 주의하세요.

| 변수 | 필수 | 뜻 |
|---|---|---|
| `OPENAI_API_KEY` | ✅ | API 키 |
| `OPENAI_MODEL` | ✅ | 모델 이름. 엔드포인트가 허용하는 이름이어야 합니다 (아니면 400) |
| `OPENAI_BASE_URL` | | OpenAI 호환 엔드포인트. 없으면 OpenAI 기본 주소 |

`.env` 는 절대 커밋하지 마세요.

---

## 자주 쓰는 명령

```bash
PY=./.venv/Scripts/python.exe

# ── 대화형 ───────────────────────────────────────────────────────────
$PY -m jupyterlab                                   # 브라우저에서 노트북 열기

# ── 명령줄에서 끝까지 실행 (결과를 노트북에 저장) ─────────────────────
PYTHONIOENCODING=utf-8 $PY -m nbconvert --to notebook --execute --inplace \
    --ExecutePreprocessor.timeout=3600 script_analysis_pipeline.ipynb
PYTHONIOENCODING=utf-8 $PY -m nbconvert --to notebook --execute --inplace \
    --ExecutePreprocessor.timeout=3600 stt_evaluation_pipeline.ipynb    # ①도 %run 으로 함께 실행됨

# ── 검증용 추가 호출 끄기 ─────────────────────────────────────────────
STT_CONSISTENCY_SAMPLES=1 PYTHONIOENCODING=utf-8 $PY -m nbconvert ... stt_evaluation_pipeline.ipynb      # 12장 반복 채점 끔
RUBRIC_CONSISTENCY_SAMPLES=1 PYTHONIOENCODING=utf-8 $PY -m nbconvert ... script_analysis_pipeline.ipynb  # 12장 일관성 측정 끔
```

PowerShell 에서는 환경 변수를 `$env:STT_CONSISTENCY_SAMPLES = "1"` 처럼 먼저 설정하세요.

> Windows venv 에는 `jupyter` 실행 파일이 따로 생기지 않을 수 있습니다. 위처럼 `python -m nbconvert` · `python -m jupyterlab` 를 쓰면 OS 와 상관없이 동작합니다.

---

## 노트북 지도

**"어디를 고쳐야 하나"** 기준으로 정리했습니다. 괄호는 노트북의 장 번호입니다.

### `script_analysis_pipeline.ipynb` — ① 대본 → 평가 기준

| 장 | 무엇 | 주요 이름 |
|---|---|---|
| 0 | 환경, `.env`, Kiwi | `ENV_PATH`, `MODEL`, `DB_PATH` |
| 1 | 대본 JSON 읽기 | `load_script_json`, `SCRIPT_FILES` |
| 2 | 대본 정규화 | `normalize_script`, `NormalizedScript` |
| 3-1 | 한국어 수 파서 | `parse_korean_number`, `format_number` |
| 3-2 | 핵심 사실 추출 | `extract_critical_facts`, `NUMERIC_PATTERNS`, `Morphemes` |
| 3-3 | TF-IDF 키워드 | `extract_keywords_tfidf` |
| 4 | LLM 1차 분석 | `SEMANTIC_SYSTEM_PROMPT`, `analyze_semantics` |
| 5 | 1차 정리·검증 | `draft_rubric`, `ROLE_IMPORTANCE`, **`SCORE_WEIGHT`**, `key_term_rejection`, `link_facts_to_key_points`, `validate_rubric` |
| 6 | LLM 최종 결론 | `FINAL_SYSTEM_PROMPT`, `review_final`, `finalize_rubric` |
| 7 | DB | `connect_db`, `save_rubric`, `load_rubric` |
| 8~9 | 파이프라인, 실행 | `run_script_analysis` |
| 10 | 대본 수정 시 재분석 | (예: 가상대본2 슬라이드 5 수정) |
| 11 | 점검 | 규칙 `assert`, `PARSER_CASES` (수 파서 회귀 사례) |
| 12 | LLM 일관성 측정 | `N_SAMPLES` ← `RUBRIC_CONSISTENCY_SAMPLES` |

### `stt_evaluation_pipeline.ipynb` — ② STT → 채점

| 장 | 무엇 | 주요 이름 |
|---|---|---|
| 0~1 | ① 불러오기, STT 읽기 | `load_take`, `takes` |
| 2 | STT 정규화 | `normalize_stt`, **`FILLER`**, `REPEATED_WORD` |
| 3 | 정렬 · 대본 충실도 | `positioned_tokens`, `align_sentences`, `script_fidelity` |
| 3-2 | 문장 분리 점검 | `segmentation_report`, **`LONG_SENTENCE`**, `FILLER_CANDIDATES` |
| 4 | 핵심 사실 검증 | `check_critical_facts` |
| 4-2 | 다른 수치의 원인 신호 | `mismatch_signals`, `reading_of` |
| 4-3 | 비슷한 말 찾기 | `find_similar_items`, `phonetic_distance`, **`WORD_DISTANCE`**, `settle_facts` |
| 5 | LLM 의미 평가 (문장별) | `SEMANTIC_EVAL_PROMPT`, `semantic_eval_message`, `judged_sentences` |
| 6 | 병합 · 충돌 → Key Point | `merge_sentences`, **`LEXICAL_PRESENT` / `LEXICAL_ABSENT`**, `aggregate_status`, `aggregate_key_points` |
| 7 | LLM 교차 검증 | `VERIFIER_PROMPT`, `verifier_message` |
| 8 | 점수 | `slide_scores`, `take_scores`, `STATUS_SCORE`, `FACT_SCORE` |
| 9 | DB · 파이프라인 | `evaluate_take`, `save_evaluation`, **`load_similar_items`** |
| 10 | 실행, 비슷한 말 목록, 슬라이드 상세 | `take_summary`, `similar_report`, `show_evaluation` |
| 11 | 정답 라벨과 비교 | `build_tables`, `kp_accuracy`, `fact_accuracy`, `similar_tables` |
| 12 | 반복 채점 일관성 | `N_EVAL_SAMPLES` ← `STT_CONSISTENCY_SAMPLES` |

코드는 노트북 셀에 있습니다. 한 셀을 고치면 **그 뒤 셀들을 다시 실행**하세요 (STT 노트북은 ①을 `%run` 하므로 ①을 고쳤으면 STT 노트북도 처음부터).

---

## 데이터 추가하기

### 대본 — `data/scripts/<대본 이름>.json`

```json
[
  {"slide_number": 1, "script": "안녕하십니까. 도서관 좌석 혼잡도를 예측해 …"},
  {"slide_number": 2, "script": "…"}
]
```

파일 이름(확장자 빼고)이 대본 이름입니다. 무대 지시문(괄호 안 동작 설명 등)은 넣지 않습니다.
**실제 발표자의 대본은 저장소에 넣지 마세요** — 공개 저장소입니다.

### 발표 연습 STT — `data/stt/<take_id>.json`

```json
{
  "script_name": "가상대본1",
  "take_id": "가상대본1_take6",
  "scenario": "인식오류",
  "slides": [{"slide_number": 1, "stt": "안녕하십니까 음 도서관 좌석 혼잡도를 …"}]
}
```

`script_name` 은 평가 기준을 만든 대본 이름이어야 합니다. `stt` 는 그 슬라이드를 띄워 둔 동안의 음성 인식 결과(원문 그대로)입니다.

### 정답 라벨 — `data/stt_labels/<take_id>.json` (성능 측정용, 선택)

```json
{
  "take_id": "가상대본1_take6",
  "slides": [{
    "slide_number": 1,
    "sentences": [{
      "index": 3, "status": "verbatim",
      "dropped_values": [], "changed_values": [],
      "asr_errors": [{"script": "312명", "stt": "412명"}], "approximated_values": [],
      "note": "인식 오류: 발표자는 맞게 말함"
    }],
    "additions": []
  }]
}
```

대본 **문장마다**(인덱스는 대본 분석의 문장 번호) 발표자가 **실제로 말한 것** 기준으로 적습니다. 필드 뜻은 [../README.md](../README.md) §10.
값 목록의 `script` 에는 수치를 **단위까지** 적으세요 (`15` 가 아니라 `15%`) — 라벨의 값도 수 파서로 읽어 비교합니다.

> **정답 라벨을 채점 결과에 맞춰 고치지 마세요.** 11장 정확도가 의미 없어집니다.

---

## 조정할 수 있는 값

| 이름 | 위치 | 기본값 | 효과 |
|---|---|---|---|
| `FILLER` | STT 2장 | `음 어 으 엄 흠 아 에` | 지우는 간투사 |
| `LONG_SENTENCE`, `FILLER_CANDIDATES` | STT 3-2장 | 120자, `그 저 뭐 …` | 문장 분리 점검 기준 (채점에 영향 없음) |
| `WORD_DISTANCE` | STT 4-3장 | 0.34 | 이 발음 거리 이하면 비슷한 단어 |
| `LEXICAL_PRESENT` / `LEXICAL_ABSENT` | STT 6장 | 0.6 / 0.2 | 단어 비율로 LLM 판정과 충돌을 잡는 기준 |
| `STATUS_SCORE`, `FACT_SCORE` | STT 8장 | 1 / 0.5 / 0 / −0.5, 1 / 0.5 | 판정별 점수 |
| `SCORE_WEIGHT` | 대본 분석 5장 | 3 / 2 / 1 | 중요도 가중치 (두 노트북 공용) |
| `RUBRIC_CONSISTENCY_SAMPLES` | 환경 변수 | 3 | 대본 분석 12장 일관성 측정 횟수 (1 이면 끔) |
| `STT_CONSISTENCY_SAMPLES` | 환경 변수 | 3 | STT 12장 반복 채점 횟수 (1 이면 끔) |

코드 상수는 채점 결과만 바꾸고 LLM 캐시는 그대로 씁니다. 프롬프트를 바꾸면 캐시가 무효화됩니다.

---

## 캐시와 비용

LLM 응답은 `outputs/rubrics.sqlite` 에 캐시됩니다. 키는 **입력 해시 + 설정 해시(모델 · 프롬프트 · 출력 스키마)** 입니다.

| 바꾼 것 | 다시 부르는 호출 |
|---|---|
| 코드 상수 · 점수 계산 · 규칙 | 없음 (단, 규칙 결과가 LLM 입력에 들어가는 곳은 입력이 바뀌면 다시 부름) |
| 대본 한 슬라이드 | 그 슬라이드의 ① 2회 + 그 대본을 쓰는 모든 연습의 해당 슬라이드 ② |
| ② 프롬프트 · 출력 스키마 · 모델 | ② 전부 (반복 채점 포함 약 660회) |
| ① 프롬프트 · 출력 스키마 · 모델 | ① 전부 → 평가 기준이 바뀌므로 ② 도 전부 |
| `outputs/` 삭제 | 전부 |

비용을 아끼려면: 먼저 `STT_CONSISTENCY_SAMPLES=1` 로 한 번 돌려 결과를 확인하고, 좋아졌을 때만 반복 채점을 켜세요.

---

## 반드시 알아야 할 함정

| 함정 | 내용 |
|---|---|
| 상위 `.env` 자동 사용 | `.env` 를 위로 찾아 올라가므로 저장소 공용 키로 바로 과금됩니다 |
| 테이블 컬럼 변경 | `CREATE TABLE IF NOT EXISTS` 라 예전 테이블이 남습니다. `similar_items` 처럼 매번 새로 채워지는 테이블은 지우고 다시 실행하세요 |
| 커널이 DB 를 잡고 있음 | Windows 에서 `outputs/rubrics.sqlite` 를 지우려면 커널을 먼저 종료하세요 |
| 한글 출력 깨짐 | 명령줄 실행 시 `PYTHONIOENCODING=utf-8` (Windows cp949 콘솔) |
| STT 텍스트를 고치고 싶을 때 | 정규화는 일부러 규칙만 씁니다 — LLM 으로 다시 쓰면 인식 오류·발표자 실수가 사라지고 원본 위치가 깨집니다 ([../README.md](../README.md) §12) |
| 영문 이름 | STT 가 한글로 적은 영문 이름(`시트플로우`)은 규칙이 못 찾아 `unverified` 가 되고, 5장 LLM 이 확인합니다 |
| 정확도 숫자 | 가상 데이터와 라벨을 같은 작성자가 만들었습니다. 규칙 임계값 일부도 같은 데이터로 정했습니다 — 실제 데이터로 다시 재세요 |

---

## 문제 해결

| 증상 | 해결 |
|---|---|
| `AssertionError: .env 에 OPENAI_API_KEY / OPENAI_MODEL 이 없다` | `v1/local/` 에서 실행했는지, 위쪽 `.env` 에 키가 있는지 확인 |
| LLM 호출 400 | `OPENAI_MODEL` 이 엔드포인트가 허용하는 이름인지 확인 |
| `No module named 'nbconvert'` / `jupyter` 명령 없음 | `pip install -r requirements.txt` 후 `python -m nbconvert` 로 실행 |
| STT 노트북 `ValueError: … 평가 기준이 없다` | STT 의 `script_name` 과 대본 파일 이름이 같은지, 대본 분석이 성공했는지 확인 |
| `table … has N columns but M values were supplied` | 테이블 스키마가 바뀜 — 해당 테이블을 지우고 다시 실행 |
| 실행할 때마다 호출이 많음 | 프롬프트나 모델을 바꿨는지 확인. 검증용 반복 채점은 `STT_CONSISTENCY_SAMPLES=1` 로 끔 |

---

## 현재 상태

| | |
|---|---|
| ✅ | 두 노트북 전 구간 동작 · 가상 데이터로 정답 비교·반복 채점 완료 · 캐시로 재실행 시 API 호출 0회 |
| ⚠️ | **실제 발표 녹음·Deepgram 결과 없음** — 모든 정확도는 가상 데이터 기준 |
| ⚠️ | 테스트 코드 · CI 없음 — 규칙 회귀 확인은 대본 분석 노트북 11장의 `assert` 뿐 |
| ⬜ | `v1/deploy` 없음 — 서비스 API 와 코칭 agent 인터페이스 미정 |

가장 큰 공백은 **실제 데이터**입니다. 실제 슬라이드별 Deepgram 결과와 문장별 라벨이 생기면 STT 노트북 3-2장 → 11장 → 12장 순서로 확인하세요.

자세한 기술 문서 · 단계별 동작 · 측정 결과 · 설계 결정 → **[../README.md](../README.md)**
