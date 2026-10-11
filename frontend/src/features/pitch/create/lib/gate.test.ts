import { describe, expect, it } from 'vitest';
import { computeGate } from './gate';
import { CHOOSE_LATEST, resolveChosen, type PitchDraft } from './draft';

/**
 * 목업 다섯 장(04~08)을 그대로 재현합니다.
 *
 * 이 상자는 "다음" 버튼이 열릴지를 정합니다. 한 조합만 틀려도 슬라이드 없이
 * 연습이 시작되거나, 다 채웠는데 버튼이 안 열립니다. 조합이 다섯 가지뿐이라
 * 전부 적어 두는 편이 규칙을 말로 설명하는 것보다 정확합니다.
 */

const EMPTY: PitchDraft = {
  title: '',
  presentationDate: '',
  timeLimitSec: 300,
  lowerToleranceSec: 30,
  upperToleranceSec: 60,
  slides: [],
  scripts: [],
  criteria: [],
};

const SCRIPT_TEXT = '가'.repeat(1_284);

/** 진행 조건은 서버 쪽 상태를 보지 않습니다 — 대본 픽스처에 공통으로 붙입니다 */
const SERVER_IDLE = { remote: null, parse: { status: 'idle' }, segmented: null } as const;

describe('진행 조건 — 목업 04~08', () => {
  it('04 슬라이드 업로드 — 아무것도 없다', () => {
    const gate = computeGate(EMPTY);

    expect(gate.message).toBe('슬라이드와 대본을 준비해 주세요');
    expect(gate.rows.map((r) => r.label)).toEqual([
      '슬라이드 미등록',
      '대본 미등록',
      '평가기준 (선택)',
    ]);
    expect(gate.ready).toBe(false);
  });

  it('05 PDF 뷰어 — 슬라이드 12장만 있다', () => {
    const gate = computeGate({ ...EMPTY, slides: [{ version: 1, pageCount: 12 }] });

    expect(gate.message).toBe('대본을 입력해주세요');
    expect(gate.rows.map((r) => r.label)).toEqual([
      '슬라이드 12장',
      '대본 미등록',
      '평가기준 (선택)',
    ]);
    expect(gate.rows[0]!.done).toBe(true);
    expect(gate.ready).toBe(false);
  });

  /** ★ 세 번째 줄이 평가기준에서 매핑으로 바뀌는 유일한 장면입니다 */
  it('06 대본 입력 — 썼지만 아직 안 나눴다', () => {
    const gate = computeGate({
      ...EMPTY,
      slides: [{ version: 1, pageCount: 12 }],
      scripts: [
        {
          version: 3,
          text: SCRIPT_TEXT,
          blocks: null,
          slideVersion: 1,
          saved: false,
          ...SERVER_IDLE,
          ...SERVER_IDLE,
        },
      ],
    });

    expect(gate.message).toBe('매핑을 실행해주세요');
    expect(gate.rows.map((r) => r.label)).toEqual(['슬라이드 12장', '대본 1,284자', '매핑 미실행']);
    expect(gate.rows[2]!.key).toBe('mapping');
    expect(gate.ready).toBe(false);
  });

  /** ★ 평가기준은 선택입니다 — 매핑까지 끝나면 바로 시작할 수 있습니다 */
  it('07 매핑 확인 — 나눴으면 평가기준 없이도 시작할 수 있다', () => {
    const gate = computeGate({
      ...EMPTY,
      slides: [{ version: 1, pageCount: 12 }],
      scripts: [
        {
          version: 3,
          text: SCRIPT_TEXT,
          blocks: Array<string>(12).fill('블록'),
          slideVersion: 1,
          saved: false,
          ...SERVER_IDLE,
        },
      ],
    });

    expect(gate.heading).toBe('준비 완료');
    expect(gate.message).toBe('바로 연습을 시작할 수 있어요');
    expect(gate.rows.map((r) => r.label)).toEqual([
      '슬라이드 12장',
      '대본 12블록 매핑 완료',
      '평가기준 (선택)',
    ]);
    expect(gate.rows[2]!.key).toBe('criteria');
    expect(gate.ready).toBe(true);
  });

  it('08 평가기준 — 셋이 다 찼다', () => {
    const gate = computeGate({
      ...EMPTY,
      slides: [{ version: 1, pageCount: 12 }],
      scripts: [
        {
          version: 3,
          text: SCRIPT_TEXT,
          blocks: Array<string>(12).fill('블록'),
          slideVersion: 1,
          saved: false,
          ...SERVER_IDLE,
        },
      ],
      criteria: [
        {
          version: 1,
          saved: true,
          items: [
            { id: 'c1', text: '시장 규모 숫자를 반드시 말한다' },
            { id: 'c2', text: '대본을 보지 않고 도입부 30초를 말한다' },
            { id: 'c3', text: '군더더기 표현 5회 이하' },
          ],
        },
      ],
    });

    expect(gate.heading).toBe('준비 완료');
    expect(gate.message).toBe('바로 연습을 시작할 수 있어요');
    expect(gate.rows.every((r) => r.done)).toBe(true);
    expect(gate.ready).toBe(true);
  });
});

