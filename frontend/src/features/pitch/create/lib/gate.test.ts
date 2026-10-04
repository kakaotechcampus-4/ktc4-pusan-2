import { describe, expect, it } from 'vitest';
import { computeGate } from './gate';
import type { CriteriaVersion, PitchDraft, ScriptVersion, SlideVersion } from './draft';

/**
 * 새 시안(발표정보 · 평가기준 · 슬라이드 · 대본 입력 · 매핑 확인)의 진행 조건.
 *
 * 이 상자는 "다음" 버튼이 열릴지를 정합니다. 한 조합만 틀려도 슬라이드 없이
 * 연습이 시작되거나, 다 채웠는데 버튼이 안 열립니다. 조합이 몇 가지뿐이라
 * 전부 적어 두는 편이 규칙을 말로 설명하는 것보다 정확합니다.
 */

const SCRIPT_TEXT = '가'.repeat(1_284);

const EMPTY: PitchDraft = {
  title: '',
  presentationDate: '',
  timeLimitSec: 300,
  toleranceBelowSec: 30,
  toleranceAboveSec: 60,
  infoSaved: false,
  slides: [],
  scripts: [],
  criteria: [],
};

const INFO: Partial<PitchDraft> = { title: '캡스톤 중간발표', infoSaved: true };

const slide = (version: number, pageCount: number | null): SlideVersion => ({
  version,
  fileName: pageCount === null ? null : 'pitch-coach.pdf',
  pageCount,
});

const script = (version: number, patch: Partial<ScriptVersion> = {}): ScriptVersion => ({
  version,
  slideVersion: 1,
  text: SCRIPT_TEXT,
  blocks: null,
  mappingSaved: false,
  ...patch,
});

const mapped = (version: number, blockCount = 12): ScriptVersion =>
  script(version, { blocks: Array<string>(blockCount).fill('블록'), mappingSaved: true });

const criteria = (version: number, count: number, saved = true): CriteriaVersion => ({
  version,
  source: '원문',
  organizedSource: '원문',
  items: Array.from({ length: count }, (_, i) => ({ id: `c${i}`, text: `기준 ${i}` })),
  skipped: [],
  saved,
});

/** 발표정보·슬라이드·매핑·평가기준이 다 저장된 초안 */
const READY: PitchDraft = {
  ...EMPTY,
  ...INFO,
  slides: [slide(1, 12)],
  scripts: [mapped(1)],
  criteria: [criteria(1, 3)],
};

describe('진행 조건 — 시안 순서대로', () => {
  it('아무것도 없다', () => {
    const gate = computeGate(EMPTY);

    expect(gate.message).toBe('발표정보를 입력하고 저장해주세요');
    expect(gate.rows.map((r) => r.label)).toEqual([
      '슬라이드 미등록',
      '대본 미등록',
      '평가기준 0개',
    ]);
    expect(gate.ready).toBe(false);
  });

  it('발표정보까지 저장했다 — 이제 슬라이드와 대본', () => {
    expect(computeGate({ ...EMPTY, ...INFO }).message).toBe('슬라이드와 대본을 입력해주세요');
  });

  it('슬라이드 12장만 있다', () => {
    const gate = computeGate({ ...EMPTY, ...INFO, slides: [slide(1, 12)] });

    expect(gate.message).toBe('대본을 입력해주세요');
    expect(gate.rows.map((r) => r.label)).toEqual(['슬라이드 12장', '대본 미등록', '평가기준 0개']);
    expect(gate.rows[0]!.done).toBe(true);
  });

  /** ★ 세 번째 줄이 평가기준에서 매핑으로 바뀌는 장면입니다 */
  it('대본을 썼지만 아직 안 나눴다', () => {
    const gate = computeGate({ ...EMPTY, ...INFO, slides: [slide(1, 12)], scripts: [script(1)] });

    expect(gate.message).toBe('매핑을 실행해주세요');
    expect(gate.rows.map((r) => r.label)).toEqual(['슬라이드 12장', '대본 1,284자', '매핑 미실행']);
    expect(gate.rows[2]!.key).toBe('mapping');
  });

  it('나눴지만 저장 전이면 저장하라고 한다', () => {
    const gate = computeGate({
      ...EMPTY,
      ...INFO,
      slides: [slide(1, 12)],
      scripts: [script(1, { blocks: Array<string>(12).fill('블록') })],
    });

    expect(gate.message).toBe('매핑을 확인하고 저장해주세요');
    expect(gate.rows[2]).toMatchObject({ key: 'mapping', label: '매핑 저장 전', done: false });
    expect(gate.ready).toBe(false);
  });

  it('매핑을 저장했고 평가기준만 남았다', () => {
    const gate = computeGate({ ...EMPTY, ...INFO, slides: [slide(1, 12)], scripts: [mapped(1)] });

    expect(gate.message).toBe('평가기준을 정리하고 저장해주세요');
    expect(gate.rows.map((r) => r.label)).toEqual([
      '슬라이드 12장',
      '대본 12블록 매핑 완료',
      '평가기준 0개',
    ]);
    expect(gate.rows[2]!.key).toBe('criteria');
  });

  it('평가기준을 정리했지만 저장 전이다', () => {
    const gate = computeGate({ ...READY, criteria: [criteria(1, 3, false)] });

    expect(gate.rows[2]).toMatchObject({ label: '평가기준 3개 · 저장 전', done: false });
    expect(gate.message).toBe('평가기준을 확인하고 저장해주세요');
    expect(gate.ready).toBe(false);
  });

  it('전부 저장했다', () => {
    const gate = computeGate(READY);

    expect(gate.heading).toBe('준비 완료');
    expect(gate.message).toBe('바로 연습을 시작할 수 있어요');
    expect(gate.rows.every((r) => r.done)).toBe(true);
    expect(gate.ready).toBe(true);
  });
});

