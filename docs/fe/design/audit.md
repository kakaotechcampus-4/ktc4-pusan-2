# 설계서와 제품 대조

설계서(`docs/fe/design/`)는 제품(`frontend/`) 코드를 import하지 않고 따로 돈다. 그래서 같은 화면이라도 설계서 미리보기와 제품이 다를 수 있다. 이 문서는 그 차이를 적는다. 같은 내용이 화면 목록(`src/catalog.ts`)의 각 화면 `productNote`에 있고, 설계서의 설명 패널에 보인다. 미리보기를 고치거나 제품이 바뀌면 둘을 함께 맞춘다.

제품 동작을 확인할 때는 제품 코드나 제품 개발 서버(http://localhost:3000)를 본다. 설명 패널의 "제품에서 열기"가 제품 목 데이터로 그 화면을 연다.

## 제품 화면 샘플

미리보기는 제품 코드를 읽고 설계서가 다시 그린 것이다. 장치 · API · 시간 흐름은 재현하지 않는다.

| 화면 | 설계서 미리보기와 제품의 차이 | 제품 코드 |
| --- | --- | --- |
| 홈 · 내 피치 | 상단바(AppHeader) · 서비스 리뷰 창 · 사용방법 창 · 바닥글을 뺐다. "사용방법 살펴보기"는 글자만 둔다. 홈에는 연습 시작 버튼이 없다(제품과 같음). | `frontend/src/features/pitch/HomePage.tsx`, `HomePitchCard.tsx` |
| 피치 생성 · 수정 | 제품의 자료 버전 트리(VersionRail) · 치치 · 발표 정보 줄(InfoBar)을 간단한 버전 목록으로 줄였다. 대본 매핑 확인 · 버전 고르기 · 평가기준 편집은 그리지 않고, "대본 매핑"은 다음 단계로 넘어간다. 슬라이드 크게 보기는 제품에서 썸네일을 눌러 연다. 단계 표시줄 · 단계별 제목과 버튼 · 안내 문구, 슬라이드를 올리기 전 아래 줄이 없는 것은 제품과 같다. | `frontend/src/features/pitch/create/PitchCreatePage.tsx`, `steps/*` |
| 시작 전 세팅 | 카메라 미리보기 · 시선 기준 전체 화면 · 장치 선택 · 음량 막대 · 평소 목소리 읽기 패널은 자리만 둔다. 시선 기준의 "누르면 시작"은 기준을 다 잡은 상태로 바로 넘어간다. 안내 줄은 카메라 · 마이크 · 시선 기준 세 갈래만 그린다. 제품은 그 앞에 시작 실패 · 여는 중 · 준비 정보 문제 · 평소 목소리 읽는 중 · 장치 오류 · 시선 분석 불가를, 뒤에 기준이 흐릿함 등을 더 말한다(순서는 `catalog.ts` 동작 규칙). 시작 조건(카메라 · 마이크 · 시선 기준 · 슬라이드와 대본 조합)은 제품과 같다. | `frontend/src/features/rehearsal/prepare/DeviceCheckPage.tsx` |
| 리허설 무대 | 카메라 영상 · 음량 막대 · PDF 대신 자리표시를 둔다. 시작 전 안내(장치 여는 중 · 연결 중 · 마이크 없음)와 코치 상태 표시는 그리지 않는다. 종료 확인은 4초 뒤 풀리지 않는다. 무대 배치는 설계서의 `src/ui/stage.css`(제품 `stage.css` 복사본)를 쓴다. | `frontend/src/features/rehearsal/stage/RehearsalPage.tsx`, `ScriptPane.tsx`, `stage.css` |
| 분석 대기 | 팁이 바뀌지 않고 "팁 자동 전환 일시정지" 버튼을 뺐다. 분석 완료 뒤 리포트로 자동 이동하는 대신 "이어지는 화면"의 "분석 완료 → 리포트"를 쓴다. | `frontend/src/features/report/ProcessingPage.tsx`, `DuskScreen.tsx` |
| 대본 확인 | 남은 시간이 줄지 않는다. 확인 항목은 하나만 그린다. "답을 반영하는 중" · "확인을 받을 수 없어요" 상태는 그리지 않는다. | `frontend/src/features/report/ConfirmPage.tsx` |
| 업로드 실패 · 재시도 | 남은 기록 수치는 예시 값이다. 기록 확인 중 · 다시 보내는 중 · 기록 없음 · 서버에서 이미 끝남 상태는 제품 문구와 같다. | `frontend/src/features/rehearsal/retry/RetryPage.tsx` |
| Take 리포트 | 상단바를 뺐다. 평가기준 · 고칠 것 · 잘한 점 · 미션 결과 · 영역별 상태 카드는 그리지 않고 코칭 노트 · 지난 Take와 비교 · 측정값 · 시선 비율 · 다음 미션만 그린다. | `frontend/src/features/report/ReportPage.tsx` |
| Take 기록 | 상단바를 뺐다. 상태 배지는 Badge로 대신 그린다. | `frontend/src/features/report/TakesPage.tsx` |
| Take 비교 | 상단바를 뺐다. 지표 탭은 청중 응시 하나만 그리고, Take별 기록 표는 그리지 않는다. | `frontend/src/features/report/ComparisonPage.tsx` |

## 옛 시안

시안은 검토 당시의 그림과 상태를 남긴다. 시안 안의 질문 · 문구는 작성 당시 내용이므로 지금 제품 동작으로 읽지 않는다. 옛 주소 `#/<이름>`(예: `#/report`)은 뷰어에서 해당 시안을 연다.

| 시안 | 지금 제품과의 관계 | 제품 코드 |
| --- | --- | --- |
| 공용 컴포넌트 | 설계서 `src/ui`의 버튼 · 카드 · 배지를 쓴다. 제품 `shared/ui`에서 복사해 시작했다. | `frontend/src/shared/ui/` |
| 피치 생성 · 시작 흐름 | 옛 목록의 "실제 화면은 아직 발표정보부터 시작" 설명은 지났다. 제품은 슬라이드 → 대본 → 발표정보 확인 → 평가기준 순서다. | `frontend/src/features/pitch/create/PitchCreatePage.tsx` |
| 시작 전 세팅 · 이번 Take 미션 | 제품에 구현됐다. 카메라 · 마이크 · 시선 기준이 모두 돼야 시작한다. | `frontend/src/features/rehearsal/prepare/DeviceCheckPage.tsx` |
| 리허설 무대 · 스타일 | 어두운 무대로 정했다. 제품은 전용 CSS로 배치와 시선 테두리를 고정한다. 시안의 배치와 계측 조건을 혼동하지 않는다. | `frontend/src/features/rehearsal/stage/RehearsalPage.tsx` |
| 분석 대기 · 8비트 로딩 | 제품에 구현됐다. 분석 실패 · 리뷰 보류 · 대본 확인 대기가 더 있다. | `frontend/src/features/report/ProcessingPage.tsx` |
| 업로드 실패 · 재시도 | 제품에 구현됐다. 남은 기록 없음 · 서버에서 이미 끝남을 따로 안내한다. | `frontend/src/features/rehearsal/retry/RetryPage.tsx` |
| 분석 방식 설명 | 제품 화면이 있다. 시안의 "구현 전" 표시는 지났다. | `frontend/src/features/info/AboutPage.tsx` |
| 미지원 브라우저 | 제품 화면이 있고 브라우저에 맞는 사유 하나만 보인다. | `frontend/src/features/info/UnsupportedPage.tsx` |
| 찾을 수 없음 | 제품의 공통 404 화면이 있다. | `frontend/src/features/info/NotFoundPage.tsx` |
| 마이페이지 | 제품 화면이 있다. | `frontend/src/features/auth/MyPage.tsx` |
| Take 기록 | 제품 화면이 있다. 불러오기 · 실패 · 빈 목록을 다룬다. | `frontend/src/features/report/TakesPage.tsx` |
| Take 비교 | 제품 화면이 있다. 완료된 Take만 비교한다. | `frontend/src/features/report/ComparisonPage.tsx` |
| Take 리포트 | 제품 화면이 있다. 시안 안의 응답 형식 질문을 지금 계약으로 읽지 않는다. | `frontend/src/features/report/ReportPage.tsx` |
| 분석 대기 · 단계 화면 | 별도 단계형 시안이다. 제품은 노을 화면에서 분석 단계를 보여 준다. | `frontend/src/features/report/ProcessingPage.tsx` |

## 디자인 값

색 · 글꼴 · 간격은 설계서의 `src/styles.css`가 기준이다. 제품 `frontend/src/index.css`와 다른 값은 `npm run tokens`로 확인한다. 설명 패널의 "디자인 값"은 `src/styles.css`에서 읽은 값을 보여 준다. 대본 칸 높이 같은 계측 조건(`--spacing-script-*`)은 설계서에서도 바꾸지 않는다.
