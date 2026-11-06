import { describe, expect, it } from 'vitest';
import { scanSlideMarkers } from '@/shared/lib/slideMarkers';
import { markerHint, unsegmentedNotice } from './markerHint';

/** 대본 입력 칸 아래 안내 — 실제 대본을 훑은 결과로 봅니다 */
const hint = (script: string, pageCount: number) =>
  markerHint(scanSlideMarkers(script), pageCount, false);

const MISMATCH = '장수를 맞춰야 연습을 시작할 수 있어요';
const LEGACY = '구분으로 인식되지 않아요';

describe('대본 입력 안내', () => {
  it('구분 뒤 본문의 번호 목록에는 예전 표기 안내를 띄우지 않는다', () => {
    const script =
      '[슬라이드 1]\n가\n[슬라이드 2]\n나\n[슬라이드 3]\n1. 첫째 이유는\n2) 둘째로\nSlide 3 shows';
    expect(hint(script, 3)).toBeNull();
  });

  it('장수가 다르면 본문에 번호 목록이 있어도 장수 안내를 띄운다', () => {
    const script = '[슬라이드 1]\n가\n[슬라이드 2]\n나\n[슬라이드 3]\n1. 첫째 이유는\n2) 둘째로';
    expect(hint(script, 5)).toBe(`구분은 3번까지, 슬라이드는 5장이에요. ${MISMATCH}.`);
  });

  it('장수 안내가 첫 구분 앞의 예전 표기 안내보다 먼저다', () => {
    const script = 'Slide 1\n[슬라이드 2]\n나';
    expect(hint(script, 3)).toContain(MISMATCH);
  });

  // 둘 다 첫 구분 앞에 예전 표기 한 줄 — 1번 구분이 빠졌을 때만 짚는다
  it.each([
    { name: '1번이 빠지면 예전 표기를 짚는다', script: 'Slide 1\n[슬라이드 2]\n나', hinted: true },
    {
      name: '첫 구분이 1번이면 앞 줄은 제목이라 짚지 않는다 — 바꾸면 번호가 겹친다',
      script: '1. 프로젝트 소개\n[1]\n가\n[2]\n나',
      hinted: false,
    },
  ])('$name', ({ script, hinted }) => {
    expect(hint(script, 2)).toBe(
      hinted ? `“Slide 1” 같은 줄은 ${LEGACY}. “[슬라이드 1]”처럼 바꿔 주세요.` : null,
    );
  });

  it('구분을 하나도 못 찾으면 장을 가리킨 예전 표기를 짚어 준다', () => {
    expect(hint('Slide 1\n가\nSlide 2\n나', 2)).toBe(
      `“Slide 1” 등 2줄은 ${LEGACY}. “[슬라이드 1]”처럼 바꿔 주세요.`,
    );
    expect(hint('1. 첫째로\n가\n슬라이드 2\n나', 2)).toBe(
      `“슬라이드 2” 같은 줄은 ${LEGACY}. “[슬라이드 1]”처럼 바꿔 주세요.`,
    );
    expect(hint('안녕하세요\n반갑습니다', 2)).toContain('슬라이드마다 구분해 주세요');
  });

  it('구분을 마크다운으로 꾸며 적었으면 그 줄을 짚는다 — 개수에는 넣지 않는다', () => {
    const script = '## [슬라이드 1]\n가\n## [슬라이드 2]\n나';
    expect(scanSlideMarkers(script).numbers).toEqual([]);
    expect(hint(script, 2)).toBe(
      '“## [슬라이드 1]”처럼 구분 앞에 다른 글자가 있으면 인식되지 않아요. “[슬라이드 1]”을 줄 맨 앞에 두세요.',
    );
    expect(hint('**[슬라이드 1]**\n가\nSlide 2\n나', 2)).toContain('“**[슬라이드 1]**”처럼');
  });

  it('구분 없이 번호 목록만 있으면 바꾸라고 하지 않고 기본 안내를 한다', () => {
    expect(hint('1. 첫째로\n가\n2. 둘째로\n나', 2)).toBe(
      '“[슬라이드 1]”처럼 슬라이드마다 구분해 주세요. 구분이 없으면 대본 전체가 한 슬라이드로 연결돼요.',
    );
    expect(hint('1) 첫째로\n2) 둘째로', 2)).toContain('슬라이드마다 구분해 주세요');
  });

  it('첫 구분 앞 글 · 번호 순서 안내는 예전 표기 안내보다 먼저다', () => {
    expect(hint('Slide 1\n인사말\n[슬라이드 2]\n나', 2)).toBe(
      '첫 구분 앞에 발표할 말이 있으면 나뉘지 않아요. 제목이 아니라면 “[슬라이드 1]”을 앞으로 옮겨 주세요.',
    );
    expect(hint('1.\n[2]\n가\n[2]\n나', 2)).toContain('구분 번호는 1부터');
  });

  it('너무 긴 대본은 길이 안내만 한다', () => {
    expect(markerHint(scanSlideMarkers('Slide 1\n가'), 2, true)).toContain('조금 줄여 주세요');
  });
});

describe('매핑했는데 나뉘지 않았을 때 안내', () => {
  const notice = (script: string) => unsegmentedNotice(scanSlideMarkers(script));

  // 입력 화면 안내와 같은 원인을 말한다 — 구분을 넣은 사람에게 "구분자가 없어"라고 하지 않는다
  it.each([
    {
      name: '구분자 없음',
      script: '가\n나',
      input: '슬라이드마다 구분해 주세요',
      after: '구분자가 없어',
    },
    {
      name: '마크다운으로 꾸민 구분',
      script: '## [슬라이드 1]\n가\n## [슬라이드 2]\n나',
      input: '구분 앞에 다른 글자가 있으면',
      after: '구분 앞에 다른 글자가 있어',
    },
    {
      name: '번호 겹침',
      script: '[1]\n가\n[1]\n나',
      input: '구분 번호는 1부터',
      after: '구분 번호가 1부터',
    },
    {
      name: '번호 거꾸로',
      script: '[2]\n가\n[1]\n나',
      input: '구분 번호는 1부터',
      after: '구분 번호가 1부터',
    },
    {
      name: '첫 구분 앞에 두 줄 이상',
      script: '제목\n인사말\n[1]\n가',
      input: '첫 구분 앞에 발표할 말이',
      after: '첫 구분 앞의 글을 발표할 말로 보고',
    },
  ])('$name', ({ script, input, after }) => {
    expect(hint(script, 1)).toContain(input);
    expect(notice(script)).toContain(after);
  });

  it('구분과 번호가 맞는데 나뉘지 않았으면 첫 구분 앞 한 줄을 LLM 이 발표할 말로 본 것이다', () => {
    expect(notice('안녕하세요, 발표를 시작하겠습니다.\n[1]\n가\n[2]\n나')).toContain(
      '첫 구분 앞의 글을 발표할 말로 보고',
    );
  });
});
