import type { ReactNode } from 'react';
import { useCreateStore } from './createStore';
import { daysUntil, dotDate } from './lib/date';
import { formatTimeLimit } from './lib/draft';

/**
 * 본문 위의 발표 정보 줄 — 제목 · 날짜 · 발표시간. 저장 전에는 기본값임을 알립니다.
 *
 * 어느 화면에서든 이 줄이 보입니다. 슬라이드나 대본을 고치는 동안에도
 * "무슨 발표를 언제 하는지"가 눈앞에 있어야 해서입니다.
 *
 * 누를 수 없는 평범한 줄입니다 — 카드 모양이면 발표정보 화면에서 바로 아래 입력 칸 대신
 * 여기에 쓰려고 누르게 됩니다. 고치려면 사이드바의 발표정보로 갑니다.
 */

function Item({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 items-start gap-2 py-1 lg:px-4 lg:first:pl-0 lg:last:pr-0">
      <dt className="shrink-0 text-xs leading-5 text-stone">{label}</dt>
      <dd className="flex min-w-0 flex-wrap items-baseline gap-x-2 text-sm">{children}</dd>
    </div>
  );
}

export function InfoBar() {
  const pitchId = useCreateStore((s) => s.pitchId);
  const title = useCreateStore((s) => s.draft.title.trim());
  const date = useCreateStore((s) => s.draft.presentationDate);
  const limitSec = useCreateStore((s) => s.draft.timeLimitSec);
  const lowerSec = useCreateStore((s) => s.draft.lowerToleranceSec);
  const upperSec = useCreateStore((s) => s.draft.upperToleranceSec);

  const d = daysUntil(date);
  const heading = pitchId === null ? '발표정보 · 아직 저장 전' : '저장된 발표정보';

  return (
    <section aria-label={heading} className="shrink-0 border-b border-line pb-2">
      <p className="mb-1 text-xs font-medium text-stone">{heading}</p>
      <dl className="grid grid-cols-1 lg:grid-cols-[minmax(0,2fr)_minmax(0,1.3fr)_minmax(0,1.3fr)] lg:divide-x lg:divide-line">
        <Item label="발표 제목">
          <span
            className={[
              'min-w-0 [overflow-wrap:anywhere]',
              title ? 'font-bold' : 'font-normal text-stone',
            ].join(' ')}
          >
            {title || '제목 미입력'}
          </span>
        </Item>

        <Item label="발표 날짜">
          <span
            className={[
              'tabular whitespace-nowrap font-medium',
              date ? '' : 'font-normal text-stone',
            ].join(' ')}
          >
            {date ? dotDate(date) : '날짜 미정'}
          </span>
          {d !== null && (
            <span className="tabular shrink-0 text-xs font-medium text-coral">D-{d}</span>
          )}
        </Item>

        <Item label="발표시간">
          <span className="tabular min-w-0 font-medium [overflow-wrap:anywhere]">
            {formatTimeLimit(limitSec, lowerSec, upperSec)}
          </span>
        </Item>
      </dl>
    </section>
  );
}
