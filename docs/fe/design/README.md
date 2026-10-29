# 화면 설계서

피치코치 화면과 시안을 모아 보는 React 앱이다. **제품(`frontend/`)과 따로 돈다** — 제품 코드를 import하지 않고, 버튼 · 카드 같은 UI와 색 · 글꼴 · 간격 값을 이 폴더가 직접 가진다. 디자인은 제품보다 앞서거나 달라질 수 있어서, 둘 사이의 차이는 [대조표](audit.md)와 아래 "제품으로 옮기기"대로 확인한다.

## 실행

이 폴더(`docs/fe/design/`)에서 실행한다. Node 24가 필요하다.

```sh
npm ci
npm run dev
```

브라우저에서 **http://localhost:3100** 을 연다. 로그인 · 목 서버 · API · 카메라 · 마이크를 쓰지 않는다.

| 명령 | 하는 일 |
| --- | --- |
| `npm run dev` | 개발 서버(3100). 파일을 저장하면 새로고침 없이 바로 바뀐다 |
| `npm run build` | 공유용 정적 파일을 `dist/`에 만든다. 정적 서버의 루트에 올리거나 `npm run preview`(3101)로 연다 |
| `npm run typecheck` · `lint` · `test` · `format:check` | 검사. 고친 뒤 넷 다 통과시킨다 |
| `npm run tokens` | 설계서와 제품의 디자인 값(CSS 변수) 중 다른 것을 보여 준다. 제품 파일은 읽기만 한다 |

## 화면을 살펴보는 방법

