import { useState } from 'react';
import { useCreateStore } from './createStore';
import type { DraftNode } from './lib/draft';

/**
 * 좌측 `발표 자료` 트리. 시안 여섯 장에 내내 서 있는 그것입니다.
 *
 * ★ 평가기준 · 슬라이드 · 대본이 **각자** 버전을 올립니다 (슬라이드 V1 · 대본 V3 · 평가기준 V2).
 *   이 화면이 마법사(다음→다음)가 아니라 한 판인 이유가 여기 있습니다 —
 *   대본만 세 번 고치는 일이 정상 흐름이고, 그때 슬라이드를 다시 올리게 하면 안 됩니다.
 *
 *   지금 서버의 `POST /{id}/upload` 는 발표자료와 대본을 **한 번에** 받습니다.
 *   그래서 이 화면대로 가려면 업로드를 둘로 쪼개는 협의가 필요합니다.
 *
 * 어느 버전으로 연습할지는 여기서 고르지 않습니다 — 시작 직전의 확인 창이 맡습니다.
 */

type VersionGroup = Exclude<DraftNode, 'info'>;

interface RailGroup {
  node: VersionGroup;
  label: string;
  versions: number[];
  /** 아무것도 안 골랐을 때 본문에 뜨는 버전. 없으면 null */
  latest: number | null;
  /** "+ 새 버전" — 고르는 게 아니라 **새 버전을 만듭니다** */
  onAdd: () => void;
}

const row =
  'flex w-full items-center gap-2 rounded px-2.5 py-2 text-left text-sm hover:bg-cream/70';

function Glyph({ kind }: { kind: 'doc' | 'folder' | 'plus' }) {
  const d = {
    doc: 'M7 3h8l4 4v14H7zM14 3v5h5M10 13h6M10 17h6',
    folder: 'M3 6h6l2 2h10v11H3z',
    plus: 'M12 5v14M5 12h14',
  }[kind];
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      className="h-4 w-4 shrink-0"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
    >
      <path d={d} />
    </svg>
  );
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 24 24"
      className={['h-3.5 w-3.5 shrink-0 text-stone', open ? 'rotate-90' : ''].join(' ')}
      fill="none"
      stroke="currentColor"
      strokeWidth="2.4"
    >
      <path d="M9 6l6 6-6 6" />
    </svg>
  );
}

function GroupBlock({ group }: { group: RailGroup }) {
  const node = useCreateStore((s) => s.node);
  const version = useCreateStore((s) => s.version);
  const select = useCreateStore((s) => s.select);
  const [open, setOpen] = useState(true);

  return (
    <li>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className={`${row} font-bold`}
      >
        <Chevron open={open} />
        <Glyph kind="folder" />
        <span className="flex-1">{group.label}</span>
      </button>

      {open && (
        <ul className="ml-4 border-l border-line pl-2">
          {group.versions.map((v) => {
            const active = node === group.node && (version ?? group.latest) === v;
            return (
              <li key={v}>
                <button
                  type="button"
                  onClick={() => select(group.node, v)}
                  aria-current={active ? 'true' : undefined}
                  className={[
                    row,
                    'border-l-[3px]',
                    active
                      ? 'border-coral bg-coral-wash font-bold text-coral-deep'
                      : 'border-transparent',
                  ].join(' ')}
                >
                  <Glyph kind="doc" />V{v}
                </button>
              </li>
            );
          })}
          <li>
            <button
              type="button"
              onClick={group.onAdd}
              className={`${row} text-stone hover:text-ink`}
            >
              <Glyph kind="plus" />새 버전
            </button>
          </li>
        </ul>
      )}
    </li>
  );
}

export function VersionRail() {
  const draft = useCreateStore((s) => s.draft);
  const node = useCreateStore((s) => s.node);
  const select = useCreateStore((s) => s.select);
  const addSlideVersion = useCreateStore((s) => s.addSlideVersion);
  const addScriptVersion = useCreateStore((s) => s.addScriptVersion);
  const addCriteriaVersion = useCreateStore((s) => s.addCriteriaVersion);

  // 시안의 순서입니다 — 평가기준이 슬라이드·대본보다 앞에 섭니다
  const groups: RailGroup[] = [
    {
      node: 'criteria',
      label: '평가기준',
      versions: draft.criteria.map((v) => v.version),
      latest: draft.criteria.at(-1)?.version ?? null,
      onAdd: addCriteriaVersion,
    },
    {
      node: 'slides',
      label: '슬라이드',
      versions: draft.slides.map((v) => v.version),
      latest: draft.slides.at(-1)?.version ?? null,
      onAdd: addSlideVersion,
    },
    {
      node: 'script',
      label: '대본',
      versions: draft.scripts.map((v) => v.version),
      latest: draft.scripts.at(-1)?.version ?? null,
      // 직전 버전의 글을 물려받습니다 — V1 → V2 → V3 는 고쳐 쓴 흔적입니다
      onAdd: () => addScriptVersion(draft.scripts.at(-1)?.text ?? ''),
    },
  ];

  return (
    <nav aria-label="발표 자료" className="flex flex-col gap-1">
      <button
        type="button"
        onClick={() => select('info')}
        aria-current={node === 'info' ? 'true' : undefined}
        className={[
          row,
          'border-l-[3px] font-bold',
          node === 'info' ? 'border-coral bg-coral-wash text-coral-deep' : 'border-transparent',
        ].join(' ')}
      >
        <Glyph kind="doc" />
        발표정보
      </button>

      <ul className="flex flex-col gap-1">
        {groups.map((g) => (
          <GroupBlock key={g.node} group={g} />
        ))}
      </ul>
    </nav>
  );
}
