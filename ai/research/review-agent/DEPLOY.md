# 배포 가이드 — 리뷰

리뷰 코어(`src/review/`)를 AI 서버(`ai/service/`)의 `features/review/`로 옮겨 BE가 호출하게 할 때 필요한 일을 정리합니다.
아직 서버는 없고, 코어를 코드 변경 없이 폴더째 옮기는 것을 전제로 합니다. 요청 · 응답 필드는 [INTERFACE.md](INTERFACE.md)에 있습니다.

## 배포 구성

```
Take 종료 → (BE) /coach/finalize → /takes/analyze [→ 확인 → /takes/rescore]
        → BE ── POST /takes/review ──▶ AI (상태 없음, 1차는 LLM 없음)
          BE ◀── 리뷰 결과 · meta ──── AI
        → BE: take_ai_results(REVIEW) · missions · take_summaries 저장 → /coach/plan
```

- **AI 서버는 아무것도 저장하지 않습니다.** 이전 Take의 반복 문제 · 미션은 BE가 저장해 두었다가 요청에 싣습니다.
- **같은 요청에는 같은 응답이 나옵니다.** 1차는 LLM을 쓰지 않습니다.

## 옮기기

| 가져가는 것 | 가져가지 않는 것 |
|---|---|
| `src/review/` 폴더 전부 (`examples/` 포함) | 없음 (실험 도구가 아직 없다) |
| 의존성은 `pydantic` 하나 | |

- 코어 안은 **상대 import**뿐이라 폴더 이름이 바뀌어도 코드를 고치지 않습니다.
- 코어는 **파일 · DB · 네트워크 · 환경변수 · 시계 · 난수를 쓰지 않습니다.**
- 테스트(`tests/unit/`)도 옮깁니다. import의 패키지 이름만 새 이름으로 바꾸고, `test_core_boundary.py`와 `conftest.py`의 코어 폴더 경로(`parents[2] / "src" / "review"`)를 새 위치에 맞춥니다.

## 라우터가 할 일

| 할 일 | 설명 |
|---|---|
| 호출 | `POST /takes/review` → `review_take(body)`. 응답은 `.model_dump(mode="json")` |
| 오류 매핑 | 요청 형식 오류(pydantic `ValidationError`)는 **422** `VALIDATION_ERROR`. 그 밖의 예외는 500 (BE가 한 번 다시 부릅니다) |
| 설정 | 기본 설정을 씁니다. 바꿀 때는 `load_config(...)`로 만든 설정을 앱 시작 때 한 번 만들어 넘깁니다 (`review_take(body, cfg)`) |

## BE가 맞춰야 할 것

- **`recurring_issues`를 그대로 넘겨 주세요.** 리뷰 응답의 `recurring_issues`(`dimension` 포함)를 저장했다가 다음 리뷰 요청의 `previous_recurring_issues`에 **필드를 고르지 말고 그대로** 싣습니다. `dimension`이 빠지면 GAZE 차원을 잘못 판정할 수 있습니다. 코치 요청에는 그대로 실어도 됩니다(코치는 모르는 필드를 무시합니다).
- **`next_missions`에 `mission_id`를 붙여** `missions`에 저장합니다.
- **`meta`와 함께 저장합니다.** 어떤 버전 · 설정으로 판단했는지 추적하기 위해서입니다.
