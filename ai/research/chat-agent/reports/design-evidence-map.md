# 대화 에이전트 설계 · 증거 지도 (`chat_agent-0.12.0`)

한 쪽으로 "무엇을 왜 모델이 정하고 무엇을 규칙으로 두었는지 → 그 코드 → 실행 로그 → 시험 → 측정"을 잇는다. 기준은 기능 문서 [`docs/ai/features/chat-agent.md`](../../../../docs/ai/features/chat-agent.md)(절 번호), 코드는 서비스 `ai/service/src/pitch_coach_ai/features/chat_agent/`, 시험 목차는 [연구 README '기준표 대응 시험 목차'](../README.md), 측정은 [연구 README 9회차](../README.md)와 [독립 최종 평가](../evals/final/README.md).

## 1. 문제 → 왜 에이전트인가
- 사용자는 Take 결과(코치)와 리뷰를 보고 "무엇이 계속 막히는지 · 다음에 무엇을 연습할지"를 스스로 판단해야 한다. 질문마다 읽을 기록(어느 Take · 장 · 지표 · 리뷰 차원)과 순서가 다르고, 기록 상태(비교 불가 · 리뷰 전 · 기록 없음 · 기본 자료에 없음)에 따라 다음 할 일이 바뀐다.
- 그래서 **판단(무엇을 읽고 무엇을 할지)은 모델이, 사실 · 판정 · 안전은 코드가** 정한다(4-3 경계표).
- 증거의 범위: 제한된 고정 순서 기준선(정해 둔 도구 순서만, 코드 먼저 읽기도 끔)보다 과제 성공률이 높았다(평가셋 0.84 > 0.76, 독립 세트 0.650 > 0.394). 질문별 규칙 라우터 같은 다른 대안 대비 효과와 사용자의 시간 절감은 재지 않았다.

## 2. 에이전트 / 규칙 경계 — 기능 문서 4-3
| 무엇 | 누가 | 코드 |
|---|---|---|
| route · 할 일의 목적 · 다음 도구 · 상황 신호에 대한 대응 · 답 문장 | 모델 | `loop.py` `plan_phase` · `research_phase`, 조사 데이터 칸 `signals` |
| 할 일 단계의 실행 · 결과 표시와 상태, 신호 감지, 결정 로그 | 코드 | `progress.py` |
| 비교 · 목표 · 주장 판정, 결론 · 성공 문장, 답 검사, '없음' 근거, 끝 상태 | 코드 | `goals.py` · `claims.py` · `compose.py` · `checks.py` · `tools/impl.py` · `turn.py` |
| 자율 등급 · 승인 · 인자 해시 · 서명 상태 · 한도 | 코드 | `actions.py` · `state.py` · `budget.py` |

## 3. 오케스트레이션 — 누가 결정 · 실행 · 검증
코치 · 리뷰는 BE가 부르는 전문 에이전트다. 대화 에이전트는 결과를 도구로 읽고(`get_take_report` 요약의 '코치가 찾은 것' · '리뷰 분석'), 필요한 일은 행동으로 BE에 시킨다(`FETCH_TAKE` AUTO · `REASSESS` · `SAVE_GOAL` ASK · `START_PRACTICE` BUTTON). 대화가 판단 → BE가 실행(ASK는 사용자 승인 뒤) → 서명 상태로 이어 부름 → 대화가 결과를 검증(행동 결과 짝 · 판 · `args_hash`)하고 계획을 바꾼다. 하위 에이전트는 두지 않았다(4-3 대응표).

