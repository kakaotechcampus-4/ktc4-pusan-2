import { useState } from 'react';
import { Badge } from '@/ui/Badge';
import { Button } from '@/ui/Button';
import { Card } from '@/ui/Panel';
import { DraftPage } from '../ui';
import { reviewFixture as review } from '@/samples/review';

/**
 * 시작 전 세팅 + 이번 Take 미션 시안 (시안 09) — 실제 화면은 `features/rehearsal/prepare/DeviceCheckPage.tsx`.
 *
 * 화면은 왼쪽 "지금 보이는 것"(카메라 · 점검), 오른쪽 "이번 Take를 어떻게 할지"(대본 표시)로 나뉩니다.
 * 미션도 "이번 Take를 어떻게 할지"라서 **오른쪽 맨 위**에 둡니다.
 *
 * 미션은 직전 리뷰가 만든 다음 미션입니다 (`review-agent.md` 5-3 `next_missions`). 리포트의
 * "Take 5에서 해 볼 것"과 같은 데이터라 같은 목(`mocks/report.ts`)을 씁니다.
 * 바로 앞 Take 의 리뷰가 없으면(분석 중 · 실패) 리뷰를 마친 그 전 Take 의 미션을 씁니다 (BE #409).
 * 둘 중 어느 쪽인지는 알 수 없어서 "분석 중"이라고 말하지 않습니다.
 */

type Variant = 'MISSION' | 'ANALYZING' | 'FIRST';

const VARIANTS = [
  { id: 'MISSION' as const, label: '미션 있음' },
  { id: 'ANALYZING' as const, label: '앞 Take 리뷰 없음' },
  { id: 'FIRST' as const, label: '첫 Take' },
];

type ScriptMode = 'HIGHLIGHT' | 'KEYWORD' | 'OFF';

const SCRIPT_MODES: { id: ScriptMode; title: string; desc: string }[] = [
  { id: 'HIGHLIGHT', title: '전체 대본', desc: '대본 전체를 보며 연습해요.' },
  { id: 'KEYWORD', title: '핵심 키워드만', desc: '문장 대신 핵심 단어만 봐요.' },
  { id: 'OFF', title: '실전 모드 · 대본 없이', desc: '대본과 실시간 코칭 없이 연습해요.' },
];

export function PrepareMissionDraft() {
  const [variant, setVariant] = useState<Variant>('MISSION');
  const [scriptMode, setScriptMode] = useState<ScriptMode>('KEYWORD');

  return (
    <DraftPage
      title="시작 전 세팅"
      eyebrow="캡스톤 중간발표 / TAKE 5"
      meta="자료 v2 · 대본 v2"
      variants={VARIANTS}
      variant={variant}
      onVariant={setVariant}
      questions={[
        '미션을 고르거나 끌 수 있게 할지 정해야 해요. 지금은 BE가 리뷰를 마친 가장 최근 Take의 미션을 모두 실어요 (GET /prepare `missions`, BE #409).',
        '미션 이유(reason)는 AI가 리뷰한 Take 기준으로 써서 "이번 Take 7번"처럼 나와요. 실제 화면은 이유를 숨겼어요 — 보이려면 AI에 "지난 Take" 표현을 요청해야 해요.',
      ]}
    >
      <div className="grid gap-5 lg:grid-cols-[1fr_340px]">
        {/* ── 왼쪽: 지금 보이는 것 (실제 화면과 같은 자리, 시안에서는 자리만) ── */}
        <div className="flex flex-col gap-4">
          <div className="flex aspect-video w-full items-center justify-center border-2 border-ink bg-stage-deep text-sm text-ink-stage/70 shadow-[4px_4px_0_var(--color-line)]">
            카메라 미리보기 · 시선 기준 잡기
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <Card title="장치 선택">
              <div className="space-y-1 text-sm text-stone">
                <p>카메라 · HCAM01L</p>
                <p>마이크 · USB Audio</p>
              </div>
            </Card>
            <Card title="점검 항목">
              <ul className="space-y-1.5 text-sm">
                <li>✓ 카메라 · 얼굴 인식됨</li>
                <li>✓ 마이크 입력 확인됨</li>
                <li>✓ 시선 기준 설정 완료</li>
              </ul>
            </Card>
          </div>
        </div>

        {/* ── 오른쪽: 이번 Take를 어떻게 할지 ── */}
        <div className="flex flex-col gap-4">
          <MissionCard variant={variant} exam={scriptMode === 'OFF'} />

          <Card title="발표 중 대본 표시">
            <div role="radiogroup" className="flex flex-col gap-2">
              {SCRIPT_MODES.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  role="radio"
                  aria-checked={scriptMode === m.id}
                  onClick={() => setScriptMode(m.id)}
                  className={[
                    'border-2 px-3 py-2 text-left',
                    scriptMode === m.id
                      ? 'border-ink bg-coral-wash'
                      : 'border-line-strong bg-white hover:bg-cream',
                  ].join(' ')}
                >
                  <p className="text-sm font-bold">{m.title}</p>
                  <p className="text-xs text-stone">{m.desc}</p>
                </button>
              ))}
            </div>
          </Card>

          <Button>{scriptMode === 'OFF' ? 'Take 5 실전 모드로 시작' : 'Take 5 시작하기'}</Button>
        </div>
      </div>
    </DraftPage>
  );
}

/**
 * 이번 Take 미션. 실전 모드에서는 코치가 말하지 않으므로 "끝나고 리포트에서 확인"으로 바꿔 말합니다
 * (실전 모드 = 대본 없이, CLAUDE.md 8번).
 */
function MissionCard({ variant, exam }: { variant: Variant; exam: boolean }) {
  if (variant === 'FIRST') {
    return (
      <Card title="이번 Take 미션">
        <p className="text-sm text-stone">
          첫 Take라 아직 미션이 없어요. 편하게 연습해 보세요. 끝나면 다음에 해 볼 것을 알려 드려요.
        </p>
      </Card>
    );
  }

  return (
    <section className="border-2 border-ink bg-coral-wash p-5 shadow-[4px_4px_0_var(--color-line)] sm:p-6">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-lg font-bold">이번 Take 미션</h2>
        <Badge tone="plain">
          {variant === 'ANALYZING' ? 'Take 3에서 정함' : 'Take 4에서 정함'}
        </Badge>
      </div>
      <ol className="mt-3 flex flex-col gap-2">
        {review.next_missions.map((m) => (
          <li key={m.priority} className="flex gap-3 border-t border-ink/15 py-3">
            <span className="tabular flex h-6 w-6 shrink-0 items-center justify-center bg-coral text-xs font-bold text-white">
              {m.priority}
            </span>
            <div className="min-w-0">
              <p className="text-sm font-bold">{m.description}</p>
              <p className="mt-0.5 text-xs text-stone">{m.reason}</p>
            </div>
          </li>
        ))}
      </ol>
      {variant === 'ANALYZING' && (
        <p className="mt-3 text-xs leading-relaxed text-stone">Take 3 리뷰에서 정한 미션이에요.</p>
      )}
      <p className="mt-3 text-xs leading-relaxed text-stone">
        {exam
          ? '실전 모드에서는 코치가 알려 주지 않아요. 끝난 뒤 리포트에서 결과를 확인해요.'
          : '발표 중에 이 미션을 기준으로 코치가 알려 드려요.'}
      </p>
    </section>
  );
}
