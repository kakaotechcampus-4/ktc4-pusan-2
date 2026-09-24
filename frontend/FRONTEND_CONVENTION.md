# Frontend Convention

## 1. Formatting

- 프로젝트의 `.prettierrc` 설정을 따른다.
- 수동 정렬보다 자동 포맷터 결과를 우선한다.

## 2. Blank Lines

- 함수, 타입, 인터페이스, 컴포넌트 선언 사이에는 한 줄을 비운다.
- 함수 내부에서는 의미가 다른 처리 단계 사이에 한 줄을 비운다.
- 밀접하게 관련된 로직은 불필요하게 분리하지 않는다.

## 3. Function Structure

- 하나의 함수가 여러 역할을 수행하면 함수 분리를 고려한다.
- 함수는 이름만으로 주요 역할을 이해할 수 있도록 작성한다.

## 4. Naming

- 변수와 함수 이름은 역할과 의도가 드러나도록 작성한다.
- 의미가 불분명한 축약어 사용을 지양한다.
- 폴더 이름은 소문자로 쓴다. 컴포넌트 파일만 PascalCase 다 (`stage/RehearsalPage.tsx`).

## 5. Project Structure

**FSD(Feature-Sliced Design)가 아니다.** 도메인별로 묶은 구조이고, FSD 의
`pages` · `widgets` · `entities` 레이어를 두지 않는다. `features/` 라는 이름만 겹친다.

```
src/
  app/        라우팅
  features/   도메인 — auth · onboarding · pitch · rehearsal · report
  shared/     ui · lib · api. 도메인을 모른다
  workers/    시선 워커. 메인 스레드에서 격리
  types/      API 타입. 런타임 코드가 없다 (잎사귀)
  mocks/      MSW
```

### 의존 방향

`features → shared → types` 한 방향이다. 역방향은 없다.

- **`features/` 끼리 서로 import 하지 않는다.** 공유가 필요해지면 `shared/` 로 올린다.
- `shared/` 는 특정 도메인을 알지 못한다. `@/features/...` 를 import 하면 방향이 뒤집힌 것이다.
- `workers/` 는 `shared/` 를 참조하지 않는다. 타입만 가져와도 import 경로가 그렇게 생기면
  나중에 누군가 같은 경로에서 런타임 코드를 가져오고, 그 순간 워커 번들에
  TanStack Query 가 들어간다. 빌드는 통과하고 워커만 런타임에 죽는다.
- `types/` 는 아무것도 참조하지 않는다. 그래서 워커·IndexedDB·목 서버가 다 같이 쓸 수 있다.

### feature 내부 구획

파일이 많은 feature 만 하위 폴더로 쪼갠다. 지금은 `rehearsal/` 하나뿐이다 (파일 20여 개).

```
features/rehearsal/
  prepare/   화면 단계 — 준비 (P4)
  stage/     화면 단계 — 무대 (P5)
  media/     장치 — 카메라 · 마이크
  lib/       순수 로직 — IndexedDB · 구간 압축
```

**이 하위 폴더들은 독립 feature 가 아니라 `rehearsal` 의 내부 구획이다.**
`rehearsal` 밖에서 import 하지 않는다. 밖에서 필요해지면 그때 `shared/` 로 올린다.

나머지 feature 는 대부분 평범한 CRUD 화면이라 쪼갤 것이 없다. 파일 수가
비슷해지기 전에는 폴더를 미리 만들지 않는다.

## 6. AI-generated Code

- AI가 생성한 코드도 프로젝트의 동일한 컨벤션을 따른다.
- 작성자가 설명할 수 없는 코드는 그대로 반영하지 않는다.
