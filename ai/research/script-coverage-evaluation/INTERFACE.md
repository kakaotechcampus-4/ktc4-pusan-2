# 입출력 — 대본 전달도 코어

코어(`src/script_coverage/`)가 무엇을 받아 무엇을 돌려주는지 정리합니다. 나중에 AI 서버의 API 요청 · 응답을 정할 때 이 문서를 기준으로 삼습니다.

- **필드의 원본은 코드의 pydantic 모델입니다.** 평가 기준은 `shared/rubric.py` · `shared/facts.py` · `shared/text.py`, 입력과 채점 결과는 `script_analysis/schemas.py` · `stt_evaluation/schemas.py`에 있습니다. 문서와 코드가 다르면 코드가 맞습니다.
- 아래 필드 표는 그 모델에서 뽑아 만들었습니다. 모델을 바꾸면 이 문서도 함께 고칩니다.
- 예시 JSON은 가상대본1의 6번 슬라이드와 그 연습(`가상대본1_take9`)의 실제 결과이고, 길어서 목록은 앞의 1~2개만 남겼습니다.

## 목차

1. [진입점 3개](#1-진입점-3개)
2. [데이터 모델](#2-데이터-모델)
3. [모듈별 입출력](#3-모듈별-입출력)
4. [LLM 입출력](#4-llm-입출력)
5. [버전과 호환](#5-버전과-호환)

---

## 1. 진입점 3개

API가 부를 함수는 세 개입니다. 셋 다 파일 · DB · 네트워크를 직접 쓰지 않고, LLM과 캐시는 인자로 받습니다.

| 진입점 | 언제 | 입력 | 출력 | LLM |
|---|---|---|---|---|
| `script_analysis.core.analyze_script` | 대본 등록 · 수정 | 슬라이드별 대본 | 슬라이드별 평가 기준 + 실행 정보 | 슬라이드당 3회 |
| `stt_evaluation.core.evaluate_take` | 연습 종료 | 슬라이드별 STT + 그 대본의 평가 기준 | 슬라이드별 평가 결과 + 실행 정보 | 슬라이드당 1회 (+ 충돌 시 1회) |
| `stt_evaluation.confirm.rescore_evaluation` | 사용자 확인 | 평가 결과 + 사용자 답 + 평가 기준 | 다시 계산한 평가 결과 | 0회 |

발표 전체 점수가 필요하면 `stt_evaluation.scoring.take_scores`에 슬라이드 평가 결과 목록을 넘깁니다 ([1-4](#1-4-발표-전체-점수-take_scores)).

### 1-1. 평가 기준 만들기 `analyze_script`

```python
analyze_script(
    script_name: str,
    slides: list[SlideScript],
    *,
    semantic_llm, final_llm, unit_llm,   # 출력 스키마가 고정된 LLM 3개 (4절)
    model: str,                          # 모델 이름. 평가 기준 메타와 캐시 키에 들어간다
    cache: LLMCache | None = None,       # 배포에서는 None
    max_workers: int = 4,                # LLM 동시 호출 수
) -> tuple[list[EvaluationRubric], dict]
```

**입력**

| 인자 | 설명 |
|---|---|
| `script_name` | 발표(대본)를 가리키는 이름. 평가 기준 `meta.script_name`에 그대로 들어간다 |
| `slides` | 슬라이드별 대본 ([SlideScript](#slidescript)). 순서는 상관없고 번호순으로 분석한다 |
| `semantic_llm` · `final_llm` · `unit_llm` | 1차 분석 · 최종 결론 · 전달 단위용 LLM ([4절](#4-llm-입출력)) |

```json
[
  {"slide_number": 6, "script": "2025년 3월부터 8주 동안 제휴 도서관 3곳에서 시범 운영을 했습니다. 참여한 이용자는 1,860명이었고, …"}
]
```

**출력**: `(평가 기준 목록, 실행 정보)`

- 평가 기준([EvaluationRubric](#evaluationrubric))은 슬라이드마다 하나입니다. LLM 호출이 실패한 슬라이드는 목록에서 빠집니다.
- 실행 정보 (예: 캐시가 빈 상태에서 9장을 분석했을 때):

```json
{"slides": 9, "analysis_calls": 9, "final_calls": 9, "unit_calls": 9, "failed": {}}
```

| 키 | 뜻 |
|---|---|
| `slides` | 받은 슬라이드 수 |
| `analysis_calls` · `final_calls` · `unit_calls` | 이번 실행에서 실제로 부른 LLM 횟수 (캐시에 있으면 0) |
| `failed` | 실패한 슬라이드 `{슬라이드 번호: "단계 이름 + 오류"}`. 예: `{3: "1차 분석 TimeoutError: …"}` |

**실패 처리**: 한 슬라이드가 실패해도 나머지는 결과를 돌려줍니다. 호출하는 쪽은 `failed`에 있는 슬라이드만 다시 요청하면 됩니다. 읽을 내용이 없는 슬라이드(빈 대본)는 LLM을 부르지 않고 빈 평가 기준을 만듭니다.

**예시 출력** (평가 기준 하나)

```json
{
  "meta": {
            "rubric_id": "a36e1ee147e44909",
            "script_name": "가상대본1",
            "slide_number": 6,
            "content_hash": "d211dcdd3f197a1f93540587dded4e2c54dda186e91eae954f1925023f1f1a3e",
            "rubric_schema_version": "1.1",
            "llm_model": "openai/gpt-5.6-luna",
            "llm_config_hash": "f7b2ef94d4e7",
            "final_config_hash": "83cb79b10a03",
            "unit_config_hash": "1e964b21a6bf",
            "created_at": "2026-10-05T15:38:48+00:00"
          },
  "normalized_script": "2025년 3월부터 8주 동안 제휴 도서관 3곳에서 시범 운영을 했습니다. 참여한 이용자는 1,860명이었고…",
  "sentences": [
                {
                  "index": 0,
                  "text": "2025년 3월부터 8주 동안 제휴 도서관 3곳에서 시범 운영을 했습니다.",
                  "start": 0,
                  "end": 41
                }
              ],
  "sentence_roles": ["detail", "claim", "evidence", "evidence", "evidence"],
  "core_claim": "시범 운영 결과 빈자리를 찾는 데 걸린 시간이 평균 23분에서 13분으로 약 42퍼센트 줄었다.",
  "key_points": [
                  {
                    "id": "KP1",
                    "content": "2025년 3월부터 8주 동안 제휴 도서관 3곳에서 시범 운영을 했다.",
                    "importance": "normal",
                    "sentence_indices": [0],
                    "source_quote": "2025년 3월부터 8주 동안 제휴 도서관 3곳에서 시범 운영을 했습니다.",
                    "source_span": [0, 41],
                    "key_terms": [],
                    "fact_ids": ["CF1", "CF2", "CF3"]
                  }
                ],
  "critical_facts": [
                      {
                        "id": "CF1",
                        "type": "date",
                        "value": "2025년 3월",
                        "normalized": "2025-03",
                        "numeric_value": null,
                        "unit": null,
                        "qualifier": null,
                        "spans": [[0, 8]],
                        "sentence_indices": [0],
                        "source": "rule",
                        "key_point_ids": ["KP1"],
                        "importance": "normal"
                      }
                    ],
  "keywords": [
                {"term": "시범", "score": 0.3784, "tf": 2},
                {"term": "시범 운영", "score": 0.3784, "tf": 2}
              ],
  "warnings": [],
  "draft_warnings": [],
  "review_changes": [],
  "name_decisions": [],
  "content_units": [
                    {
                      "id": "S0-U1",
                      "sentence_index": 0,
                      "text": "2025년 3월부터 시범 운영함",
                      "quote": "2025년 3월부터",
                      "span": [0, 10],
                      "fact_ids": ["CF1"],
                      "source": "llm"
                    },
                    {
                      "id": "S0-U2",
                      "sentence_index": 0,
                      "text": "8주 동안 시범 운영함",
                      "quote": "8주 동안",
                      "span": [11, 16],
                      "fact_ids": ["CF2"],
                      "source": "llm"
                    }
                  ]
}
```

### 1-2. 채점 `evaluate_take`

```python
evaluate_take(
    take: Take,
    rubrics: dict[int, EvaluationRubric],   # {슬라이드 번호: 그 슬라이드의 평가 기준}
    *,
    eval_llm, verifier_llm,                 # 의미 평가 · 교차 검증용 LLM 2개 (4절)
    model: str,
    cache: LLMCache | None = None,
    max_workers: int = 4,
) -> tuple[list[SlideEvaluation], dict]
```

**입력**

| 인자 | 설명 |
|---|---|
| `take` | 연습 한 번의 슬라이드별 STT ([Take](#take)) |
| `rubrics` | 이 대본의 평가 기준. `take.slides`에 있는 모든 슬라이드 번호가 있어야 한다 |

```json
{
  "script_name": "가상대본1",
  "take_id": "가상대본1_take9",
  "slides": [{"slide_number": 6, "stt": "이천이십오 년 삼 월부터 팔 주 동안 제휴 도서관 세 곳에서 시범 운영을 했습니다 …"}]
}
```

**출력**: `(평가 결과 목록, 실행 정보)`

- 평가 결과([SlideEvaluation](#slideevaluation))는 슬라이드마다 하나입니다.
- 실행 정보 (예: 캐시가 빈 상태에서 9장 중 2장에 충돌이 있었을 때):

```json
{"slides": 9, "semantic_calls": 9, "verifier_calls": 2, "verified_slides": 2, "failed": {}}
```

| 키 | 뜻 |
|---|---|
| `slides` | 받은 슬라이드 수 |
| `semantic_calls` · `verifier_calls` | 이번 실행에서 실제로 부른 의미 평가 · 교차 검증 횟수 |
| `verified_slides` | 충돌이 있어 교차 검증 대상이 된 슬라이드 수 |
| `failed` | 실패한 슬라이드 `{슬라이드 번호: "의미 평가 …" 또는 "교차 검증 …"}` |

**실패 처리**
- 평가 기준이 없는 슬라이드가 있으면, LLM을 부르기 전에 `ValueError`로 멈춥니다.
- LLM 호출이 실패한 슬라이드는 결과에서 빠지고 `failed`에 남습니다.

**예시 출력** (평가 결과 하나)

```json
{
  "take_id": "가상대본1_take9",
  "script_name": "가상대본1",
  "slide_number": 6,
  "rubric_id": "a36e1ee147e44909",
  "scores": {
              "content_coverage": 1.0,
              "critical_fact_accuracy": 1.0,
              "script_fidelity": 1.0,
              "similar_words": 1,
              "similar_numbers": 0,
              "_weights": {"key_points": 10, "facts": 25, "script_tokens": 39}
            },
  "sentences": [
                {
                  "sentence_index": 0,
                  "text": "2025년 3월부터 8주 동안 제휴 도서관 3곳에서 시범 운영을 했습니다.",
                  "role": "detail",
                  "status": "said",
                  "first_status": "said",
                  "units": [
                              {
                                "unit_id": "S0-U1",
                                "text": "2025년 3월부터 시범 운영함",
                                "fact_ids": ["CF1"],
                                "status": "said",
                                "first_status": "said",
                                "raised_by_rule": false
                              },
                              {
                                "unit_id": "S0-U2",
                                "text": "8주 동안 시범 운영함",
                                "fact_ids": ["CF2"],
                                "status": "said",
                                "first_status": "said",
                                "raised_by_rule": false
                              }
                            ],
                  "confidence": "high",
                  "evidence": [0],
                  "reason": "2025년 3월, 8주 동안, 제휴 도서관 3곳에서 시범 운영했다는 세 단위를 모두 말했습니다.",
                  "lexical_coverage": 1.0,
                  "evidence_coverage": 1.0,
                  "fact_statuses": {"CF1": "matched", "CF2": "matched", "CF3": "matched"},
                  "similar_ids": [],
                  "conflicts": [],
                  "verified": false
                }
              ],
  "key_points": [
                  {
                    "key_point_id": "KP1",
                    "content": "2025년 3월부터 8주 동안 제휴 도서관 3곳에서 시범 운영을 했다.",
                    "importance": "normal",
                    "sentence_indices": [0],
                    "status": "covered",
                    "first_status": "covered",
                    "evidence": [0],
                    "evidence_text": "이천이십오 년 삼 월부터 팔 주 동안 제휴 도서관 세 곳에서 시범 운영을 했습니다",
                    "reason": "근거 문장을 모두 전달함",
                    "lexical_coverage": 1.0,
                    "fact_statuses": {"CF1": "matched", "CF2": "matched", "CF3": "matched"},
                    "similar_ids": [],
                    "conflicts": [],
                    "verified": false
                  }
                ],
  "critical_facts": [
                      {
                        "fact_id": "CF1",
                        "type": "date",
                        "value": "2025년 3월",
                        "normalized": "2025-03",
                        "importance": "normal",
                        "key_point_ids": ["KP1"],
                        "status": "matched",
                        "stt_value": "이천이십오 년 삼 월",
                        "stt_ids": [0],
                        "note": "",
                        "stt_normalized": null,
                        "stt_numeric_value": null,
                        "stt_qualifier": null,
                        "stt_span": null,
                        "signals": [],
                        "rule_cause": null,
                        "sound": null,
                        "cause": null,
                        "cause_reason": ""
                      }
                    ],
  "similar_items": [
                    {
                      "item_id": "R1",
                      "kind": "word",
                      "script_text": "노쇼",
                      "stt_text": "노소",
                      "script_sentence_index": 3,
                      "script_span": [143, 145],
                      "stt_sentence_index": 3,
                      "stt_span": [148, 150],
                      "stt_raw_span": [148, 150],
                      "fact_id": null,
                      "key_point_ids": ["KP4"],
                      "signals": ["발음 거리 0.17 ('노쇼' ↔ '노소')", "대본 단어가 이 자리에서 빠지고 발음이 비슷한 다른 말이 나옴"],
                      "rule_guess": "asr_error"
                    }
                  ],
  "fidelity": {"recall": 1.0, "precision": 1.0, "fidelity": 1.0, "script_tokens": 39},
  "alignments": [{"sentence_index": 0, "stt_ids": [0], "coverage": 1.0}],
  "verification": {"required": false, "sentences": [], "conflicts": {}},
  "stt_sentences": ["이천이십오 년 삼 월부터 팔 주 동안 제휴 도서관 세 곳에서 시범 운영을 했습니다"],
  "stt_text": "이천이십오 년 삼 월부터 팔 주 동안 제휴 도서관 세 곳에서 시범 운영을…",
  "confirmations": {},
  "created_at": "2026-10-05T15:38:53+00:00"
}
```

### 1-3. 다시 계산 `rescore_evaluation`

```python
rescore_evaluation(
    evaluation: SlideEvaluation,     # 처음 채점 결과
    answers: dict[str, str],         # {비슷한 말 item_id: "as_script" | "as_stt"}
    rubric: EvaluationRubric,        # 채점에 쓴 평가 기준
) -> SlideEvaluation
```

| 사용자 답 | 뜻 | 다시 계산 |
|---|---|---|
| `as_script` | 대본대로 말함. 음성 인식이 잘못 적음 | 사실 `sound_alike` → `matched`. 문장 판정은 그대로 |
| `as_stt` | 들린 대로 말함. 발표자가 다른 말을 함 | 사실 → `mismatched`, 그 자리가 든 전달 단위 → `contradicted` → 문장 · Key Point · 점수를 다시 모음 |

- 출력은 처음 평가와 같은 모양이고, `confirmations`에 반영한 답이 채워집니다. **처음 평가는 바꾸지 않고 새 객체를 돌려줍니다.**
- 답하지 않은 항목은 계속 보류입니다.
- `evaluation.rubric_id`와 `rubric.meta.rubric_id`가 다르면 `ValueError`입니다. 평가 기준이 바뀌었으면 다시 채점해야 합니다.
- `stt_text`가 빈 예전 평가도 `ValueError`입니다.
- **사용자 답을 저장할 때는 `item_id`와 함께 그 항목의 `script_text` · `stt_text`도 저장하세요.** 다시 채점하면 `item_id`가 바뀔 수 있어서, 답을 다시 적용할 때 표현이 같은지 확인해야 합니다. research에서는 `coverage_lab/store.py`의 `confirm_similar_item`이 이렇게 합니다.

### 1-4. 발표 전체 점수 `take_scores`

```python
take_scores(slide_evaluations: list[SlideEvaluation]) -> dict
```

슬라이드 점수를 가중 평균합니다. 가중치는 Key Point 가중치 합 · 사실 가중치 합 · 대본 형태소 수이고, 비슷한 말 개수는 더합니다.

```json
{"content_coverage": 0.924, "critical_fact_accuracy": 1.0, "script_fidelity": 0.965, "similar_words": 6, "similar_numbers": 2}
```

---

## 2. 데이터 모델

### 입력

#### SlideScript

`script_analysis/schemas.py`

| 필드 | 타입 | 뜻 |
|---|---|---|
| `slide_number` | `int` | 슬라이드 번호 |
| `script` | `str` | 슬라이드 대본 원문 |

#### Take

`stt_evaluation/schemas.py`. 슬라이드마다 `SlideSTT`가 하나씩 들어 있습니다.

| 필드 | 타입 | 뜻 |
|---|---|---|
| `script_name` | `str` | 어느 대본의 연습인지 (대본 JSON 파일 이름) |
| `take_id` | `str` | 연습 한 번을 가리키는 id |
| `scenario` | `str` (기본값 있음) | 가상 데이터의 시나리오 (충실 / 의역 / 누락 / 실수 …). 실제 데이터에는 없어도 된다 |
| `slides` | `list[SlideSTT]` | 슬라이드별 STT |

| 필드 | 타입 | 뜻 |
|---|---|---|
| `slide_number` | `int` | 슬라이드 번호 |
| `stt` | `str` | 이 슬라이드에서 발표자가 말한 내용의 음성 인식 결과 |

### 평가 기준

#### EvaluationRubric

`shared/rubric.py`. 1단계가 만들고 2단계가 읽는 약속입니다.

| 필드 | 타입 | 뜻 |
|---|---|---|
| `meta` | `RubricMeta` | 평가 기준 메타 |
| `normalized_script` | `str` | 정규화한 대본 전체 |
| `sentences` | `list[Sentence]` | 정규화한 대본 문장 |
| `sentence_roles` | `list[str]` | 문장별 역할 (claim / evidence / detail / skip). sentences 와 순서가 같다 |
| `core_claim` | `str` | 슬라이드의 핵심 주장 한 문장 |
| `key_points` | `list[KeyPoint]` | Key Point 목록 |
| `critical_facts` | `list[CriticalFact]` | 핵심 사실 목록 |
| `keywords` | `list[Keyword]` | TF-IDF 키워드 |
| `warnings` | `list[str]` | 최종 검증 경고 |
| `draft_warnings` | `list[str]` (기본값 있음) | 1차 결과에 대한 코드 검증 경고 (최종 결론의 입력) |
| `review_changes` | `list[str]` (기본값 있음) | 최종 결론이 1차 결과에서 바꾼 점 |
| `name_decisions` | `list[NameDecision]` (기본값 있음) | 이름·용어 후보별 최종 결정 |
| `content_units` | `list[ContentUnit]` (기본값 있음) | 문장별 전달 단위. skip 문장은 나누지 않는다 |

`sentence_roles`의 값: `claim`(핵심 주장, 슬라이드당 최대 1문장) · `evidence`(근거) · `detail`(세부) · `skip`(인사 · 전환, 채점하지 않음)

#### RubricMeta

| 필드 | 타입 | 뜻 |
|---|---|---|
| `rubric_id` | `str` | 대본·LLM 설정·스키마가 같으면 같은 값. 평가 결과가 어떤 기준으로 채점됐는지 가리킨다 |
| `script_name` | `str` | 입력 JSON 파일 이름 (확장자 제외) |
| `slide_number` | `int` | 슬라이드 번호 |
| `content_hash` | `str` | 정규화 대본의 해시. 대본이 바뀌었는지 볼 때 쓴다 |
| `rubric_schema_version` | `str` | 평가 기준 형식 버전 (1.1) |
| `llm_model` | `str` | 분석에 쓴 LLM 모델 |
| `llm_config_hash` | `str` | 1차 분석 LLM 설정 해시 |
| `final_config_hash` | `str` (기본값 있음) | 최종 결론 LLM 설정 해시. 1차 결과(초안)면 빈 값 |
| `unit_config_hash` | `str` (기본값 있음) | 전달 단위 LLM 설정 해시. 단위를 나누기 전이면 빈 값 |
| `created_at` | `str` | 만든 시각 (UTC) |

#### Sentence

`shared/text.py`. 위치(`start` · `end`)는 정규화 대본 텍스트 기준입니다.

| 필드 | 타입 | 뜻 |
|---|---|---|
| `index` | `int` | 문장 번호 (대본은 S번호, STT 는 T번호) |
| `text` | `str` | 문장 |
| `start` | `int` | 정규화 텍스트 기준 시작 오프셋 |
| `end` | `int` | 정규화 텍스트 기준 끝 오프셋 (exclusive) |

#### KeyPoint

| 필드 | 타입 | 뜻 |
|---|---|---|
| `id` | `str` | Key Point id (KP1, KP2 …) |
| `content` | `str` | Key Point 내용 |
| `importance` | `critical \| high \| normal` | 근거 문장의 역할로 코드가 정한다 (ROLE_IMPORTANCE) |
| `sentence_indices` | `list[int]` | 근거 문장 번호 (LLM 이 번호로 지정) |
| `source_quote` | `str` | 근거 문장들의 원문 |
| `source_span` | `tuple[int, int] \| None` | 근거 문장들의 위치. 유효한 번호가 없으면 None |
| `key_terms` | `list[str]` | Key Point 의 핵심 용어 |
| `fact_ids` | `list[str]` | 이 Key Point 가 직접 말하는 Critical Fact |

중요도는 근거 문장의 역할로 정합니다: `claim`이 있으면 `critical`, `evidence`면 `high`, 나머지 `normal`. 점수 가중치는 critical 3 · high 2 · normal 1 (`SCORE_WEIGHT`).

#### CriticalFact

`shared/facts.py`

| 필드 | 타입 | 뜻 |
|---|---|---|
| `id` | `str` (기본값 있음) | 사실 id (CF1, CF2 …) |
| `type` | `percentage \| money \| date \| time \| duration \| quantity \| ratio \| number \| proper_noun \| term` | 사실 종류 |
| `value` | `str` | 대본에 적힌 표기 그대로 (예: '약 1,240만 건') |
| `normalized` | `str` | STT 와 비교할 정규형 (예: '12,400,000건') |
| `numeric_value` | `float \| None` (기본값 있음) | 숫자 값 |
| `unit` | `str \| None` (기본값 있음) | 단위 |
| `qualifier` | `str \| None` (기본값 있음) | 약·이상·이내 같은 한정어 |
| `spans` | `list[tuple[int, int]]` | 정규화 텍스트 기준 등장 위치 (첫 번째가 대표) |
| `sentence_indices` | `list[int]` | 사실이 나온 문장 번호 |
| `source` | `rule \| llm` (기본값 있음) | rule: 규칙이 찾음 / llm: LLM 이 보탬 |
| `key_point_ids` | `list[str]` (기본값 있음) | 평가 기준을 만들 때 연결 (`draft.py`) |
| `importance` | `critical \| high \| normal` (기본값 있음) | 연결된 Key Point 중 가장 높은 중요도 |

| `type` | 예 (표기 → `normalized`) |
|---|---|
| `percentage` | `약 42퍼센트` → `42%` (한정어 `약`) |
| `money` | `약 24억 원` → `2,400,000,000원` |
| `quantity` | `약 1,240만 건` → `12,400,000건`, `열두 곳` → `12곳` |
| `duration` · `date` · `time` | `8주 동안` → `8주` / `2026년 상반기` → `2026-H1` / `오후 여섯 시 반` → `18:30` |
| `ratio` · `number` | `삼 대 일` → `3:1` / `4 곱하기 6` → `4×6` |
| `proper_noun` · `term` | 영문 이름(`SeatFlow`), 고유명사, LLM이 보탠 핵심 용어 |

#### ContentUnit

| 필드 | 타입 | 뜻 |
|---|---|---|
| `id` | `str` | S{문장 번호}-U{순번} |
| `sentence_index` | `int` | 단위가 속한 대본 문장 번호 |
| `text` | `str` | 단위의 내용을 짧게 쓴 것 |
| `quote` | `str` | 대본 문장 속 해당 표현 (대본 표기 그대로) |
| `span` | `tuple[int, int]` | quote 의 정규화 대본 기준 위치 |
| `fact_ids` | `list[str]` (기본값 있음) | 이 단위 안에 있는 Critical Fact |
| `source` | `llm \| fact \| sentence` | llm: LLM 이 나눔 / fact: 어느 단위에도 안 들어간 수치·이름을 코드가 보탬 / sentence: 나누지 못해 문장 전체 |

#### Keyword · NameDecision

| 필드 | 타입 | 뜻 |
|---|---|---|
| `term` | `str` | 키워드 |
| `score` | `float` | TF-IDF 점수 (슬라이드 안에서 L2 정규화) |
| `tf` | `int` | 슬라이드 안 등장 횟수 |

| 필드 | 타입 | 뜻 |
|---|---|---|
| `value` | `str` | 이름 · 용어 후보 |
| `source` | `rule \| llm` | 후보를 찾은 쪽 |
| `keep` | `bool` | 발표자가 이 말을 그대로 해야 하는가 (True 면 Critical Fact) |
| `reason` | `str` | 판단 이유 |

### 평가 결과

#### SlideEvaluation

`stt_evaluation/schemas.py`

| 필드 | 타입 | 뜻 |
|---|---|---|
| `take_id` | `str` | 연습 id |
| `script_name` | `str` | 대본 이름 |
| `slide_number` | `int` | 슬라이드 번호 |
| `rubric_id` | `str` | 채점에 쓴 평가 기준 (대본 분석 결과의 rubric_id) |
| `scores` | `dict` | 점수 (아래 표) |
| `sentences` | `list[SentenceResult]` | 대본 문장별 판정 (전달 단위 판정을 모은 문장 판정 + 규칙 + 교차 검증) |
| `key_points` | `list[KeyPointResult]` | Key Point 판정 = 근거 문장 판정을 모은 것 |
| `critical_facts` | `list[FactCheck]` | 사실별 확인 결과 |
| `similar_items` | `list[SimilarItem]` | 비슷한 말 — 비율에서 뺀 판단 보류 항목 (위치 포함) |
| `fidelity` | `dict` | 대본 충실도 세부 (recall / precision / fidelity) |
| `alignments` | `list[Alignment]` | 대본 문장 ↔ STT 문장 짝 |
| `verification` | `dict` | 교차 검증 여부, 대상 문장, 충돌 조건별 문장 번호 |
| `stt_sentences` | `list[str]` | 정규화한 STT 문장 (근거 번호 T0, T1 … 의 원문) |
| `stt_text` | `str` (기본값 있음) | 정규화한 STT 전체 텍스트 — 사용자 확인 뒤 점수를 다시 계산할 때 쓴다 (`confirm.py`) |
| `confirmations` | `dict[str, str]` (기본값 있음) | 반영한 사용자 확인 (비슷한 말 item_id → as_script / as_stt). 처음 채점에는 비어 있다 |
| `created_at` | `str` | 채점 시각 (UTC) |

**`scores`**

| 키 | 뜻 | 계산 |
|---|---|---|
| `content_coverage` | 내용 전달 (0~1) | Key Point 판정 점수(`covered` 1 · `partial` 0.5 · `missing` 0 · `contradicted` −0.5)를 중요도 가중치로 평균. 음수면 0 |
| `critical_fact_accuracy` | 수치 정확도 (0~1, 사실이 없으면 `null`) | `matched` 1 · `approximate` 0.5 · 나머지 0. `sound_alike`(판단 보류)는 빼고 계산 |
| `script_fidelity` | 대본 충실도 (0~1) | 대본 형태소가 순서대로 나온 정도 (recall · precision 조화평균). 비슷한 말 자리는 뺌 |
| `similar_words` · `similar_numbers` | 판단 보류한 단어 · 수치 개수 | |
| `_weights` | 발표 전체 점수를 낼 때 쓰는 가중치 (`take_scores`가 읽음) | 응답에 노출할 필요는 없음 |

**`verification`**: `{"required": 교차 검증했는가, "sentences": [충돌한 문장 번호], "conflicts": {충돌 조건: [문장 번호]}}`

#### SentenceResult

| 필드 | 타입 | 뜻 |
|---|---|---|
| `sentence_index` | `int` | 대본 문장 번호 |
| `text` | `str` | 대본 문장 |
| `role` | `str` | 문장 역할 |
| `status` | `said \| partial \| missing \| contradicted` | 최종 판정 (전달 단위 최종 판정을 모은 것) |
| `first_status` | `said \| partial \| missing \| contradicted` | LLM 1차 판정 (전달 단위 1차 판정을 모은 것) |
| `units` | `list[UnitResult]` (기본값 있음) | 전달 단위별 판정 |
| `confidence` | `str` | LLM 확신도 (high / medium / low) |
| `evidence` | `list[int]` | 근거 STT 문장 번호 (범위 밖 번호는 버림) |
| `reason` | `str` | 판정 이유 (사용자에게 보여 줄 수 있음) |
| `lexical_coverage` | `float` | 이 문장의 내용 형태소가 STT 정렬 구간에 나온 비율 (`align_sentences`) |
| `evidence_coverage` | `float` | 이 문장의 내용 형태소가 LLM 근거 STT 문장에 나온 비율 (근거가 이 문장과 관련 있는지) |
| `fact_statuses` | `dict[str, str]` | 이 문장에 있는 사실별 규칙 검증 결과 |
| `similar_ids` | `list[str]` (기본값 있음) | 이 문장 자리의 비슷한 말 (R번호, 판단 보류) |
| `conflicts` | `list[str]` (기본값 있음) | 규칙 결과와 LLM 판정이 어긋난 이유 |
| `verified` | `bool` (기본값 있음) | 교차 검증으로 다시 판정했는가 |

| 문장 `status` | 뜻 |
|---|---|
| `said` | 뜻이 전달됨 (의역 포함, 핵심 수치 · 이름까지) |
| `partial` | 일부만 (수치를 흐리거나 내용 일부만) |
| `missing` | 없음 |
| `contradicted` | 다른 수치 · 반대 내용 |

`conflicts`에 들어가는 충돌 조건(예: `fact_missing`: LLM은 "말함"인데 규칙이 그 수치를 STT에서 못 찾음)은 `stt_evaluation/merge.py`의 `CONFLICT_TEXT`에 뜻과 함께 정의돼 있습니다.

#### UnitResult

| 필드 | 타입 | 뜻 |
|---|---|---|
| `unit_id` | `str` | 전달 단위 id |
| `text` | `str` | 단위 내용 |
| `fact_ids` | `list[str]` (기본값 있음) | 단위 안의 사실 |
| `status` | `said \| vague \| missing \| contradicted` | 최종 판정 |
| `first_status` | `said \| vague \| missing \| contradicted` | 1차 판정 (LLM 판정 + 규칙 보정) |
| `raised_by_rule` | `bool` (기본값 있음) | LLM 은 missing 이라 했지만 이 단위의 수치·이름이 STT 에 있어 vague 로 올림 |

단위 `status`: `said`(말함) · `vague`(흐리게 말함) · `missing`(없음) · `contradicted`(다르게 말함). 문장 판정은 단위 판정을 모아 코드가 정합니다. 모두 `said`면 `said`, 모두 `missing`이면 `missing`, 하나라도 `contradicted`면 `contradicted`, 나머지는 `partial`입니다.

#### KeyPointResult

| 필드 | 타입 | 뜻 |
|---|---|---|
| `key_point_id` | `str` | Key Point id |
| `content` | `str` | Key Point 내용 |
| `importance` | `critical \| high \| normal` | 중요도 |
| `sentence_indices` | `list[int]` | 근거 대본 문장 |
| `status` | `covered \| partial \| missing \| contradicted` | 최종 판정 |
| `first_status` | `covered \| partial \| missing \| contradicted` | LLM 1차 문장 판정을 모은 판정 |
| `evidence` | `list[int]` | 근거 STT 문장 번호 |
| `evidence_text` | `str` | 근거 STT 문장 원문 |
| `reason` | `str` | 판정 이유 |
| `lexical_coverage` | `float` | 근거 문장들의 내용 형태소가 STT 에 나온 비율 |
| `fact_statuses` | `dict[str, str]` | 이 Key Point 에 연결된 사실별 규칙 검증 결과 |
| `similar_ids` | `list[str]` (기본값 있음) | 이 Key Point 자리의 비슷한 말 |
| `conflicts` | `list[str]` (기본값 있음) | 근거 문장들의 충돌 이유 |
| `verified` | `bool` (기본값 있음) | 교차 검증으로 다시 판정했는가 |

Key Point `status`: `covered` · `partial` · `missing` · `contradicted` (근거 문장 판정을 모은 값)

#### FactCheck

| 필드 | 타입 | 뜻 |
|---|---|---|
| `fact_id` | `str` | 사실 id |
| `type` | `str` | 사실 종류 |
| `value` | `str` | 대본 표기 |
| `normalized` | `str` | 비교용 정규형 |
| `importance` | `critical \| high \| normal` | 중요도 |
| `key_point_ids` | `list[str]` | 연결된 Key Point |
| `status` | `matched \| mismatched \| missing \| unverified \| sound_alike \| approximate` | matched: 같은 값을 말함 / mismatched: 다른 값을 말함 / missing: 없음 / unverified: 규칙으로 판단 불가(LLM 확인) / sound_alike: 발음이 비슷한 다른 말로 나옴 — 판단 보류, 비율에서 뺌 / approximate: 값을 어림해 말함 |
| `stt_value` | `str \| None` (기본값 있음) | STT 에서 찾은 표기 (다른 값이면 그 값) |
| `stt_ids` | `list[int]` (기본값 있음) | 찾은 STT 문장 번호 |
| `note` | `str` (기본값 있음) | 메모 |
| `stt_normalized` | `str \| None` (기본값 있음) | STT 쪽 정규형 |
| `stt_numeric_value` | `float \| None` (기본값 있음) | STT 쪽 숫자 값 |
| `stt_qualifier` | `str \| None` (기본값 있음) | STT 쪽 한정어 |
| `stt_span` | `tuple[int, int] \| None` (기본값 있음) | 정규화 STT 기준 위치 |
| `signals` | `list[str]` (기본값 있음) | 원인 판단에 쓰는 규칙 신호 |
| `rule_cause` | `speaker_error \| asr_error \| approximation` (기본값 있음) | 규칙 신호만으로 본 원인 (비교용) |
| `sound` | `str \| None` (기본값 있음) | 두 수의 발음 관계: similar / near / swap / different / approx |
| `cause` | `speaker_error \| asr_error \| approximation` (기본값 있음) | 규칙으로 정한 원인 (발음이 비슷하면 정하지 않음) |
| `cause_reason` | `str` (기본값 있음) | 원인 판단 이유 |

| 사실 `status` | 뜻 |
|---|---|
| `matched` | 같은 값을 말함 |
| `approximate` | 어림해 말함 (`40퍼센트 넘게`). 사실은 맞음 |
| `mismatched` | 다른 값을 말함 (발음이 전혀 다르거나 음절 순서가 바뀜) |
| `missing` | 말하지 않음 |
| `sound_alike` | 발음이 비슷한 다른 말. **판단 보류** |
| `unverified` | 규칙으로 못 찾은 영문 이름. LLM 이름 확인으로 확정 |

#### SimilarItem

| 필드 | 타입 | 뜻 |
|---|---|---|
| `item_id` | `str` | 항목 id (R1, R2 …). 사용자 답의 키 |
| `kind` | `number \| word` | number: 수치 / word: 단어 |
| `script_text` | `str` | 대본 표현 |
| `stt_text` | `str` | STT 표현 |
| `script_sentence_index` | `int` | 대본 문장 번호 |
| `script_span` | `tuple[int, int]` | 정규화 대본 텍스트 기준 위치 |
| `stt_sentence_index` | `int` | 정규화 STT 문장 번호 (T번호) |
| `stt_span` | `tuple[int, int]` | 정규화 STT 텍스트 기준 위치 |
| `stt_raw_span` | `tuple[int, int] \| None` | 원본 STT 텍스트 기준 위치 — Deepgram 단어 타임스탬프와 맞출 때 쓴다 |
| `fact_id` | `str \| None` (기본값 있음) | 관련 Critical Fact |
| `key_point_ids` | `list[str]` | 이 자리가 걸린 Key Point |
| `signals` | `list[str]` | 규칙 신호 (발음 거리, 읽기 비교 등) |
| `rule_guess` | `asr_error \| speaker_error \| paraphrase` | 규칙 신호만으로 본 원인 — 참고 (asr_error: 인식 오류 쪽 / speaker_error: 발표자 실수 쪽) |

`stt_raw_span`은 간투사를 지우기 전 **원본 STT** 기준 위치입니다. Deepgram 단어 타임스탬프와 맞춰 녹음 구간을 찾는 데 씁니다.

#### Alignment

| 필드 | 타입 | 뜻 |
|---|---|---|
| `sentence_index` | `int` | 대본 문장 번호 |
| `stt_ids` | `list[int]` | 가장 비슷한 STT 문장 번호 (연속 1~3문장) |
| `coverage` | `float` | 대본 문장의 내용 형태소 중 STT 에 나온 비율 (0~1) |

---

## 3. 모듈별 입출력

코어 안에서 단계마다 무엇을 주고받는지입니다. 코드를 고칠 때 참고합니다. API에서 직접 부를 필요는 없습니다.

### 1단계: `analyze_script` 안

| 순서 | 함수 (파일) | 입력 | 출력 |
|---|---|---|---|
| ① | `normalize_script` (`shared/text.py`) | 대본 문자열 | `NormalizedScript` (정규화 텍스트, 문장 목록, `content_hash`) |
| ② | `extract_keywords_tfidf` (`keywords.py`) | 발표 전체의 `NormalizedScript` 목록 | 슬라이드별 `list[Keyword]` |
| ③ | `extract_critical_facts` (`shared/facts.py`) | `NormalizedScript` | `list[CriticalFact]` |
| ③ | `semantic_user_message` → `analyze_semantics` (`semantic.py`) | `SlideScript`, `NormalizedScript` → 메시지, LLM | `SlideSemanticAnalysis` |
| ④ | `draft_rubric` (`draft.py`) | 슬라이드, 정규화 결과, 사실, 키워드, 1차 분석, 발표 전체 텍스트, 모델 | `EvaluationRubric` (1차) |
| ⑤ | `name_candidates` · `final_user_message` → `review_final` (`final.py`) | 1차 평가 기준, 사실, 이름 후보 → 메시지, LLM | `FinalReview` |
| ⑤ | `finalize_rubric` (`final.py`) | 1차 평가 기준, `FinalReview`, 사실, 키워드, 후보, 모델 | `EvaluationRubric` (최종) |
| ⑥ | `unit_user_message` → `extract_units` (`units.py`) | 슬라이드, 최종 평가 기준 → 메시지, LLM | `SlideUnits` |
| ⑥ | `attach_units` (`units.py`) | 평가 기준, `SlideUnits`, 모델 | 전달 단위가 붙은 `EvaluationRubric` |

### 2단계: `evaluate_take` 안 (슬라이드마다)

| 순서 | 함수 (파일) | 입력 | 출력 |
|---|---|---|---|
| ① | `normalize_stt` (`normalize.py`, `fillers.py` 사용) | STT 문자열 | `NormalizedScript` (간투사 · 반복 제거) |
| ① | `positioned_tokens` (`align.py`) | `NormalizedScript` | 위치가 붙은 내용 형태소 목록 (대본 · STT 각각) |
| ② | `align_sentences` (`align.py`) | 대본 · STT 정규화 결과와 형태소, 문장 역할 | `list[Alignment]` |
| ③ | `check_critical_facts` → `annotate_mismatches` (`fact_check.py`) | 평가 기준, STT 정규화 결과, 정렬 | `list[FactCheck]` (다른 값에는 원인 신호 추가) |
| ④ | `find_similar_items` → `settle_facts` (`similar.py`) | 평가 기준, 정규화 결과 · 형태소, 정렬, 사실 확인, 원본 STT | `list[SimilarItem]` (사실 상태도 확정) |
| ④ | `fidelity_excluding` (`align.py`) | 평가 기준, 형태소, 비슷한 말 | 대본 충실도 `dict` |
| ⑤ | `semantic_eval_message` → `evaluate_semantics` (`judge.py`) | 슬라이드 번호, 평가 기준, STT 정규화 결과, 미확인 이름 → 메시지, LLM | `SemanticEvaluation` |
| ⑥ | `resolve_name_checks` · `merge_sentences` (`merge.py`) | 평가 기준, `SemanticEvaluation`, 사실 확인, 정렬, 비슷한 말, 형태소 | `list[SentenceResult]` (충돌 표시 포함) |
| ⑦ | `verifier_message` → `verify` → `apply_verification` (`verify.py`) | 충돌한 문장과 관련 정보 → 메시지, LLM | `VerifierResult` → 문장 판정에 반영 |
| ⑧ | `aggregate_key_points` (`merge.py`) | 평가 기준, 문장 판정, 사실 확인, 비슷한 말 | `list[KeyPointResult]` |
| ⑨ | `slide_scores` (`scoring.py`) | Key Point 판정, 사실 확인, 충실도, 비슷한 말 | `scores` `dict` |

`→`는 앞 함수의 출력을 뒤 함수가 받는다는 뜻입니다. 사실 확인 · 문장 판정을 그 자리에서 고치는 함수(`annotate_mismatches`, `settle_facts`, `resolve_name_checks`, `apply_verification`)는 값을 돌려주지 않습니다.

---

## 4. LLM 입출력

코어는 LLM을 `llm.invoke([("system", 프롬프트), ("user", 메시지)])` 한 가지 방식으로만 부릅니다. LLM 객체는 출력 스키마가 고정된 것(구조화 출력)이어야 하고, 답은 그 스키마의 객체로 돌아옵니다.

| 단계 | 인자 이름 | 프롬프트 | 메시지를 만드는 함수 | 출력 스키마 | 캐시 종류 · 키 |
|---|---|---|---|---|---|
| 1차 분석 | `semantic_llm` | `script_analysis/prompts/semantic.py` | `semantic_user_message` | `SlideSemanticAnalysis` | `script.semantic` · 대본 `content_hash` |
| 최종 결론 | `final_llm` | `script_analysis/prompts/final.py` | `final_user_message` | `FinalReview` | `script.final` · 메시지 해시 |
| 전달 단위 | `unit_llm` | `script_analysis/prompts/units.py` | `unit_user_message` | `SlideUnits` | `script.units` · 메시지 해시 |
| 의미 평가 | `eval_llm` | `stt_evaluation/prompts/semantic.py` | `semantic_eval_message` | `SemanticEvaluation` | `stt.semantic` · 메시지 해시 |
| 교차 검증 | `verifier_llm` | `stt_evaluation/prompts/verifier.py` | `verifier_message` | `VerifierResult` | `stt.verifier` · 메시지 해시 |

출력 스키마의 모양 (`script_analysis/schemas.py`, `stt_evaluation/schemas.py`):

```
SlideSemanticAnalysis(sentence_roles: list[SentenceLabel(id, role)], core_claim: str, key_points: list[KeyPointDraft(content, sentence_ids, key_terms)])
FinalReview(sentence_roles: list[SentenceLabel], core_claim: str, key_points: list[FinalKeyPoint(content, sentence_ids)],
            name_verdicts: list[NameVerdict(candidate_id, keep, reason)], changes: list[str])
SlideUnits(sentences: list[SentenceUnitsDraft(sentence_id, units: list[UnitDraft(text, quote)])])
SemanticEvaluation(sentences: list[SentenceJudgment(sentence_id, evidence, reason, units: list[UnitJudgment(unit_id, status)], confidence)],
                   name_checks: list[NameCheck(name_id, said, evidence)])
VerifierResult(judgments: list[VerifiedSentence(sentence_id, reason, units: list[UnitJudgment])])
```

- **프롬프트 · 출력 스키마(클래스 이름 · docstring · 필드 순서 · 설명) · 모델 이름은 캐시 키(설정 해시)에 들어갑니다.** 바꾸면 기존 캐시를 쓸 수 없고 LLM을 다시 부릅니다.
- 서버의 LLM 클라이언트를 만들 때 참고할 구현은 `coverage_lab/llm.py`의 `script_llms` · `stt_llms`입니다 (`ChatOpenAI(…, max_retries=2).with_structured_output(스키마)`).

---

## 5. 버전과 호환

| 값 | 위치 | 언제 바뀌나 |
|---|---|---|
| `FEATURE_VERSION` (`1.0`) | `version.py` | 출력의 **의미**가 바뀔 때. 응답에 실어 BE가 결과와 함께 저장한다 |
| `RUBRIC_SCHEMA_VERSION` (`1.1`) | `version.py` → 평가 기준 `meta.rubric_schema_version` | 평가 기준의 모양이 바뀔 때 |
| `rubric_id` | 평가 기준 `meta.rubric_id` → 평가 결과 `rubric_id` | 대본 · LLM 설정 · 스키마가 바뀌면 달라진다. 평가 결과가 어느 기준으로 채점됐는지 가리킨다 |

- 필드 이름은 모두 snake_case입니다.
- 위치(`span`)는 모두 `[시작, 끝)` 글자 위치이고, 별도 표시가 없으면 **정규화 텍스트** 기준입니다. 원본 STT 기준은 `stt_raw_span` 하나뿐입니다.
- 시각(`created_at`)은 UTC ISO 8601 문자열입니다.
