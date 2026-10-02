import { useState } from 'react';
import { useCreateStore } from './createStore';
import type { DraftNode } from './lib/draft';
import { CaretIcon, ChevronIcon, FolderIcon, PixelMark } from './icons';

/**
 * 좌측 "발표 자료" 트리. 슬라이드 · 대본 · 평가기준 폴더 아래에 버전이 달립니다.
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
  /** 버전이 하나도 없을 때 폴더 아래에 적는 말. 시안 04 의 "등록된 파일 없음" · "선택 사항" */
  emptyHint: string | null;
  /** 버전이 없을 때도 추가 줄을 보일지. 평가기준은 선택이라 폴더를 눌러 본문에서 만듭니다 */
  addWhenEmpty: boolean;
  versions: number[];
  /** 굵게 표시할 최신 버전. 없으면 null */
  latest: number | null;
  /** "+ … 추가" — 고르는 게 아니라 **새 버전을 만듭니다** */
  onAdd: () => void;
}

/** 연습 버튼 색. 선택된 줄(active) 위에서는 배경이 진해서 같은 상태라도 색을 바꿉니다 */
function practiceButtonTone(going: boolean, active: boolean): string {
  if (going && active) return 'bg-panel/25';
  if (going) return 'bg-coral-wash text-coral-deep';
  if (active) return 'text-panel/60 hover:bg-panel/20';
  return 'text-stone hover:bg-coral-wash hover:text-coral-deep';
}

/** 트리의 가지 선. 폴더 아이콘 아래에서 내려와 각 줄로 꺾입니다 */
const branch =
  'relative pl-4 before:absolute before:left-0 before:top-0 before:h-1/2 before:w-3 before:border-b before:border-l before:border-line-strong';

function GroupBlock({ group }: { group: RailGroup }) {
  const node = useCreateStore((s) => s.node);
  const version = useCreateStore((s) => s.version);
  const select = useCreateStore((s) => s.select);
  const chosen = useCreateStore((s) => s.chosen);
  const chooseVersion = useCreateStore((s) => s.chooseVersion);
  const [open, setOpen] = useState(true);

  // 연습에 들고 갈 버전. 고르지 않았으면 최신입니다
  const goingWith = chosen[group.node] ?? group.latest;
  const showing = node === group.node;
  const empty = group.versions.length === 0;

  return (
    <li>
      <div className="flex items-center gap-1.5">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-label={`${group.label} ${open ? '접기' : '펼치기'}`}
          className="flex h-5 w-4 shrink-0 items-center justify-center"
        >
          <CaretIcon open={open} />
        </button>

        {/* 폴더를 누르면 그 갈래의 최신(또는 빈 화면)을 본문에 띄웁니다 */}
        <button
          type="button"
          onClick={() => select(group.node)}
          aria-current={showing ? 'true' : undefined}
          className="flex min-w-0 flex-1 items-center gap-2 py-1 text-left text-sm font-bold"
        >
          <FolderIcon />
          <span>{group.label}</span>
          {/*
            ★ 최신이 아니라 **연습에 들고 갈 버전**입니다.
              여기 걸린 숫자가 곧 Take 에 고정되는 값이라(CLAUDE.md 8번), 최신을 띄우면
              V1 로 연습하기로 해 놓고 머리말만 V3 인 상태가 됩니다.
              최신과 다르면 그 사실을 옆에 적어 — 실수로 옛 버전을 들고 가는 것을 막습니다.
          */}
          {goingWith !== null && (
            <span className="flex items-baseline gap-1 font-normal">
              <span className="tabular font-mono text-xs text-coral">V{goingWith}</span>
              {goingWith !== group.latest && (
                <span className="text-[10px] text-stone" title={`최신은 V${group.latest} 입니다`}>
                  (최신 V{group.latest})
                </span>
              )}
            </span>
          )}
          <span className="ml-auto">
            <ChevronIcon up={showing} />
          </span>
        </button>
      </div>

      {open && (
        <ul className="ml-[1.6rem] mt-1 flex flex-col gap-1">
          {empty && group.emptyHint && (
            <li className={`${branch} py-1 text-xs text-stone`}>{group.emptyHint}</li>
          )}

          {group.versions.map((v) => {
            const active = showing && (version ?? group.latest) === v;
            const going = v === goingWith;

            return (
              <li key={v} className={branch}>
                <div
                  className={[
                    'flex items-center rounded',
                    active ? 'bg-coral text-panel' : 'text-ink hover:bg-panel',
                  ].join(' ')}
                >
                  {/* 왼쪽 — 본문에 띄우기. 고르는 것과 **보는 것은 다릅니다** */}
                  <button
                    type="button"
                    onClick={() => select(group.node, v)}
                    aria-current={active ? 'true' : undefined}
                    className="flex-1 px-2 py-1 text-left text-xs"
                  >
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
                      practiceButtonTone(going, active),
                    ].join(' ')}
                  >
                    {going ? '연습' : '연습으로'}
                  </button>
                </div>
              </li>
            );
          })}

          {(!empty || group.addWhenEmpty) && (
            <li className={branch}>
              <button
                type="button"
                onClick={group.onAdd}
                className={[
                  'w-full rounded border px-2 py-1 text-left text-xs',
                  // 비어 있는 갈래를 보고 있으면 지금 할 일이 이 줄입니다 — 시안 04
                  showing && empty
                    ? 'border-coral bg-panel text-coral-deep'
                    : 'border-transparent text-stone hover:text-ink',
                ].join(' ')}
              >
                + {group.addLabel}
              </button>
            </li>
          )}
        </ul>
      )}
    </li>
  );
}

