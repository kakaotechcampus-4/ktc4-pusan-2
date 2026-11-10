# 피치 만들기 · 수정 (FE)

> **기준 범위**: 피치 만들기 · 수정 화면(`/pitch/new` · `/pitch/:id/edit`)의 FE 기준 문서입니다. 대본 업로드 · 파싱 · 재시도의 서버 규칙은 [docs/be/script-parse.md](../../be/script-parse.md), AI가 대본을 나누는 규칙은 [script-parser.md](../../ai/features/script-parser.md), 평가 기준 정리는 [evaluation-criteria.md](../../ai/features/evaluation-criteria.md)가 기준입니다. 이 문서에는 화면 흐름과 FE가 정하는 규칙(버전 · 대본 ↔ 슬라이드 연결 · 연습 조합)만 적습니다.
>
> **한 줄 요약**: 슬라이드 · 대본 · 평가 기준을 버전으로 쌓습니다. 슬라이드와 대본의 장 수가 맞고 대본을 장마다 나눈 결과(매핑)를 저장해야 연습할 수 있고, 그 조합을 장치 점검으로 넘깁니다.
>
> **기준값**: 한도는 대부분 `lib/draft.ts`(평가 기준 원문 2,000자만 `steps/CriteriaPane.tsx`), 폴링 간격 · 최대 시간은 `scriptParse.ts`, 기다림 안내 단계는 `lib/parseWait.ts`의 상수가 기준입니다. 8절의 숫자는 그 값을 옮긴 것입니다.

| 절 | 내용 |
|---|---|
| 0 | 한눈에 보기 |
| 1 | 왜 필요한가 · 용어 |
| 2 | 화면 흐름 |
| 3 | 상태를 어디에 두나 |
| 4 | 폴더 · 주요 파일 |
| 5 | BE · AI 계약 |
| 6 | 처리 흐름 |
| 7 | 로딩 · 빈 · 오류 · 부분 실패 |
| 8 | 시간 · 성능 제약 |
| 9 | 테스트 |
| 10 | 열린 질문 · 알려진 한계 |

---

## 0. 한눈에 보기

- 라우트는 `/pitch/new`(새로 만들기)와 `/pitch/:id/edit`(수정) 둘이고, 같은 화면을 씁니다. 수정은 서버의 피치를 초안 모양으로 읽어 와 시작합니다(`loadPitch.ts`).
- 화면은 한 페이지에 네 구역(슬라이드 · 대본 연결 · 발표정보 · 평가기준)이 차례로 이어지고, 슬라이드 · 대본 · 평가 기준은 **버전**을 쌓습니다.
- 대본 매핑은 서버에 올린 뒤 AI가 나눈 결과를 1초마다 조회해 받습니다. 기다린 시간에 따라 안내가 바뀝니다.
- 슬라이드와 대본의 장 수가 맞고 매핑을 저장해야 연습할 조합(`PracticeCombo`)이 생기고, 그 조합을 라우터 state로 장치 점검에 넘깁니다.

## 1. 왜 필요한가 · 용어

리허설 · 리포트는 "어느 슬라이드 버전과 어느 대본 버전으로 연습했나"를 기준으로 Take를 비교합니다. 이 화면은 그 조합을 사용자가 확인하고 정하는 곳입니다.

| 용어 | 뜻 |
|---|---|
| 피치 | 발표 하나. 제목 · 발표 날짜 · 발표 시간(허용 오차 포함)을 가집니다 |
| 버전 | 슬라이드 · 대본 · 평가 기준마다 쌓이는 판. 화면 왼쪽의 접히는 '자료 버전 관리'(`VersionRail`)에서 고릅니다 |
| 구분자 | 대본 안의 `[슬라이드 1]` 같은 표시. AI는 이 표시로만 장을 나눕니다 |
| 매핑 | 대본을 슬라이드 장마다 나눈 결과(`blocks`). 확인한 뒤 저장해야 연습에 씁니다 |
| 연습 조합 | 슬라이드 버전 + 대본 버전(+ 그때의 목표 시간). 장치 점검으로 넘기고, 두 버전이 Take에 박힙니다. 제한 시간은 BE가 피치의 지금 설정으로 기록합니다 |
| 진행 조건(gate) | `lib/gate.ts`가 계산하는 "다음에 할 일" 한 줄. 대본 저장 줄의 안내와 아래 버튼을 누를 수 있는지에 씁니다 |

