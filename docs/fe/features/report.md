# 분석 대기 · 리포트 · Take 기록 (FE)

> **기준 범위**: 분석 대기 · 대본 확인 · Take 리포트 · Take 기록 화면의 FE 기준 문서입니다. 분석 단계와 결과 모양은 [docs/be/review.md](../../be/review.md) · [realtime-coach.md](../../be/realtime-coach.md), 리뷰 내용은 [review-agent.md](../../ai/features/review-agent.md), 설문은 [take-survey.md](../../be/take-survey.md)가 기준입니다. 이 문서에는 FE가 그 결과를 언제 · 어떻게 보이는지만 적습니다.
>
> **한 줄 요약**: 끝낸 Take의 리포트를 끝날 때까지 2초마다 다시 묻고, 점수 대신 지난 Take와의 변화와 측정 근거를 보이며, 리뷰가 없어도 측정값은 먼저 보입니다.
>
> **기준값**: 다시 조회 간격과 멈추는 조건은 `shared/api/report.ts`(`POLL_MS` · `isSettled`), 분석 단계 이름은 `features/report/ProcessingPage.tsx`가 기준입니다.

| 절 | 내용 |
|---|---|
| 0 | 한눈에 보기 |
| 1 | 왜 필요한가 · 용어 |
| 2 | 화면 흐름 |
| 3 | 상태를 어디에 두나 |
| 4 | 폴더 · 주요 파일 |
| 5 | BE 계약 |
| 6 | 처리 흐름 |
| 7 | 로딩 · 빈 · 오류 · 부분 실패 |
| 8 | 시간 · 성능 제약 |
| 9 | 테스트 |
| 10 | 열린 질문 · 알려진 한계 |

---

## 0. 한눈에 보기

- 끝낸 Take는 **분석 대기**에서 리포트가 나올 때까지 2초마다 다시 묻고, 끝나면 **리포트**로 넘어갑니다. 화면을 떠나도 분석은 서버에서 이어집니다.
- 인식이 헷갈린 말이 있으면 서버가 **대본 확인**을 기다립니다. 답하지 않아도 최대 10분이 지나면 그대로 진행됩니다.
- 리포트는 **점수를 보여 주지 않습니다.** 요약과 지난 Take와의 변화를 먼저, 측정 근거와 다음 미션을 그 뒤에 보입니다.
- 리뷰가 아직이거나 실패해도 측정값은 먼저 보입니다.
- 시선 3값(`CAMERA` · `BOTTOM` · `UNCERTAIN`)의 3색은 리허설 테두리 전용이라 리포트에서는 쓰지 않습니다.

| 화면 | 주소 |
|---|---|
| 분석 대기 | `/takes/:takeId/processing` |
| 대본 확인 | `/takes/:takeId/confirm` |
| Take 리포트 | `/takes/:takeId` |
| Take 기록 · 비교 | `/takes` (`/pitch/:id/comparison`은 여기로 넘기는 옛 주소) |

## 1. 왜 필요한가 · 용어

"점수가 아니라 Take 사이의 변화"가 FE의 기준입니다([frontend/AGENTS.md](../../../frontend/AGENTS.md)). 이 화면들은 사용자가 다음 Take에서 고칠 것 한 가지를 알게 하는 곳입니다.

