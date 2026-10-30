/** 목록의 "기초" 칸. 값은 src/styles.css가 원본이고, 여기에는 보기 · 규칙만 적는다 */
import type { GuideFoundation } from '../model';

const group = '기초';
const sources = ['frontend/src/index.css'];
const productNote =
  '값의 원본은 설계서 src/styles.css다. 제품 frontend/src/index.css와 다른 값은 `npm run tokens`로 본다.';

export const foundationEntries: GuideFoundation[] = [
  {
    kind: 'foundation',
    id: 'colors',
    title: '색',
    group,
    status: '제품에 있음',
    summary: '지면 · 글자 · 강조 · 무대 · 시선 · 분석 대기 색과 밝은 지면, 실제 조합의 글자 대비.',
    states: [
      {
        id: 'all',
        label: '전체 색',
        description: 'styles.css @theme의 색을 제목 주석별로 묶어 보인다.',
      },
      {
        id: 'light',
        label: '밝은 지면',
        description: '`pitch-create` 안에서 바뀌는 색과 그 지면의 글자 대비.',
      },
      {
        id: 'contrast',
        label: '글자 대비',
        description: '실제로 쓰는 글자 · 바탕 조합의 대비와 AA 판정.',
      },
    ],
    rules: [
      '여기 있는 이름만 쓴다. 임의값(`bg-[#…]`)은 쓰지 않는다.',
      '시선 상태 3색은 리허설 화면 테두리에만 쓴다. 다른 곳에서 초록 = 좋음으로 읽히면 안 된다.',
      '분석 대기(dusk) 색은 그 화면에서만 쓴다.',
      '대비가 4.5 미만인 조합은 본문에 쓰지 않는다(3 이상이면 큰 글자에만).',
      '밝은 지면은 색 이름을 바꾸지 않고 `pitch-create`로 감싸 값만 바꾼다.',
    ],
    knownIssues: [
      '기본 지면의 stone(보조 글자, 3.0~3.3)과 coral-deep 글자(4.3)는 AA에 못 미친다. 제품은 stone을 작은 캡션에 쓰고 있다. 밝은 지면의 stone(4.8)은 통과한다.',
    ],
    tokens: [],
    sources,
    productNote,
  },
  {
    kind: 'foundation',
    id: 'typography',
    title: '글꼴',
    group,
    status: '제품에 있음',
    summary: 'Pretendard · IBM Plex Mono와 제품에서 쓰는 글자 크기 · 굵기.',
    states: [
      { id: 'scale', label: '크기 · 굵기', description: '많이 쓰는 순서의 글자 크기와 쓰는 곳.' },
      { id: 'families', label: '글꼴', description: '본문 · 숫자 글꼴과 숫자 정렬 · 줄바꿈 규칙.' },
    ],
    rules: [
      '본문 · 버튼은 text-sm, 캡션 · 배지는 text-xs가 기본이다.',
      '강조는 font-bold 하나로 한다. 크기를 키우기보다 굵기와 위치로 위계를 만든다.',
      '타이머 · 지표처럼 숫자가 세로로 맞아야 하는 곳은 `tabular`를 붙인다.',
    ],
    tokens: [],
    sources,
    productNote,
  },
  {
    kind: 'foundation',
    id: 'spacing',
    title: '간격 · 크기',
    group,
    status: '제품에 있음',
    summary: '이름 붙은 크기, 바꾸면 안 되는 계측 조건, 간격 단위, 누르는 영역.',
    states: [
      { id: 'default', label: '기본', description: '크기 값 · 계측 조건 · 간격 · 누르는 영역.' },
    ],
    rules: [
      '대본 칸 높이(`--spacing-script-*`)는 계측 조건이다. 바꾸려면 팀 합의가 필요하다.',
      '누르는 것은 높이 44px 이상(min-h-11)으로 둔다.',
      '간격은 Tailwind 4px 단위(gap-2 · 3 · 4 · 6)에서 고른다.',
    ],
    tokens: [],
    sources,
    productNote,
  },
  {
    kind: 'foundation',
    id: 'surfaces',
    title: '테두리 · 그림자',
    group,
    status: '제품에 있음',
    summary: '선 · 어긋난 단색 그림자 · 각진 모서리 · 포커스 링.',
    states: [
      {
        id: 'default',
        label: '기본',
        description: '테두리 세 가지, 그림자 세 가지, 모서리, 포커스.',
      },
    ],
    rules: [
      '흐린 그림자 대신 어긋난 단색 그림자를 쓴다.',
      '면은 각지게 두고, 둥근 것은 원형 표시(rounded-full)에만 쓴다.',
      '포커스 링(`:focus-visible`)을 지우지 않는다.',
    ],
    tokens: [],
    sources,
    productNote,
  },
  {
    kind: 'foundation',
    id: 'responsive',
    title: '반응형 기준',
    group,
    status: '제품에 있음',
    summary:
      'Tailwind 크기 기준, 설계서가 더한 one-screen, 무대 전용 기준, 확인할 크기 · 브라우저 확대.',
    states: [
      {
        id: 'default',
        label: '기본',
        description: '지금 미리보기 크기 · 확대에서 켜진 기준과 기준별로 바뀌는 곳.',
      },
    ],
    rules: [
      '작은 화면부터 쓰고 넓어질 때 바꾼다(min-width 앞붙이).',
      '팀 기준 크기는 1366 × 650 · 1920 × 950(노트북 · 데스크톱 전용)이다. 휴대폰 · 태블릿은 참고용이다.',
      '브라우저 확대(크롬 125% · 150%, Windows 배율 포함)는 페이지가 쓰는 폭을 그만큼 줄인다. 1366 × 650을 125%로 보면 1093 × 520으로 배치된다. 팀 기준 두 크기를 125% · 150%에서도 본다.',
      '무대 대본 칸 높이는 어느 크기에서도 바꾸지 않는다(계측 조건).',
    ],
    tokens: [],
    sources: [...sources, 'frontend/src/features/rehearsal/stage/stage.css'],
    productNote,
  },
  {
    kind: 'foundation',
    id: 'writing',
    title: '문구',
    group,
    status: '제품에 있음',
    summary: '말투(해요체 · 마스코트), 상황별 문구, 숫자 · 용어 · 기호 표기.',
    states: [
      { id: 'tone', label: '말투', description: '기본 해요체, 치치 반말, 피코 존댓말과 섞인 곳.' },
      {
        id: 'patterns',
        label: '상황별',
        description: '실패 · 진행 중 · 막힘 · 버튼 · 되돌리기 어려움 · 빈 자리.',
      },
      {
        id: 'format',
        label: '표기',
        description: '측정 못 한 값 · 시간 · 비율 · 날짜 · 용어 · 화살표.',
      },
    ],
    rules: [
      '새 문구는 해요체로 쓴다. 마스코트만 각자의 말투를 쓴다.',
      '실패는 무엇이 안 됐는지와 할 일을 같이 쓴다.',
      '측정하지 못한 값은 "—"와 이유로 쓰고 0으로 채우지 않는다.',
    ],
    knownIssues: [
      '로그인 상태 줄 · 약관 · 웰컴 일부 · 시작 전 세팅 안내 줄 · 대본 표시 설명 등 일부 문구가 합니다체다. 해요체로 맞출지 FE와 정한다.',
    ],
    tokens: [],
    sources: [],
    productNote: '제품 문구(2026-10-29)를 보고 정리했다. 화면별 문구의 원본은 각 화면 샘플이다.',
  },
];
