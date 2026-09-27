import { useState } from 'react';
import { useNavigate } from 'react-router';
import { ScreenLabel } from '@/shared/ui/ScreenLabel';
import { MetaBar } from './MetaBar';
import { NextGate } from './NextGate';
import { StartConfirm } from './StartConfirm';
import { VersionRail } from './VersionRail';
import { useCreateStore } from './createStore';
import { latestCriteria, latestScript, latestSlides } from './lib/draft';
import { CriteriaPane } from './steps/CriteriaPane';
import { ScriptPane } from './steps/ScriptPane';
import { SlidePane } from './steps/SlidePane';

/**
 * P3 피치 생성. 목업 04~08 이 **한 라우트**입니다.
 *
 * 마법사(다음 → 다음)가 아닌 이유 — 사이드바가 버전 트리라 아무 때나 지난
 * 버전으로 돌아갈 수 있고, 목업에서 대본만 세 번 고친 흔적(V3)이 그 증거입니다.
 * "다음" 버튼은 단계 이동이 아니라 **연습 시작**입니다.
 *
 * 본문은 사이드바에서 고른 것(`node` + `version`)이 정합니다.
 */
function PitchCoachMark() {
  return (
    <svg aria-hidden="true" className="h-6 w-8" viewBox="0 0 34 26" shapeRendering="crispEdges">
      <rect className="fill-coral" x="2" y="0" width="6" height="4" />
      <rect className="fill-coral" x="18" y="0" width="6" height="4" />
      <rect className="fill-coral" x="0" y="4" width="26" height="16" />
      <rect className="fill-coral" x="26" y="8" width="8" height="4" />
      <rect className="fill-coral" x="3" y="20" width="5" height="4" />
      <rect className="fill-coral" x="15" y="20" width="5" height="4" />
      <rect className="fill-ink" x="5" y="9" width="5" height="5" />
      <rect className="fill-ink" x="15" y="9" width="5" height="5" />
    </svg>
  );
}

export function PitchCreatePage() {
  const draft = useCreateStore((s) => s.draft);
  const node = useCreateStore((s) => s.node);
  const version = useCreateStore((s) => s.version);
  const pitchId = useCreateStore((s) => s.pitchId);
  const draftId = useCreateStore((s) => s.draftId);
  const navigate = useNavigate();
  const [locked, setLocked] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const slideCount = latestSlides(draft)?.pageCount ?? 0;

  // 고른 버전이 없으면 그 갈래의 최신을 봅니다 — 사이드바가 굵게 표시한 것
  const pane = (() => {
    if (node === 'slides') {
      const picked = draft.slides.find((v) => v.version === version) ?? latestSlides(draft);
      return <SlidePane slide={picked} />;
    }
    if (node === 'script') {
      const picked = draft.scripts.find((v) => v.version === version) ?? latestScript(draft);
      return <ScriptPane script={picked} slideCount={slideCount} />;
    }
    const picked = draft.criteria.find((v) => v.version === version) ?? latestCriteria(draft);
    return <CriteriaPane criteria={picked} />;
  })();

  return (
    <main className="flex h-dvh flex-col gap-3 bg-greige p-4">
      {/* 시안 대조용. 걷어낼 때는 이 줄만 지웁니다 */}
      <ScreenLabel screenNo="04" screenName="피치 생성" entry="진입 · 홈의 새 피치 만들기" />

      <div className="flex min-h-0 flex-1 gap-0 rounded border border-line bg-cream">
        {/* ── 좌측: 로고 · 버전 트리 · 진행 조건 ─────────────────── */}
        <aside className="flex w-sidebar shrink-0 flex-col gap-5 border-r border-line-strong p-4">
          <div className="flex items-center gap-2 font-bold">
            <PitchCoachMark />
            <span className="text-sm tracking-tight">PITCH COACH</span>
          </div>

          <VersionRail />

          {/* 바로 넘어가지 않습니다 — 어떤 버전이 고정되는지 먼저 확인시킵니다 */}
          <NextGate onStart={() => setConfirming(true)} />
        </aside>

        {/* ── 우측: 상단 메타 + 본문 ──────────────────────────────── */}
        <section className="flex min-w-0 flex-1 flex-col gap-4 p-4">
          <MetaBar locked={locked} onToggleLock={() => setLocked((v) => !v)} />
          {pane}
        </section>
      </div>

      {confirming && (
        <StartConfirm
          onClose={() => setConfirming(false)}
          onGo={() => {
            // 장치 점검 → 준비 화면 → 시작 CTA 순서입니다.
            // ★ 여기서 POST /takes 를 부르지 않습니다 (CLAUDE.md 8번) —
            //   Take 는 준비 화면의 시작 CTA 에서만 생깁니다. 여기서 만들면
            //   점검하다 그만둔 만큼 빈 Take 가 쌓이고 takeNumber 가 어긋납니다.
            //
            // ★ pitchId 가 null 인 것은 생성 API 가 아직 없어서입니다.
            //   그동안은 draftId 로 이동합니다 — 목은 어떤 id 로도 답하므로
            //   화면 흐름은 확인되지만 서버에 저장된 것은 없습니다.
            navigate(`/pitch/${pitchId ?? draftId}/device-check`);
          }}
        />
      )}
    </main>
  );
}
