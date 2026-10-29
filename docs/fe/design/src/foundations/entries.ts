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
      '알려진 문제: 기본 지면의 stone(보조 글자, 3.0~3.3)과 coral-deep 글자(4.3)는 AA에 못 미친다. 제품은 stone을 작은 캡션에 쓰고 있어 FE와 고칠지 정한다. 밝은 지면의 stone(4.8)은 통과한다.',
      '밝은 지면은 색 이름을 바꾸지 않고 `pitch-create`로 감싸 값만 바꾼다.',
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
];
