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
  /** 제품 경로(제품에 있음) 또는 옛 시안 주소(시안만) */
  route: string;
  status: '제품에 있음' | '시안만';
  summary: string;
  states: GuideState[];
  rules: string[];
  components: string[];
  tokens: string[];
  /** 제품 파일 경로. 참고용 문자열이고 import하지 않는다 */
  sources: string[];
  /** 설계서 미리보기가 제품과 다른 점. 같은 내용을 audit.md에도 적는다 */
  productNote?: string;
  productLinks?: ProductLink[];
  next: { label: string; screenId: string; stateId?: string }[];
}
