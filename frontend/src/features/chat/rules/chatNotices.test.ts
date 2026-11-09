import { describe, expect, it } from 'vitest';
import type { PlanStep } from '@/types/chat';
import {
  decisionFailedText,
  partialHeader,
  runEndNotice,
  runNoticeText,
  sendFailedText,
} from './chatNotices';
import { ANSWER_FAILED_TEXT } from './chatResponse';

describe('보내기 거절 문구 (12-4 F8 · L3-17)', () => {
  it.each([
    ['AGENT_BUSY', '오늘은 사용량이 많아 잠시 뒤 다시 이용해 주세요.'],
    ['AGENT_DAILY_LIMIT', '오늘 이용 횟수를 모두 썼으니 내일 다시 이용해 주세요.'],
    ['AGENT_CONTEXT_TOO_LARGE', '자료가 너무 많으니 범위를 좁혀 다시 물어봐 주세요.'],
    ['VALIDATION_ERROR', '메시지를 확인해 주세요.'],
    ['AGENT_REQUEST_CONFLICT', '보낸 질문과 내용이 달라 받지 못했어요. 다시 보내 주세요.'],
    ['NOT_FOUND', '대화를 찾을 수 없어요.'],
    ['SOMETHING_NEW', ANSWER_FAILED_TEXT],
  ])('%s → %s', (code, text) => {
    expect(sendFailedText(code)).toBe(text);
  });
});

describe('실행이 끝난 뒤의 안내 (12-4 F8)', () => {
  const end = (status: string, errorCode: string | null = null, endedWhileWaiting = false) =>
    runEndNotice({ status: status as never, errorCode, endedWhileWaiting });

  it('끝까지 답함 · 중간까지 답함 · 범위 밖 거절에는 붙이지 않는다', () => {
    expect(end('DONE')).toBeNull();
    expect(end('PARTIAL', 'AGENT_TIMEOUT')).toBeNull();
    expect(end('REFUSED')).toBeNull();
  });

  it('만료 · 취소 — 승인 대기에서 곧바로 끝났으면 BE 고정 문장만 보이고 함께 붙이지 않는다', () => {
    expect(end('EXPIRED')).toEqual({ text: '시간이 지나 다시 물어봐 주세요.', resend: false });
    expect(end('CANCELLED')).toEqual({ text: '요청을 취소했어요.', resend: false });
    expect(end('EXPIRED', 'AGENT_EXPIRED', true)).toBeNull();
    expect(end('CANCELLED', 'AGENT_CANCELLED', true)).toBeNull();
  });

  it.each([
    ['AGENT_TIMEOUT', '답이 늦어지고 있으니 다시 보내 주세요.', true],
    ['AGENT_BUSY', '요청이 많으니 잠시 뒤 다시 이용해 주세요.', true],
    ['AGENT_DAILY_LIMIT', '오늘 이용 횟수를 모두 썼으니 내일 다시 이용해 주세요.', false],
    ['AGENT_CONTEXT_TOO_LARGE', '자료가 너무 많으니 범위를 좁혀 다시 물어봐 주세요.', false],
    ['AGENT_STATE_INVALID', '실행 상태가 만료되었거나 업데이트되었으니 다시 물어봐 주세요.', false],
    ['AGENT_OUTPUT_TOO_LARGE', '답이 너무 길어 완료하지 못했으니 질문 범위를 좁혀 주세요.', true],
    ['AGENT_UPSTREAM_ERROR', ANSWER_FAILED_TEXT, true],
    [null, ANSWER_FAILED_TEXT, true],
  ])('실패 %s → 문구와 [다시 보내기] 여부', (code, text, resend) => {
    expect(end('FAILED', code)).toEqual({ text, resend });
  });

  it('꺼짐(AGENT_DISABLED)은 문구 없이 — 버튼을 숨기는 것은 부른 쪽', () => {
    expect(end('FAILED', 'AGENT_DISABLED')).toBeNull();
  });
});

describe('"여기까지 확인했어요" (12-4 F8 PARTIAL)', () => {
  const plan: PlanStep[] = [
    { step: '최근 Take 비교', status: 'DONE', note: null },
    { step: '3장 확인', status: 'DOING', note: null },
    { step: '자료 찾기', status: 'TODO', note: null },
    { step: '목표 저장', status: 'SKIPPED', note: null },
  ];

  it('남은 일(할 일 · 하는 중)을 함께, 없으면 머리만', () => {
    expect(partialHeader('PARTIAL', '문장', plan)).toBe(
      '여기까지 확인했어요. 남은 일: 3장 확인 · 자료 찾기',
    );
    expect(partialHeader('PARTIAL', '문장', [])).toBe('여기까지 확인했어요.');
  });

  it('PARTIAL 이 아니면 붙이지 않는다', () => {
    expect(partialHeader('DONE', '문장', plan)).toBeNull();
    expect(partialHeader(null, null, plan)).toBeNull();
  });

  it.each([
    '확인 시간이 지나 실행하지 않았어요. 필요하면 다시 말해 주세요.',
    '새 메시지를 받아 앞의 확인 요청은 취소했어요.',
    '확인 요청을 취소했어요.',
  ])('승인 대기를 끝낸 BE 고정 문장으로 끝났으면 붙이지 않는다 — %s', (last) => {
    expect(partialHeader('PARTIAL', last, plan)).toBeNull();
  });
});

describe('승인 · 거절 실패 · 진행 중 안내', () => {
  it.each([
    ['APPROVAL_HASH_MISMATCH', '요청이 변경되어 다시 확인이 필요해요.'],
    ['AGENT_EXPIRED', '시간이 지나 다시 물어봐 주세요.'],
    ['AGENT_CANCELLED', '요청을 취소했어요.'],
    ['REQUEST_FAILED', ANSWER_FAILED_TEXT],
  ])('승인 · 거절 %s → %s', (code, text) => {
    expect(decisionFailedText(code)).toBe(text);
  });

  it('조회 실패 · 크기 초과 · 늦어짐 · 취소 확인', () => {
    expect(runNoticeText({ kind: 'pollTrouble', tooLarge: false })).toBe(
      '진행 상태를 확인하지 못해 다시 연결하고 있어요.',
    );
    expect(runNoticeText({ kind: 'pollTrouble', tooLarge: true })).toBe(
      '일부 진행 정보를 표시하지 못했어요.',
    );
    expect(runNoticeText({ kind: 'slow' })).toBe(
      '결과 확인이 늦어지고 있으니 상태를 다시 확인해 주세요.',
    );
    expect(runNoticeText({ kind: 'cancelChecking' })).toBe('취소 여부를 확인하고 있어요.');
  });
});
