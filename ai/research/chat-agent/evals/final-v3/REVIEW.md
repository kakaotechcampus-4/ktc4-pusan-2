# 독립 평가 v3 — 작성 · 검수 기록 (동결 때는 개수만)

봉인 경계(RULES 3절)에 따라 동결 때는 실행 여부 · 개수만 적는다. 지적 내용 · 문항별 처리 상세는 저장소 밖 보관 폴더에 두고 최종 실행 뒤 이 파일에 옮긴다.

| 작성자 | 문항 | 묶음 | 보조 검수 | 지적 → 처리 |
|---|---|---|---|---|
| 작성 에이전트 A | `v3-01` ~ `v3-20` | representative 4 · pitch_analysis 4 · global_scope 4 · not_comparable 3 · stale 2 · autonomy 2 · guide 1 | 작성자와 다른 보조 검수 모델, 한 번(`exit=0`) | 17 → 고침 5 · 뺀 검사 4 · 그대로 8(형식 한계로 바꿀 수 없는 가상 기록 칸 2 — 그 칸을 보는 검사 없음, 검사 범위 한계 4 — 기대값은 맞음, 자료 원문으로 정답이 정해지는 자료 문항 2) |
| 작성 에이전트 B | `v3-21` ~ `v3-40` | failure 4 · reassess 3 · refusal 4 · injection_abuse 2 · permission 1 · tampering 1 · multi_turn 1 · wrong_claim 1 · pressure 1 · approval 1 · small_talk 1 | 같음(`exit=0`) | 16 → 고침 10 · 뺀 검사 6 |

- 경로 검사(`observe: "trajectory"` · `metric: "path"`)를 둔 문항: A 19 · B 20 (RULES 2절 25개 이상).
- 형식 검사(`holdout check`): 40/40 통과. 유사도(기존 문항 네 폴더와): 40문항 모두 PASS, FLAG 0(B 는 첫 검사의 FLAG 1 문항을 다시 써서 0).
- 가짜 모델 흐름(`--fake --repeat 1`): 두 묶음 모두 실행기 종료 코드 0.
- 사람 검수: 하지 않았다(RULES 3절). 결과 보고에 "사람 검수 미완(보조 모델 검수만)"을 적는다.
