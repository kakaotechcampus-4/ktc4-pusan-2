import { useCreateStore } from './createStore';
import { daysUntil, formatSeconds } from './lib/duration';

/**
 * 상단 세 칸 — 발표 제목 · 발표 날짜 · 발표시간. 시안에서 모든 화면 위에 서 있습니다.
 *
 * 읽기 전용입니다. 값을 고치는 곳은 사이드바의 `발표정보` 한 곳뿐이고, 여기는
 * 지금 무엇을 연습하는 중인지 보여 주기만 합니다. 슬라이드를 고치든 대본을 고치든
 * 이 셋은 피치 한 판에 하나뿐이라 버전 트리 밖에 둡니다.
 */

const card = 'flex min-w-0 items-center gap-3 rounded border border-line bg-panel px-4 py-3';
const label = 'shrink-0 text-xs text-stone';

/** 아이콘은 글자 앞의 장식입니다 — 의미는 옆의 라벨이 말합니다 */
function Icon({ d }: { d: string }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      className="h-5 w-5 shrink-0 text-ink"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
    >
      <path d={d} />
    </svg>
  );
}

export function MetaBar() {
  const draft = useCreateStore((s) => s.draft);
  const d = daysUntil(draft.presentationDate);

  const tolerance = `−${formatSeconds(draft.toleranceBelowSec)} / +${formatSeconds(draft.toleranceAboveSec)}`;

  return (
    <div className="grid gap-3 md:grid-cols-[1.35fr_1fr_1.1fr]">
      <div className={card}>
        <Icon d="M7 3h8l4 4v14H7zM14 3v5h5M10 12h6M10 16h6" />
        <span className={label}>발표 제목</span>
        <span className="truncate text-sm font-bold">{draft.title || '제목 없음'}</span>
      </div>

      <div className={card}>
        <Icon d="M4 6h16v14H4zM4 10h16M8 3v4M16 3v4" />
        <span className={label}>발표 날짜</span>
        <span className="tabular text-sm font-bold">
          {draft.presentationDate ? draft.presentationDate.replaceAll('-', '.') : '미정'}
        </span>
        {d !== null && (
          <span className="tabular rounded bg-coral-wash px-2 py-0.5 font-mono text-xs font-bold text-coral-deep">
            D-{d}
          </span>
        )}
      </div>

      <div className={card}>
        <Icon d="M12 3a9 9 0 100 18 9 9 0 000-18zM12 7v5l3 2" />
        <span className={label}>발표시간</span>
        <span className="tabular truncate text-sm font-bold">
          {formatSeconds(draft.timeLimitSec)} ({tolerance})
        </span>
      </div>
    </div>
  );
}
