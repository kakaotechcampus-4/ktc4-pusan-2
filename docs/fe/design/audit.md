# 설계서와 제품 대조

설계서(`docs/fe/design/`)는 제품(`frontend/`) 코드를 import하지 않고 따로 돈다. 그래서 같은 화면이라도 설계서 미리보기와 제품이 다를 수 있다. 이 문서는 그 차이를 어디서 보고 어떻게 맞추는지 적는다.

## 화면별 차이

화면마다 제품과 다르게 그린 점은 화면 목록 `src/catalog.ts`의 `productNote`가 원본이다. 설계서 설명 패널의 "제품과 다른 점"에 보인다. 여기에 따로 적지 않는다 — 두 곳에 적으면 한쪽이 낡는다.

- **제품 화면 샘플**: 제품 코드를 읽고 설계서가 다시 그린 것이다. 장치 · API · 시간 흐름은 재현하지 않는다. 그리지 않은 것은 productNote에 "그리지 않는다", 제품과 맞춘 것은 "제품과 같다"로 적는다. 샘플이 줄인 곳을 "같다"고 쓰지 않는다.
- **시안**(`status: '시안'`): 제품에 아직 없는 새 디자인이다. 제품과 비교할 것이 없으니 productNote 대신 상태 설명에 무엇을 바꾸려는지 적는다. 제품에 이미 반영된 과거 검토안도 비교 기록이나 공유 URL이 남아 있으면 시안으로 보관할 수 있다.

