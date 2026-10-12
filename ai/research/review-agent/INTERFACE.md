# 인터페이스 — BE ↔ 리뷰

BE가 리뷰를 부르는 호출의 입력 · 출력입니다. 모양의 기준은 `src/review/schemas.py`이고, 이 문서는 그것을 사람이 읽기 좋게 풀어 쓴 것입니다.
실제 값은 실행 가능한 예시로 확인합니다: `src/review/examples/review/request.json` → `response.json` (계약 테스트가 리뷰에 넣어 응답과 맞는지 봅니다).

| 호출 | 코어 함수 | 언제 |
|---|---|---|
| `POST /takes/review` | `review_take(request)` | Take 종료 뒤 대본 전달도 채점 · 확인이 끝나면 한 번 (백그라운드) |

공통 규칙

- 필드 이름은 snake_case, 시각은 모두 **Take 시작 기준 ms**입니다.
- 요청은 모르는 필드를 무시합니다 (BE가 필드를 먼저 추가해도 깨지지 않음). 응답은 정해진 필드만 냅니다.
- 리뷰는 아무것도 저장하지 않고 지표를 다시 계산하지 않습니다. 같은 요청에는 같은 응답이 나옵니다.
- 숫자 기준값은 응답에 싣지 않습니다. `meta.criteria_versions.review`(`review-1.0+<설정 해시 12자리>`)가 어떤 설정으로 판단했는지 남깁니다.

---

## 1. 요청

| 필드 | 필수 | 뜻 |
|---|---|---|
| `take_result` | O | 코치 Take 결과 (`/coach/finalize`). 리뷰는 `take_id` · `areas` · `problem_segments` · `gave_up`만 읽습니다 |
| `script_coverage` | | 대본 전달도 채점 (`/takes/rescore` 결과, 확인하지 않았으면 `/takes/analyze` 결과). 모든 장이 실패했으면 `null`. 리뷰는 `take_scores` · `evaluations[].{slide_number, scores, key_points}` · `failed`만 읽습니다 |
| `previous_takes` | | 이전 Take (최근 3개). **1차는 받기만 하고 쓰지 않습니다** |
| `previous_recurring_issues` | | 직전 리뷰의 `recurring_issues` 그대로. `dimension`이 있으면 그 차원, 없으면 `area`의 차원 전부를 반복 문제로 봅니다 |
| `missions` | | 이번 Take 미션 `[{mission_id, area, slide_number, target{metric, operator, value}}]`. `slide_number`가 `null`이면 Take 전체 |
| `user_criteria` | | 사용자 평가 기준 `{display_criteria, targets[{display, metric, operator, value}], script_used}`. 숫자 목표가 없는 기준은 판정하지 않습니다 |

리스트 필드와 `user_criteria`는 `null`이어도 됩니다(빈 것으로 봅니다).

### 읽는 Take 결과 필드

| 필드 | 쓰는 곳 |
|---|---|
| `areas.<영역>.measured_ratio` | 판단 가능 여부, 미션 판정 |
| `areas.<영역>.take.<지표>` · `slides[].{slide_number, measured_ratio, metrics}` | 미션 판정, 다음 미션의 지금 값, 근거(`evidence`) |
| `problem_segments[].{area, issue_type, slide_number, start_ms, end_ms, mean_severity, reliable}` | 문제 부담. 길이는 코치가 되돌린 구간 |
| `gave_up[].issue_type` | 부담 가중 |

## 2. 응답