describe('진행 조건 — 목업에 없는 경계', () => {
  /**
   * PDF 를 올렸지만 서버 변환이 아직 안 끝난 상태. 장수를 모르면 "12장" 을 쓸 수 없고,
   * 넘어가게 두면 매핑이 몇 블록으로 나뉠지도 정해지지 않습니다.
   */
  it('변환 전(pageCount null)은 슬라이드를 끝난 것으로 보지 않는다', () => {
    const gate = computeGate({ ...EMPTY, slides: [{ version: 1, pageCount: null }] });

    expect(gate.rows[0]).toMatchObject({ label: '슬라이드 미등록', done: false });
  });

  it('빈 문자열만 있는 대본은 쓴 것으로 보지 않는다', () => {
    const gate = computeGate({
      ...EMPTY,
      scripts: [
        { version: 1, text: '   \n ', blocks: null, slideVersion: 1, saved: false, ...SERVER_IDLE },
      ],
    });

    expect(gate.rows[1]).toMatchObject({ label: '대본 미등록', done: false });
  });

  it('최신 버전만 본다 — 지난 버전이 채워져 있어도 현재가 비었으면 미등록이다', () => {
    const gate = computeGate({
      ...EMPTY,
      slides: [
        { version: 1, pageCount: 12 },
        { version: 2, pageCount: null },
      ],
    });

    expect(gate.rows[0]!.done).toBe(false);
  });
});

