# 대화 에이전트 설계 · 증거 지도 (`chat_agent-0.13.0`)

한 쪽으로 "무엇을 왜 모델이 정하고 무엇을 규칙으로 두었는지 → 그 코드 → 실행 로그 → 시험 → 측정"을 잇는다. 기준은 기능 문서 [`docs/ai/features/chat-agent.md`](../../../../docs/ai/features/chat-agent.md)(절 번호)와 테크스펙 [`chat-agent-techspec.md`](../../../../docs/ai/chat-agent-techspec.md), 코드는 서비스 `ai/service/src/pitch_coach_ai/features/chat_agent/`, 시험 목차는 [연구 README '기준표 대응 시험 목차'](../README.md), 측정은 [연구 README 10회차](../README.md)와 [독립 평가 v2](../evals/final-v2/README.md).

## 1. 문제 → 왜 에이전트인가
- 사용자는 Take 결과(코치)와 리뷰를 보고 "무엇이 계속 막히는지 · 다음에 무엇을 연습할지"를 스스로 판단해야 한다. 질문마다 읽을 기록(어느 Take · 장 · 지표 · 리뷰 차원)과 순서가 다르고, 기록 상태(비교 불가 · 리뷰 전 · 기록 없음 · 기본 자료에 없음)에 따라 다음 할 일이 바뀐다.
- 그래서 **판단(무엇을 읽고 무엇을 할지)은 모델이, 사실 · 판정 · 안전은 코드가** 정한다(4-3 경계표). 실행 기록에서 대응이 늘 같았던 신호는 코드로 옮겼다(아래 2절 표 '결정 비율').
- 증거의 범위: 제한된 고정 순서 기준선보다 과제 성공률이 높았다(9회차 평가셋 0.84 > 0.76, 독립 세트 v1 0.650 > 0.394, v2는 [`evals/final-v2/`](../evals/final-v2/README.md)). 질문별 규칙 라우터 같은 다른 대안 대비 효과와 사용자의 시간 절감은 재지 않았다.

## 2. 에이전트 / 규칙 경계 — 기능 문서 4-3
| 무엇 | 누가 | 코드 |
|---|---|---|
| route · 할 일의 목적 · 다음 도구 · 비교 불가 · 기록 없음 · 도구 실패 · 사용자 이의에 대한 대응 · 답 문장 | 모델 | `loop.py` `plan_phase` · `research_phase`, 조사 데이터 칸 `signals` |
| 리뷰 없음 → 코치 기록, 기본 자료에 없음 → 가져오기, 가장 최근 발표의 대상, 한도로 멈춤 | 코드 | `loop.py` `_code_signal` · `_latest_prefetch_calls`, `progress.py` |
| 할 일 단계의 실행 · 결과 표시와 상태, 신호 감지, 결정 로그 | 코드 | `progress.py` |
| 비교 · 목표 · 주장 판정, 결론 · 성공 문장, 답 검사, '없음' 근거, 요청 해소 · 끝 상태 | 코드 | `goals.py` · `claims.py` · `compose.py` · `checks.py` · `tools/impl.py` · `turn.py` |
| 한 턴 전체 검사(같은 사실 반복 빼기 · 기준 없는 '확인 불가' 바꾸기) | 코드 | `output_guard.py` |
| '안 된다' 답의 대안 · 다음 걸음 · [이어서 확인] 반복 막기 | 코드 | `guidance.py` |
| 자율 등급 · 승인 · 인자 해시 · 서명 상태 · 한도 | 코드 | `actions.py` · `state.py` · `budget.py` |

