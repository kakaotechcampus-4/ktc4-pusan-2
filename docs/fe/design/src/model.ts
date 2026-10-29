/** 화면 목록(catalog)과 미리보기 · 뷰어가 함께 쓰는 설계서 전용 모델이다. */
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

export interface GuideScreen {
  id: string;
  title: string;
  group: string;
  /** 제품 경로. 시안이면 제품에 넣을 경로 */
  route: string;
  /** 시안: 제품에 아직 없는 새 디자인. 제품에 들어가면 '제품에 있음'으로 바꾼다(README "시안 관리") */
  status: '제품에 있음' | '시안';
  summary: string;
  states: GuideState[];
  rules: string[];
  components: string[];
  tokens: string[];
  /** 제품 파일 경로. 참고용 문자열이고 import하지 않는다 */
  sources: string[];
  /** 설계서 미리보기가 제품과 다른 점. 차이의 원본이고 설명 패널에 보인다 */
  productNote?: string;
  productLinks?: ProductLink[];
  next: { label: string; screenId: string; stateId?: string }[];
}