/** "2026-10-09" → "2026.10.09" */
const dotDate = (iso: string) => iso.replaceAll('-', '.');

/**
 * 맨 위 "발표 정보". 버전이 없어 접을 것도 고를 것도 없습니다 —
 * 누르면 본문에서 제목과 날짜를 고치고, 아래 두 줄은 지금 들어간 값을 보여 줍니다.
 */
function InfoItem() {
  const node = useCreateStore((s) => s.node);
  const select = useCreateStore((s) => s.select);
  const title = useCreateStore((s) => s.draft.title);
  const date = useCreateStore((s) => s.draft.presentationDate);
  const showing = node === 'info';

  return (
    <li>
      <button
        type="button"
        onClick={() => select('info')}
        aria-current={showing ? 'true' : undefined}
        className="flex w-full items-center gap-2 py-1 pl-[1.375rem] text-left text-sm font-bold"
      >
        <FolderIcon />
        <span>발표 정보</span>
        <span className="ml-auto">
          <ChevronIcon up={showing} />
        </span>
      </button>

      <ul className="ml-[1.6rem] mt-1 flex flex-col gap-1 text-xs">
        <li className={`${branch} truncate py-0.5 ${title.trim() ? 'text-ink' : 'text-stone'}`}>
          {title.trim() || '제목 미입력'}
        </li>
        <li className={`${branch} tabular py-0.5 ${date ? 'text-ink' : 'text-stone'}`}>
          {date ? dotDate(date) : '날짜 미정'}
        </li>
      </ul>
    </li>
  );
}

export function VersionRail() {
  const draft = useCreateStore((s) => s.draft);
  const select = useCreateStore((s) => s.select);
  const addSlideVersion = useCreateStore((s) => s.addSlideVersion);
  const addScriptVersion = useCreateStore((s) => s.addScriptVersion);
  const addCriteriaVersion = useCreateStore((s) => s.addCriteriaVersion);

  const latestSlide = draft.slides.at(-1);

  const groups: RailGroup[] = [
    {
      node: 'slides',
      label: '슬라이드',
      addLabel: '슬라이드 추가',
      emptyHint: '등록된 파일 없음',
      addWhenEmpty: true,
      versions: draft.slides.map((v) => v.version),
      latest: latestSlide?.version ?? null,
      // 올릴 자리가 이미 열려 있으면(없음 · 변환 대기) 빈 버전을 또 만들지 않고 그 자리를 띄웁니다.
      // 파일을 받는 순간 `attachSlides` 가 버전을 채우거나 만듭니다
      onAdd: () =>
        latestSlide === undefined || latestSlide.pageCount === null
          ? select('slides')
          : addSlideVersion(),
    },
    {
      node: 'script',
      label: '대본',
      addLabel: '대본 추가',
      emptyHint: null,
      addWhenEmpty: true,
      versions: draft.scripts.map((v) => v.version),
      latest: draft.scripts.at(-1)?.version ?? null,
      // 직전 버전의 글을 물려받습니다 — 목업의 V1 → V2 → V3 는 고쳐 쓴 흔적입니다
      onAdd: () => addScriptVersion(draft.scripts.at(-1)?.text ?? ''),
    },
    {
      node: 'criteria',
      label: '평가기준',
      addLabel: '평가기준 추가',
      emptyHint: '선택 사항',
      addWhenEmpty: false,
      versions: draft.criteria.map((v) => v.version),
      latest: draft.criteria.at(-1)?.version ?? null,
      onAdd: addCriteriaVersion,
    },
  ];

  return (
    <nav aria-label="발표 자료" className="flex min-h-0 flex-1 flex-col">
      <div className="mb-4 flex items-center justify-between border-b border-line-strong pb-3">
        <h2 className="text-sm font-bold">발표 자료</h2>
        <PixelMark />
      </div>
      <ul className="flex flex-col gap-4 overflow-y-auto">
        <InfoItem />
        {groups.map((g) => (
          <GroupBlock key={g.node} group={g} />
        ))}
      </ul>
    </nav>
  );
}
