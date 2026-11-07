import { describe, expect, it } from 'vitest';
import { scanSlideMarkers } from '@/shared/lib/slideMarkers';
import { markerHint, unsegmentedNotice } from './markerHint';

/** 대본 입력 칸 아래 안내 — 실제 대본을 훑은 결과로 봅니다 */
const hint = (script: string, pageCount: number) =>
  markerHint(scanSlideMarkers(script), pageCount, false);

const MISMATCH = '장수를 맞춰야 연습을 시작할 수 있어요';
const LEGACY = '구분으로 인식되지 않아요';
const FORM_FIX = '꾸밈이나 다른 괄호 없이 “[슬라이드 1]”처럼 줄 맨 앞에 적어 주세요.';
/** 전각 공백 — 원문에 보이지 않는 글자를 그대로 두지 않으려고 만듭니다 */
const WIDE_SPACE = String.fromCodePoint(0x3000);

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

  it('장수 안내가 첫 구분 앞의 인식되지 않는 줄 안내보다 먼저다', () => {
    const script = '슬라이드 1.\n[슬라이드 2]\n나';
    expect(hint(script, 3)).toContain(MISMATCH);
  });

  // 둘 다 첫 구분 앞에 인식되지 않는 한 줄 — 1번 구분이 빠졌을 때만 짚는다
  it.each([
    {
      name: '1번이 빠지면 그 줄을 짚는다',
      script: '슬라이드 1.\n[슬라이드 2]\n나',
      expected: `“슬라이드 1.” 같은 줄은 ${LEGACY}. “[슬라이드 1]”처럼 바꿔 주세요.`,
    },
    {
      name: '1번이 빠지면 꾸민 줄도 짚는다',
      script: '**[슬라이드 1]**\n[슬라이드 2]\n나',
      expected: `“**[슬라이드 1]**” 같은 줄은 ${LEGACY}. ${FORM_FIX}`,
    },
    {
      name: '첫 구분이 1번이면 앞 줄은 제목이라 짚지 않는다 — 바꾸면 번호가 겹친다',
      script: '1. 프로젝트 소개\n[1]\n가\n[2]\n나',
      expected: null,
    },
  ])('$name', ({ script, expected }) => {
    expect(hint(script, 2)).toBe(expected);
  });

  // script_parser-1.3 부터 구분자 — 예전에는 "구분으로 인식되지 않아요" 로 짚던 표기입니다
  it.each([
    'Slide 1\n가\nSlide 2\n나',
    '슬라이드 1\n가\n슬라이드 2\n나',
    '## [슬라이드 1]\n가\n## [슬라이드 2]\n나',
    '## Slide 1 — 문제\n가\n## Slide 2 — 해결\n나',
    '[슬라이드1]\n가\n슬라이드 2： 나',
  ])('%j 는 구분으로 세어 안내하지 않는다', (script) => {
    expect(scanSlideMarkers(script).numbers).toEqual([1, 2]);
    expect(hint(script, 2)).toBeNull();
  });

  it('구분을 하나도 못 찾으면 장을 가리킨 줄을 짚어 준다', () => {
    expect(hint('슬라이드 1.\n가\n슬라이드 2.\n나', 2)).toBe(
      `“슬라이드 1.” 등 2줄은 ${LEGACY}. “[슬라이드 1]”처럼 바꿔 주세요.`,
    );
    expect(hint('1. 첫째로\n가\n슬라이드 2 - 문제\n나', 2)).toBe(
      `“슬라이드 2 - 문제” 같은 줄은 ${LEGACY}. “[슬라이드 1]”처럼 바꿔 주세요.`,
    );
    expect(hint('안녕하세요\n반갑습니다', 2)).toContain('슬라이드마다 구분해 주세요');
  });

  // #826 리뷰: 1.3 에서도 받지 않는 흔한 꼴 — 무엇을 고칠지 그 줄을 짚는다. 개수에는 넣지 않는다
  it.each([
    { script: '**[슬라이드 1]**\n가\n**[슬라이드 2]**\n나', line: '**[슬라이드 1]**” 등 2줄은' },
    { script: '【슬라이드 1】\n가\n【슬라이드 2】\n나', line: '【슬라이드 1】” 등 2줄은' },
    { script: '> [1] 도입\n가', line: '> [1] 도입” 같은 줄은' },
    { script: '#Slide 1\n가', line: '#Slide 1” 같은 줄은' },
    // 꾸민 줄이 장을 가리킨 줄보다 먼저다 — 고칠 곳이 더 분명하다
    { script: '슬라이드 1.\n가\n> [2]\n나', line: '> [2]” 같은 줄은' },
  ])('꾸민 구분 $line', ({ script, line }) => {
    expect(scanSlideMarkers(script).numbers).toEqual([]);
    expect(hint(script, 2)).toBe(`“${line} ${LEGACY}. ${FORM_FIX}`);
  });

  it('줄 앞의 특수 공백 때문에 인식되지 않으면 공백을 지우라고 한다', () => {
    const script = `${WIDE_SPACE}[1]\n가\n${WIDE_SPACE}[2]\n나`;
    expect(scanSlideMarkers(script).numbers).toEqual([]);
    expect(hint(script, 2)).toBe(
      `“[1]” 앞에 전각 공백 같은 특수 공백이 있어 ${LEGACY}. 줄 맨 앞의 공백을 지워 주세요.`,
    );
  });

  it('구분 없이 번호 목록만 있으면 바꾸라고 하지 않고 기본 안내를 한다', () => {
    expect(hint('1. 첫째로\n가\n2. 둘째로\n나', 2)).toBe(
      '“[슬라이드 1]”처럼 슬라이드마다 구분해 주세요. 구분이 없으면 대본 전체가 한 슬라이드로 연결돼요.',
    );
    expect(hint('1) 첫째로\n2) 둘째로', 2)).toContain('슬라이드마다 구분해 주세요');
  });

  it('첫 구분 앞 글 · 번호 순서 안내는 예전 표기 안내보다 먼저다', () => {
    expect(hint('슬라이드 1.\n인사말\n[슬라이드 2]\n나', 2)).toBe(
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
      script: '**[슬라이드 1]**\n가\n**[슬라이드 2]**\n나',
      input: `${LEGACY}. ${FORM_FIX}`,
      after: `구분으로 인식되지 않아 대본 전체를 한 슬라이드로 연결했어요. ${FORM_FIX}`,
    },
    {
      name: '장을 가리킨 줄',
      script: '슬라이드 1.\n가\n슬라이드 2.\n나',
      input: `“슬라이드 1.” 등 2줄은 ${LEGACY}`,
      after: '“슬라이드 1.” 등 2줄은 구분으로 인식되지 않아 대본 전체를',
    },
    {
      name: '줄 앞 특수 공백',
      script: `${WIDE_SPACE}[1]\n가\n${WIDE_SPACE}[2]\n나`,
      input: '줄 맨 앞의 공백을 지워 주세요',
      after:
        '특수 공백이 있어 대본 전체를 한 슬라이드로 연결했어요. 줄 맨 앞의 공백을 지워 주세요.',
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
