# 화면 설계서

피치코치 화면과 시안을 모아 보는 React 앱이다. **제품(`frontend/`)과 따로 돈다** — 제품 코드를 import하지 않고, 버튼 · 카드 같은 UI와 색 · 글꼴 · 간격 값을 이 폴더가 직접 가진다. 디자인은 제품보다 앞서거나 달라질 수 있어서, 화면별 차이는 설명 패널의 "제품과 다른 점"(`src/catalog.ts`의 `productNote`)에서 보고, 대조 방법은 [제품 대조](audit.md)와 아래 "제품이 바뀌었을 때" · "제품으로 옮기기"에 있다.

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
| `npm run drift` | 마지막 대조 뒤로 바뀌거나 없어진 제품 코드를 화면별로 보여 준다(기본 `origin/develop-next`, 먼저 `git fetch`). 다른 ref는 `npm run drift -- <ref>` |

## 화면을 살펴보는 방법

- 왼쪽 목록에서 화면 이름이나 경로를 검색한다. 좁은 화면에서는 "화면 목록 열기"를 누른다.
- "제품 화면 · 설계서 샘플" · "안내 · 계정 화면"은 제품 코드를 읽고 설계서가 다시 그린 **샘플**이다. 서버 처리와 장치는 재현하지 않는다. 아직 제품에 없는 새 디자인은 상태가 **시안**으로 표시된다(아래 "시안 관리").
- **개별 보기**에서 "화면 상태"를 고르고, 화면 안 버튼으로 피치 생성 → 시작 전 세팅 → 리허설 → 분석 대기 → 리포트를 따라간다. 대본 확인 · 재시도 · 기록 · 비교 갈래도 이어진다. 화면 아래 "이어지는 화면"은 제품에서 자동으로 넘어가는 이동(분석 완료 → 리포트 등)도 대신한다.
- **전체 보기**에서는 모든 화면을 나란히 두고 카드마다 상태를 바꿔 비교한다. 카드 안에서 같은 화면의 상태를 바꾸면 전체 보기에 머물고, 다른 화면으로 가는 버튼 · 링크를 누르면 그 화면의 개별 보기로 간다.
- 미리보기는 **iframe** 안에 그린다. iframe 폭이 곧 화면 폭이라, 샘플의 `sm:` · `lg:` 배치와 `stage.css`의 미디어 쿼리가 제품처럼 그 화면 크기에 반응한다(뷰어 안에 바로 그리면 브라우저 전체 폭에 반응해 좁은 칸에 2단 배치가 켜진다). 위 **미리보기 크기**에서 "칸에 맞춤" 또는 모바일 · 태블릿 · 노트북 · 데스크톱 크기를 고르면 그 크기로 그린 뒤 칸 폭에 맞게 줄여 보인다. 전체 보기에도 같이 적용된다.
- **새 탭에서 화면만 보기**는 지금 화면 · 상태를 뷰어 없이 브라우저 창 크기로 연다(`#/frame?screen=&state=`). 그 탭에서도 화면 안 버튼으로 이동한다.
- 오른쪽 **설명 패널**은 상태의 뜻, 제품 동작 규칙, 제품과 다른 점, "제품에서 열기", 사용하는 컴포넌트, 디자인 값, 제품 코드 위치를 보여 준다. "제품에서 열기"는 제품 개발 서버(http://localhost:3000)의 같은 화면을 제품 목 데이터로 새 탭에 연다. 로그인이 필요하면 그 탭의 브라우저 콘솔에 `mockSession.on()`을 입력한다.

목록에 없는 제품 화면(웰컴 `/welcome` · 로그인 `/login` · 개인정보 처리방침 `/privacy` · 이용약관 `/terms`)은 제품 개발 서버에서 바로 연다.

선택은 주소의 `screen`, `state`, `view`, `device`(미리보기 크기, "칸에 맞춤"이면 없음)에 저장된다. 예를 들어 다음 주소는 시작 전 세팅의 마이크 오류 상태를 노트북 크기로 연다.

```text
http://localhost:3100/#/?screen=prepare-mission&state=mic-error&view=single&device=laptop
```

전체 보기의 카드별 상태는 `state.home=empty`처럼 `state.<화면 ID>`에도 저장된다. 다른 화면으로 가면 기록이 쌓여 브라우저 뒤로 가기로 돌아오고, 같은 화면 안 상태 전환은 기록을 쌓지 않는다. 옛 시안 주소 `#/<이름>`(예: `#/report`)은 같은 ID의 화면으로 바뀌어 열리고, 없는 이름이면 첫 화면이 열린다.

## 화면 추가하는 법

1. `src/catalog.ts`에 화면을 등록한다. 고유 `id` · 상태 · 동작 규칙 · 제품 코드 위치(`sources`, 참고용 문자열) · `status`(`제품에 있음` / `시안`)를 적고, 제품과 다르게 그린 점은 `productNote`에만 적는다([제품 대조](audit.md)의 쓰는 법). 제품 개발 서버로 열 수 있으면 `productLinks`에 예시 경로를 넣는다.
2. `src/screens/`에 화면 파일(예: `HomeScreen.tsx`)을 만들어 상태 ID마다 무엇이 바뀌는지 그리고, `src/ScreenPreview.tsx`의 `CurrentScreen`에 그 `id`를 한 줄 연결한다. 화면 안 버튼은 `onNavigate(화면 ID, 상태 ID)`로 이동하고, 여러 화면이 쓰는 틀 · 샘플 이름은 `src/screens/shared.tsx`에 둔다. 제품 경로로 가는 링크는 `ScreenPreview.tsx`의 `targetFor`에 연결하면 설계서 안에서만 이동한다.
3. 새 UI 조각이 필요하면 `src/ui/`에 둔다. 제품 컴포넌트를 가져올 때는 import하지 않고 복사하며, 파일 머리에 어디서 복사해 시작했는지 한 줄 적는다.
## 시안 관리

새 디자인과 제품 샘플을 **같은 자리**(`catalog.ts` + `src/screens/`)에서 관리한다. 시안을 따로 모아 두는 폴더는 두지 않는다 — 검사 · 빌드에서 빼야 하고, 제품이 바뀌어도 아무도 고치지 않아 금방 낡는다.

1. **새 디자인**: `catalog.ts`에 `status: '시안'`으로 화면을 더하고(`route`는 제품에 넣을 경로), `src/screens/`에 그린다. 기존 화면을 바꾸는 시안이면 그 화면에 상태를 더하고 상태 설명에 "시안"을 적는다.
2. **제품에 들어가면**: `status`를 `제품에 있음`으로 바꾸고 `sources` · `productNote` · `productLinks`를 채운다. 샘플은 제품 코드에 맞춘다.
3. **채택하지 않으면**: 지운다. 기록은 git 이력에 남는다.

예전 시안 14개(`frontend/src/dev/design/`에서 옮겨 온 것)는 모두 제품에 구현되거나 다른 방식으로 바뀌어 지웠다. 필요하면 git 이력에서 본다.

```sh
# 목록
git ls-tree -r --name-only d83376c6 -- frontend/src/dev/design/
# 한 파일 (예: 리포트 시안)
git show d83376c6:frontend/src/dev/design/report/ReportDraft.tsx
```

## 저장 후 최신화 확인

개발 서버의 HMR(고친 모듈을 브라우저에서 바로 바꿔 끼우는 기능)이 이 폴더 전체를 감시한다. 뷰어와 미리보기 iframe이 각각 바꿔 끼우고, iframe이 다시 그리면 설명 패널의 "디자인 값"도 다시 읽는다.

1. `npm run dev`로 띄우고 개별 화면과 설명 패널을 열어 둔다.
2. `src/catalog.ts`의 상태 설명이나 `src/screens/`의 화면 문구를 고치고 저장한다.
3. 새로고침 없이 바뀐 문구가 보이는지 확인한다.
4. `src/styles.css`의 값을 바꿔 저장하면 미리보기 스타일과 설명 패널의 "디자인 값"이 함께 바뀌는지 확인한다. 확인용 변경은 되돌린다.

React 코드 종류에 따라 화면 안의 입력 상태는 처음으로 돌아갈 수 있다. 고른 화면과 상태는 주소로 복원된다. `npm run build` · `preview`의 결과는 저장을 자동 반영하지 않으므로 다시 빌드한다.

## 폴더

| 위치 | 담는 것 |
| --- | --- |
| `src/main.tsx` | 진입점. HashRouter로 뷰어를 연다 |
| `src/DesignGuide.tsx` | 뷰어. 검색 · 개별/전체 보기 · 상태 · 미리보기 크기 고르기 · 설명 패널 · 주소 저장과 옛 주소 바꾸기 |
| `src/PreviewFrame.tsx` · `src/FramePage.tsx` · `src/frame.ts` | 미리보기 iframe(크기 · 줄이기 · 이동 메시지), iframe이 여는 화면만 보기 주소, 둘이 함께 쓰는 값 |
| `src/catalog.ts` · `src/model.ts` | 화면 목록과 그 모양(상태 · 규칙 · 제품과 다른 점 · 제품에서 열기 경로) |
| `src/ScreenPreview.tsx` | 미리보기 진입점. 화면 ID로 그릴 화면을 고르고, 미리보기 안의 링크를 설계서 안에서만 이동시킨다 |
| `src/screens/` | 화면별 샘플(장치 · API 없이 그림). 분석 대기 · 대본 확인은 `AnalysisScreen.tsx`, 소개 · 미지원 · 404 · 마이페이지는 `InfoScreens.tsx`, 공통 틀 · 이동 타입은 `shared.tsx` |
| `src/*.test.tsx` | 뷰어의 주소 · 포커스, 미리보기 iframe의 크기 · 메시지, 미리보기 흐름 · 무대 클래스, 모든 화면 · 상태가 그려지는지 확인 |
| `src/ui/` | 버튼 · 카드 · 배지 · 마스코트 · 워드마크와 홈 줄(HomePitchCard) · 노을 화면(DuskScreen) · 피치 생성 머리/바닥(PaneHeading · PaneFooter) · 무대 CSS(stage.css). 제품에서 복사해 시작했다 |
| `src/styles.css` | 색 · 글꼴 · 간격 등 디자인 값. 제품 `index.css`에서 복사해 시작했다 |
| `src/lib/` · `src/samples/` | 시간 표기와 예시 데이터 |
| `public/` | 글꼴 · 마스코트 · 노을 그림 |
| `scripts/compare-tokens.mjs` · `scripts/product-drift.mjs` | `npm run tokens` · `npm run drift`의 스크립트 |
| `audit.md` | 제품과의 차이를 어디서 보고 어떻게 맞추는지 |

## 제품으로 옮기기

1. 설계서에서 디자인을 고친다(`src/styles.css` · `src/ui/` · 미리보기 · 시안).
2. `npm run tokens`로 제품과 다른 디자인 값을 확인한다.
3. 제품에 반영할 것만 FE 작업으로 `frontend/src/index.css` · `shared/ui` 등에 옮긴다. 제품에서 먼저 바뀐 값은 반대로 설계서에 가져온다.

## 제품이 바뀌었을 때

FE PR이 merge돼 설계서가 가리키는 제품 화면이 바뀌면 설명을 다시 맞춘다. 출시 전처럼 제품이 자주 바뀔 때는 화면 설계서를 고치기 전에 한 번씩 한다.

1. 저장소에서 `git fetch`한 뒤 이 폴더에서 `npm run drift`를 실행한다. 바뀐 화면과 파일이 나온다.
2. 바뀐 파일의 변경을 읽는다. 출력 첫 줄의 두 커밋으로 `git diff <대조 기준> origin/develop-next -- <파일>`을 본다.
3. 그 화면의 `catalog.ts` 동작 규칙 · 상태 · `productNote`와 `src/screens/`의 샘플 문구를 제품 코드에 맞춘다. 파일이 옮겨졌으면 `sources`를 고친다. 그리지 않을 것은 productNote에 적는다.
4. `catalog.ts`의 `PRODUCT_CHECKED`를 비교한 커밋(출력 마지막 줄)과 날짜로 올리고, `npm run drift`가 "바뀐 제품 코드가 없습니다"를 내는지 본다.
5. 검사(`typecheck` · `lint` · `test` · `format:check`)를 통과시키고, 2~4를 한 커밋으로 올린다.

제품 화면 자체는 제품 개발 서버(`frontend/`에서 `npm run dev`, http://localhost:3000)에서 본다. 설명 패널의 "제품에서 열기"가 그 주소를 새 탭으로 연다.
