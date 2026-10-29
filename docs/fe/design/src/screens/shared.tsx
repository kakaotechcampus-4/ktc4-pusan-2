/** 설계서 샘플 화면(`src/screens/*`)이 함께 쓰는 이동 타입 · 샘플 이름 · 글 위주 틀. */
import type { ReactNode } from 'react';
import { Button } from '@/ui/Button';
import { PitchCoachWordmark } from '@/ui/PitchCoachWordmark';

/** 미리보기 안의 버튼 · 링크가 부르는 이동. 같은 화면이면 상태만, 다른 화면이면 화면을 바꾼다 */
export type Navigate = (screenId: string, stateId?: string) => void;

// 샘플 데이터. 화면끼리 이어지도록 같은 피치 · Take 번호를 쓴다(지난 Take 2 → 다음 Take 3)
export const PITCH = '캡스톤 중간발표';

/**
 * 글 위주 화면의 틀. 제품 `shared/ui/InfoPage` · `report/ReportFrame`과 같은 모양이다.
 * `onHome`을 주면 InfoPage(logo=true)처럼 홈으로 가는 로고 줄을 둔다(로그인 없이 여는 화면).
 * 제품은 본문이 `<main>`이지만, 설계서 안에서는 뷰어가 `<main>`을 가지므로 `<div>`로 둔다.
 */
export function Frame({
  title,
  meta,
  onHome,
  children,
}: {
  title: string;
  meta?: string;
  onHome?: () => void;
  children: ReactNode;
}) {
  return (
    <div className="pitch-create min-h-[40rem] bg-greige text-ink">
      <header className="mx-auto flex max-w-page flex-wrap items-center justify-between gap-x-4 gap-y-3 px-6 pt-6 lg:px-10">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
          {onHome && (
            <>
              <button type="button" onClick={onHome} aria-label="피치코치 홈">
                <PitchCoachWordmark />
              </button>
              <span className="h-5 w-px bg-line-strong" aria-hidden="true" />
            </>
          )}
          <h1 className="text-lg font-bold">{title}</h1>
        </div>
        {meta && <p className="tabular text-sm text-stone">{meta}</p>}
      </header>
      <div className="mx-auto flex max-w-page flex-col gap-6 px-6 pb-12 pt-5 lg:px-10">
        {children}
      </div>
    </div>
  );
}

/** 제품 `report/QueryState`의 불러오는 중 · 실패 한 칸 */
export function QueryNotice({
  stateId,
  pending = '연습 기록을 가져오는 중이에요…',
  failed = '연습 기록을 가져오지 못했어요.',
  onRetry,
}: {
  stateId: string;
  pending?: string;
  failed?: string;
  onRetry: () => void;
}) {
  if (stateId === 'loading')
    return (
      <p role="status" className="border-2 border-ink bg-panel p-8 text-center text-stone">
        {pending}
      </p>
    );
  if (stateId === 'error')
    return (
      <div role="alert" className="border-2 border-ink bg-panel p-8 text-center">
        <p className="font-bold">{failed}</p>
        <p className="mt-2 text-sm text-stone">잠시 후 다시 시도해 주세요.</p>
        <div className="mt-5 flex justify-center">
          <Button variant="secondary" onClick={onRetry}>
            다시 불러오기
          </Button>
        </div>
      </div>
    );
  return null;
}
