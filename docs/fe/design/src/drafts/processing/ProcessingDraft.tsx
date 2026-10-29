import { useState } from 'react';
import { Badge, type Tone } from '@/ui/Badge';
import { Card } from '@/ui/Panel';
import { Button } from '@/ui/Button';
import { DraftPage, Mascot } from '../ui';

/**
 * 분석 대기 시안 (P6-0) — 발표를 끝낸 뒤 리포트가 나오기 전.
 *
 * 단계는 BE 흐름 그대로입니다 (`docs/ai/architecture.md` 1-3 · 4번):
 *   Take 결과 만들기(`/coach/finalize`) → 말한 내용 채점(`/takes/analyze`)
 *   → 확인할 것이 있으면 **대본 확인**(사용자 답) → 코치 총평(`/takes/review`)
 * 대본 확인은 리뷰를 부르기 전까지만 받습니다. 건너뛰거나 화면을 떠나도 분석은 이어집니다.
 */

type Variant = 'RUNNING' | 'CONFIRM' | 'FAILED';

const VARIANTS = [
  { id: 'RUNNING' as const, label: '분석 중' },
  { id: 'CONFIRM' as const, label: '대본 확인' },
  { id: 'FAILED' as const, label: '실패' },
];

type StepState = 'DONE' | 'RUNNING' | 'WAITING' | 'PENDING' | 'FAILED';

const STEP_BADGE: Record<StepState, { label: string; tone: Tone }> = {
  DONE: { label: '끝남', tone: 'strong' },
  RUNNING: { label: '진행 중', tone: 'good' },
  WAITING: { label: '확인 필요', tone: 'bad' },
  PENDING: { label: '대기', tone: 'muted' },
  FAILED: { label: '실패', tone: 'bad' },
};

const STEPS: Record<Variant, { name: string; detail: string; state: StepState }[]> = {
  RUNNING: [
    {
      name: '발표 기록 정리',
      detail: '시선 · 속도 · 시간 기록을 Take 결과로 묶어요',
      state: 'DONE',
    },
    {
      name: '말한 내용 채점',
      detail: '대본의 핵심 내용을 전달했는지 장마다 봐요',
      state: 'RUNNING',
    },
    { name: '코치 총평', detail: '먼저 고칠 것과 다음 미션을 정해요', state: 'PENDING' },
  ],
  CONFIRM: [
    {
      name: '발표 기록 정리',
      detail: '시선 · 속도 · 시간 기록을 Take 결과로 묶어요',
      state: 'DONE',
    },
    { name: '말한 내용 채점', detail: '비슷하게 들린 말이 있어 확인이 필요해요', state: 'WAITING' },
    { name: '코치 총평', detail: '먼저 고칠 것과 다음 미션을 정해요', state: 'PENDING' },
  ],
  FAILED: [
    {
      name: '발표 기록 정리',
      detail: '이번 Take의 기록으로 결과를 만들지 못했어요',
      state: 'FAILED',
    },
    { name: '말한 내용 채점', detail: '', state: 'PENDING' },
    { name: '코치 총평', detail: '', state: 'PENDING' },
  ],
};

/** `script-coverage-evaluation.md` 5절 `confirmations` */
const CONFIRMATIONS = [
  { slide_number: 3, item_id: 'R1', script_text: '37%', stt_text: '삼십칠 퍼센트' },
  { slide_number: 5, item_id: 'R2', script_text: '2만 명', stt_text: '이만 명' },
];