describe('고른 버전으로 판정한다', () => {
  /**
   * ★ 핵심 — 최신이 멀쩡해도 **들고 갈 버전**이 비어 있으면 시작할 수 없습니다.
   *   대본 V2 는 매핑했지만 연습에 V1 을 쓰기로 골랐고, 그 V1 이 안 나뉘어 있다면
   *   리허설 화면이 대본을 슬라이드에 못 붙입니다.
   */
  it('대본 V1 을 골랐고 그 V1 이 매핑 전이면 시작할 수 없다', () => {
    const draft: PitchDraft = {
      ...EMPTY,
      slides: [{ version: 1, pageCount: 12 }],
      scripts: [
        {
          version: 1,
          text: SCRIPT_TEXT,
          blocks: null,
          slideVersion: 1,
          saved: false,
          ...SERVER_IDLE,
          ...SERVER_IDLE,
        },
        {
          version: 2,
          text: SCRIPT_TEXT,
          blocks: Array<string>(12).fill('블록'),
          slideVersion: 1,
          saved: false,
          ...SERVER_IDLE,
        },
      ],
      criteria: [{ version: 1, saved: true, items: [{ id: 'c1', text: '기준' }] }],
    };

    // 최신(V2)으로 보면 다 찼습니다
    expect(computeGate(draft).ready).toBe(true);
    // 그런데 V1 을 들고 가기로 하면 매핑이 없습니다
    expect(computeGate(draft, { slides: null, script: 1, criteria: null }).ready).toBe(false);
    expect(computeGate(draft, { slides: null, script: 1, criteria: null }).message).toBe(
      '매핑을 실행해주세요',
    );
  });

  /** 슬라이드 V2 · 대본 V1 · 평가기준 V1 — 섞어 고르는 것이 정상 흐름입니다 */
  it('셋을 따로 고른 조합도 성립한다', () => {
    const draft: PitchDraft = {
      ...EMPTY,
      slides: [
        { version: 1, pageCount: 8 },
        { version: 2, pageCount: 12 },
      ],
      scripts: [
        {
          version: 1,
          text: SCRIPT_TEXT,
          blocks: Array<string>(12).fill('블록'),
          slideVersion: 1,
          saved: false,
          ...SERVER_IDLE,
        },
        { version: 2, text: '', blocks: null, slideVersion: 1, saved: false, ...SERVER_IDLE },
      ],
      criteria: [{ version: 1, saved: true, items: [{ id: 'c1', text: '기준' }] }],
    };

    const gate = computeGate(draft, { slides: 2, script: 1, criteria: 1 });

    expect(gate.ready).toBe(true);
    expect(gate.rows.map((r) => r.label)).toEqual([
      '슬라이드 12장',
      '대본 12블록 매핑 완료',
      '평가기준 1개',
    ]);
  });

  /**
   * ★ 섞어 고르면 장수가 어긋나기 쉽습니다. 8장으로 나눈 대본 V1 을 12장짜리
   *   슬라이드 V2 와 들고 가면 9~12번 슬라이드에 붙을 대본이 없습니다.
   */
  it('고른 슬라이드와 대본의 장수가 어긋나면 다시 매핑해야 한다', () => {
    const draft: PitchDraft = {
      ...EMPTY,
      slides: [
        { version: 1, pageCount: 8 },
        { version: 2, pageCount: 12 },
      ],
      scripts: [
        {
          version: 1,
          text: SCRIPT_TEXT,
          blocks: Array<string>(8).fill('블록'),
          slideVersion: 1,
          saved: false,
          ...SERVER_IDLE,
        },
      ],
      criteria: [{ version: 1, saved: true, items: [{ id: 'c1', text: '기준' }] }],
    };

    // 같은 대본도 8장짜리 V1 과 들고 가면 맞습니다
    expect(computeGate(draft, { slides: 1, script: 1, criteria: 1 }).ready).toBe(true);

    const gate = computeGate(draft, { slides: 2, script: 1, criteria: 1 });
    expect(gate.ready).toBe(false);
    expect(gate.message).toBe('대본과 슬라이드 장수가 달라요. 대본의 구분을 확인해 주세요');
    expect(gate.rows.map((r) => r.label)).toEqual([
      '슬라이드 12장',
      '대본 1,284자',
      '장수 다름 (대본 8 / 슬라이드 12장)',
    ]);
  });

  it('없는 번호를 가리키면 최신으로 떨어진다 — 지운 버전을 들고 시작하지 않는다', () => {
    const draft: PitchDraft = { ...EMPTY, slides: [{ version: 2, pageCount: 12 }] };

    expect(computeGate(draft, { slides: 99, script: null, criteria: null }).rows[0]!.label).toBe(
      '슬라이드 12장',
    );
  });
});

describe('평가기준은 서버에 저장된 것만 연습에 들고 간다', () => {
  /** "+ 새 버전"으로 만들고 아직 정리하지 않은 버전 — 서버에 없는 기준으로 채점하면 안 됩니다 */
  it('저장되지 않은 버전은 평가기준이 없는 것으로 본다', () => {
    const gate = computeGate({
      ...EMPTY,
      criteria: [{ version: 1, saved: false, items: [{ id: 'c1', text: '시장 규모 말하기' }] }],
    });
    expect(gate.rows[2]!.label).toBe('평가기준 (선택)');
  });

  it('저장된 V1 뒤에 저장되지 않은 V2 가 있으면 V1 을 쓴다', () => {
    const { criteria } = resolveChosen(
      {
        ...EMPTY,
        criteria: [
          { version: 1, saved: true, items: [{ id: 'a', text: 'V1 기준' }] },
          { version: 2, saved: false, items: [{ id: 'b', text: 'V2 기준' }] },
        ],
      },
      CHOOSE_LATEST,
    );
    expect(criteria?.version).toBe(1);
  });
});
