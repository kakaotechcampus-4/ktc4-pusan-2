import { beforeEach, describe, expect, it } from 'vitest';
import { useCreateStore } from './createStore';

const state = () => useCreateStore.getState();
beforeEach(() => state().reset());

describe('발표 자료 편집의 연결 상태', () => {
  it('같은 장수의 PDF 교체도 연결된 매핑을 무효화하며 다른 버전은 보존한다', () => {
    state().attachSlides('first.pdf', 2);
    state().addScriptVersion('원문');
    state().setBlocks(1, ['첫 장', '둘째 장']);
    state().saveMapping(1);
    state().addSlideVersion();
    state().attachSlides('second.pdf', 2);
    state().addScriptVersion('다른 원문');
    state().linkSlide(2, 2);
    state().setBlocks(2, ['다른 첫 장', '다른 둘째 장']);
    state().saveMapping(2);
    state().replaceSlides(1, 'replacement.pdf', 2);
    expect(state().draft.scripts[0]).toMatchObject({ blocks: null, mappingSaved: false });
    expect(state().draft.scripts[1]).toMatchObject({ mappingSaved: true });
  });

  it('매핑에서 고친 문장은 입력 화면으로 돌아와도 남는다', () => {
    state().addScriptVersion('원문');
    state().setBlocks(1, ['첫 장', '둘째 장']);
    state().saveMapping(1);
    state().editBlock(1, 0, '수정한 첫 장');
    expect(state().draft.scripts[0]?.mappingSaved).toBe(false);
    state().clearMapping(1);
    expect(state().draft.scripts[0]?.text).toBe('수정한 첫 장\n\n둘째 장');
  });

  it('이전에 만든 빈 슬라이드 버전에 파일을 붙인다', () => {
    state().addSlideVersion();
    state().addSlideVersion();
    state().select('slides', 1);
    state().attachSlides('first.pdf', 3);
    expect(state().draft.slides).toEqual([
      { version: 1, fileName: 'first.pdf', pageCount: 3 },
      { version: 2, fileName: null, pageCount: null },
    ]);
    expect(state().version).toBe(1);
  });

  it('목표시간을 줄이면 하한 허용오차도 목표 이내로 제한한다', () => {
    state().setMeta({ toleranceBelowSec: 240 });
    state().setMeta({ timeLimitSec: 60 });
    expect(state().draft.toleranceBelowSec).toBe(60);
  });
});
