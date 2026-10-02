import { Link, useNavigate } from 'react-router';
import { PitchCoachWordmark } from '@/shared/ui/PitchCoachWordmark';
import { InfoBar } from './InfoBar';
import { NextGate } from './NextGate';
import { VersionRail } from './VersionRail';
import { useCreateStore } from './createStore';
import { latestCriteria, latestScript, latestSlides } from './lib/draft';
import { CriteriaPane } from './steps/CriteriaPane';
import { InfoPane } from './steps/InfoPane';
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
export function PitchCreatePage() {
  const draft = useCreateStore((s) => s.draft);
  const node = useCreateStore((s) => s.node);
  const version = useCreateStore((s) => s.version);
  const pitchId = useCreateStore((s) => s.pitchId);
  const draftId = useCreateStore((s) => s.draftId);
  const navigate = useNavigate();

  const slideCount = latestSlides(draft)?.pageCount ?? 0;

  // 고른 버전이 없으면 그 갈래의 최신을 봅니다 — 사이드바가 굵게 표시한 것
  const pane = (() => {
    if (node === 'info') return <InfoPane />;
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
    <div className="flex h-dvh flex-col bg-panel text-ink">
      <header className="flex shrink-0 items-center justify-between border-b border-line-strong px-6 py-4">
        <Link to="/" aria-label="PITCH COACH 홈">
          <PitchCoachWordmark />
        </Link>
        <Link to="/" className="text-sm font-bold hover:text-coral-deep">
          ← 내 피치
        </Link>
      </header>

      <div className="flex min-h-0 flex-1 gap-4 p-4">
        {/* ── 좌측: 발표 정보 · 발표 자료 트리 · 응원 · 진행 조건 ──── */}
        <aside className="flex w-sidebar shrink-0 flex-col gap-4 rounded border border-line-strong bg-cream p-3">
          <VersionRail />

          <div className="flex items-center gap-2">
            <img
              src="/onboarding/chichi-portrait-longsleeve.png"
              alt=""
              className="h-14 w-14 shrink-0 rounded-full object-cover [image-rendering:pixelated]"
            />
            <p className="text-xs font-bold leading-snug">
              준비는 꼼꼼하게,
              <br />
              발표는 자신 있게!
            </p>
          </div>

          {/*
            장치 점검 → 준비 화면 → 시작 CTA 순서입니다. 연습할 버전은 사이드바의 "연습"
            버튼으로 고르고, 고르지 않은 갈래는 최신으로 갑니다 (확인 창은 두지 않습니다).

            ★ 여기서 POST /takes 를 부르지 않습니다 (CLAUDE.md 8번) —
              Take 는 준비 화면의 시작 CTA 에서만 생깁니다. 여기서 만들면
              점검하다 그만둔 만큼 빈 Take 가 쌓이고 takeNumber 가 어긋납니다.

            ★ pitchId 가 null 인 것은 생성 API 가 아직 없어서입니다.
              그동안은 draftId 로 이동합니다 — 목은 어떤 id 로도 답하므로
              화면 흐름은 확인되지만 서버에 저장된 것은 없습니다.
          */}
          <NextGate onStart={() => navigate(`/pitch/${pitchId ?? draftId}/device-check`)} />
        </aside>

        {/* ── 우측: 경로 · 발표 정보 줄 · 본문 ────────────────────── */}
        <main className="flex min-w-0 flex-1 flex-col gap-4 overflow-y-auto px-2 py-1">
          <nav aria-label="경로" className="flex items-center gap-2 text-xs text-stone">
            <Link to="/" className="hover:text-ink">
              내 피치
            </Link>
            <span aria-hidden="true">›</span>
            <span aria-current="page" className="border-b border-ink text-ink">
              {draft.title.trim() || '새 피치'}
            </span>
          </nav>

          <InfoBar />
          {pane}
        </main>
      </div>
    </div>
  );
}