제품 동작을 확인할 때는 제품 코드나 제품 개발 서버(http://localhost:3000)를 본다. 설명 패널의 "제품에서 열기"가 제품 목 데이터로 그 화면을 연다.

## 제품이 바뀌었는지 보기

`npm run drift`가 `catalog.ts`의 `PRODUCT_CHECKED`(마지막으로 대조한 제품 커밋) 뒤에 각 화면의 제품 코드(`sources`)가 바뀌었거나 없어졌는지 화면별로 보여 준다. 맞추는 절차는 README "제품이 바뀌었을 때"다.

## 디자인 값

색 · 글꼴 · 간격은 설계서의 `src/styles.css`가 기준이다. 제품 `frontend/src/index.css`와 다른 값은 `npm run tokens`로 확인한다. 설명 패널의 "디자인 값"은 `src/styles.css`에서 읽은 값을 보여 준다. 대본 칸 높이 같은 계측 조건(`--spacing-script-*`)은 설계서에서도 바꾸지 않는다.

## 2026-11-08 제품 동기화 점검 (#1111 후속)

기준: `develop-next`의 `c8a3881193ef759e3b935898850cd815510df2a0`와 선행 문서 PR #1111의 화면 ID·그룹 규칙. 실제 브라우저에서 제품을 실행한 픽셀 단위 비교가 아니라 **해당 리비전의 라우트·컴포넌트·주요 상태 코드 대조**다. 설계서의 모든 동작은 서버·카메라·마이크 없이 예시 데이터를 사용한다.

### 사용자 라우트 ↔ 설계서 항목

| 제품 라우트 | 실제 화면 구성 기준 | 설계서 ID | 점검 결과 |
| --- | --- | --- | --- |
| `/`, `/pitches` | `HomePage` · `HomePitchCard` · `HomeSurveyNotice` · `DeletePitchDialog` | `home` | 홈 푸터 정책 링크 추가; TIP·별도 스크롤의 샘플 차이는 주석에 기록 |
| `/pitch/new`, `/pitch/:id/edit` | `PitchCreatePage` · `VersionRail` · `SlidePane` · `ScriptPane` · `InfoPane` · `CriteriaPane` · 확인 대화 상자 | `pitch-create` | 이전 단계형 샘플 대신 제품의 단일 페이지형 구조로 교체; 기존 `preview` 상태 URL을 새 확인 창으로 연결 |
| `/pitch/:pitchId/device-check` | `DeviceCheckPage` · `ScreenFrame` · `CheckCard` · `ScriptModeChoice` | `prepare-mission` | 장치 점검 이후 Take 생성 규칙 유지; 자세한 제품과 샘플 차이는 `productNote` |
| `/pitch/:pitchId/prepare` | `PrepareRedirect` | `prepare-mission` | 호환 주소. 새 화면으로 중복 등록하지 않음 |
| `/takes/:takeId/rehearsal`, `/takes/:takeId/exam` | `RehearsalPage` · `ScriptPane` · `CoachStrip` | `stage`, `stage-blocked` | 동일 무대의 모드·접근 제한 상태를 구분; 샘플은 장치·WebSocket 미연동 |
| `/takes/:takeId/processing` | `ProcessingPage` | `processing-loading` | 상태별 독립 미리보기 유지 |
| `/takes/:takeId/confirm` | `ConfirmPage` | `confirm` | 텍스트 확인 미리보기 유지 |
| `/takes/:takeId/retry` | `RetryPage` | `retry` | 재전송·기록 없음 상태 미리보기 유지 |
| `/takes/:takeId` | `ReportPage` · `TakeSurvey` | `report` | 리포트·설문 샘플 유지; 상세 지표 일부는 설명 패널에 차이 기록 |
| `/takes`, `/takes?pitch=…&view=compare` | `TakesPage` · `TakeComparison` | `takes` | 목록/지표 비교 통합 구조가 이미 반영되어 있음 |
| `/pitch/:id/comparison` | `TakeComparisonRedirect` | `comparison` | 구 주소가 통합 Take 화면을 재사용; 공유 ID·경로 보존 |
| `/welcome`, `/login` | `WelcomePage` · `LoginPage` | `welcome`, `login` | 공개 시작·인증 샘플 유지; 실제 OAuth·애니메이션 일부는 미재현 |
| `/terms`, `/privacy` | `PolicyPage` | `terms`, `privacy` | **설계서에 누락되어 추가**. 각 7개 검토용 초안 조항을 제품 소스에서 복사 |
| `/about`, `/me`, `/unsupported`, `*` | 안내·계정·미지원·404 화면 | `about`, `me`, `unsupported`, `not-found` | 기존 상태·설명 유지 |

`/dev/stage`, `/dev/media`, `/dev/stt`는 사용자용 제품 화면이 아니라 내부 레이아웃·미디어·STT 검증 도구라 별도 화면을 만들지 않았다. 채팅 에이전트(`ChatAgentHost`)는 페이지 자체가 아닌 앱 전역 레이어이고, 제품에 이미 있는 부분을 설계서의 독립 API/실시간 채팅 시연으로 오인하지 않도록 미리보기 범위에서 제외한다.

### 기존 시안 분류

| 대상 | 분류 | 조치와 근거 |
| --- | --- | --- |
| `pitch-create` (기본 제품 샘플) | **최신화** | 제품은 이미 한 페이지형이다. `CreateConceptScreen`을 재사용하도록 전환하고 상태 설명·원본 경로 최신화 |
| `pitch-create-a` | **유지** | A안이 제품에 채택된 당시 검토 기록이다. 기본 제품 화면과 유사하지만 기존 PR·시안 비교 URL이 있어 삭제하지 않음 |
| `prepare-layout-review` | **유지** | 시작 전 세팅의 대체 배치를 검토하는 독립안. 실제 제품에 적용되었다고 표시하지 않음 |
| `takes` / `comparison` | **통합(기존 반영)** | 지표 비교는 `TakesScreen` 하나로 그리며 `comparison`은 호환용 래퍼로 유지 |
| `CreateScreen.tsx` | **제거 후보** | 이전 단계형 피치 생성 구현 파일. 현재 기본 설계서 렌더링에는 더 이상 연결하지 않지만, 과거 테스트·외부 참조 확인 전 삭제하지 않음 |
| `slide-preview` 구 샘플 ID / `pitch-create?state=preview` | **통합(호환)** | 슬라이드 확인 창으로 연결해 이름·ID를 유지. 별도 구형 팝업 UI는 만들지 않음 |
| `welcome` · `report` · `stage`의 단순화된 미리보기 | **유지·후속 최신화 후보** | 제품의 애니메이션·동적 분석·실시간 미디어는 정적 미리보기 대상이 아니다. 실제 표시 문구나 상태를 바꾸면 개별 동기화 필요 |

### 디자인 토큰과 동작 차이

- 제품 `frontend/src/index.css`와 설계서 `src/styles.css`를 대조하여 입력 미터의 `--color-meter-*` 7개, 생성 화면 전용 `--color-coral-deep` 재정의, `--font-mono: initial`을 맞췄다. 제품은 Pretendard와 `tabular-nums`를 사용한다. 옛 IBM Plex Mono 폰트 파일은 제거하지 않았다.
- **의도적인 미리보기 차이**: 서버 저장, PDF 렌더링, AI 평가기준 정리, 마이크·카메라·시선 보정, 실시간 코칭, 분석 진행, 설문 전송, OAuth 인증을 실제 수행하지 않는다. 각 `productNote`가 해당 화면의 축약 범위를 설명한다.
- **추가 대조 대상**: 피치 수정의 로딩·없는 자료·오류별 안내, 기준 검증·대본 매핑의 세부 오류, 랜딩 모션·채팅 레이어, 다양한 화면 높이에서의 스크롤·반응형 배치. 이번 작업의 정적 소스 검토만으로 시각적 동일성을 보증하지 않는다.
- `PRODUCT_CHECKED`는 대조한 `develop-next` 커밋으로 갱신했다. 브랜치에서 `npm run drift`는 Git 전체 이력이 필요하므로 checkout 후 `git fetch`를 선행한다. `npm run tokens`는 색·타이포 변수의 차이가 남았는지 점검한다.

### 검증 및 머지 순서

`docs/fe/design/`에서 `npm run typecheck`, `npm run lint`, `npm run test`, `npm run format:check`, `npm run build`, `npm run drift`, `npm run tokens`를 확인한다. 제품은 실행·수정하지 않는다. **선행 Draft PR #1111 → 후속 Draft PR 순**으로 리뷰하며, 먼저 머지하지 않는다.

## 2026-11-08 내부 명칭·구조 정리 (#1114 후속)

동기화 기록 위의 `CreateConceptScreen`과 `CreateScreen.tsx`는 당시 이름이다. 후속 정리 이후 현재 제품형 샘플은 `src/screens/PitchCreateScreen.tsx`이고 `pitch-create`와 기존 `pitch-create-a` ID에 모두 연결된다. 사용되지 않는 단계형 `CreateScreen.tsx`와 그 전용 `PaneHeading.tsx` · `PaneFooter.tsx`는 제거했다. `slide-preview` 호환 진입점은 제품형 샘플로 계속 연결된다.

`AnalysisScreen.tsx`는 실제 두 화면(`ProcessingScreen`, `ConfirmScreen`)을 내보내므로 `AnalysisScreens.tsx`로, `PrepareLayoutDraft.tsx`는 `PrepareLayoutDraftScreen.tsx`로 이름을 명확히 했다. 검토 시안 전용 UI와 CSS는 `src/ui/prepare-draft/`로, 시안 fixture는 `src/samples/prepareDraft.ts`로 이동했다. `ComparisonScreen.tsx`는 옛 주소 호환을 위해 유지하며, 실제 `frontend/` 소스와 공개 경로 및 화면 ID는 변경하지 않는다.

