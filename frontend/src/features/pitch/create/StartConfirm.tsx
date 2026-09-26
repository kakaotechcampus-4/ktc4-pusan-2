import { useEffect, useRef } from 'react';
import { useCreateStore } from './createStore';
import {
  countChars,
  resolveChosen,
  type DraftNode,
  type PitchDraft,
  type Resolved,
} from './lib/draft';
import { computeGate } from './lib/gate';

/**
 * 연습을 시작하기 전에 **어떤 버전으로 가는지** 확인하는 단계.
 *
 * ── 왜 이 단계가 필요한가 ───────────────────────────────────────────
 * Take 는 여기서 정해진 셋을 **스냅샷합니다** (CLAUDE.md 8번). 나중에 새 버전을
 * 올려도 그 Take 의 리포트는 영원히 이 버전들을 근거로 읽힙니다. 되돌릴 수 없는
 * 선택인데, 이 단계가 없으면 말없이 최신이 들어갑니다 — 사이드바에서 V1 을 보고
 * 있어도 V3 로 연습이 시작되는 식입니다.
 *
 * ── 셋을 따로 고릅니다 ──────────────────────────────────────────────
 * 슬라이드 V2 · 대본 V1 · 평가기준 V1 같은 조합이 정상입니다. 자료를 고쳤다고
 * 대본까지 새것을 써야 할 이유가 없고, 대본만 다듬어 보는 연습도 흔합니다.
 */

interface RowSpec {
  node: DraftNode;
  label: string;
  versions: number[];
  chosen: number | null;
  /** 그 버전에 무엇이 들었는지 — 번호만 보고는 고를 수 없습니다 */
  detail: string;
  ok: boolean;
}

function describe(draft: PitchDraft, resolved: Resolved): RowSpec[] {
  const slides = resolved.slides;
  const script = resolved.script;
  const criteria = resolved.criteria;

  const blocks = script?.blocks ?? null;

  return [
    {
      node: 'slides',
      label: '슬라이드',
      versions: draft.slides.map((v) => v.version),
      chosen: slides?.version ?? null,
      detail: slides?.pageCount != null ? `${slides.pageCount}장` : '변환 대기 · 장수 미정',
      ok: slides?.pageCount != null && slides.pageCount > 0,
    },
    {
      node: 'script',
      label: '대본',
      versions: draft.scripts.map((v) => v.version),
      chosen: script?.version ?? null,
      detail: script
        ? blocks
          ? `${countChars(script.text).toLocaleString()}자 · ${blocks.length}블록 매핑됨`
          : `${countChars(script.text).toLocaleString()}자 · 매핑 안 됨`
        : '없음',
      ok: blocks !== null && blocks.length > 0,
    },
    {
      node: 'criteria',
      label: '평가기준',
      versions: draft.criteria.map((v) => v.version),
      chosen: criteria?.version ?? null,
      detail: criteria ? `${criteria.items.length}개` : '없음',
      ok: (criteria?.items.length ?? 0) > 0,
    },
  ];
}

export function StartConfirm({ onClose, onGo }: { onClose: () => void; onGo: () => void }) {
  const draft = useCreateStore((s) => s.draft);
  const chosen = useCreateStore((s) => s.chosen);
  const chooseVersion = useCreateStore((s) => s.chooseVersion);

  const resolved = resolveChosen(draft, chosen);
  const rows = describe(draft, resolved);
  // 고른 조합이 기준입니다 — 최신이 멀쩡해도 고른 것이 비었으면 시작할 수 없습니다
  const gate = computeGate(draft, chosen);

  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => closeRef.current?.focus(), []);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="start-confirm-title"
      onKeyDown={(e) => e.key === 'Escape' && onClose()}
      className="fixed inset-0 z-50 flex items-center justify-center bg-stage/60 p-4"
    >
      <div className="w-full max-w-lg rounded border-2 border-ink bg-panel p-6">
        <h2 id="start-confirm-title" className="text-lg font-bold">
          이 버전으로 연습합니다
        </h2>
        <p className="mt-1 text-xs text-stone">
          시작하면 아래 셋이 이번 Take에 고정됩니다. 나중에 새 버전을 올려도 이 기록은 바뀌지
          않습니다.
        </p>

        <ul className="mt-5 flex flex-col gap-3">
          {rows.map((row) => (
            <li key={row.node} className="flex items-center gap-3">
              <span className="w-16 shrink-0 text-sm font-bold">{row.label}</span>

              {row.versions.length === 0 ? (
                <span className="flex-1 text-sm text-stone">아직 없습니다</span>
              ) : (
                <select
                  aria-label={`${row.label} 버전`}
                  value={row.chosen ?? ''}
                  onChange={(e) => chooseVersion(row.node, Number(e.target.value))}
                  className="tabular rounded border border-line-strong bg-panel px-2 py-1.5 font-mono text-sm"
                >
                  {row.versions.map((v) => (
                    <option key={v} value={v}>
                      V{v}
                      {v === row.versions.at(-1) ? ' (최신)' : ''}
                    </option>
                  ))}
                </select>
              )}

              <span
                className={[
                  'flex-1 text-right text-xs',
                  row.ok ? 'text-stone' : 'font-bold text-coral',
                ].join(' ')}
              >
                {row.detail}
              </span>
            </li>
          ))}
        </ul>

        {!gate.ready && (
          <p role="alert" className="mt-4 rounded bg-coral-wash px-3 py-2 text-xs font-bold">
            {gate.message}
          </p>
        )}

        <div className="mt-6 flex justify-end gap-2">
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            className="rounded border border-line-strong px-4 py-2.5 text-sm"
          >
            돌아가기
          </button>
          <button
            type="button"
            disabled={!gate.ready}
            onClick={onGo}
            className={[
              'rounded px-5 py-2.5 text-sm font-bold',
              gate.ready
                ? 'bg-coral text-panel hover:bg-coral-deep'
                : 'cursor-not-allowed bg-cream text-stone',
            ].join(' ')}
          >
            이 버전으로 시작 ▶
          </button>
        </div>
      </div>
    </div>
  );
}
