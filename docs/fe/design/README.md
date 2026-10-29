# 화면 설계서

피치코치 화면 시안을 모아 보는 React 앱이다. **제품(`frontend/`)과 따로 돈다** — 제품 코드를 import하지 않고, 버튼 · 카드 같은 UI와 색 · 글꼴 · 간격 값을 이 폴더가 직접 가진다. 디자인은 제품보다 앞서거나 달라질 수 있어서, 둘 사이의 차이는 아래 "제품으로 옮기기"대로 확인한다.

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

## 폴더

| 위치 | 담는 것 |
| --- | --- |
| `src/drafts/` | 화면별 시안. `frontend/src/dev/design/`에서 옮겨 왔다 |
| `src/ui/` | 시안이 쓰는 버튼 · 카드 · 배지 · 마스코트 · 워드마크. 제품 `shared/ui`에서 복사해 시작했다 |
| `src/styles.css` | 색 · 글꼴 · 간격 등 디자인 값. 제품 `index.css`에서 복사해 시작했다 |
| `src/lib/` · `src/samples/` | 시안이 쓰는 시간 표기와 예시 데이터 |
| `public/` | 글꼴 · 마스코트 이미지 |
| `scripts/compare-tokens.mjs` | `npm run tokens`의 비교 스크립트 |

## 제품으로 옮기기

1. 설계서에서 디자인을 고친다(`src/styles.css` · `src/ui/` · 시안).
2. `npm run tokens`로 제품과 다른 디자인 값을 확인한다.
3. 제품에 반영할 것만 FE 작업으로 `frontend/src/index.css` · `shared/ui`에 옮긴다. 제품에서 먼저 바뀐 값은 반대로 설계서에 가져온다.

제품 화면 자체는 제품 개발 서버(`frontend/`에서 `npm run dev`, http://localhost:3000)에서 본다. 설계서 목록의 "구현된 화면"은 그 주소를 새 탭으로 연다.
