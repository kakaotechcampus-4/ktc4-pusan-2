# script-coverage-evaluation

발표 연습 코칭 서비스 **Pitch Coach**의 대본 전달 평가 프로젝트입니다.

발표자가 슬라이드 대본의 내용을 **실제로 얼마나 전달했는지** 채점합니다.
대본을 한 번 분석해 슬라이드별 **평가 기준(Evaluation Rubric)** 을 만들어 두고,
발표 연습마다 음성 인식 결과(STT)를 그 기준과 비교해 **대본 문장 · Key Point · 핵심 수치** 단위로 판정합니다.

> 위치: `workspaces/jewon-kim/script-coverage-evaluation/`
> 워크스페이스 규칙(버전 축, `local`/`deploy`, 문서 범위)은 [../README.md](../README.md)에 있습니다.

---

## 폴더 구조

```
script-coverage-evaluation/
├── README.md          ← 이 문서: 프로젝트가 무엇이고 어디서 시작하나
│
└── v1/
    ├── README.md      # v1 모델의 전체 기술 레퍼런스 (local/deploy 공유)
    ├── local/         # 실험·평가 리그 (Jupyter 노트북 2개)  ← 지금 코드는 전부 여기
    │   └── README.md
    └── deploy/        # 서비스 투입용 패키징 (아직 없음)
```

`v1`은 **모델 버전**이지 릴리스 버전이 아닙니다. 제품으로 나갈 때는
`releases/vX.Y.Z/script-coverage-evaluation/`에 묶이며, 두 축은 독립적으로 움직입니다
([워크스페이스 README](../README.md) 참고).

---

## 어디서 시작하나

| 하려는 일 | 위치 |
|---|---|
| **노트북 돌려보기 · 개발하기** | [v1/local/README.md](v1/local/README.md) |
| 파이프라인을 이해하기 · v1의 범위와 현재 상태 | [v1/README.md](v1/README.md) — 전체 기술 레퍼런스 |

---

## 두 단계로 동작합니다

```
[대본 JSON]  ──▶ ① 대본 분석 (대본 등록·수정 시 1번, 슬라이드당 LLM 3회) ──▶ 평가 기준 (DB)
                                                                               │
[슬라이드별 STT] ──▶ ② STT 평가 (연습마다, 슬라이드당 LLM 1회 + 필요할 때 1회) ◀──┘
                                    │
                                    ▼
                  평가 결과 + 비슷한 말 위치 (DB) ──▶ 리뷰 agent ──▶ 사용자 확인
                                    ▲                                    │
                                    └──── 점수 다시 계산 (코드, LLM 없음) ◀─┘
```

- **① 대본 분석** — 규칙으로 수치·이름을 뽑고, LLM 이 문장 역할·핵심 주장·Key Point 를 정한 뒤, LLM 이 한 번 더 최종 결론을 냅니다.
  마지막으로 문장마다 **전달 단위**(주장·사실·수치·나열 항목 하나)를 나눠 둡니다.
- **② STT 평가** — 규칙으로 정렬·수치 검증·비슷한 말 찾기를 하고, LLM 이 **전달 단위마다** 말했는지 판정하면 코드가 대본 문장 판정으로 모읍니다.
  규칙과 LLM 이 어긋난 문장만 LLM 이 다시 보고, 점수는 코드가 계산합니다.
- **발음이 비슷한 말**(`노쇼` → `노조`)은 인식 오류인지 발표자가 실제로 다르게 말했는지 텍스트로는 알 수 없어 점수에서 빼 둡니다.
  리뷰 agent 가 사용자에게 확인하면 그 답으로 점수를 다시 계산합니다.

---

## 다른 프로젝트가 이 프로젝트를 쓰는 법

소비자(리뷰 agent)가 받는 것은 연습 한 번의 **슬라이드별 평가 결과**와 **비슷한 말 목록**이고, 돌려주는 것은 **비슷한 말에 대한 사용자 답**입니다.

```json
{
  "take_id": "가상대본1_take9",
  "slide_number": 6,
  "scores": {"content_coverage": 1.0, "critical_fact_accuracy": 1.0, "script_fidelity": 1.0,
             "similar_words": 1, "similar_numbers": 0},
  "sentences": [
    {"sentence_index": 0, "text": "2025년 3월부터 8주 동안 제휴 도서관 3곳에서 시범 운영을 했습니다.",
     "status": "said", "evidence": [0], "reason": "…핵심 내용과 수치가 모두 전달되었습니다.", "verified": false,
     "units": [{"unit_id": "S0-U1", "text": "2025년 3월부터 시범 운영을 함", "status": "said"}]}
  ],
  "key_points": [{"key_point_id": "KP1", "importance": "normal", "status": "covered", "sentence_indices": [0]}],
  "similar_items": [
    {"item_id": "R1", "kind": "word", "script_text": "노쇼", "stt_text": "노소",
     "stt_sentence_index": 3, "stt_raw_span": [148, 150], "key_point_ids": ["KP4"]}
  ]
}
```

- `sentences` · `key_points` 의 판정은 근거 STT 문장 번호(`evidence`)와 이유를 함께 줍니다. 사용자에게 지적할 때는 근거 문장을 인용하세요.
- `units` 는 문장 판정의 근거가 된 전달 단위별 판정입니다. "시험 기간과 날씨를 빠뜨렸다"처럼 빠진 정보를 짚을 때 쓰세요.
- `similar_items` 는 **판단을 보류한 항목**입니다 — 발표자가 잘못 말했는지 음성 인식이 잘못 적었는지 텍스트만으로는 알 수 없어,
  점수 비율에서 빼고 위치만 남겼습니다. 리뷰 agent 가 위치(→ 녹음 구간)를 보여 주며 사용자에게 확인하고,
  `confirm_similar_item(take_id, slide, item_id, "as_script" | "as_stt")` 으로 답을 넘기면 그 슬라이드의 판정과 점수를 다시 계산합니다.
  최종 점수는 `confirmed_evaluation` · `confirmed_take_scores` 로 읽습니다 (확인한 답이 없으면 처음 채점과 같음).

> ⚠️ **아직 서비스 API가 없습니다.** 지금은 노트북이 로컬 SQLite(`v1/local/outputs/rubrics.sqlite`)에 씁니다.
> 소비자는 이 DB의 `slide_evaluations` · `similar_items` 를 읽고, 사용자 답은 `similar_confirmations` 에, 다시 계산한 결과는 `confirmed_evaluations` 에 남습니다. 서비스에 붙이는 형태는 `v1/deploy/`에서 정해질 예정입니다.

각 필드의 의미는 [v1/README.md](v1/README.md)의 §2를 보세요.

---

## 현재 상태

**v1 / local만 존재합니다.**

| | |
|---|---|
| ✅ | 두 노트북 전 구간 동작 · 가상 데이터(대본 2개, 발표 연습 18개)로 정답 비교와 반복 채점까지 완료 |
| ⚠️ | **실제 발표 녹음·Deepgram 결과가 없어 실제 정확도는 측정되지 않았습니다** — 모든 수치는 가상 데이터 기준 |
| ⬜ | `v1/deploy` 미작성 · 테스트 코드와 CI 없음 |

가장 큰 공백은 실제 데이터입니다.
"v1을 완료라고 부르려면 무엇이 필요한가"는 [v1/README.md](v1/README.md)에 있습니다.
