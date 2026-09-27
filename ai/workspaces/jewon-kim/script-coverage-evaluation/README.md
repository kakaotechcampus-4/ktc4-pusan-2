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
[대본 JSON]  ──▶ ① 대본 분석 (대본 등록·수정 시 1번, 슬라이드당 LLM 2회) ──▶ 평가 기준 (DB)
                                                                               │
[슬라이드별 STT] ──▶ ② STT 평가 (연습마다, 슬라이드당 LLM 1회 + 필요할 때 1회) ◀──┘
                                    │
                                    ▼
                  평가 결과 + 비슷한 말 위치 (DB) ──▶ 코칭(리뷰) agent
```

- **① 대본 분석** — 규칙으로 수치·이름을 뽑고, LLM 이 문장 역할·핵심 주장·Key Point 를 정한 뒤, LLM 이 한 번 더 최종 결론을 냅니다.
- **② STT 평가** — 규칙으로 정렬·수치 검증·비슷한 말 찾기를 하고, LLM 이 **대본 문장마다** 전달 여부를 판정합니다.
  규칙과 LLM 이 어긋난 문장만 LLM 이 다시 보고, 점수는 코드가 계산합니다.

---

## 다른 프로젝트가 이 프로젝트를 쓰는 법

소비자(코칭·리뷰 agent)가 받는 것은 연습 한 번의 **슬라이드별 평가 결과**와 **비슷한 말 목록**입니다.

```json
{
  "take_id": "가상대본1_take9",
  "slide_number": 6,
  "scores": {"content_coverage": 1.0, "critical_fact_accuracy": 1.0, "script_fidelity": 1.0,
             "similar_words": 1, "similar_numbers": 0},
  "sentences": [
    {"sentence_index": 0, "text": "2025년 3월부터 8주 동안 제휴 도서관 3곳에서 시범 운영을 했습니다.",
     "status": "said", "evidence": [0], "reason": "…핵심 내용과 수치가 모두 전달되었습니다.", "verified": false}
  ],
  "key_points": [{"key_point_id": "KP1", "importance": "normal", "status": "covered", "sentence_indices": [0]}],
  "similar_items": [
    {"item_id": "R1", "kind": "word", "script_text": "노쇼", "stt_text": "노소",
     "stt_sentence_index": 3, "stt_raw_span": [148, 150], "key_point_ids": ["KP4"]}
  ]
}
```

- `sentences` · `key_points` 의 판정은 근거 STT 문장 번호(`evidence`)와 이유를 함께 줍니다. 사용자에게 지적할 때는 근거 문장을 인용하세요.
- `similar_items` 는 **판단을 보류한 항목**입니다 — 발표자가 잘못 말했는지 음성 인식이 잘못 적었는지 텍스트만으로는 알 수 없어,
  점수 비율에서 빼고 위치만 남겼습니다. 녹음이나 발표자 확인으로 판단하는 것은 소비자의 몫입니다.

> ⚠️ **아직 서비스 API가 없습니다.** 지금은 노트북이 로컬 SQLite(`v1/local/outputs/rubrics.sqlite`)에 씁니다.
> 소비자는 이 DB의 `slide_evaluations` · `similar_items` 테이블을 읽습니다. 서비스에 붙이는 형태는 `v1/deploy/`에서 정해질 예정입니다.

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
