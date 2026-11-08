/**
 * 대화창 첫 화면과 거절(`REFUSED`) 답 아래에 같이 쓰는 안내 문구 (12-4 F8 `REFUSED` "할 수 있는 질문 예시").
 * 첫 화면에 붙이는 것은 FE 가 더한 것입니다(문서에 없음).
 * 문구 · 예시는 FE 가 정했습니다(문서에는 동작만 있고 문장은 없습니다). 피치를 고르지 않은 전체 범위에서도 어색하지 않게
 * 피치 이름을 넣지 않습니다
 */

export const ASK_HELP_TITLE = '이런 걸 도와드릴 수 있어요';

export const ASK_HELP_ABILITIES = [
  '내 연습 기록에서 고칠 점 찾기',
  'Take끼리 비교하기',
  '대화 목표 정하고 다음 Take에서 확인하기',
  '발표법 · 화면 사용법 안내',
] as const;

export const ASK_HELP_EXAMPLES = [
  '최근 Take에서 가장 고칠 점이 뭐야?',
  '지난 Take랑 비교해 줘',
  '대화 목표는 어떻게 저장하고 다음 Take에서 확인해?',
  '발표 시작은 어떻게 하면 좋아?',
] as const;