## 4. 판단 지점이 보이는 곳 — 실행 로그 발췌(최종 코드 `e5dee8cf`, [`run-logs/`](run-logs/))
| 장면 | 화면 할 일 단계(마지막 plan 줄) | 결정 로그 `agent_decision` | 파일 |
|---|---|---|---|
| 기본 자료에 없음 → 가져오기, 비교 불가 → 대응 | `기록 가져오기 → 가져옴` · `두 Take의 관객 시선 기록을 비교한다: 비교 → 비교 불가(대본 버전 다름)` | NOT_LOADED ACTION CODE → NOT_COMPARABLE FACT_SHOWN CODE → NONE LLM | [path-nc-script-alt](run-logs/path-nc-script-alt.md) |
| 읽은 범위에 기록 없음(Take 0개) | `최근 발표 기록을 확인한다: Take 목록 → Take 없음` | NO_RECORD FACT_SHOWN CODE | [path-norec-latest-no-take](run-logs/path-norec-latest-no-take.md) |
| 리뷰 없음 → 코치 결과로 | `기록 보기: Take 5 기록 → 리뷰 분석 전` → `기록 보기: Take 5 기록 → 문제 구간 있음` | REVIEW_MISSING FACT_SHOWN CODE → ALT_TOOL LLM | [path-rm-score-ask](run-logs/path-rm-score-ask.md) |
| 기본 자료에 없음 → 가져와 읽기 | `… 기록 가져오기 → 가져옴` · `기록 보기: Take 3 기록 → 확인` | NOT_LOADED ACTION CODE | [path-nl-fetch-read](run-logs/path-nl-fetch-read.md) |
| 사용자 이의 → 다시 평가(승인 → 재개) | `기록 보기: Take 4 기록 → 리뷰 우선 개선 대본 의존도` | USER_DISPUTE ACTION LLM | [path-ud-script-dispute](run-logs/path-ud-script-dispute.md) |
| 시간 부족 → 부분 결과 | 실행한 단계는 모두 `DONE`, 끝 상태 `PARTIAL`(이 발췌는 채점 실패 — 남은 일이 `TODO`로 남지 않음) | 이 발췌에는 없음. 최종 경로 측정 3번 중 2번은 BUDGET_LOW STOP_PARTIAL CODE | [path-fail-budget-partial](run-logs/path-fail-budget-partial.md) |
| 목표 저장(승인) → 연습 준비 | `해당 Take의 장별 시선 기록 확인: Take 5 기록 → 3장 확인` | — | [rep-save-goal](run-logs/rep-save-goal.md) |

로그에 보이는 표시 한계: 계획 때 만든 단계가 실행 뒤 덧붙은 단계에 일을 넘기고 `건너뜀`으로 남는 경우가 있고(path-rm-score-ask), 계획이 낸 자료 검색어마다 단계가 하나씩 생겨 같은 글이 되풀이될 수 있다(path-fail-budget-partial). 또 조사가 스스로 끝난 뒤 답 쓰기에서 시간이 모자라면 끝 상태는 `PARTIAL`(진단 기록의 끝난 이유 `TIME`)이지만 결정 로그 `BUDGET_LOW`와 남은 일 `TODO`가 남지 않는다(같은 발췌 — 조사 중에 시간이 다 된 측정 2번에는 남았다). 발췌는 합성 자료로 한 번씩 돈 실행이고, 채점 결과(통과 · 실패)는 각 파일 머리에 있다.

## 5. 증거
| 기준표 | 증거 | 결과 | 위치 |
|---|---|---|---|
| ⑤ 단계 분기 · 계획, ⑥ 도구 선택 · 계획 변경 평가 | 경로 채점(사건 순서 · 여러 유효 순서) · 경로 세트 24 | 경로 정확도 0.78 · 재계획 적합률 0.92(고정 순서 0.41 · 0.69) | 연구 README 9회차 |
| ③ 쓸모 · ⑥ 독립 평가 | 독립 최종 평가 60(맥락 분리 작성 · 개선 전 동결 · 한 번) | 0.650 · pass^3 0.617 · 경로 0.878 · 재계획 0.877 — **합격선 미달**(고치기 전 0.528, 고정 순서 0.394) | [`evals/final/`](../evals/final/README.md) |
| ⑧ 통합 · E2E | 가짜 모델 경로 통합 시험(신호별 · 계획 고치기 · 실패 · 재개 · 계약) · 실제 FE · BE · DB E2E 6 | 통과(서비스 4028 · 연구 370 · E2E 6/6) | 시험 목차 · [`e2e/`](../e2e/README.md) |
| ⑤ 결과 → 결정 로그 | `agent_decision`(코드 enum · 번호만, 서비스 로그) | 4절 발췌 | 기능 문서 11-5 |
| ② · ⑦ 품질 · 가드레일 | 평가셋 · 안전 · 객관성 · 서비스 세트 × 3 | 평가셋 0.84(목표 0.90 미달) · 안전 21/23 · 객관성 0.98 · 금지 0 · 근거성 0.95(사람 보정 전) | 연구 README 9회차 |
| 성능 | 단계별 시간 · 호출 · 토큰(`perf.py`) | 일반 턴 중간 10.0초 · 상위 5% 19.9초, 다시 평가 상위 5% 21.5초 | 연구 README 9회차 |

## 6. 이 판에 없는 증거
배포(dev)에서의 반복 완주(대표 경로 × 여러 번) · 근거성 판정의 사람 보정 · 사용자 시험(수작업 대비 단계 · 시간) · 실제 모델을 쓴 화면 시연 · 배포 부하에서 기존 기능 영향. (독립 세트의 기대값 사람 검수는 팀원이 문항별로 대조해 끝났다 — `evals/final/REVIEW.md`.)
