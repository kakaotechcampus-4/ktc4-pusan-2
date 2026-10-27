import type { ReactNode } from 'react';
import { Card } from '@/shared/ui/Panel';

/**
 * 점검 항목 카드. 장치 점검과 리허설 준비가 **같은 항목**을 보여 줍니다.
 *
 * 꼭 할 셋 — 카메라 · 마이크 · 시선 기준점 — 과 하면 좋은 것(평소 목소리)이 있습니다.
 * 꼭 할 항목 중 안 된 것만 코랄 테두리로 튀게 두고, 된 항목은 조용히 가라앉힙니다.
 * 선택 항목은 안 해도 튀지 않고 개수에도 넣지 않습니다 — 시작을 막지 않는 것을 막는 것처럼
 * 보이면 안 됩니다. 발표 직전 화면이라 "지금 뭘 해야 하나"가 한 눈에 보여야 합니다.
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
    /**
     * 줄 오른쪽에 붙는 표시물 — 마이크 레벨 막대처럼 **누르는 것이 아닌** 것.
     * `action` 과 같이 쓰지 않습니다. 줄 전체가 버튼이 되면 막대를 누르려다
     * 캘리브레이션이 시작되는 식으로 어긋납니다.
     */
    trailing?: ReactNode;
    /** 안 해도 시작할 수 있는 항목. 안 됐어도 튀지 않고, 개수에 넣지 않습니다 */
    optional?: boolean;
  }[];
  children?: ReactNode;
}) {
  const required = rows.filter((r) => !r.optional);
  const done = required.filter((r) => r.done).length;

  return (
    <Card
      title={title}
      action={
        <span className="tabular font-mono text-xs text-ink/80">
          {done} / {required.length}
        </span>
      }
    >
      <ul className="flex flex-col gap-2">
        {rows.map(({ id, ...row }) => (
          <li key={id}>
            <CheckRow {...row} />
          </li>
        ))}
      </ul>

      {children}
    </Card>
  );
}

function CheckRow({
  label,
  done,
  action,
  trailing,
  optional = false,
}: {
  label: ReactNode;
  done: boolean;
  action?: { text: string; onClick: () => void } | null;
  trailing?: ReactNode;
  optional?: boolean;
}) {
  const skin = rowSkin(done, optional);

  const body = (
    <>
      {/* 체크 표시는 색만으로 구분하지 않습니다 — 모양이 같이 바뀝니다 */}
      <span aria-hidden className="text-xs">
        {done ? '✓' : '○'}
      </span>
      <span className="min-w-0 flex-1 text-sm [overflow-wrap:anywhere]">{label}</span>
      {optional && !done ? <span className="text-xs text-ink/80">선택</span> : null}
      {action ? <span className="ml-auto text-xs font-semibold">{action.text}</span> : null}
      {trailing ? <span className="ml-auto w-20 shrink-0">{trailing}</span> : null}
    </>
  );

  const className = `flex w-full items-center gap-2 border px-3 py-2.5 text-left ${skin}`;
  const hover = done || optional ? 'hover:bg-cream' : 'hover:bg-coral/20';

  // 끝난 항목에도 동작이 붙을 수 있습니다 — 시선 측정을 제외했을 때 '다시 잡기'
  if (action) {
    return (
      <button type="button" onClick={action.onClick} className={`${className} ${hover}`}>
        {body}
      </button>
    );
  }
  return <div className={className}>{body}</div>;
}

/** 된 것은 가라앉히고, 꼭 할 것 중 안 된 것만 튀게 합니다. 선택 항목은 안 됐어도 차분하게 둡니다 */
function rowSkin(done: boolean, optional: boolean): string {
  if (done) return 'border-line bg-panel text-ink';
  if (optional) return 'border-dashed border-line-strong bg-panel text-ink';
  return 'border-coral bg-coral/10 text-ink';
}
