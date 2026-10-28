import { useState } from 'react';
import { Card, Stat } from '@/shared/ui/Panel';
import { Button } from '@/shared/ui/Button';
import { DraftPage, Mascot } from '../ui';

/**
 * 업로드 실패 · 재시도 시안 (P15, Track A) — 발표를 끝냈는데 종료 정보를 서버에 못 보냈을 때.
 *
 * 브라우저가 원본입니다 (CLAUDE.md 4번). 기록은 IndexedDB에 남아 있으니 **잃은 게 없다**는 걸 먼저 말합니다.
 * 재시도는 같은 `clientSessionId`(멱등 키)로 보내므로 여러 번 눌러도 Take가 겹치지 않습니다.
 */

type Variant = 'FAILED' | 'RETRYING' | 'ABANDONED';

const VARIANTS = [
  { id: 'FAILED' as const, label: '보내기 실패' },
  { id: 'RETRYING' as const, label: '다시 보내는 중' },
  { id: 'ABANDONED' as const, label: '끝나지 않은 기록 발견' },
];

const HEADLINE: Record<Variant, string> = {
  FAILED: '발표 기록을 서버로 보내지 못했어요',
  RETRYING: '발표 기록을 다시 보내고 있어요',
  ABANDONED: '끝내지 못한 발표 기록이 있어요',
};

const BODY: Record<Variant, string> = {
  FAILED:
    '기록은 이 브라우저에 남아 있지만, 서버는 몇 분 뒤 이 Take를 끝내요. 가능하면 지금 다시 보내 주세요.',
  RETRYING: '잠시만 기다려 주세요. 다 보낼 때까지 이 창을 열어 두세요.',
  ABANDONED: '지난번 발표 도중 창이 닫힌 것 같아요. 남은 기록으로 이 Take를 마무리할 수 있어요.',
};

export function RetryDraft() {
  const [variant, setVariant] = useState<Variant>('FAILED');

  return (
    <DraftPage
      title="업로드 실패 · 재시도"
      eyebrow="캡스톤 중간발표 / TAKE 4"
      variants={VARIANTS}
      variant={variant}
      onVariant={setVariant}
      questions={[
        '발표 종료가 /complete 대신 Take 수정 API로 바뀌는 중이에요 (fix/fe-take-end-flow). 무엇을 다시 보내는지 그쪽과 맞춰야 해요.',
        '끝나지 않은 기록을 어디서 발견해 이 화면으로 보낼지(홈 진입 · 리허설 진입) 정해야 해요.',
        '"기록 버리기"를 둘지 정해야 해요. 버리면 되돌릴 수 없어요.',
      ]}
    >
      <Card>
        <h2 className="text-2xl font-bold">{HEADLINE[variant]}</h2>
        <p className="text-stone">{BODY[variant]}</p>
        <div className="flex flex-wrap gap-3">
          <Button>{variant === 'ABANDONED' ? '이 Take 마무리하기' : '다시 보내기'}</Button>
          <Button variant="secondary">나중에 할게요</Button>
        </div>
      </Card>

      <section aria-label="남아 있는 기록" className="grid gap-4 sm:grid-cols-3">
        <Stat label="발표 길이" value="04:38">
          끝내기를 누른 시각까지
        </Stat>
        <Stat label="시선 기록" value="278초">
          1초 기록 278개
        </Stat>
        <Stat label="슬라이드 이동" value="11회">
          마지막 장 12번
        </Stat>
      </section>

      <Card>
        <Mascot who="chichi">
          브라우저 기록(쿠키 · 사이트 데이터)을 지우면 이 기록도 사라져요. 보내기 전에는 지우지 말아
          줘!
        </Mascot>
      </Card>
    </DraftPage>
  );
}
