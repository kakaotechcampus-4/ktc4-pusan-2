import { useState } from 'react';
import { Card, Stat } from '@/shared/ui/Panel';
import { Button } from '@/shared/ui/Button';
import { DraftPage } from '../ui';

/**
 * 마이페이지 시안 (P9) — 계정 · 연습 요약 · 이 브라우저의 기록.
 *
 * 리허설 기록의 원본은 브라우저(IndexedDB)입니다 (CLAUDE.md 4번). 그래서 "이 브라우저 기록 지우기"는
 * 서버 기록과 따로 보여 줍니다. 보내지 못한 기록이 있으면 지우기를 막습니다.
 */

type Variant = 'OK' | 'UNSENT';

const VARIANTS = [
  { id: 'OK' as const, label: '정상' },
  { id: 'UNSENT' as const, label: '보내지 못한 기록 있음' },
];

export function MyPageDraft() {
  const [variant, setVariant] = useState<Variant>('OK');

  return (
    <DraftPage
      title="마이페이지"
      eyebrow="데모 사용자"
      variants={VARIANTS}
      variant={variant}
      onVariant={setVariant}
      questions={[
        '회원 탈퇴를 둘지, 둔다면 BE에서 무엇을 지우는지 정해야 해요.',
        '마이페이지에 연습 설정(기본 대본 보기 등)을 둘지 정해야 해요. 지금은 시작 전 세팅에서만 골라요.',
      ]}
    >
      <Card title="계정">
        <dl className="grid gap-3 text-sm sm:grid-cols-[8rem_1fr]">
          <dt className="text-stone">이름</dt>
          <dd className="font-bold">데모 사용자</dd>
          <dt className="text-stone">이메일</dt>
          <dd className="font-bold">demo@pitchcoach.local</dd>
          <dt className="text-stone">로그인</dt>
          <dd className="font-bold">Google 계정</dd>
        </dl>
        <div>
          <Button variant="secondary">로그아웃</Button>
        </div>
      </Card>

      <section aria-label="연습 요약" className="grid gap-4 sm:grid-cols-3">
        <Stat label="내 피치" value="2개" />
        <Stat label="쌓은 연습" value="6 TAKES" />
        <Stat label="누적 발표 시간" value="29:20" />
      </section>

      <Card title="이 브라우저의 기록">
        <p className="text-sm leading-relaxed text-stone">
          발표 중 기록은 먼저 이 브라우저에 저장되고, 발표를 끝내면 서버로 보내요. 카메라 영상은
          저장하지도 보내지도 않아요.
        </p>
        {variant === 'UNSENT' && (
          <p
            role="alert"
            className="border-2 border-coral bg-coral-wash px-4 py-3 text-sm font-bold"
          >
            아직 서버로 보내지 못한 Take가 1개 있어요. 지우면 그 Take는 리포트를 만들 수 없어요.
          </p>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="secondary">
            {variant === 'UNSENT' ? '먼저 보내기' : '이 브라우저 기록 지우기'}
          </Button>
          <span className="text-xs text-stone">서버에 저장된 리포트는 지워지지 않아요.</span>
        </div>
      </Card>

      <Card title="계정 삭제">
        <p className="text-sm text-stone">
          피치 · 연습 기록 · 리포트가 모두 지워지고 되돌릴 수 없어요.
        </p>
        <div>
          <button
            type="button"
            className="text-sm font-bold text-coral-deep underline underline-offset-4"
          >
            계정 삭제하기
          </button>
        </div>
      </Card>
    </DraftPage>
  );
}
