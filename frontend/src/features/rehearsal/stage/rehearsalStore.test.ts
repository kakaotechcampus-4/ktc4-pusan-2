import { beforeEach, describe, expect, it } from 'vitest';
import { useRehearsalStore } from './rehearsalStore';

const phase = () => useRehearsalStore.getState().phase;
beforeEach(() => {
  useRehearsalStore.getState().reset();
});

describe('단계는 한 방향으로만 간다', () => {
  it('RUNNING 에서 시작한다', () => {
    expect(phase()).toBe('RUNNING');
  });

  it('앞으로는 간다', () => {
    useRehearsalStore.getState().setPhase('ENDING');
    expect(phase()).toBe('ENDING');

    useRehearsalStore.getState().setPhase('ENDED');
    expect(phase()).toBe('ENDED');
  });

  it('건너뛰는 것도 앞으로면 간다 — 종료가 한 번에 끝나는 경우', () => {
    useRehearsalStore.getState().setPhase('ENDED');
    expect(phase()).toBe('ENDED');
  });

  /**
   * ★ 이 테스트가 이번 사고를 막습니다.
   *   되돌아가면 시계가 t0 를 다시 잡아 durationMs 가 재시도 시점부터 재측정됩니다.
   */
  it('ENDING 에서 RUNNING 으로 되돌아가지 않는다', () => {
    useRehearsalStore.getState().setPhase('ENDING');
    useRehearsalStore.getState().setPhase('RUNNING');
    expect(phase()).toBe('ENDING');
  });

  it('ENDED 에서는 어디로도 되돌아가지 않는다', () => {
    useRehearsalStore.getState().setPhase('ENDED');

    useRehearsalStore.getState().setPhase('ENDING');
    expect(phase()).toBe('ENDED');

    useRehearsalStore.getState().setPhase('RUNNING');
    expect(phase()).toBe('ENDED');
  });

  it('같은 단계를 다시 넣어도 상태 객체가 바뀌지 않는다 — 불필요한 렌더를 막는다', () => {
    useRehearsalStore.getState().setPhase('ENDING');
    const before = useRehearsalStore.getState();

    useRehearsalStore.getState().setPhase('ENDING');
    expect(useRehearsalStore.getState()).toBe(before);
  });

  it('reset 만이 되돌리는 길이다 — 새 Take 를 시작한다는 뜻이다', () => {
    useRehearsalStore.getState().setPhase('ENDED');
    useRehearsalStore.getState().reset();

    expect(phase()).toBe('RUNNING');
  });

  it('reset 은 슬라이드와 코치도 처음으로 돌린다', () => {
    useRehearsalStore.getState().setSlide(7);
    useRehearsalStore.getState().showCoach({ type: 'PACE', text: '천천히', atMs: 1000 });

    useRehearsalStore.getState().reset();

    expect(useRehearsalStore.getState().slideNumber).toBe(1);
    expect(useRehearsalStore.getState().coach).toBeNull();
  });
});