- 왼쪽 목록에서 화면 이름이나 경로를 검색한다. 좁은 화면에서는 "화면 목록 열기"를 누른다.
- "제품 화면 · 설계서 샘플"은 제품 코드를 읽고 설계서가 다시 그린 **샘플**이다. 서버 처리와 장치는 재현하지 않는다. "옛 시안"은 이전 검토 내용을 그대로 남긴다.
- **개별 보기**에서 "화면 상태"를 고르고, 화면 안 버튼으로 피치 생성 → 시작 전 세팅 → 리허설 → 분석 대기 → 리포트를 따라간다. 대본 확인 · 재시도 · 기록 · 비교 갈래도 이어진다. 화면 아래 "이어지는 화면"은 제품에서 자동으로 넘어가는 이동(분석 완료 → 리포트 등)도 대신한다.
- **전체 보기**에서는 모든 화면을 나란히 두고 카드마다 상태를 바꿔 비교한다. 카드 안에서 같은 화면의 상태를 바꾸면 전체 보기에 머물고, 다른 화면으로 가는 버튼 · 링크를 누르면 그 화면의 개별 보기로 간다.
- 오른쪽 **설명 패널**은 상태의 뜻, 제품 동작 규칙, 제품과 다른 점, "제품에서 열기", 사용하는 컴포넌트, 디자인 값, 제품 코드 위치를 보여 준다. "제품에서 열기"는 제품 개발 서버(http://localhost:3000)의 같은 화면을 제품 목 데이터로 새 탭에 연다. 로그인이 필요하면 그 탭의 브라우저 콘솔에 `mockSession.on()`을 입력한다.

목록에 없는 제품 화면(웰컴 `/welcome` · 로그인 `/login` · 개인정보 처리방침 `/privacy` · 이용약관 `/terms`)은 제품 개발 서버에서 바로 연다.

선택은 주소의 `screen`, `state`, `view`에 저장된다. 예를 들어 다음 주소는 시작 전 세팅의 마이크 오류 상태를 연다.

```text
http://localhost:3100/#/?screen=prepare-mission&state=mic-error&view=single
```

전체 보기의 카드별 상태는 `state.home=empty`처럼 `state.<화면 ID>`에도 저장된다. 다른 화면으로 가면 기록이 쌓여 브라우저 뒤로 가기로 돌아오고, 같은 화면 안 상태 전환은 기록을 쌓지 않는다. 옛 시안 주소 `#/<이름>`(예: `#/report`)은 해당 시안 항목(`draft-report`)으로 바뀌어 열리고, 없는 이름이면 첫 화면이 열린다.

## 화면 추가하는 법

1. `src/catalog.ts`에 화면을 등록한다. 고유 `id` · 상태 · 동작 규칙 · 제품 코드 위치(`sources`, 참고용 문자열) · `status`(`제품에 있음` / `시안만`)를 적고, 제품과 다르게 그린 점은 `productNote`와 [대조표](audit.md)에 함께 적는다. 제품 개발 서버로 열 수 있으면 `productLinks`에 예시 경로를 넣는다.
2. `src/screens/`에 화면 파일(예: `HomeScreen.tsx`)을 만들어 상태 ID마다 무엇이 바뀌는지 그리고, `src/ScreenPreview.tsx`의 `CurrentScreen`에 그 `id`를 한 줄 연결한다. 화면 안 버튼은 `onNavigate(화면 ID, 상태 ID)`로 이동하고, 여러 화면이 쓰는 틀 · 샘플 이름은 `src/screens/shared.tsx`에 둔다. 제품 경로로 가는 링크는 `ScreenPreview.tsx`의 `targetFor`에 연결하면 설계서 안에서만 이동한다.
3. 새 UI 조각이 필요하면 `src/ui/`에 둔다. 제품 컴포넌트를 가져올 때는 import하지 않고 복사하며, 파일 머리에 어디서 복사해 시작했는지 한 줄 적는다.
4. 옛 시안을 더할 때는 `src/drafts/`에 두고 `catalog.ts`의 `draftEntries`와 `ScreenPreview.tsx`의 `draftScreens`에 한 줄씩 넣는다.

## 저장 후 최신화 확인

개발 서버의 HMR(고친 모듈을 브라우저에서 바로 바꿔 끼우는 기능)이 이 폴더 전체를 감시한다.

1. `npm run dev`로 띄우고 개별 화면과 설명 패널을 열어 둔다.
2. `src/catalog.ts`의 상태 설명이나 `src/screens/`의 화면 문구를 고치고 저장한다.
3. 새로고침 없이 바뀐 문구가 보이는지 확인한다.
4. `src/styles.css`의 값을 바꿔 저장하면 미리보기 스타일과 설명 패널의 "디자인 값"이 함께 바뀌는지 확인한다. 확인용 변경은 되돌린다.

React 코드 종류에 따라 화면 안의 입력 상태는 처음으로 돌아갈 수 있다. 고른 화면과 상태는 주소로 복원된다. `npm run build` · `preview`의 결과는 저장을 자동 반영하지 않으므로 다시 빌드한다.

## 폴더

| 위치 | 담는 것 |
| --- | --- |
| `src/main.tsx` | 진입점. HashRouter로 뷰어를 연다 |
| `src/DesignGuide.tsx` | 뷰어. 검색 · 개별/전체 보기 · 상태 고르기 · 설명 패널 · 주소 저장과 옛 시안 주소 바꾸기 |
| `src/catalog.ts` · `src/model.ts` | 화면 목록과 그 모양(상태 · 규칙 · 제품과 다른 점 · 제품에서 열기 경로) |
| `src/ScreenPreview.tsx` | 미리보기 진입점. 화면 ID로 그릴 화면을 고르고, 미리보기 안의 링크를 설계서 안에서만 이동시킨다. 옛 시안은 시안 컴포넌트를 연다 |
| `src/screens/` | 제품 화면별 샘플(장치 · API 없이 그림). 분석 대기 · 대본 확인은 `AnalysisScreen.tsx`에 함께, 공통 틀 · 이동 타입은 `shared.tsx` |
| `src/*.test.tsx` · `src/drafts/drafts.test.tsx` | 뷰어의 주소 · 포커스, 미리보기 흐름 · 무대 클래스, 모든 시안이 그려지는지 확인 |
| `src/drafts/` | 화면별 옛 시안. `frontend/src/dev/design/`에서 옮겨 왔다 |
| `src/ui/` | 버튼 · 카드 · 배지 · 마스코트 · 워드마크와 홈 줄(HomePitchCard) · 노을 화면(DuskScreen) · 피치 생성 머리/바닥(PaneHeading · PaneFooter) · 무대 CSS(stage.css). 제품에서 복사해 시작했다 |
| `src/styles.css` | 색 · 글꼴 · 간격 등 디자인 값. 제품 `index.css`에서 복사해 시작했다 |
| `src/lib/` · `src/samples/` | 시간 표기와 예시 데이터 |
| `public/` | 글꼴 · 마스코트 · 노을 그림 |
| `scripts/compare-tokens.mjs` | `npm run tokens`의 비교 스크립트 |
| `audit.md` | 설계서 미리보기 · 옛 시안과 제품의 차이 |

## 제품으로 옮기기

1. 설계서에서 디자인을 고친다(`src/styles.css` · `src/ui/` · 미리보기 · 시안).
2. `npm run tokens`로 제품과 다른 디자인 값을 확인한다.
3. 제품에 반영할 것만 FE 작업으로 `frontend/src/index.css` · `shared/ui` 등에 옮긴다. 제품에서 먼저 바뀐 값은 반대로 설계서에 가져오고, [대조표](audit.md)를 맞춘다.

제품 화면 자체는 제품 개발 서버(`frontend/`에서 `npm run dev`, http://localhost:3000)에서 본다. 설명 패널의 "제품에서 열기"가 그 주소를 새 탭으로 연다.
