import { useCreateStore } from '../createStore';
import { AlertIcon } from '../icons';

/**
 * 피치가 아직 없을 때의 안내. 대본 · 평가기준은 서버의 피치 아래에 올라가는데,
 * 피치는 첫 단계에서 슬라이드를 올릴 때 생깁니다 (`slideUpload.ts`).
 * 단계 표시줄 · 사이드바가 그 전에는 다른 단계를 막으므로, 여기는 거의 오지 않는 자리입니다.
 */
export function NeedPitch({ what }: { what: string }) {
  const select = useCreateStore((s) => s.select);
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed border-line-strong bg-white px-6 py-12 text-center">
      <AlertIcon className="h-6 w-6 fill-stone" />
      <p className="font-bold">슬라이드를 먼저 올려 주세요</p>
      <p className="text-sm text-stone">{what}은 슬라이드를 올리면 생기는 발표 아래에 저장돼요.</p>
      <button
        type="button"
        onClick={() => select('slides')}
        className="mt-2 rounded-lg bg-coral px-5 py-2.5 text-sm font-bold text-white hover:bg-coral-deep"
      >
        슬라이드 올리기
      </button>
    </div>
  );
}
