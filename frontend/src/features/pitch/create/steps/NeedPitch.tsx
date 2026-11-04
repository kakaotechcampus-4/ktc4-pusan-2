import { useCreateStore } from '../createStore';
import { AlertIcon } from '../icons';

/**
 * 피치가 아직 없을 때의 안내. 발표자료 · 대본 · 평가기준은 전부 서버의 피치 아래에 올라가므로
 * 발표정보를 먼저 저장해야 합니다 (`POST /pitches/add` 가 pitch_id 를 줍니다).
 */
export function NeedPitch({ what }: { what: string }) {
  const select = useCreateStore((s) => s.select);
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed border-line-strong bg-white px-6 py-12 text-center">
      <AlertIcon className="h-6 w-6 fill-stone" />
      <p className="font-bold">슬라이드를 먼저 올려 주세요</p>
      <p className="text-sm text-stone">PDF를 올린 뒤 {what}을 준비할 수 있어요.</p>
      <button
        type="button"
        onClick={() => select('slides')}
        className="mt-2 rounded-lg bg-coral px-5 py-2.5 text-sm font-bold text-white hover:bg-coral-deep"
      >
        슬라이드로 가기
      </button>
    </div>
  );
}
