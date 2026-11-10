import { expect, it } from 'vitest';
import type { HomePitch, HomeTake } from '@/types/home';
import { invitesTutorial } from './tutorialInvite';

const pitch = (isDemo: boolean, takes: HomeTake[] = []): HomePitch => ({
  pitch_id: isDemo ? 'demo' : 'mine',
  pitch_title: isDemo ? '[튜토리얼] 치치와 함께하는 First Take' : '내 발표',
  pitch_time: 180,
  thumbnail_url: null,
  presentation_date: null,
  takes,
  can_delete: true,
  is_demo: isDemo,
});

const take = (status: HomeTake['status'], endReason: HomeTake['end_reason'] = null): HomeTake => ({
  take_version: 1,
  take_elapsed: null,
  take_time: 180,
  script_mode: 'HIGHLIGHT',
  score: null,
  delta: null,
  take_id: 't1',
  status,
  end_reason: endReason,
});

it('샘플 피치가 없으면 권한다 — 내 피치에 연습이 많아도 같다', () => {
  expect(invitesTutorial([], false)).toBe(true);
  expect(invitesTutorial([pitch(false, [take('COMPLETED')])], false)).toBe(true);
});

it.each([
  ['장치 점검에서 멈춤', take('READY')],
  ['발표 중', take('RUNNING')],
  ['시작하지 않고 버려짐', take('FAILED', 'ABANDONED')],
  ['창을 닫아 끊김', take('FAILED', 'CLOSED')],
])('샘플로 끝까지 하지 않았으면(%s) 계속 권한다', (_, sampleTake) => {
  expect(invitesTutorial([pitch(true, [sampleTake])], false)).toBe(true);
});

it.each([
  ['분석 중', take('ANALYZING')],
  ['완료', take('COMPLETED')],
  ['끝냈지만 분석 실패', take('FAILED')],
  ['침묵으로 자동 종료 후 분석 실패', take('FAILED', 'SILENCE')],
  ['제한 시간 종료 후 분석 실패', take('FAILED', 'TIME_LIMIT')],
])('샘플로 끝까지 연습했으면(%s) 더 권하지 않는다', (_, sampleTake) => {
  expect(invitesTutorial([pitch(false), pitch(true, [take('READY'), sampleTake])], false)).toBe(
    false,
  );
});

it('튜토리얼 리포트를 이미 받았으면 샘플을 지웠어도 권하지 않는다', () => {
  expect(invitesTutorial([], true)).toBe(false);
  expect(invitesTutorial([pitch(true, [take('READY')])], true)).toBe(false);
});
