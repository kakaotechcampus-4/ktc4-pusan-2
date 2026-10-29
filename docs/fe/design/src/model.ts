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
  productLinks?: ProductLink[];
}

/** 색 · 글꼴 · 간격처럼 모든 화면이 따르는 기초. 값은 src/styles.css에서 읽는다 */
export interface GuideFoundation extends GuideEntryBase {
  kind: 'foundation';
}

export interface GuideScreen extends GuideEntryBase {
  kind: 'screen';
  /** 제품 경로. 시안이면 제품에 넣을 경로 */
  route: string;
  components: string[];
  next: { label: string; screenId: string; stateId?: string }[];
}

export type GuideEntry = GuideFoundation | GuideScreen;
