/**
 * #931: 튜토리얼 안내 화면에 보여 줄 샘플 발표의 장 목록.
 *
 * 샘플 발표 자체(PDF · 장별 대본 · 미리 만든 AI 결과)는 BE 가 갖고 있습니다
 * (`backend/src/pitch_coach_backend/module/pitch/sample/`). 장 제목을 바꾸면 그 PDF 와 맞지 않게 되니
 * 샘플을 바꿀 때 BE 와 같이 고칩니다.
 */
export const FIRST_TAKE_SLIDES = [
  {
    mission: 'MISSION 01',
    title: '첫 번째 Take, 시작합니다',
    subtitle: '완벽한 발표가 아닌 첫 연습의 완수가 목표예요.',
  },
  {
    mission: 'MISSION 02',
    title: '슬라이드가 바뀌면, 대본도 함께',
    subtitle: '현재 장에 연결된 대본을 보면서 발표해 보세요.',
  },
  {
    mission: 'MISSION 03',
    title: '발표 중에 확인할 세 가지',
    subtitle: '말하는 동안에도 발표 흐름을 살펴볼 수 있어요.',
  },
  {
    mission: 'MISSION 04',
    title: '코칭은 힌트, 발표는 내가',
    subtitle: '코칭을 기다리지 말고 이야기를 이어 가세요.',
  },
  {
    mission: 'MISSION 05',
    title: '첫 미션을 마무리할 시간',
    subtitle: '끝까지 연습한 뒤 실제 분석 결과를 확인해 보세요.',
  },
] as const;