```json
{
  "take_id": "take-123",
  "overall_summary": "좋았던 점은 청중 응시, 말 속도와 시간 배분, 목소리 크기입니다. 먼저 다듬을 점은 3번 장 대본 의존도입니다.",
  "score": 82,
  "dimensions": {"audience_gaze": "STRENGTH", "script_independence": "PRIORITY", "pacing_rhythm": "STRENGTH",
                 "vocal_delivery": "STRENGTH", "fluency": "STABLE", "content_delivery": "NOT_EVALUABLE"},
  "strengths": [{"area": "GAZE", "text": "청중을 고르게 바라봤습니다", "evidence": {"audience_ratio": 0.58}}],
  "improvements": [{"area": "GAZE", "slide_number": 3,
                    "text": "3번 장에서 대본을 이어 읽기보다 문장 끝에서 고개를 들어 보세요",
                    "evidence": {"script_ratio": 0.62}}],
  "comparison": [],
  "mission_results": [{"mission_id": "m-1", "status": "PARTIAL", "metric": "filler_per_min", "operator": "LTE",
                       "slide_number": null, "target": 2, "observed": 2.4}],
  "criteria_results": [{"display": "추임새 5번 이하", "metric": "filler_count", "operator": "LTE",
                        "target": 5, "observed": 7, "status": "FAILED"}],
  "next_missions": [
    {"area": "FILLER", "slide_number": null, "description": "추임새 5번 이하",
     "target": {"metric": "filler_count", "operator": "LTE", "value": 5}, "priority": 1,
     "reason": "직접 정한 기준인데 이번 Take 7"},
    {"area": "GAZE", "slide_number": 3, "description": "3번 장 대본 응시 비율 52% 이하",
     "target": {"metric": "script_ratio", "operator": "LTE", "value": 0.52}, "priority": 2,
     "reason": "3번 장 이번 Take 0.62"}
  ],
  "recurring_issues": [{"area": "GAZE", "slide_number": 3, "dimension": "script_independence"}],
  "fallback_reason": "LLM_UNAVAILABLE",
  "meta": {"schema_version": "1.0", "feature_version": "review-1.0",
           "criteria_versions": {"review": "review-1.0+<설정 해시>"}, "model": null}
}
```

(줄여 적었습니다. 전체는 `src/review/examples/review/response.json`)

| 필드 | 뜻 · 받는 곳 |
|---|---|
| `overall_summary` · `score` | 리포트. `take_summaries.summary` · `score`. 평가할 차원이 없으면 `score`는 `null` |
| `dimensions` | 차원 6개의 상태: `IMPROVED` · `PRIORITY` · `STABLE` · `STRENGTH` · `NOT_EVALUABLE` |
| `strengths` · `improvements` | 리포트 문장. 문장 안 숫자는 장 번호뿐이고 지표 값은 `evidence`에 입력 그대로 |
| `comparison` | 이전 Take 비교. **1차는 늘 `[]`** |
| `mission_results` · `criteria_results` | 상태: `ACHIEVED` · `PARTIAL` · `FAILED` · `NOT_EVALUABLE` |
| `next_missions` | 다음 Take의 `missions`. `mission_id`는 없으니 BE가 저장하며 붙이고, 다음 요청에는 `{mission_id, area, slide_number, target}`만 싣습니다. `target`은 Take 결과의 지표 이름을 그대로 씁니다 |
| `recurring_issues` | 다음 Take의 `recurring_issues`(코칭 계획 · 코치)와 다음 리뷰의 `previous_recurring_issues`. **`dimension`은 명세에 없는 필드**입니다 (README 5절). 코치는 `area` · `slide_number`만 읽으면 됩니다 |
| `fallback_reason` | LLM을 쓰지 못했을 때의 원인. 1차는 LLM 단계가 없어 늘 `LLM_UNAVAILABLE` |
| `meta` | `schema_version` · `feature_version` · `criteria_versions.review` · `model`(1차는 `null`). 결과와 함께 저장합니다 |

## 3. 실패

| 상황 | 동작 |
|---|---|
| 요청 형식 오류 | pydantic `ValidationError` → 라우터가 **422** `VALIDATION_ERROR` |
| 측정 부족 · 대본 전달도 없음 | 오류가 아닙니다. 그 차원 · 미션이 `NOT_EVALUABLE` |
| `key_points.importance`가 없거나 모르는 값 | 오류가 아닙니다. normal로 계산합니다 |
| 리뷰 안의 예외 | 같은 입력이면 같은 예외가 납니다. BE가 한 번 다시 부르고, 그래도 실패하면 리포트에 결과 없음을 보이고 다음 Take는 이전 미션 · 반복 문제로 시작합니다 (명세 7절) |
