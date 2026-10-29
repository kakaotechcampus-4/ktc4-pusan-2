/** 설계서 목록(catalog)과 미리보기 · 뷰어가 함께 쓰는 설계서 전용 모델이다. */
export interface GuideState {
  id: string;
  label: string;
  description: string;
}

/** 제품 개발 서버에서 같은 화면을 여는 예시 경로. 제품 목 데이터의 id를 쓴다 */
export interface ProductLink {
  label: string;
  path: string;
  note?: string;
}

/** 목록 한 칸의 공통 모양. 상태는 화면이면 화면 상태, 기초 · 컴포넌트면 보기(변형)다 */
interface GuideEntryBase {
  id: string;
  title: string;
  group: string;
  /** 시안: 제품에 아직 없는 새 디자인. 제품에 들어가면 '제품에 있음'으로 바꾼다(README "시안 관리") */
  status: '제품에 있음' | '시안';
  summary: string;
  states: GuideState[];
  rules: string[];
  tokens: string[];
  /** 제품 파일 경로. 참고용 문자열이고 import하지 않는다. `npm run drift`가 이 파일들의 변경을 본다 */
  sources: string[];
  /** 설계서 미리보기가 제품과 다른 점. 차이의 원본이고 설명 패널에 보인다 */
  productNote?: string;
  /** 제품에 남아 있어 FE와 정할 문제. 쓰는 규칙과 섞지 않는다 */
  knownIssues?: string[];
  productLinks?: ProductLink[];
}

/** 색 · 글꼴 · 간격처럼 모든 화면이 따르는 기초. 값은 src/styles.css에서 읽는다 */
export interface GuideFoundation extends GuideEntryBase {
  kind: 'foundation';
}

/** 버튼 · 배지 · 대화 상자처럼 여러 화면이 같이 쓰는 조각. 견본은 화면 샘플과 같은 src/ui를 쓴다 */
export interface GuideComponent extends GuideEntryBase {
  kind: 'component';
  /** 설계서 src/ui의 파일. 제품으로 옮길 때 이 파일을 본다 */
  file: string;
  /** 복사해 쓰는 사용 예 */
  code: string;
  /** [이름, 값, 설명] */
  props: [string, string, string][];
}

export interface GuideScreen extends GuideEntryBase {
  kind: 'screen';
  /** 제품 경로. 시안이면 제품에 넣을 경로 */
  route: string;
  components: string[];
  next: { label: string; screenId: string; stateId?: string }[];
}

export type GuideEntry = GuideFoundation | GuideComponent | GuideScreen;
