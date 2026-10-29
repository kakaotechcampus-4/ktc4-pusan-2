import { useId, useLayoutEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { apiRequest } from '@/shared/api/client';
import { toMessage } from '@/shared/api/errorMessage';
import { takeSurveyKeys, usePendingSurveys } from '@/shared/api/takeSurvey';
import { useSession, type User } from '@/shared/api/useSession';
import { Button } from '@/shared/ui/Button';

/**
 * 홈 위쪽 한 줄 (#776). 1차 배포의 별점 팝업(#540)을 대신합니다.
 * - 테스트 참여에 아직 동의하지 않았으면 동의 안내를 둡니다. 연습은 막지 않습니다(BE 도 기록만 합니다).
 * - 동의했으면 설문을 남기지 않은 리포트를 보여 주고, 누르면 그 리포트로 갑니다. 설문은 리포트 아래에서 합니다.
 *
 * 안내문을 고치면 `TEST_CONSENT_VERSION` 을 올립니다. BE 가 동의한 안내문 버전을 같이 남깁니다.
 */
export const TEST_CONSENT_VERSION = 1;
const PENDING_SHOWN = 3;

export function HomeSurveyNotice() {
  const { data: user } = useSession();
  if (!user) return null;
  return user.test_consent_at ? <PendingSurveyList /> : <ConsentPrompt />;
}

function PendingSurveyList() {
  const pending = usePendingSurveys(true);
  const items = pending.data?.items ?? [];
  // 목록을 못 받으면 아무것도 두지 않습니다. 설문은 리포트에서도 남길 수 있습니다
  if (!items.length) return null;
  return (
    <section aria-label="남은 설문" className="mx-auto mt-5 w-full max-w-page px-6 lg:px-10">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-2 border-ink bg-coral-wash px-4 py-3 text-sm">
        <p className="font-bold">설문을 기다리는 리포트가 {items.length}개 있어요.</p>
        <ul className="flex flex-wrap gap-x-4 gap-y-1">
          {items.slice(0, PENDING_SHOWN).map((item) => (
            <li key={item.take_id}>
              <Link to={`/takes/${item.take_id}`} className="underline hover:text-coral-deep">
                {item.pitch_title} · Take {item.take_number}
              </Link>
            </li>
          ))}
          {items.length > PENDING_SHOWN && (
            <li className="text-ink/80">외 {items.length - PENDING_SHOWN}개</li>
          )}
        </ul>
      </div>
    </section>
  );
}

function ConsentPrompt() {
  const [open, setOpen] = useState(false);
  return (
    <section
      aria-label="사용자 테스트 참여"
      className="mx-auto mt-5 w-full max-w-page px-6 lg:px-10"
    >
      <div className="flex flex-wrap items-center justify-between gap-3 border border-dashed border-line-strong bg-white px-4 py-3 text-sm">
        <p>
          <span className="font-bold">사용자 테스트에 참여해 주세요.</span> 연습이 끝날 때마다 짧은
          설문으로 분석이 맞았는지 알려 주시면 됩니다.
        </p>
        <Button size="sm" variant="secondary" onClick={() => setOpen(true)}>
          안내 보고 참여하기
        </Button>
      </div>
      {open && <ConsentDialog onClose={() => setOpen(false)} />}
    </section>
  );
}

function ConsentDialog({ onClose }: { onClose: () => void }) {
  const client = useQueryClient();
  const ref = useRef<HTMLDialogElement>(null);
  const title = useId();
  const consent = useMutation({
    mutationFn: async () => {
      const me = await apiRequest<User>('/api/users/me', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ test_consent_version: TEST_CONSENT_VERSION }),
      });
      // 동의를 모르는 BE(#777 전)는 칸을 무시하고 200 으로 옛 사용자를 돌려줍니다. 그대로 두면 창은 닫히는데
      // 카드가 남아 동의된 줄 압니다 — 저장되지 않은 것으로 보고 알립니다(#786 리뷰)
      if (!me.test_consent_at) throw new Error('TEST_CONSENT_NOT_SAVED');
      return me;
    },
    onSuccess: (me) => {
      client.setQueryData(['session'], me);
      client.invalidateQueries({ queryKey: takeSurveyKeys.pending }).catch(() => undefined);
    },
  });

  const alive = useRef(true);

  // 닫기를 DOM 에서 빠지기 전에 해야 브라우저가 초점을 연 버튼으로 돌려줍니다 (#771)
  useLayoutEffect(() => {
    alive.current = true;
    const dialog = ref.current;
    dialog?.showModal();
    return () => {
      alive.current = false;
      dialog?.close();
    };
  }, []);

  return (
    <dialog
      ref={ref}
      aria-labelledby={title}
      onCancel={(event) => {
        event.preventDefault();
        if (!consent.isPending) onClose();
      }}
      onClose={() => {
        // Esc 를 연달아 받은 브라우저가 강제로 닫았으면 화면 상태를 맞춥니다. 저장 중이면 다시 띄웁니다
        if (!alive.current || ref.current?.open) return;
        if (consent.isPending) ref.current?.showModal();
        else onClose();
      }}
      className="pitch-create fixed inset-0 m-auto max-h-[calc(100dvh_-_2rem)] w-[calc(100%_-_2rem)] max-w-lg overflow-hidden border-2 border-ink bg-panel p-0 text-ink backdrop:bg-ink/40"
    >
      <div className="flex max-h-[calc(100dvh_-_2rem_-_4px)] flex-col">
        <div className="min-h-0 overflow-y-auto p-4 text-sm leading-relaxed sm:p-6">
          <h2 id={title} className="text-xl font-bold leading-snug">
            사용자 테스트 참여 안내
          </h2>
          {/*
            문구를 고치면 TEST_CONSENT_VERSION 을 올립니다. 동의로 달라지는 것(설문)과 동의와 상관없이
            저장되는 것을 나눠 적습니다 — 섞으면 연습 기록 저장이 이 동의에 달린 것처럼 읽힙니다(#786 리뷰).
            보관 기한은 BE 삭제 절차가 정해진 뒤 확정합니다(BE take-survey.md 5절)
          */}
          <dl className="mt-4 flex flex-col gap-3">
            <div>
              <dt className="font-bold">동의하면 무엇이 달라지나요</dt>
              <dd className="mt-1">
                리포트마다 짧은 설문(분석 정확도 · 피드백 유용성 · 아쉬운 점)을 부탁드리고, 남긴
                설문이 있으면 홈에서 알려 드려요. 설문 답은 분석이 실제 발표와 맞는지 확인하고
                피드백을 고치는 데 씁니다.
              </dd>
            </div>
            <div>
              <dt className="font-bold">동의와 상관없이 저장되는 것</dt>
              <dd className="mt-1">
                리포트를 만들려고 올린 발표자료 · 대본, 발표 음성을 글로 옮긴 결과, 시선 구간과
                말하기 분석 결과는 동의와 관계없이 저장됩니다. 음성 · 영상 원본은 저장하지 않습니다.
                1초 단위 측정값을 연습이 끝난 뒤에도 남길지는 마이페이지의 &lsquo;연습 기록 사용
                동의&rsquo;로 따로 정해요.
              </dd>
            </div>
            <div>
              <dt className="font-bold">언제까지 두나요</dt>
              <dd className="mt-1">
                설문 답과 참여 기록은 2026년 12월 31일까지 보관한 뒤 지웁니다.
              </dd>
            </div>
          </dl>
          <p className="mt-4 text-ink/80">참여하지 않아도 연습과 리포트는 똑같이 쓸 수 있어요.</p>
          {consent.isError && (
            <p role="alert" className="mt-3">
              참여를 저장하지 못했어요. {toMessage(consent.error)}
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center justify-between gap-3 border-t border-line px-4 py-3 sm:px-6">
          <Button variant="quiet" disabled={consent.isPending} onClick={onClose}>
            나중에
          </Button>
          <Button
            disabled={consent.isPending}
            onClick={() => consent.mutate(undefined, { onSuccess: onClose })}
          >
            {consent.isPending ? '저장하는 중…' : '동의하고 참여하기'}
          </Button>
        </div>
      </div>
    </dialog>
  );
}