export function ProcessingDraft() {
  const [variant, setVariant] = useState<Variant>('RUNNING');
  const [answers, setAnswers] = useState<Record<string, 'as_script' | 'as_stt'>>({});

  return (
    <DraftPage
      title="분석 대기"
      eyebrow="캡스톤 중간발표 / TAKE 4"
      meta="발표 길이 04:38"
      variants={VARIANTS}
      variant={variant}
      onVariant={setVariant}
      questions={[
        '분석 진행 상태를 FE가 어떻게 받을지(주기적으로 묻기 · WebSocket) BE와 정해야 해요.',
        '대본 확인을 기다리는 시간 제한을 화면에 보여 줄지 정해야 해요 (BE가 정한 시간이 지나면 그대로 진행).',
        '분석 중에 리포트로 먼저 넘어가 측정값부터 보게 할지 정해야 해요.',
      ]}
    >
      <div className="grid items-start gap-6 lg:grid-cols-[1fr_1.2fr]">
        <Card title="리포트를 준비하고 있어요">
          <ol className="flex flex-col">
            {STEPS[variant].map((s, i) => (
              <li key={s.name} className="flex gap-4 border-b border-line py-4 last:border-b-0">
                <span className="tabular font-mono text-2xl font-bold text-stone">0{i + 1}</span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <p className="font-bold">{s.name}</p>
                    <Badge tone={STEP_BADGE[s.state].tone}>{STEP_BADGE[s.state].label}</Badge>
                  </div>
                  {s.detail && <p className="mt-1 text-sm text-stone">{s.detail}</p>}
                </div>
              </li>
            ))}
          </ol>
          {variant === 'RUNNING' && (
            <p className="text-sm text-stone">
              보통 1분 안에 끝나요. 화면을 떠나도 분석은 계속되고, 홈에서 다시 볼 수 있어요.
            </p>
          )}
        </Card>

        {variant === 'RUNNING' && (
          <Card>
            <Mascot who="chichi">수고했어! 피코 교수님이 발표를 꼼꼼히 보고 계셔.</Mascot>
            <div className="flex flex-wrap gap-3">
              <Button variant="secondary">홈으로</Button>
            </div>
          </Card>
        )}

        {variant === 'CONFIRM' && (
          <Card title="이 말, 대본대로 하셨나요?">
            <p className="text-sm text-stone">
              음성 인식이 숫자나 단어를 다르게 적었을 수 있어요. 답해 주시면 채점에 반영해요.
            </p>
            <ul className="flex flex-col gap-3">
              {CONFIRMATIONS.map((c) => (
                <li key={c.item_id} className="border-2 border-ink bg-white p-4">
                  <p className="text-xs font-bold text-stone">{c.slide_number}번 장</p>
                  <div className="mt-2 grid grid-cols-2 gap-3 text-sm">
                    <div>
                      <p className="text-xs text-stone">대본</p>
                      <p className="mt-0.5 font-bold">{c.script_text}</p>
                    </div>
                    <div>
                      <p className="text-xs text-stone">들린 말</p>
                      <p className="mt-0.5 font-bold">{c.stt_text}</p>
                    </div>
                  </div>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {(
                      [
                        ['as_script', '대본대로 말했어요'],
                        ['as_stt', '들린 대로 말했어요'],
                      ] as const
                    ).map(([value, label]) => (
                      <button
                        key={value}
                        type="button"
                        aria-pressed={answers[c.item_id] === value}
                        onClick={() => setAnswers((a) => ({ ...a, [c.item_id]: value }))}
                        className={[
                          'border-2 border-ink px-3 py-1.5 text-sm font-bold',
                          answers[c.item_id] === value
                            ? 'bg-ink text-panel'
                            : 'bg-panel hover:bg-cream',
                        ].join(' ')}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                </li>
              ))}
            </ul>
            <div className="flex flex-wrap justify-end gap-3">
              <Button variant="secondary">건너뛰기</Button>
              <Button>답 보내기</Button>
            </div>
          </Card>
        )}

        {variant === 'FAILED' && (
          <Card title="이번 Take는 분석하지 못했어요">
            <p className="text-sm leading-relaxed text-stone">
              발표 기록이 너무 짧거나 측정이 거의 되지 않았을 수 있어요. 이번 Take는 연습 기록에
              &apos;분석 실패&apos;로 남아요.
            </p>
            <div className="flex flex-wrap gap-3">
              <Button>다시 연습하기</Button>
              <Button variant="secondary">홈으로</Button>
            </div>
          </Card>
        )}
      </div>
    </DraftPage>
  );
}
