import { useState } from 'react';
import { Link } from 'react-router';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/shared/ui/Button';
import { Card } from '@/shared/ui/Panel';
import { InfoPage } from '@/shared/ui/InfoPage';
import { apiRequest } from '@/shared/api/client';
import { deleteLocalRecords } from '@/shared/lib/localData';
import { useWithdraw } from '@/shared/api/useWithdraw';
import { useSession, type User } from './useSession';

/**
 * P9 마이페이지.
 *
 * 로그아웃은 헤더에, 연습 요약은 홈에 있어서 여기 두지 않습니다.
 * 탈퇴 확인은 브라우저 confirm 입니다. 확인 방식(공용 모달 등)이 정해지면 바꿉니다.
 */
export function MyPage() {
  const user = useSession().data;
  const withdraw = useWithdraw();
  const client = useQueryClient();
  // 원자료 사용 동의 (#361). 서버가 돌려준 내 정보로 세션을 바로 바꿉니다
  const consent = useMutation({
    mutationFn: (value: boolean) =>
      apiRequest<User>('/api/users/me', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ raw_data_consent: value }),
      }),
    onSuccess: (me) => client.setQueryData(['session'], me),
  });
  const consented = consent.isPending ? consent.variables : (user?.raw_data_consent ?? false);
  const [cleared, setCleared] = useState<'OK' | 'BLOCKED' | null>(null);

  const clearRecords = () => {
    if (!window.confirm('이 브라우저에 남은 연습 기록을 지울까요? 되돌릴 수 없어요.')) return;
    deleteLocalRecords()
      .then(() => setCleared('OK'))
      .catch(() => setCleared('BLOCKED'));
  };

  const confirmWithdraw = () => {
    if (
      window.confirm(
        '탈퇴하면 피치 · 연습 기록 · 리포트가 모두 지워지고 되돌릴 수 없어요. 탈퇴할까요?',
      )
    )
      withdraw.mutate();
  };

  return (
    <InfoPage title="마이페이지" logo={false}>
      <Card title="계정">
        <dl className="grid gap-3 text-sm [overflow-wrap:anywhere] sm:grid-cols-[8rem_minmax(0,1fr)]">
          <dt className="text-stone">이름</dt>
          <dd className="font-bold">{user?.name}</dd>
          <dt className="text-stone">이메일</dt>
          <dd className="font-bold">{user?.email}</dd>
          <dt className="text-stone">로그인</dt>
          <dd className="font-bold">Google 계정</dd>
        </dl>
      </Card>

      {/* 동의 범위 · 지우는 때는 `docs/ai/architecture.md` 1-1 '원자료 사용 동의' · '지우기 · 보관' */}
      <Card title="연습 기록 사용 동의">
        <p className="text-sm leading-relaxed text-stone">
          리허설 중 1초마다 잰 시선 · 목소리 크기 기록을 연습이 끝난 뒤에도 남겨, 분석 기준이
          바뀌었을 때 지난 연습을 다시 분석하는 데 써요. 동의하지 않아도 리포트는 똑같이 나오고, 이
          기록은 리포트를 만든 뒤 지워요(분석에 실패한 연습은 7일 뒤).
        </p>
        <label className="flex min-h-11 items-center gap-3 text-sm font-bold">
          <input
            id="raw-data-consent"
            type="checkbox"
            checked={consented}
            disabled={!user || consent.isPending}
            onChange={(e) => consent.mutate(e.target.checked)}
            className="size-4 shrink-0 accent-ink"
          />
          연습 기록을 남겨 다시 분석하는 데 동의해요
        </label>
        <span className="text-xs text-stone">
          바꾼 설정은 다음에 시작하는 연습부터 적용돼요. 이미 끝난 연습은 그대로예요.
        </span>
        {consent.isError && (
          <p role="alert" className="text-sm text-coral-deep">
            동의 설정을 바꾸지 못했어요. 다시 시도해 주세요.
          </p>
        )}
      </Card>

      <Card title="이 브라우저의 기록">
        <p className="text-sm leading-relaxed text-stone">
          리허설 중 시선 · 슬라이드 · 코치 기록은 이 브라우저에도 저장돼요. 카메라 영상과 마이크
          소리는 저장하지 않아요.
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="secondary" onClick={clearRecords}>
            이 브라우저 기록 지우기
          </Button>
          <span className="text-xs text-stone">서버에 저장된 피치와 리포트는 지워지지 않아요.</span>
        </div>
        <p role="status" className="text-sm">
          {cleared === 'OK' && '이 브라우저의 기록을 지웠어요.'}
          {cleared === 'BLOCKED' &&
            '다른 탭에서 피치코치가 열려 있어 아직 지우지 못했어요. 그 탭을 닫으면 지워져요.'}
        </p>
      </Card>

      <Card title="약관 · 정책">
        <ul className="space-y-2 text-sm">
          <li>
            <Link
              to="/terms?from=home"
              className="inline-flex min-h-11 items-center underline underline-offset-4"
            >
              이용약관
            </Link>
          </li>
          <li>
            <Link
              to="/privacy?from=home"
              className="inline-flex min-h-11 items-center underline underline-offset-4"
            >
              개인정보 처리방침
            </Link>
          </li>
        </ul>
      </Card>

      <Card title="계정 삭제">
        <p className="text-sm text-stone">
          피치 · 연습 기록 · 리포트가 모두 지워지고 되돌릴 수 없어요.
        </p>
        <div>
          <button
            type="button"
            disabled={withdraw.isPending}
            onClick={confirmWithdraw}
            className="min-h-11 text-sm font-bold text-coral-deep underline underline-offset-4 disabled:opacity-50"
          >
            {withdraw.isPending ? '삭제하는 중…' : '계정 삭제하기'}
          </button>
        </div>
        {withdraw.isError && (
          <p role="alert" className="text-sm text-coral-deep">
            계정을 삭제하지 못했어요. 다시 시도해 주세요.
          </p>
        )}
      </Card>
    </InfoPage>
  );
}
