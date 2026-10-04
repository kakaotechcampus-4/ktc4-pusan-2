import { useState } from 'react';
import { Link, useNavigate } from 'react-router';
import { PitchCoachWordmark } from '@/shared/ui/PitchCoachWordmark';
import { Mascot } from './Mascot';
import { MetaBar } from './MetaBar';
import { StartConfirm } from './StartConfirm';
import { VersionRail } from './VersionRail';
import { useCreateStore } from './createStore';
import { latestCriteria, latestScript, latestSlides, type PitchDraft } from './lib/draft';
import { CriteriaPane } from './steps/CriteriaPane';
import { InfoPane } from './steps/InfoPane';
import { ScriptPane } from './steps/ScriptPane';
import { SlidePane } from './steps/SlidePane';

/**
 * P3 피치 생성. 시안 여섯 장(발표정보 · 평가기준 · 슬라이드 · 대본 입력 · 매핑 확인)이
 * **한 라우트**입니다.
 *
 * 마법사(다음 → 다음)가 아닌 이유 — 사이드바가 버전 트리라 아무 때나 지난
 * 버전으로 돌아갈 수 있고, 대본만 여러 번 고치는 일이 정상 흐름이기 때문입니다.
 * "다음" 버튼은 단계 이동이 아니라 **연습 시작**입니다 — 매핑 확인 화면에 있습니다.
 *
 * 본문은 사이드바에서 고른 것(`node` + `version`)이 정합니다.
 */

/** 마스코트 말풍선. 비어 있으면 풍선 없이 서 있기만 합니다 */
function mascotLine(node: string, draft: PitchDraft, version: number | null): string | undefined {
  if (node === 'info') return '준비부터 차근차근!';
  if (node === 'slides') return '발표할 자료를 확인해 봐!';
  if (node === 'script') {
    const script = draft.scripts.find((v) => v.version === version) ?? draft.scripts.at(-1) ?? null;
    if (script?.blocks && script.mappingSaved) return '저장 완료! 이제 연습하자.';
    return script?.blocks ? '이대로 괜찮은지 봐 줘!' : '한 문장씩 준비해 보자!';
  }
  return undefined;
}

export function PitchCreatePage() {
  const draft = useCreateStore((s) => s.draft);
  const node = useCreateStore((s) => s.node);
  const version = useCreateStore((s) => s.version);
  const pitchId = useCreateStore((s) => s.pitchId);
  const draftId = useCreateStore((s) => s.draftId);
  const navigate = useNavigate();
  const [confirming, setConfirming] = useState(false);

  // 고른 버전이 없으면 그 갈래의 최신을 봅니다
  const pane = (() => {
    if (node === 'info') return <InfoPane />;
    if (node === 'slides') {
      const picked = draft.slides.find((v) => v.version === version) ?? latestSlides(draft);
      return <SlidePane slide={picked} />;
    }
    if (node === 'script') {
      const picked = draft.scripts.find((v) => v.version === version) ?? latestScript(draft);
      return (
        <ScriptPane script={picked} slides={draft.slides} onNext={() => setConfirming(true)} />
      );
    }
    const picked = draft.criteria.find((v) => v.version === version) ?? latestCriteria(draft);
    return <CriteriaPane criteria={picked} />;
  })();

  return (
    <div className="flex h-dvh flex-col bg-greige text-ink">
      <header className="flex items-center justify-between border-b border-line-strong bg-panel px-6 py-4">
        <Link to="/" aria-label="PITCH COACH 홈">
          <PitchCoachWordmark />
        </Link>
        <Link to="/" className="text-sm font-bold hover:text-coral-deep">
          ← 내 피치
        </Link>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* ── 좌측: 발표 자료 트리 · 마스코트 ─────────────────────── */}
        <aside className="flex w-sidebar shrink-0 flex-col gap-4 overflow-y-auto border-r border-line-strong bg-cream/60 p-4">
          <h2 className="px-2.5 text-sm font-bold">발표 자료</h2>
          <VersionRail />
          <Mascot message={mascotLine(node, draft, version)} />
        </aside>

        {/* ── 우측: 위치 · 메타 · 본문 ────────────────────────────── */}
        <main className="flex min-w-0 flex-1 flex-col gap-4 overflow-y-auto p-6">
          <p className="text-xs text-stone">
            <Link to="/" className="hover:text-ink">
              내 피치
            </Link>
            <span aria-hidden="true"> › </span>
            <span className="font-bold text-ink">{draft.title.trim() || '새 피치'}</span>
          </p>
          <MetaBar />
          {pane}
        </main>
      </div>

      {confirming && (
        <StartConfirm
          onClose={() => setConfirming(false)}
          onGo={() => {
            // 장치 점검 → 시작 CTA 순서입니다.
            // ★ 여기서 POST /takes 를 부르지 않습니다 (CLAUDE.md 8번) —
            //   Take 는 시작 전 세팅의 시작 CTA 에서만 생깁니다. 여기서 만들면
            //   점검하다 그만둔 만큼 빈 Take 가 쌓이고 takeNumber 가 어긋납니다.
            //
            // ★ pitchId 가 null 인 것은 생성 API 가 아직 없어서입니다.
            //   그동안은 draftId 로 이동합니다 — 목은 어떤 id 로도 답하므로
            //   화면 흐름은 확인되지만 서버에 저장된 것은 없습니다.
            navigate(`/pitch/${pitchId ?? draftId}/device-check`);
          }}
        />
      )}
    </div>
  );
}
