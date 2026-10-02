import { useId } from 'react';
import { useCreateStore } from '../createStore';
import { CalendarIcon } from '../icons';

/**
 * 사이드바 맨 위 "발표 정보" — 발표 제목과 날짜.
 *
 * 피치 한 판에 하나뿐이라 버전이 없습니다. 슬라이드를 고치든 대본을 고치든
 * 이 둘은 그대로입니다. 발표시간은 채점에 쓰이므로 평가기준 00번 줄에 둡니다.
 */

const box =
  'flex h-11 items-center gap-2 rounded border border-line-strong bg-white px-3 text-sm focus-within:border-ink';

export function InfoPane() {
  const draft = useCreateStore((s) => s.draft);
  const setMeta = useCreateStore((s) => s.setMeta);
  const id = useId();

  return (
    <div className="flex flex-1 flex-col">
      <h2 className="text-2xl font-bold">발표 정보를 입력해주세요</h2>
      <p className="mt-2 text-sm text-stone">어떤 발표를 준비하는지 알려 주세요.</p>

      <div className="mt-6 flex max-w-xl flex-col gap-5">
        <div className="flex flex-col gap-1.5">
          <label htmlFor={`${id}-title`} className="text-xs font-bold">
            발표 제목
          </label>
          <div className={box}>
            <input
              id={`${id}-title`}
              placeholder="발표 제목을 입력해 주세요"
              value={draft.title}
              onChange={(e) => setMeta({ title: e.target.value })}
              className="min-w-0 flex-1 bg-transparent outline-none placeholder:text-stone"
            />
          </div>
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor={`${id}-date`} className="text-xs font-bold">
            발표 날짜
          </label>
          <div className={`${box} max-w-xs`}>
            <CalendarIcon />
            <input
              id={`${id}-date`}
              type="date"
              value={draft.presentationDate}
              onChange={(e) => setMeta({ presentationDate: e.target.value })}
              className="tabular min-w-0 flex-1 bg-transparent outline-none"
            />
          </div>
        </div>
      </div>
    </div>
  );
}
