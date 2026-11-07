import { useId, useState } from 'react';
import { Link } from 'react-router';
import { chipLabel, type Chip } from './chatResponse';

/**
 * 근거 칩 (12-4 F4) — 내 기록은 형식이 맞는 Take ID 일 때만 리포트로 이동하고, 화면 값은 링크 없이,
 * 자료는 누르면 인용 · 출처 · 라이선스를 펼칩니다(빠진 칸은 "정보 없음")
 */

const MISSING_INFO = '정보 없음';

export function ChipList({ chips }: { chips: readonly Chip[] }) {
  if (chips.length === 0) return null;
  return (
    <ul aria-label="근거" className="mt-1 flex flex-wrap gap-1.5">
      {chips.map((chip) => (
        <li key={chip.key}>
          <ChipView chip={chip} />
        </li>
      ))}
    </ul>
  );
}

const CHIP_CLASS =
  'inline-flex min-h-8 items-center rounded-full border border-line-strong bg-cream px-2.5 text-xs font-bold';

function ChipView({ chip }: { chip: Chip }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const label = chipLabel(chip);
  if (chip.type === 'RECORD' && chip.takeId)
    return (
      <Link
        to={`/takes/${chip.takeId}`}
        className={`${CHIP_CLASS} underline-offset-2 hover:underline`}
      >
        {label}
      </Link>
    );
  if (chip.type !== 'SOURCE') return <span className={CHIP_CLASS}>{label}</span>;
  // 자료 칩은 펼치면 인용 · 출처 · 라이선스. 빠진 칸은 "정보 없음" (12-4 F4)
  return (
    <>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen((v) => !v)}
        className={CHIP_CLASS}
      >
        {label}
      </button>
      {open && (
        <dl id={id} className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 text-xs leading-5">
          <dt className="text-stone">인용</dt>
          <dd>{chip.quote ?? MISSING_INFO}</dd>
          <dt className="text-stone">출처</dt>
          <dd>{chip.source ?? MISSING_INFO}</dd>
          <dt className="text-stone">라이선스</dt>
          <dd>{chip.license ?? MISSING_INFO}</dd>
        </dl>
      )}
    </>
  );
}
