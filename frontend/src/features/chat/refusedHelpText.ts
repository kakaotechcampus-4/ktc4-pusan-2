/**
 * 거절(`REFUSED`) 답 아래 안내 문구 (12-4 F8 `REFUSED` "할 수 있는 질문 예시").
 * 문구 · 예시는 FE 가 정했습니다(문서에는 동작만 있고 문장은 없습니다). 피치를 고르지 않은 전체 범위에서도 어색하지 않게
 * 피치 이름을 넣지 않습니다
 */

export const REFUSED_HELP_TITLE = '이런 걸 도와드릴 수 있어요';

export const REFUSED_HELP_ABILITIES = [
  '내 연습 기록에서 고칠 점 찾기',
  'Take끼리 비교하기',
  '다음 연습 목표 잡기',
  '발표법 · 화면 사용법 안내',
] as const;

export const REFUSED_HELP_EXAMPLES = [
  '최근 Take에서 가장 고칠 점이 뭐야?',
  '지난 Take랑 비교해 줘',
  '다음 연습 목표 잡아 줘',
  '발표 시작은 어떻게 하면 좋아?',
] as const;