## 2. 화면 흐름

```
1. 슬라이드 ──▶ 2. 대본 연결 ──▶ 3. 발표정보 ──▶ 4. 평가기준(선택) ──▶ [최종 확인 →] ──▶ 발표 준비 최종 확인 창 ──▶ [확인하고 시작 전 점검 →] ──▶ /pitch/:pitchId/device-check
(SlidePane)    (ScriptPane)     (InfoPane)      (CriteriaPane)                           (FinalReviewDialog)                                 (state: { practice })
```

- 네 구역이 한 페이지에 세로로 이어집니다(`PitchCreatePage.tsx`의 `SECTIONS`).
- 왼쪽 '준비 순서'로 구역을 옮기고, 그 아래 접히는 '자료 버전 관리'에서 버전을 고릅니다.
- 아래 고정 버튼 하나가 다음 할 일로 데려갑니다. 문구는 "슬라이드 확인하기 ↓" · "대본 연결 확인하기 ↓" … 처럼 바뀌다가, 다 되면 "최종 확인 →"이 됩니다.
- 화면 그림은 화면 설계서의 `pitch-create` 화면에 있습니다. `docs/fe/design/`에서 `npm run dev`를 띄운 뒤 `http://localhost:3100/#/?screen=pitch-create`를 엽니다([설계서 사용법](../design/README.md)). 설명 패널의 "제품과 다른 점"에 시안과 제품이 다른 곳이 적혀 있습니다.

| 구역 | 하는 일 |
|---|---|
| 슬라이드 | 드롭존 → 올리는 중 → 썸네일 + 뷰어. 피치가 아직 없으면 파일 이름을 제목으로, 시간은 기본값으로 피치부터 만든 뒤 올립니다 |
| 대본 | 같은 대본 버전에 두 상태가 있습니다 — 매핑 전에는 **입력**, 매핑 뒤에는 **장별 확인 · 수정**. 입력 중에는 지금 구분자대로 나누면 어떻게 될지 미리 알려 줍니다(`lib/markerHint.ts`) |
| 평가 기준 | 왼쪽에 자연어로 한 번에 적으면 AI가 항목으로 정리하고, 오른쪽에서 확인 · 고친 뒤 저장합니다 |
| 최종 확인 | "발표 준비 최종 확인" 창에서 고른 조합을 다시 보이고, [확인하고 시작 전 점검 →]을 누르면 문제가 없을 때만 장치 점검으로 이동합니다(6-4) |

## 3. 상태를 어디에 두나

| 무엇 | 위치 | 비고 |
|---|---|---|
| 작성 중인 초안(정보 · 버전들 · 고른 버전 · 업로드 · 나누기 상태) | Zustand `createStore.ts` | 한 번의 작성을 `draftId`로 구분합니다. 새 피치를 시작하면 앞 작성의 늦게 온 응답이 들어오지 않게 막습니다 |
| 초안 위의 규칙(장 수 · 저장 가능 · 연습 조합 · 새 버전 가능) | 순수 함수 `lib/draft.ts` · `lib/gate.ts` | 화면 밖에서 시험합니다 |
| BE 응답 ↔ 초안 변환 | `lib/beAdapter.ts` | 옮기는 규칙은 이 파일에만 둡니다 |
| 대본 나누기 폴링 | `scriptParse.ts` (화면 밖) | 다른 화면으로 가도 끝날 때까지(최대 100초) 돌고, 결과는 그 대본에 들어갑니다 |
| 연습 조합 | 라우터 state(`practice`) | 장치 점검으로 넘깁니다([architecture.md](../architecture.md) 3절) |
| 대화형 에이전트에 넘길 화면 상태 | `chatScreen.ts` | 입력 중인 값을 대화창이 읽을 수 있게 등록합니다 |

- **서버는 대본이 어느 슬라이드에 맞춘 것인지 저장하지 않습니다.** 그래서 FE가 정합니다(6-3).

## 4. 폴더 · 주요 파일

모두 `frontend/src/features/pitch/create/` 아래입니다.

**화면**