| 용어 | 뜻 |
|---|---|
| 분석 단계(`analysis_stage`) | 서버의 분석 진행 상태. 아래 표 참고 |
| 측정값 | 시선 · 말 속도 · 음량 · 추임새 등 코드가 잰 값. 리뷰가 없어도 있습니다 |
| 리뷰 | AI가 측정값을 보고 쓴 설명 · 비교 · 다음 미션. 실패하거나 건너뛸 수 있습니다 |
| 대본 확인 | 인식이 헷갈린 말을 "대본대로 말했어요 / 들린 대로 말했어요 / 잘 모르겠어요" 중에서 고르는 단계 |
| 자동 종료 | 말이 없는 상태가 2분 넘게 이어지거나(`SILENCE`) 최대 1시간에 닿아(`TIME_LIMIT`) 서버가 Take를 끝낸 경우(#925). 무대 쪽 흐름은 [rehearsal.md](rehearsal.md) 6-3 |
| 분석 보류(`analysis_hold_reason`) | 서버가 분석을 잠시 멈춘 상태. 사람이 복구할 때까지 이어지지 않습니다 |

**분석 단계**

```
FINALIZING → SCORING → (AWAITING_CONFIRMATION) → REVIEWING → DONE
```

- 확인 단계(`AWAITING_CONFIRMATION`)는 채점 결과에 확인할 말이 있을 때만 거칩니다.
- 화면은 세 단계로 접어 보입니다: "발표 기록 정리" · "말한 내용 채점" · "코치 총평"
- Take 상태가 `ANALYZING`인 동안에만 단계 표시 위에 "TAKE RECORDED · 발표 기록 접수" 띠를 둡니다(`ProcessingPage.tsx`, #1118).

## 2. 화면 흐름

```
리허설 끝내기
   │
   ▼
/takes/:takeId/processing   분석 대기 (노을 화면, 2초마다 리포트 조회)
   │  AWAITING_CONFIRMATION 이면 [비슷하게 들린 말 확인하기] 버튼 (저절로 넘기지 않음)
   │        └──▶ /takes/:takeId/confirm  대본 확인 → 보내면 분석 대기로
   │  더 기다려도 바뀌지 않는 상태가 되면
   ▼
/takes/:takeId              Take 리포트
   │  일반 피치: [Take N 연습하기] → 장치 점검 (연습 조합 없이 → 서버가 고른 짝)
   │  샘플 피치(is_demo): [내 자료로 연습하기] → /pitch/new (새 피치 생성)
   │  [설문] (자동으로 띄우지 않음)
   ▼
/takes                      Take 기록 · 한 피치의 Take 비교
```

- **대본 확인으로 저절로 넘기지 않는 이유** — 확인 화면에서 돌아온 사용자를 다시 튕겨 보내게 됩니다.
- **분석 대기에 남는 경우** — 두 가지뿐입니다. 리뷰만 실패했으면 리포트로 넘어갑니다.
  - 분석 자체가 실패함: "이번 Take는 분석하지 못했어요" + [다시 연습하기]
  - 분석이 보류됨: "분석이 잠시 멈췄어요" + 홈에서 나중에 다시 확인하라는 안내 + [홈으로]
- **[샘플 리포트]** — 튜토리얼 샘플(`is_demo`)은 다음 Take 대신 [내 자료로 연습하기]를 보여 `/pitch/new`로 이동합니다. 화면의 미션은 샘플에서 얻은 참고 피드백이며, 새 피치의 코칭 미션으로 자동 이월되지 않습니다.
- **[Take N 연습하기]** — 장치 점검에 연습 조합 없이 들어가서, 새 PDF를 올린 뒤면 지난 자료가 골라질 수 있습니다. 그때는 장치 점검이 알리고 확인받습니다([rehearsal.md](rehearsal.md) 2-3).

## 3. 상태를 어디에 두나

| 무엇 | 위치 |
|---|---|
| 리포트 · 분석 상태 | TanStack Query `shared/api/report.ts` (끝날 때까지 2초 간격으로 다시 조회) |
| Take 기록 | `shared/api/takeHistory.ts` |
| 대본 확인 목록 · 보내기 | `shared/api/confirmation.ts` (목록은 항상 새로 읽음, `staleTime: 0`. 같은 화면의 리포트 머리 정보는 다시 묻지 않음) |
| 설문 | `shared/api/takeSurvey.ts` |
| 자동 종료 사유 | 무대 → 분석 대기 → 리포트로 라우터 state로 넘깁니다. 분석이 빨리 끝나면 분석 대기에서 놓치기 때문입니다 |

## 4. 폴더 · 주요 파일

모두 `frontend/src/features/report/` 아래입니다.

> 파일 위치 정리 PR #1151이 합쳐지면 이 폴더가 `analysis/`(분석 대기 · 대본 확인) · `report/`(리포트 · 설문) · `history/`(Take 기록 · 비교) · `api/`(`confirmation.ts` · `takeHistory.ts`)로 나뉩니다. 그때 4절 · 9절 경로를 함께 고칩니다.

| 파일 | 하는 일 |
|---|---|
| `ProcessingPage.tsx` · `DuskScreen.tsx` | 분석 대기. 노을 그림 위 대화 상자에 상태 · 진행 · 팁 |
| `ConfirmPage.tsx` · `confirmFormat.ts` | 대본 확인. 문장 안에서 헷갈린 말을 강조 |
| `ReportPage.tsx` · `ReportFrame.tsx` | 리포트. 요약 · 측정 · 시선 내역 · 지난 Take 비교 · 리뷰 차원 · 다음 미션 |
| `reportFormat.ts` | 서버 단계 → 화면 세 단계 상태 |
| `AutoEndAlert.tsx` | 자동 종료 안내(분석 대기 · 리포트 공용) |
| `TakeSurvey.tsx` | 리포트마다 받는 설문 |
| `TakesPage.tsx` · `TakeComparison.tsx` · `TakeComparisonRedirect.tsx` · `takeHistoryFormat.ts` | Take 기록 · 비교 |
| `QueryState.tsx` | 로딩 · 오류 공용 표시 |

## 5. BE 계약

| 무엇 | 경로 | FE 코드 |
|---|---|---|
| Take 리포트(분석 상태 포함) | `GET /api/takes/{takeId}/report` | `shared/api/report.ts` |
| Take 기록 | `GET /api/takes/` · `GET /api/takes/?pitch_id={id}` | `shared/api/takeHistory.ts` |
| 대본 확인 | `GET · POST /api/takes/{takeId}/confirmations` | `shared/api/confirmation.ts` |
| 설문 | `GET · POST /api/takes/{takeId}/survey` · `GET /api/take-surveys/pending` | `shared/api/takeSurvey.ts` |

- 결과의 모양과 분석 단계는 [docs/be/realtime-coach.md](../../be/realtime-coach.md) · [docs/be/review.md](../../be/review.md), 리뷰 내용은 [docs/ai/features/review-agent.md](../../ai/features/review-agent.md), 설문은 [docs/be/take-survey.md](../../be/take-survey.md)가 기준입니다.

## 6. 처리 흐름

리포트를 2초마다 다시 묻다가, 다음 중 **하나라도** 되면 멈춥니다(`shared/api/report.ts` `isSettled`). 더 기다려도 바뀌지 않는 상태라는 뜻입니다.

1. 분석 실패(`status: FAILED`)
2. 리뷰 결과가 있음(실패 · 건너뜀 포함)
3. 분석 단계가 `DONE`
4. 분석 보류(`analysis_hold_reason`이 있음)

멈춘 뒤 분석 대기는 리포트로 넘기고(분석 실패 · 보류면 남음), 리포트 화면은 다시 묻기를 그칩니다.

## 7. 로딩 · 빈 · 오류 · 부분 실패

| 상황 | 화면 |
|---|---|
| 분석 중 | 맨 위 "TAKE RECORDED · 발표 기록 접수" 띠 + 세 단계 진행 표시("발표 기록 정리" · "말한 내용 채점" · "코치 총평") |
| 대본 확인 대기 | 두 번째 단계(채점)가 기다림 상태, 안내문("비슷하게 들린 말이 있어요. …답이 없으면 10분 안에 그대로 진행돼요.") + [비슷하게 들린 말 확인하기] |
| 리뷰가 없음 | 측정값은 보이고, 요약 자리에 한 줄(아래 표) |
| 분석 실패 | 분석 대기에 남아 "이번 Take는 분석하지 못했어요"와 사유 안내(기록이 너무 짧거나 측정이 거의 안 됨), [다시 연습하기] |
| 분석 보류 | 분석 대기에 남아 "분석이 잠시 멈췄어요"와 나중에 홈에서 확인하라는 안내, [홈으로] |
| 자동 종료 | 맨 위에 사유 안내(`AutoEndAlert`). 말 없음 2분 · 최대 1시간 중 어느 쪽인지 알립니다 |
| 측정하지 못한 값 | `0`이 아니라 `—`로 보입니다(값이 `null`) |

**리뷰가 없을 때의 한 줄** (`ReportPage.tsx` `noReviewLine`)

| 리뷰 상태 | 문구 |
|---|---|
| 아직 없음 | "발표를 분석하고 있어요. 측정값은 먼저 보여 드릴게요." |
| 건너뜀 | "이번 Take는 총평 없이 측정값만 보여 드려요." |
| 실패 | "이번 Take는 총평을 만들지 못했어요. 측정값은 그대로 확인할 수 있어요." |

**리포트 칸마다 비었을 때** (`ReportPage.tsx` · `ReportFrame.tsx`)

| 칸 | 언제 | 문구 |
|---|---|---|
| 리포트 전체 | 불러오지 못함 | "연습 기록을 불러오지 못했어요." + 오류 문구(`toMessage`) + [홈으로] |
| 측정값 | 아직 정리 전 | "발표 기록을 정리하고 있어요." |
| 시선 비율 | 시선 비율 값(카메라 · 화면 · 대본 · 다른 곳)이 모두 `null` | "이번 Take는 시선을 재지 않았어요. 말하기 · 시간 기록은 그대로예요." |
| 평가기준 | 리뷰 전 | "분석이 끝나면 평가기준 결과를 보여 드릴게요." |
| 평가기준 | 리뷰가 있는데 결과를 못 받음 | "이번 Take의 평가기준 결과를 불러오지 못했어요." |
| 내가 정한 평가기준 | 숫자로 볼 기준이 없음 | "숫자로 확인할 수 있는 평가기준이 없어요." |
| 먼저 고칠 것 | 항목 없음 | "이번 Take에는 크게 고칠 곳이 없어요." |
| 잘한 점 | 항목 없음 | "아직 강점으로 꼽을 만한 곳이 없어요." |
| 이번 Take 미션 결과 | 미션 없이 연습함 | "이번 Take에는 미션이 없었어요." |
| 지난 Take와 비교 | 첫 Take 등 비교할 Take 없음 | "비교할 지난 Take가 없어요." |
| 지난 Take와 비교 | 함께 잰 지표가 없음 | "지난 Take와 함께 잰 지표가 없어요." |
| 지난 Take와 비교(지표 하나) | 장별 비교가 없음 | "슬라이드별 비교 결과가 없어요." (발표 전체로만 재는 지표면 "발표 전체 기준 지표예요.") |
| 영역별 상태 | 판단할 수 없는 영역 | 그 영역만 "판단 불가" 배지(`NOT_EVALUABLE`, 문구는 `shared/lib/metricLabels.ts`) |
| Take N+1에서 해 볼 것 | 새 미션 없음 | "이번에는 새 미션이 없어요. 지금처럼 연습해 보세요." |

- 리뷰가 없으면 "먼저 고칠 것" 아래 칸들은 그리지 않고, 요약 자리에 위 "리뷰가 없을 때의 한 줄"만 둡니다.

## 8. 시간 · 성능 제약

| 값 | 크기 | 위치 |
|---|---|---|
| 리포트 다시 조회 간격 | 2초 | `shared/api/report.ts` `POLL_MS` |
| 대본 확인 대기 상한 | 최대 10분 (BE가 정함, 지나면 그대로 진행) | [docs/be/review.md](../../be/review.md) |

## 9. 테스트

| 무엇 | 파일 (`features/report/`) |
|---|---|
| 분석 대기 | `ProcessingPage.test.tsx` |
| 대본 확인 | `ConfirmPage.test.tsx` · `confirm.test.ts` |
| 리포트 | `ReportPage.test.tsx` · `ReportFrame.test.tsx` · `report.test.ts` |
| 리포트 응답 모양 | `ReportContract.test.tsx` |
| Take 기록 · 비교 | `TakesUnified.test.tsx` · `takeHistory.test.ts` · `HistoryAccessibility.test.tsx` |
| 설문 · 서비스 의견 | `TakeSurvey.test.tsx` · `ServiceReviewFlow.test.tsx` |

## 10. 열린 질문 · 알려진 한계

- **리포트에서 다음 Take로 갈 때의 짝** — [Take N 연습하기]는 BE `GET /prepare`가 고른 짝을 씁니다. 새 PDF를 올린 뒤면 지난 자료가 골라질 수 있습니다. [rehearsal.md](rehearsal.md) 10절에 정리해 두었습니다.