**결정 비율**(최종 회귀 재측정 `6f0f4cec` 603실행의 마지막 차례 결정 로그, 사실 표시 `FACT_SHOWN` 제외): 코드 228(가져오기 97 · 한 턴 전체 검사가 뺀 문장 78 · 한도로 멈춤 37 · 리뷰 없음 → 코치 기록 16) · 모델 148(기록 없음 56 · 사용자 이의 36 · 비교 불가 30 · 도구 실패 16 · 가져오기 10). 기록 없음은 코드로 닫았다가 회귀 문항이 늘어 모델에게 되돌렸다([`failure-analysis-1194.md`](failure-analysis-1194.md), Issue #1194 4-G).

## 3. 오케스트레이션 — 누가 결정 · 실행 · 검증
코치 · 리뷰는 BE가 부르는 전문 에이전트다. 대화 에이전트는 결과를 도구로 읽고(`get_take_report` 요약의 '코치가 찾은 것' · '리뷰 분석'), 필요한 일은 행동으로 BE에 시킨다(`FETCH_TAKE` · `FETCH_PITCH` AUTO · `REASSESS` · `SAVE_GOAL` ASK · `START_PRACTICE` BUTTON). 대화가 판단 → BE가 실행(ASK는 사용자 승인 뒤) → 서명 상태로 이어 부름 → 대화가 결과를 검증(행동 결과 짝 · 판 · `args_hash`)하고 계획을 바꾼다. 하위 에이전트는 두지 않았다(4-3 대응표).

## 4. 판단 지점이 보이는 곳 — 실행 로그 발췌([`run-logs/`](run-logs/), 최종 회귀 재측정 `6f0f4cec`의 첫 실행, `runlog_from_run.py`)
| 장면 | 화면 할 일 단계 | 결정 로그 `agent_decision` | 파일 |
|---|---|---|---|
| 기본 자료에 없음 → 가져오기, 비교 불가 → 까닭 | `기록 가져오기 → 가져옴` · `…: 비교 → 비교 불가(대본 버전 다름)` | NOT_LOADED ACTION CODE → NOT_COMPARABLE FACT_SHOWN CODE → NONE LLM | [path-nc-script-alt](run-logs/path-nc-script-alt.md) |
| 읽은 범위에 기록 없음(Take 0개) | `피치 목록 → Take 없음` | NO_RECORD FACT_SHOWN CODE → NONE LLM | [path-norec-latest-no-take](run-logs/path-norec-latest-no-take.md) |
| 리뷰 없음 → 코치 기록(코드) | `…: Take 5 기록 → 리뷰 분석 전` → `리뷰 없어 코치 기록: Take 5 기록 → 확인` | REVIEW_MISSING FACT_SHOWN CODE → ALT_TOOL CODE | [path-rm-score-ask](run-logs/path-rm-score-ask.md) |
| 기본 자료에 없음 → 가져와 읽기 | `기록 가져오기 → 가져옴` · `…: Take 3 기록 → 확인` | NOT_LOADED ACTION CODE | [path-nl-fetch-read](run-logs/path-nl-fetch-read.md) |
| 사용자 이의 → 다시 평가, 한 턴 전체 검사 | `기록 보기: Take 4 기록 → 리뷰 우선 개선 대본 의존도` | USER_DISPUTE ACTION LLM, OUTPUT_CHECK DROPPED CODE × 2 | [path-ud-script-dispute](run-logs/path-ud-script-dispute.md) |
| 한도 → 부분 결과 | `물은 항목 확인: 시간 부족`(남은 일 TODO), 끝 상태 `PARTIAL` | BUDGET_LOW STOP_PARTIAL CODE | [path-fail-budget-partial](run-logs/path-fail-budget-partial.md) |
| 목표 저장(승인) | `…: Take 5 기록 → 3장 확인` | OUTPUT_CHECK DROPPED CODE | [rep-save-goal](run-logs/rep-save-goal.md) |
| 화면에서 본 두 차례 대화(최근 발표 목소리 → 크게 말하는 법) | 차례 2 `…: Take 2 기록 → 문제 구간 있음` · `자료 찾기 → 자료 있음` | — | [path-voice-followup](run-logs/path-voice-followup.md) |
| 화면에서 본 두 차례 대화(… → '대본 응시 비율이 너무 높은거같아') | 차례 2 `…: Take 2 기록 → 문제 구간 있음`, 답 "기록과 맞아요: Take 2 대본 응시", 32% 한 번 | — | [path-script-high-followup](run-logs/path-script-high-followup.md) |
| 뜻을 묻는 질문(기록 안 읽음 · DONE) | `…: 자료 찾기 → 자료 있음` | — | [path-combo-definition-only](run-logs/path-combo-definition-only.md) |

로그에 보이는 표시 한계: 결정 로그는 마지막 차례만 실행 기록에 남는다. 할 일 단계 글은 60자에서 '…'로 잘리고, '기록 가져오기'와 '기록 보기'가 두 단계로 보인다. 발췌는 합성 자료로 돈 실행이고 채점 결과는 각 파일 머리에 있다(path-rm-score-ask 는 장 번호 · 값 검사 실패).

## 5. 증거
| 기준표 | 증거 | 결과 | 위치 |
|---|---|---|---|
| ⑤ 단계 분기 · 계획, ⑥ 도구 선택 · 계획 변경 평가 | 경로 채점(사건 순서 · 여러 유효 순서) · 경로 세트 28 × 3 | 과제 성공 0.833(고치기 전 24문항 0.625) | 연구 README 10회차 |
| ③ 쓸모 · ⑥ 독립 평가 | 독립 평가 v2 60(맥락 분리 작성 · 고치기 전 동결 · 봉인 기준선 · 끝나고 한 번) | 0.550 → 0.617(+0.067, 짝 부트스트랩 [−0.011, +0.150] — 통계적으로 확인되지 않음) · pass^3 0.450 → 0.483 · 경로 0.857 · 재계획 0.889 — **자체 기준 미달**, 고정 순서 0.489 | [`evals/final-v2/`](../evals/final-v2/README.md) |
| ⑧ 통합 · E2E | 가짜 모델 경로 통합 시험 · 실제 FE · BE · DB E2E 7 · 실사용 흐름 점검 4장면 × 3 | 서비스 대화 · 리뷰 2126 · 연구 370 · E2E 7/7 · 시연 장면 1~4 12/12(처음 10/12, 실패 2건을 고친 뒤 그 장면 6/6) · 두 차례 대화 2/2 | 시험 목차 · [`e2e/`](../e2e/README.md) |
| ⑤ 결과 → 결정 로그 | `agent_decision`(코드 enum · 번호만, 서비스 로그) | 4절 발췌 · 2절 결정 비율 | 기능 문서 11-5 |
| ② · ⑦ 품질 · 가드레일 | 회귀 세트(평가셋 · 안전 · 객관성 · 서비스 113문항 × 3) · 한 턴 전체 검사 | 0.941(고치기 전 0.920), 시간 초과 0 | 연구 README 10회차 |
| 맞지만 헷갈리는 답 | 사람이 읽듯 보는 검토(보정 전 보조 검토, 73대화) | 고친 7 · 남은 6 | [`evals/reader-review/RESULTS.md`](../evals/reader-review/RESULTS.md) |
| 성능 | 단계별 시간 · 호출 · 토큰(`perf.py`, 회귀 main 374턴) | 일반 턴 중간 10.95초 · 상위 5% 19.75초, 다시 평가 중간 19.41초 · 상위 5% 32.46초, 턴당 모델 호출 3.9 | 연구 README 10회차 |

## 6. 이 판에 없는 증거
배포(dev)에서의 반복 완주 · 근거성 판정의 사람 보정 · 사람이 읽듯 보는 검토의 사람 보정(판정 표만 준비) · 사용자 시험(수작업 대비 단계 · 시간) · 배포 부하에서 기존 기능 영향 — 기능 문서 15-4.
