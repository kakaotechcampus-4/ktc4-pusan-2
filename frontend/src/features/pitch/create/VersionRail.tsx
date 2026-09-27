import { useCreateStore } from './createStore';
import type { DraftNode } from './lib/draft';

/**
 * 좌측 버전 트리. 목업 다섯 장에 내내 서 있는 그것입니다.
 *
 * ★ 셋이 **각자** 버전을 올립니다 (슬라이드 V1 · 대본 V3 · 평가기준 V1).
 *   이 화면이 마법사(다음→다음)가 아니라 한 판인 이유가 여기 있습니다 —
 *   대본만 세 번 고치는 일이 정상 흐름이고, 그때 슬라이드를 다시 올리게 하면 안 됩니다.
 *
 *   지금 서버의 `POST /{id}/upload` 는 발표자료와 대본을 **한 번에** 받습니다.
 *   그래서 이 화면대로 가려면 업로드를 둘로 쪼개는 협의가 필요합니다.
 */

interface RailGroup {
  node: DraftNode;
  label: string;
  addLabel: string;
  versions: number[];
  /** 굵게 표시할 최신 버전. 없으면 null */
  latest: number | null;
  /** "+ 새로운 … 추가" — 고르는 게 아니라 **새 버전을 만듭니다** */
  onAdd: () => void;
}

function GroupBlock({ group }: { group: RailGroup }) {
  const node = useCreateStore((s) => s.node);
  const version = useCreateStore((s) => s.version);
  const select = useCreateStore((s) => s.select);
  const chosen = useCreateStore((s) => s.chosen);
  const chooseVersion = useCreateStore((s) => s.chooseVersion);

  // 연습에 들고 갈 버전. 고르지 않았으면 최신입니다
  const goingWith = chosen[group.node] ?? group.latest;

  return (
    <li>
      <div className="flex items-baseline justify-between px-1">
        <span className={group.latest === null ? 'text-sm text-stone' : 'text-sm font-bold'}>
          {group.label}
        </span>
        {/*
          ★ 최신이 아니라 **연습에 들고 갈 버전**입니다.
            여기 걸린 숫자가 곧 Take 에 고정되는 값이라(CLAUDE.md 8번), 최신을 띄우면
            V1 로 연습하기로 해 놓고 머리말만 V3 인 상태가 됩니다.
            최신과 다르면 그 사실을 옆에 적어 — 실수로 옛 버전을 들고 가는 것을 막습니다.
        */}
        {goingWith !== null && (
          <span className="flex items-baseline gap-1">
            <span className="tabular font-mono text-xs text-coral">V{goingWith}</span>
            {goingWith !== group.latest && (
              <span className="text-[10px] text-stone" title={`최신은 V${group.latest} 입니다`}>
                (최신 V{group.latest})
              </span>
            )}
          </span>
        )}
      </div>

      <ul className="mt-1">
        {group.versions.map((v) => {
          const active = node === group.node && (version ?? group.latest) === v;
          const going = v === goingWith;

          return (
            <li
              key={v}
              className={[
                'flex items-center rounded',
                active ? 'bg-coral text-panel' : 'text-ink hover:bg-cream',
              ].join(' ')}
            >
              {/* 왼쪽 — 본문에 띄우기. 고르는 것과 **보는 것은 다릅니다** */}
              <button
                type="button"
                onClick={() => select(group.node, v)}
                aria-current={active ? 'true' : undefined}
                className="flex flex-1 items-center gap-2 px-2 py-1.5 text-left text-sm"
              >
                <span aria-hidden="true" className="font-mono text-xs opacity-60">
                  ┗
                </span>
                V{v}
              </button>

              {/*
                오른쪽 — 이번 연습에 쓸 버전 고르기.
                ★ 보고 있는 것과 연습에 쓰는 것을 따로 둡니다. 지난 버전을 열어 보다가
                  실수로 그것으로 연습이 시작되면 안 되고, 반대로 최신을 보면서
                  V1 로 연습하겠다고 정할 수도 있어야 합니다.
              */}
              <button
                type="button"
                onClick={() => chooseVersion(group.node, v)}
                aria-pressed={going}
                title={going ? '이번 연습에 사용됩니다' : `V${v}로 연습하기`}
                className={[
                  'mr-1 shrink-0 rounded px-1.5 py-0.5 text-[10px] font-bold',
                  // 숨기지 않습니다 — hover 로만 나타나면 터치와 키보드에서 못 찾습니다
                  going
                    ? active
                      ? 'bg-panel/25'
                      : 'bg-coral-wash text-coral-deep'
                    : active
                      ? 'text-panel/60 hover:bg-panel/20'
                      : 'text-stone hover:bg-coral-wash hover:text-coral-deep',
                ].join(' ')}
              >
                {going ? '연습' : '연습으로'}
              </button>
            </li>
          );
        })}
      </ul>

      <button
        type="button"
        onClick={group.onAdd}
        className="mt-1 px-2 text-xs text-stone hover:text-ink"
      >
        + {group.addLabel}
      </button>
    </li>
  );
}

export function VersionRail() {
  const draft = useCreateStore((s) => s.draft);
  const addSlideVersion = useCreateStore((s) => s.addSlideVersion);
  const addScriptVersion = useCreateStore((s) => s.addScriptVersion);
  const addCriteriaVersion = useCreateStore((s) => s.addCriteriaVersion);

  const groups: RailGroup[] = [
    {
      node: 'slides',
      label: '슬라이드',
      addLabel: '새로운 슬라이드 추가',
      versions: draft.slides.map((v) => v.version),
      latest: draft.slides.at(-1)?.version ?? null,
      onAdd: addSlideVersion,
    },
    {
      node: 'script',
      label: '대본',
      addLabel: '새로운 대본 추가',
      versions: draft.scripts.map((v) => v.version),
      latest: draft.scripts.at(-1)?.version ?? null,
      // 직전 버전의 글을 물려받습니다 — 목업의 V1 → V2 → V3 는 고쳐 쓴 흔적입니다
      onAdd: () => addScriptVersion(draft.scripts.at(-1)?.text ?? ''),
    },
    {
      node: 'criteria',
      label: '평가기준',
      addLabel: '새로운 평가기준 추가',
      versions: draft.criteria.map((v) => v.version),
      latest: draft.criteria.at(-1)?.version ?? null,
      onAdd: addCriteriaVersion,
    },
  ];

  return (
    <nav aria-label="버전" className="flex-1">
      <ul className="flex flex-col gap-5">
        {groups.map((g) => (
          <GroupBlock key={g.node} group={g} />
        ))}
      </ul>
    </nav>
  );
}