describe('발표정보', () => {
  it('제목이 비어 있으면 저장했어도 열리지 않는다', () => {
    expect(computeGate({ ...READY, title: '  ' }).ready).toBe(false);
  });

  it('저장하지 않았으면 열리지 않는다 — 값을 고치면 저장 표시가 풀린다', () => {
    const gate = computeGate({ ...READY, infoSaved: false });

    expect(gate.ready).toBe(false);
    expect(gate.message).toBe('발표정보를 입력하고 저장해주세요');
  });
});

describe('진행 조건 — 경계', () => {
  /**
   * PDF 를 올렸지만 서버 변환이 아직 안 끝난 상태. 장수를 모르면 "12장" 을 쓸 수 없고,
   * 넘어가게 두면 매핑이 몇 블록으로 나뉠지도 정해지지 않습니다.
   */
  it('변환 전(pageCount null)은 슬라이드를 끝난 것으로 보지 않는다', () => {
    const gate = computeGate({ ...EMPTY, slides: [slide(1, null)] });

    expect(gate.rows[0]).toMatchObject({ label: '슬라이드 미등록', done: false });
  });

  it('빈 문자열만 있는 대본은 쓴 것으로 보지 않는다', () => {
    const gate = computeGate({ ...EMPTY, scripts: [script(1, { text: '   \n ' })] });

    expect(gate.rows[1]).toMatchObject({ label: '대본 미등록', done: false });
  });

  it('최신 버전만 본다 — 지난 버전이 채워져 있어도 현재가 비었으면 미등록이다', () => {
    const gate = computeGate({ ...EMPTY, slides: [slide(1, 12), slide(2, null)] });

    expect(gate.rows[0]!.done).toBe(false);
  });

  it('항목이 하나도 없는 평가기준은 저장했어도 없는 것이다', () => {
    expect(computeGate({ ...READY, criteria: [criteria(1, 0)] }).ready).toBe(false);
  });
});

describe('고른 버전으로 판정한다', () => {
  it('장수가 같아도 연결되지 않은 슬라이드 버전으로 시작하지 않는다', () => {
    const draft: PitchDraft = { ...READY, slides: [slide(1, 12), slide(2, 12)] };
    const gate = computeGate(draft, { slides: 2, script: 1, criteria: 1 });
    expect(gate.ready).toBe(false);
    expect(gate.message).toContain('대본에 연결된 슬라이드 버전');
  });
  /**
   * ★ 핵심 — 최신이 멀쩡해도 **들고 갈 버전**이 비어 있으면 시작할 수 없습니다.
   *   대본 V2 는 매핑했지만 연습에 V1 을 쓰기로 골랐고, 그 V1 이 안 나뉘어 있다면
   *   리허설 화면이 대본을 슬라이드에 못 붙입니다.
   */
  it('대본 V1 을 골랐고 그 V1 이 매핑 전이면 시작할 수 없다', () => {
    const draft: PitchDraft = { ...READY, scripts: [script(1), mapped(2)] };

    // 최신(V2)으로 보면 다 찼습니다
    expect(computeGate(draft).ready).toBe(true);
    // 그런데 V1 을 들고 가기로 하면 매핑이 없습니다
    const gate = computeGate(draft, { slides: null, script: 1, criteria: null });
    expect(gate.ready).toBe(false);
    expect(gate.message).toBe('매핑을 실행해주세요');
  });

  /** 대본 V1 은 슬라이드 V1(8장)에 붙어 있습니다. 슬라이드 V2 가 생겨도 V1 의 짝은 그대로입니다 */
  it('슬라이드를 고르지 않으면 대본이 붙은 슬라이드를 따라간다', () => {
    const draft: PitchDraft = {
      ...READY,
      slides: [slide(1, 8), slide(2, 12)],
      scripts: [mapped(1, 8)],
    };

    const gate = computeGate(draft);
    expect(gate.ready).toBe(true);
    expect(gate.rows[0]!.label).toBe('슬라이드 8장');
  });

  /**
   * ★ 섞어 고르면 장수가 어긋나기 쉽습니다. 8장으로 나눈 대본 V1 을 12장짜리
   *   슬라이드 V2 와 들고 가면 9~12번 슬라이드에 붙을 대본이 없습니다.
   */
  it('고른 슬라이드와 대본의 장수가 어긋나면 다시 매핑해야 한다', () => {
    const draft: PitchDraft = {
      ...READY,
      slides: [slide(1, 8), slide(2, 12)],
      scripts: [mapped(1, 8)],
    };

    const gate = computeGate(draft, { slides: 2, script: 1, criteria: 1 });
    expect(gate.ready).toBe(false);
    expect(gate.message).toBe('슬라이드 장수가 바뀌었어요. 매핑을 다시 실행해주세요');
    expect(gate.rows.map((r) => r.label)).toEqual([
      '슬라이드 12장',
      '대본 1,284자',
      '매핑 다시 필요 (8블록 / 12장)',
    ]);
  });

  it('없는 번호를 가리키면 최신으로 떨어진다 — 지운 버전을 들고 시작하지 않는다', () => {
    const draft: PitchDraft = { ...EMPTY, slides: [slide(2, 12)] };

    expect(computeGate(draft, { slides: 99, script: null, criteria: null }).rows[0]!.label).toBe(
      '슬라이드 12장',
    );
  });
});