| 파일 | 하는 일 |
|---|---|
| `PitchCreatePage.tsx` | 새 작성인지 판단 · 네 구역 · '준비 순서' · 아래 고정 버튼 · 최종 확인으로 가기 |
| `steps/SlidePane.tsx` · `ScriptPane.tsx` · `InfoPane.tsx` · `CriteriaPane.tsx` | 네 구역 |
| `steps/NeedPitch.tsx` · `steps/PaneHeading.tsx` · `steps/DatePicker.tsx` | 피치가 없을 때 안내 · 구역 제목 · 날짜 고르기 |
| `VersionRail.tsx` · `VersionPreviewDialog.tsx` | 버전 고르기 · 미리보기 |
| `FinalReviewDialog.tsx` | 발표 준비 최종 확인 창 |
| `steps/ParseWaitHint.tsx` | 나누는 동안 기다린 시간 안내 |
| `chatScreen.ts` | 대화형 에이전트에 넘길 화면 상태 등록 |

- `StepBar.tsx` · `InfoBar.tsx` · `steps/PaneFooter.tsx`는 지금 어디서도 쓰지 않습니다(한 페이지 화면으로 바뀌기 전의 단계 줄 · 정보 줄 · 이전/다음 버튼). 지울지는 따로 정리할 거리입니다.

**상태 · 흐름**

| 파일 | 하는 일 |
|---|---|
| `createStore.ts` | 초안과 동작(버전 추가 · 슬라이드 붙이기 · 매핑 고치기 · 저장) |
| `slideUpload.ts` | 슬라이드 올리기 · 실패 사유를 문구로 |
| `scriptParse.ts` | 대본 올리기 · 다시 나누기 · 폴링 |
| `loadPitch.ts` | 수정 화면: 서버 피치 → 초안 |
| `useSavePitchInfo.ts` | 발표 정보 저장 |

**규칙** (순수 함수)

| 파일 | 하는 일 |
|---|---|
| `lib/draft.ts` | 초안 타입 · 한도 · 연습 조합 · 새 버전 가능 여부 |
| `lib/gate.ts` | 진행 조건(다음에 할 일 한 줄) |
| `lib/markerHint.ts` · `lib/parseWait.ts` · `lib/date.ts` | 입력 중 안내 · 기다림 안내 · 날짜 |
| `lib/beAdapter.ts` | BE 응답 변환 |

## 5. BE · AI 계약

| 무엇 | 경로 | FE 코드 |
|---|---|---|
| 피치 만들기 · 고치기 · 지우기 | `POST /api/pitches/add` · `PATCH /api/pitches/update/{id}` · `DELETE /api/pitches/delete/{id}` | `shared/api/pitch.ts` |
| 피치 정보 · 버전 목록 | `GET /api/pitches/{id}` · `GET /api/pitches/{id}/resources` | `shared/api/pitch.ts` |
| 슬라이드 올리기 · 읽기 | `POST /api/pitches/add/{id}/presentation` · `GET /api/pitches/{id}/presentations/{versionId}` | `shared/api/presentation.ts` |
| 대본 올리기(202) · 읽기 · 다시 나누기 | `POST /api/pitches/{id}/scripts` · `GET /api/pitches/{id}/scripts/{versionId}` · `POST .../scripts/{versionId}/parse` | `shared/api/script.ts` |
| 평가 기준 정리 · 저장 · 읽기 | `POST /api/pitches/add/{id}/standards` · `POST /api/pitches/{id}/standards` · `GET /api/pitches/{id}/evaluations/{version}` | `shared/api/standards.ts` |

- 대본 업로드 · 파싱 · 재시도 · 만료 규칙은 [docs/be/script-parse.md](../../be/script-parse.md)가 기준입니다. AI가 나누는 규칙(구분자)은 [docs/ai/features/script-parser.md](../../ai/features/script-parser.md), 평가 기준 정리는 [docs/ai/features/evaluation-criteria.md](../../ai/features/evaluation-criteria.md)입니다.
- 슬라이드 쪽수는 BE가 센 값(`page_count`)을 쓰고, 없을 때(옛 발표자료)만 PDF를 열어 셉니다.

## 6. 처리 흐름

### 6-1. 대본 매핑

