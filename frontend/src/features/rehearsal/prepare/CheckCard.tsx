import type { ReactNode } from 'react';

/**
 * 점검 항목 카드. 장치 점검과 리허설 준비가 **같은 항목 셋**을 보여 줍니다.
 *
 * 세 줄이 전부입니다 — 카메라 · 마이크 · 시선 기준점.
 * 안 된 항목만 코랄 테두리로 튀게 두고, 된 항목은 조용히 가라앉힙니다.
 * 발표 직전 화면이라 "지금 뭘 해야 하나"가 한 눈에 보여야 합니다.
 */
export function CheckCard({
  title,
  rows,
  children,
}: {
  title: string;
  rows: {
    id: string;
    label: ReactNode;
    done: boolean;
    /** 아직 안 된 항목의 오른쪽 동작. 없으면 문구만 */
    action?: { text: string; onClick: () => void } | null;
  }[];
  children?: ReactNode;
}) {
  const done = rows.filter((r) => r.done).length;

  return (
    <section className="rounded-xl border border-stage-panel bg-stage-panel/40 p-4">
      <header className="flex items-baseline gap-2">
        <h2 className="text-sm font-bold">{title}</h2>
        <span className="tabular ml-auto font-mono text-xs text-stone">
          {done} / {rows.length}
        </span>
      </header>

      <ul className="mt-3 flex flex-col gap-2">
        {rows.map((row) => (
          <li key={row.id}>
            <CheckRow {...row} />
          </li>
        ))}
      </ul>

      {children ? <div className="mt-3">{children}</div> : null}
    </section>
  );
}

function CheckRow({
  label,
  done,
  action,
}: {
  label: ReactNode;
  done: boolean;
  action?: { text: string; onClick: () => void } | null;
}) {
  const skin = done
    ? 'border-stage-panel bg-stage-panel text-ink-stage'
    : 'border-coral bg-coral/10 text-coral';

  const body = (
    <>
      {/* 체크 표시는 색만으로 구분하지 않습니다 — 모양이 같이 바뀝니다 */}
      <span aria-hidden className="text-xs">
        {done ? '✓' : '○'}
      </span>
      <span className="text-sm">{label}</span>
      {action ? <span className="ml-auto text-xs font-semibold">{action.text}</span> : null}
    </>
  );

  const className = `flex w-full items-center gap-2 rounded-lg border px-3 py-2.5 text-left ${skin}`;

  // 끝난 항목에도 동작이 붙을 수 있습니다 — 시선 측정을 제외했을 때 '다시 잡기'
  if (action) {
    return (
      <button
        type="button"
        onClick={action.onClick}
        className={`${className} ${done ? 'hover:bg-stage-panel' : 'hover:bg-coral/20'}`}
      >
        {body}
      </button>
    );
  }
  return <div className={className}>{body}</div>;
}
