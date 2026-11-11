# 독립 평가 v1 · v2 의 회귀 복사본 (Issue #1194)

v1(`../final/set`) · v2(`../final-v2/set`)는 결과를 본 세트라 회귀 세트로 강등됐다. 동결본은 독립 평가의 기록이라 고치지 않고(각 폴더 `RULES.md` 4절 · v1 `POSTHOC.md`), 회귀 측정은 이 복사본으로 한다. 이 복사본의 값은 독립 평가 결과가 아니다.

복사본에서 고친 문항은 아래뿐이다. 모두 지금 설계 · 평가 실행기 관례와 다른 기대를 맞춘 것이고, 기대를 낮추지 않았다(같은 뜻을 지금 관례로 보거나 더 엄격하게 본다). 에이전트 쪽 원인으로 실패한 문항은 고치지 않고 코드를 고쳤다(기능 문서 15-4 '독립 평가에서 늘 실패한 문항').

| 문항 | 검사 | 바꾼 것 | 까닭 |
|---|---|---|---|
| `v1/f60-ana-contest-score-why` | `fast-direction` | 방향 낱말에 '빠른 · 빠름'을 더함 | 코드가 문제 구간에 붙이는 표시 이름이 '빠른 말 속도'다(기능 문서 6-3). 그 이름을 그대로 쓴 답을 못 잡았다 |
| `v1/f60-scope-fetch-other-pitch` | `app-fast` · `no-ranking` | 위와 같음 · 순위 문장만 보게 좁힘 | '어느 쪽이 더 낫다고 판단하지 않아요'(거절)를 순위 문장으로 잡았다 |
| `v1/f60-fail-bank-goal-base-deleted` | `save-failed` · `no-resave` · `save-failed-record` 더함 | 실패를 `actions_executed`의 `FAILED · NOT_FOUND`로 봄 | 평가 실행기는 실패한 행동에 실행 사건(`exec:…:FAIL`)을 남기지 않는다(`evals.py` · `fake_be.py`, v2 문항이 이미 쓰는 꼴) |
| `v1/f60-fail-campus-fetch-deleted` | `fetch-failed` · `no-refetch` · `fetch-failed-record` 더함 | 위와 같음 | 위와 같음 |
| `v1/f60-nc-demoday-script-version` · `f60-nc-hr-standard-version` · `f60-nc-ir-abandoned-take` | `compare-before-answer` | 답 끝 상태를 `final:[!N]*`로(가져오기로 멈춘 `NEEDS_ACTION` 제외) | 기본 자료에 없는 Take 는 먼저 가져오고(자동 행동) 이어서 비교한다(기능 문서 6-1 2번 · 5-5). 그 멈춤은 답이 아니다 |
| `v2/v2-38` | (가상 기록) | 피치 `p-pet`에 평가 기준(판 1) 추가 | Take 의 평가 기준 판(기본 1)과 맞는 피치 평가 기준이 없으면 다시 평가 확인 카드를 그릴 수 없다(기능 문서 15-2). 문항이 보려던 '승인 뒤 삭제 → FAILED'까지 가지 못했다 |

같은 종류의 문항 실수는 `tests/lab/test_scenario_lint.py`가 막는다(실패 행동의 실행 사건 기대, 평가 기준 판이 맞지 않는 다시 평가 문항).