```
[대본 매핑]  POST /scripts (202) ──▶ 서버 버전 id     나누기 상태: pending (시작 시각 기록)
                 ▼
            GET /scripts/{id} 를 1초마다 (최대 100초)
                 ▼
            매핑 확인 화면 → 장 수가 맞으면 [저장] → 연습 조합이 생김
```

**조회 결과에 따라**

| 응답 | 하는 일 |
|---|---|
| `PENDING` | 계속 묻습니다 |
| `DONE` | 장별 본문(`blocks`)을 받아 매핑 확인 화면으로 |
| `FAILED` | 실패 문구 + [다시 시도] |
| 다시 물어도 답이 같은 실패(401 · 403 · 404 등 4xx) | 멈춥니다 |
| 408 · 429 · 네트워크 오류 | 잠시 뒤 다시 물으면 되니 계속 묻습니다 |
| 100초가 지남 | "나누는 데 너무 오래 걸려요" + [다시 시도] |

**기다리는 동안**

- 대본을 고치면 결과와 어긋나서 입력칸을 잠급니다.
- 기다린 시간에 따라 안내가 바뀝니다(멘토 리뷰 #923).

  | 기다린 시간 | 안내 |
  |---|---|
  | 3초 전 | 없음 (버튼의 "나누는 중…"만) |
  | 3~10초 | "장별로 나누는 중이에요(보통 10초 안팎)" |
  | 10초 이상 | "평소보다 오래 걸리는 중이에요. 다른 화면으로 들어가도 결과는 이 대본에 들어가요" |

### 6-2. 버전

"+ 새 버전"은 저장하지 않은 버전이 있는 동안 막습니다. 빈 버전이 끝없이 쌓이지 않게 하려는 것입니다(`pendingVersion`).

| 구역 | "저장하지 않음"의 뜻 |
|---|---|
| 슬라이드 | 파일을 아직 올리지 않음(올리면 그 자체로 저장) |
| 대본 | 매핑을 아직 저장하지 않음 |
| 평가 기준 | 정리 전 빈 버전이거나, 고친 내용을 저장하지 않음 |

### 6-3. 대본 ↔ 슬라이드 연결

서버가 연결을 저장하지 않아 FE가 정합니다.

| 언제 | 규칙 |
|---|---|
| 새 슬라이드를 올릴 때 (`createStore.ts` `relinkScripts`) | 지금 고른 대본을 **장 수와 관계없이** 새 슬라이드에 붙이고 매핑을 다시 확인받습니다. 장 수가 다르면 진행 조건이 "장수 다름"으로 막습니다 |
| 수정 화면을 열 때 (`loadPitch.ts`) | 가장 최근 대본은 최신 슬라이드에, 지난 대본 버전은 장 수가 같은 가장 최근 슬라이드에(없으면 최신) 붙입니다 |

- **왜 장 수가 달라도 옮기나** — 예전에는 장 수가 다르면 옮기지 않았습니다. 그러면 대본이 예전 슬라이드에 붙은 채 남아, 새 슬라이드를 올려도 예전 슬라이드로 연습이 시작됐습니다(멘토 리뷰 #923).
- **왜 가장 최근 대본만 다시 확인받나** — 지난 버전까지 "저장 안 함"으로 두면 6-2 규칙 때문에 "+ 새 버전"이 막힙니다.

### 6-4. 최종 확인 → 장치 점검

**연습 조합이 생기는 조건** (`lib/draft.ts` `toPracticeCombo`) — 모두 만족해야 합니다.

1. 슬라이드와 대본이 서버에 있고, 대본을 나누는 중이 아닙니다.
2. 장 수가 맞습니다.
3. 매핑을 저장했습니다.
4. 대본이 고른 슬라이드에 붙어 있습니다.

**이동 직전에 한 번 더 보는 것** (`PitchCreatePage.tsx` `start()`) — 최종 확인 창에서 [확인하고 시작 전 점검 →]을 누르면, 아래 중 하나라도 걸리면 이동하지 않습니다.

- 발표 정보를 확인 · 저장하지 않음
- 새로 올린 슬라이드를 아직 확인하지 않음
- 정리된 평가 기준 결과를 아직 확인하지 않았거나 저장하지 않음
- 진행 중인 요청이 있음(업로드 · 나누기 · 평가 기준 정리 · 저장)

## 7. 로딩 · 빈 · 오류 · 부분 실패

| 상황 | 화면 |
|---|---|
| 피치가 아직 없음 | 대본 · 평가 기준 구역에 안내(`NeedPitch`). 슬라이드는 올리면서 피치를 만듭니다(2절) |
| 슬라이드 올리는 중 · 실패 | 올리는 중 표시 · 사유별 문구(`slideUpload.ts`, BE `pitch/exception.py` 기준) |
| 대본 나누는 중 | 버튼 "나누는 중…" + 기다린 시간 안내, 입력칸 잠금 |
| 대본 나누기 실패 · 너무 오래 걸림 | "대본을 슬라이드로 나누지 못했어요. 다시 시도해 주세요." · "나누는 데 너무 오래 걸려요. 다시 시도해 주세요." + [다시 시도] |
| 수정 화면을 열었는데 나누는 중이던 대본 | "나누는 중이던 대본이에요. 잠시 뒤 다시 열어 주세요." (여기서 기다리지 않습니다) |
| 장 수가 다름 | 저장 줄에 "대본은 N장, 슬라이드는 M장이라 저장할 수 없어요." + 구분 넣는 법, 진행 조건 "대본과 슬라이드 장수가 달라요. 대본의 구분을 확인해 주세요". 저장 · 다음 단계 막힘 |
| 대본이 너무 김 | 50,000자(공백 포함)까지. 넘으면 매핑 버튼을 막고 안내 |

## 8. 시간 · 성능 제약

| 값 | 크기 | 위치 |
|---|---|---|
| 대본 나누기 조회 간격 · 최대 | 1초 · 100초 | `scriptParse.ts` |
| 기다림 안내 단계 | 3초 · 10초 | `lib/parseWait.ts` |
| 슬라이드 파일 크기 | 40MB | `lib/draft.ts` `MAX_SLIDE_BYTES` |
| 대본 길이 | 50,000자 | `MAX_SCRIPT_CHARS` |
| 제목 · 발표 시간 · 허용 오차 | 50자 · 1~60분 · 30초 단위로 5분까지 | `MAX_TITLE_CHARS` · `MIN_TIME_LIMIT_MIN` · `MAX_TIME_LIMIT_MIN` · `TOLERANCE_STEP_SEC` · `MAX_TOLERANCE_SEC` |
| 평가 기준 | 10개 · 항목당 50자 | `MAX_CRITERIA` · `MAX_CRITERION_CHARS` |
| 평가 기준 원문(정리 전 입력) | 2,000자 | `steps/CriteriaPane.tsx` (상수 아님, 숫자로 적힘) |

- 폴링 간격은 1초로 둡니다. 수십 초 안에 끝나는 작업이라 간격을 늘리면 결과를 늦게 보입니다(멘토 리뷰 #923).

## 9. 테스트

| 무엇 | 파일 |
|---|---|
| 화면 흐름 · 최종 확인 → 장치 점검 | `PitchCreatePage.test.tsx` · `practiceNavigation.test.tsx` |
| 초안 동작 · 대본 ↔ 슬라이드 연결 | `createStore.test.ts` |
| 수정 화면 읽기 | `loadPitch.test.ts` |
| 대본 매핑 폴링 | `scriptParse.test.ts` |
| 규칙 | `lib/draft.test.ts` · `lib/gate.test.ts` · `lib/markerHint.test.ts` · `lib/parseWait.test.ts` · `pendingVersion.test.ts` |
| 브라우저 끝까지(E2E) | `frontend/e2e/pitch.e2e.ts` — 최종 확인 → 장치 점검 · 발표정보 저장 · 새 피치 첫 안내. PDF 올리기는 보지 않습니다 |

## 10. 열린 질문 · 알려진 한계

- **장치 점검을 조합 없이 열 때의 짝** — BE `GET /prepare`가 고르는 짝이 6-3의 FE 규칙과 다릅니다. [rehearsal.md](rehearsal.md) 10절에 정리해 두었습니다.
- **대본 ↔ 슬라이드 연결을 서버가 저장하지 않음** — 그래서 FE가 규칙으로 추정합니다(6-3). 서버가 저장하면 이 추정을 지울 수 있습니다.
