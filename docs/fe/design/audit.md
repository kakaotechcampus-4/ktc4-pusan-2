# 설계서와 제품 대조

설계서(`docs/fe/design/`)는 제품(`frontend/`) 코드를 import하지 않고 따로 돈다. 그래서 같은 화면이라도 설계서 미리보기와 제품이 다를 수 있다. 이 문서는 그 차이를 어디서 보고 어떻게 맞추는지 적는다.

## 화면별 차이

화면마다 제품과 다르게 그린 점은 화면 목록 `src/catalog.ts`의 `productNote`가 원본이다. 설계서 설명 패널의 "제품과 다른 점"에 보인다. 여기에 따로 적지 않는다 — 두 곳에 적으면 한쪽이 낡는다.

- **제품 화면 샘플**: 제품 코드를 읽고 설계서가 다시 그린 것이다. 장치 · API · 시간 흐름은 재현하지 않는다. 그리지 않은 것은 productNote에 "그리지 않는다", 제품과 맞춘 것은 "제품과 같다"로 적는다. 샘플이 줄인 곳을 "같다"고 쓰지 않는다.
- **시안**(`status: '시안'`): 제품에 아직 없는 새 디자인이다. 제품과 비교할 것이 없으니 productNote 대신 상태 설명에 무엇을 바꾸려는지 적는다. 제품에 이미 반영된 과거 검토안도 비교 기록이나 공유 URL이 남아 있으면 시안으로 보관할 수 있다.

제품 동작을 확인할 때는 제품 코드나 제품 개발 서버(http://localhost:3000)를 본다. 설명 패널의 "제품에서 열기"가 제품 목 데이터로 그 화면을 연다.

## 제품이 바뀌었는지 보기

`npm run drift`가 `catalog.ts`의 `PRODUCT_CHECKED`(마지막으로 대조한 제품 커밋) 뒤에 각 화면의 제품 코드(`sources`)가 바뀌었거나 없어졌는지 화면별로 보여 준다. 맞추는 절차는 README "제품이 바뀌었을 때"다.

### 2026-11-09 · #874 병합본 대조

- 검증 기준은 #874 병합 커밋 `0bf5dbb1`이다. 버튼 입력 차단, 연습 종료 확인 창의 처리 중 초점 유지, 설문·동의 완료 초점, 수정 대본 매핑 완료 초점, 장·미션 탐색의 경계 동작을 제품 코드와 대조했다.
- `npm run tokens`에 남은 두 선언은 제품 채팅 런처의 기본 위치와 작은 화면의 피치 생성 위치에 쓰이는 `--chat-launcher-bottom`이다. 설계서에 채팅 런처 샘플이 없어 제품에만 두 위치 값을 둔다.
- `npm run drift -- origin/develop-next`에서 `a313178b`까지 남은 14개 항목은 후속 동기화 범위다. 토큰 값 비교와 별도로 기반 스타일·반응형, 상단바·도움말, 홈, 슬라이드 확인·피치 생성, 시작 전 세팅, 리포트·Take 비교, 서비스 소개의 후속 변경을 대조한다.

## 디자인 값

색 · 글꼴 · 간격은 설계서의 `src/styles.css`가 기준이다. 제품 `frontend/src/index.css`와 다른 값은 `npm run tokens`로 확인한다. 설명 패널의 "디자인 값"은 `src/styles.css`에서 읽은 값을 보여 준다. 대본 칸 높이 같은 계측 조건(`--spacing-script-*`)은 설계서에서도 바꾸지 않는다.

## 제품 경로 ↔ 설계서 화면

제품 `frontend/src/app/routes.tsx`의 사용자 경로와 설계서 화면 ID다. 화면별 차이는 위 "화면별 차이"대로 productNote에만 적는다.

| 제품 경로 | 설계서 ID |
| --- | --- |
| `/`, `/pitches` | `home` |
| `/pitch/new`, `/pitch/:id/edit` | `pitch-create` (옛 A안 링크 `pitch-create-a`) |
| `/pitch/:pitchId/device-check`, `/pitch/:pitchId/prepare`(호환) | `prepare-mission` |
| `/takes/:takeId/rehearsal`, `/takes/:takeId/exam` | `stage`, `stage-blocked` |
| `/takes/:takeId/processing` | `processing-loading` |
| `/takes/:takeId/confirm` | `confirm` |
| `/takes/:takeId/retry` | `retry` |
| `/takes/:takeId` | `report` |
| `/takes` | `takes` |
| `/pitch/:id/comparison`(호환) | `comparison` |
| `/welcome`, `/login` | `welcome`, `login` |
| `/terms`, `/privacy` | `terms`, `privacy` |
| `/about`, `/me`, `/unsupported`, `*` | `about`, `me`, `unsupported`, `not-found` |

`/dev/stage` · `/dev/media` · `/dev/stt`는 내부 검증 도구라서, 채팅 에이전트(`ChatAgentHost`)는 페이지가 아닌 앱 전역 레이어라서 화면을 따로 두지 않는다.


## 2026-11-09 · 내부 명칭 및 검토 시안 구조 정리 (#1115)

기존 화면 ID·공유 주소와 제품 대조 기록(#874, 토큰 검사, 후속 drift 14개 항목)은 유지한다.

- 화면 샘플 파일·컴포넌트 이름: `CreateConceptScreen` → `PitchCreateScreen`, `AnalysisScreen` → `AnalysisScreens`, `PrepareLayoutDraft` → `PrepareLayoutDraftScreen`.
- 시작 전 세팅 시안의 UI와 CSS는 `src/ui/prepare-draft/`로, 예시 데이터는 `src/samples/prepareDraft.ts`로 이동했다.
- 사용하지 않는 이전 단계형 `CreateScreen.tsx`와 `PaneHeading.tsx` · `PaneFooter.tsx`는 선행 #1114에서 이미 제거했다.
