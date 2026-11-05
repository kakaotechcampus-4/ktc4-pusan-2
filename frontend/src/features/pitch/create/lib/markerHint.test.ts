import { describe, expect, it } from 'vitest';
import { scanSlideMarkers } from '@/shared/lib/slideMarkers';
import { markerHint } from './markerHint';

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
    expect(hint(script, 2)).toBe(`“Slide 1” 같은 줄은 ${LEGACY}. “[슬라이드 1]”처럼 바꿔 주세요.`);
  });

  it('구분을 하나도 못 찾으면 예전 표기를 짚어 준다', () => {
    expect(hint('Slide 1\n가\nSlide 2\n나', 2)).toBe(
      `“Slide 1” 등 2줄은 ${LEGACY}. “[슬라이드 1]”처럼 바꿔 주세요.`,
    );
    expect(hint('안녕하세요\n반갑습니다', 2)).toContain('슬라이드마다 구분해 주세요');
  });

  it('첫 구분 앞 글 · 번호 순서 안내는 예전 표기 안내보다 먼저다', () => {
    expect(hint('Slide 1\n인사말\n[슬라이드 2]\n나', 2)).toContain('첫 구분 앞에는 제목 한 줄만');
    expect(hint('1.\n[2]\n가\n[2]\n나', 2)).toContain('구분 번호는 1부터');
  });

  it('너무 긴 대본은 길이 안내만 한다', () => {
    expect(markerHint(scanSlideMarkers('Slide 1\n가'), 2, true)).toContain('조금 줄여 주세요');
  });
});
