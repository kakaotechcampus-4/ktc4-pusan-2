import { useState } from 'react';
import { formatDuration } from '@/shared/lib/clock';
import { Badge, type Tone } from '@/shared/ui/Badge';
import { Card } from '@/shared/ui/Panel';
import { Button } from '@/shared/ui/Button';
import { DraftPage, Mascot } from '../ui';
import { SCRIPT_MODE_LABEL, allTakes, pct, type TakeStatus } from '../takesSample';

/**
 * Take 기록 시안 (P16) — 모든 피치의 연습 기록을 최신순으로.
 * 홈은 피치별로 접어 보여 주고, 여기는 "언제 무엇을 연습했나"를 한 줄씩 봅니다.
 */

type Variant = 'OK' | 'EMPTY';

const VARIANTS = [
  { id: 'OK' as const, label: '정상' },
  { id: 'EMPTY' as const, label: '기록 없음' },
];

const STATUS: Record<TakeStatus, { label: string; tone: Tone; link: string }> = {
  ANALYZING: { label: '분석 중', tone: 'good', link: '진행 보기 →' },
  COMPLETED: { label: '완료', tone: 'plain', link: '리포트 →' },
  FAILED: { label: '분석 실패', tone: 'muted', link: '' },
};

export function TakesDraft() {
  const [variant, setVariant] = useState<Variant>('OK');
  const [pitch, setPitch] = useState('전체');
  const rows = variant === 'EMPTY' ? [] : allTakes;
  const pitches = ['전체', ...new Set(allTakes.map((t) => t.pitch_title))];
  const shown = pitch === '전체' ? rows : rows.filter((t) => t.pitch_title === pitch);

  return (
    <DraftPage
      title="Take 기록"
      eyebrow="내 연습 기록"
      meta={`이번 주 ${variant === 'EMPTY' ? 0 : 3}번 연습`}
      variants={VARIANTS}
      variant={variant}
      onVariant={setVariant}
      questions={[
        '홈의 피치별 연습 기록과 겹쳐요. 이 화면을 따로 둘지, 홈으로 합칠지 정해야 해요.',
        '기록이 많아질 때 페이지를 나눌지(더 보기 · 무한 스크롤) 정해야 해요.',
      ]}
    >
      {rows.length === 0 ? (
        <Card>
          <Mascot who="chichi">아직 연습 기록이 없어. 첫 Take를 시작해 보자!</Mascot>
          <div>
            <Button>내 피치로</Button>
          </div>
        </Card>
      ) : (
        <Card
          action={
            <label className="flex items-center gap-2 text-sm font-bold">
              피치
              <select
                value={pitch}
                onChange={(e) => setPitch(e.target.value)}
                className="border-2 border-ink bg-white px-2 py-1"
              >
                {pitches.map((p) => (
                  <option key={p}>{p}</option>
                ))}
              </select>
            </label>
          }
          title="최근 연습"
        >
          <ul className="flex flex-col">
            {shown.map((t) => (
              <li
                key={t.take_id}
                className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-b border-line py-4 last:border-b-0"
              >
                <div className="min-w-0">
                  <p className="text-xs text-stone">
                    {t.date} · {t.pitch_title}
                  </p>
                  <p className="mt-0.5 flex items-center gap-2 font-bold">
                    TAKE {t.take_number}
                    <Badge tone={STATUS[t.status].tone}>{STATUS[t.status].label}</Badge>
                  </p>
                </div>
                <dl className="tabular flex flex-wrap gap-x-6 gap-y-1 text-sm">
                  <div>
                    <dt className="sr-only">대본 보기</dt>
                    <dd className="text-stone">{SCRIPT_MODE_LABEL[t.script_mode]}</dd>
                  </div>
                  <div>
                    <dt className="sr-only">시간</dt>
                    <dd>
                      {formatDuration(t.duration_ms)}
                      <span className="text-stone"> / {formatDuration(t.goal_ms)}</span>
                    </dd>
                  </div>
                  <div>
                    <dt className="sr-only">청중 응시</dt>
                    <dd>응시 {pct(t.audience_ratio)}</dd>
                  </div>
                </dl>
                <span className="text-sm font-bold">{STATUS[t.status].link}</span>
              </li>
            ))}
          </ul>
        </Card>
      )}
    </DraftPage>
  );
}
