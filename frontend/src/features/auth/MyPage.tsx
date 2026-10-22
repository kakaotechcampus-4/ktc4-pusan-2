import { useState } from 'react';
import { Link } from 'react-router';
import { Button } from '@/shared/ui/Button';
import { Card } from '@/shared/ui/Panel';
import { InfoPage } from '@/shared/ui/InfoPage';
import { deleteLocalRecords } from '@/shared/lib/localData';
import { useWithdraw } from '@/shared/api/useWithdraw';
import { useSession } from './useSession';

/**
 * P9 마이페이지.
 *
 * 로그아웃은 헤더에, 연습 요약은 홈에 있어서 여기 두지 않습니다.
 * 탈퇴 확인은 브라우저 confirm 입니다. 확인 방식(공용 모달 등)이 정해지면 바꿉니다.
 */
export function MyPage() {
  const user = useSession().data;
  const withdraw = useWithdraw();
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
    <InfoPage title="마이페이지">
      <Card title="계정">
        <dl className="grid gap-3 text-sm sm:grid-cols-[8rem_1fr]">
          <dt className="text-stone">이름</dt>
          <dd className="font-bold">{user?.name}</dd>
          <dt className="text-stone">이메일</dt>
          <dd className="font-bold">{user?.email}</dd>
          <dt className="text-stone">로그인</dt>
          <dd className="font-bold">Google 계정</dd>
        </dl>
      </Card>

      <Card title="이 브라우저의 기록">
        <p className="text-sm leading-relaxed text-stone">
          리허설 중 시선 · 슬라이드 · 코치 기록은 이 브라우저에 저장돼요. 지금은 서버로 보내지
          않아서 이 브라우저에만 있어요. 카메라 영상과 마이크 소리는 저장하지 않아요.
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
            <Link to="/terms?from=home" className="underline underline-offset-4">
              이용약관
            </Link>
          </li>
          <li>
            <Link to="/privacy?from=home" className="underline underline-offset-4">
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
            className="text-sm font-bold text-coral-deep underline underline-offset-4 disabled:opacity-50"
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
