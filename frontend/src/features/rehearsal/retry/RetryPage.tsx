import { useEffect, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router';
import { useEndTake } from '@/shared/api/take';
import { toMessage } from '@/shared/api/errorMessage';
import { ApiFailure } from '@/shared/api/tokenStore';
import { formatDuration } from '@/shared/lib/clock';
import { Button, ButtonLink } from '@/shared/ui/Button';
import { InfoPage } from '@/shared/ui/InfoPage';
import { Mascot } from '@/shared/ui/Mascot';
import { Card, Stat } from '@/shared/ui/Panel';
import { countAll, findSessionByTakeId, getSession, readSlideChanges } from '../lib/db';
import type { SessionRow } from '../lib/db';
import { sendEnding, type Ending } from '../stage/sendEnding';
import type { RehearsalTicket } from '@/types/api';

/** 다시 보낼 세션. 행이 없거나 무대를 열 값(`ticket`)이 없으면 다시 보낼 수 없습니다 */
interface Found {
  row: SessionRow;
  ticket: RehearsalTicket;
  ending: Ending;
  /** 남아 있는 기록. 못 읽으면 null — 보내는 데는 필요 없습니다 */
  kept: { gazeSec: number; slideMoves: number; lastSlide: number | null } | null;
}

/**
 * 업로드 실패 · 재시도 (P15, Track A) — 발표를 끝냈는데 종료를 서버에 못 보냈을 때 리허설이 여기로 보냅니다.
 *
 * 브라우저가 원본입니다 (CLAUDE.md 4번). 기록은 IndexedDB 에 남아 있으니 **잃은 게 없다**는 걸 먼저 말합니다.
 * 다시 보내기는 리허설 끝내기와 같은 `sendEnding` 으로 같은 세션의 같은 시각을 다시 적습니다 —
 * 여러 번 눌러도 결과가 같습니다.
 *
 * 라우터 state(`clientSessionId`)는 새로고침해도 남습니다. 주소로 바로 들어오면 없어서 takeId 로 행을 찾습니다.
 */
export function RetryPage() {
  const { takeId = '' } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const passed = (location.state as { clientSessionId?: string } | null)?.clientSessionId;

  /** `undefined` 는 아직 찾는 중, `null` 은 다시 보낼 세션이 없음입니다 */
  const [found, setFound] = useState<Found | null | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  /** 서버가 이미 끝낸 Take 입니다. 다시 보내도 같아서 버튼을 내립니다 */
  const [gone, setGone] = useState(false);
  const endTake = useEndTake(found?.ticket.pitchId ?? '', takeId);

  useEffect(() => {
    (async () => {
      const row = passed ? await getSession(passed) : await findSessionByTakeId(takeId);
      if (!row?.ticket) {
        setFound(null);
        return;
      }
      // 이미 보낸 Take 입니다 (보낸 뒤 뒤로 가기 · 다른 탭에서 보냄)
      if (row.submittedAt !== null) {
        navigate(`/takes/${takeId}/processing`, { replace: true });
        return;
      }
      const ending = row.ending ?? {
        // ending 을 남기기 전에 끝낸 행입니다. 마지막 하트비트가 가장 가까운 값입니다
        durationMs: row.elapsedMs,
        endedAtIso: new Date(row.lastBeatAt).toISOString(),
      };
      const kept = await Promise.all([
        countAll(row.clientSessionId),
        readSlideChanges(row.clientSessionId),
      ])
        .then(([counts, slides]) => ({
          gazeSec: counts.gazeSamples ?? 0,
          // 첫 장은 0ms 에 적힙니다. 넘긴 것만 셉니다
          slideMoves: slides.filter((s) => s.atMs > 0).length,
          lastSlide: slides.at(-1)?.slideNumber ?? null,
        }))
        .catch(() => null);
      setFound({ row, ticket: row.ticket, ending, kept });
    })().catch((err: unknown) => {
      // IndexedDB 를 못 열면(사생활 보호 모드 · 사이트 데이터 삭제) 다시 보낼 기록도 없습니다
      console.error('[retry] 세션을 읽지 못했습니다', err);
      setFound(null);
    });
  }, [takeId, passed, navigate]);

  // IndexedDB 를 읽는 동안(몇 ms)은 아무것도 그리지 않습니다
  if (found === undefined) return null;

  if (found === null) {
    return (
      <InfoPage title="업로드 실패 · 재시도">
        <Card>
          <h2 className="text-2xl font-bold">다시 보낼 발표 기록이 없어요</h2>
          <p className="text-stone">
            이 브라우저에 이 Take의 기록이 남아 있지 않아요. 발표한 브라우저에서 다시 열거나, 새
            Take로 연습해 주세요.
          </p>
          <div className="flex flex-wrap gap-3">
            <ButtonLink to="/">내 피치로</ButtonLink>
          </div>
        </Card>
      </InfoPage>
    );
  }

  const { row, ticket, ending, kept } = found;
  const sending = endTake.isPending;

  const resend = async () => {
    setError(null);
    try {
      await sendEnding(takeId, row.clientSessionId, ending, endTake.mutateAsync);
      navigate(`/takes/${takeId}/processing`, { replace: true });
    } catch (e) {
      console.error('[retry] 종료 처리 실패', e);
      if (e instanceof ApiFailure && (e.code === 'TAKE_ENDED' || e.code === 'TAKE_NOT_STARTED')) {
        setGone(true);
        return;
      }
      setError(toMessage(e));
    }
  };

  return (
    <InfoPage title="업로드 실패 · 재시도">
      <p className="border-b border-line pb-4 font-bold">
        {ticket.title}
        {ticket.takeNumber !== null && (
          <>
            {' '}
            <span className="mx-2 text-line-strong">/</span> TAKE {ticket.takeNumber}
          </>
        )}
      </p>

      {gone ? (
        <Card>
          <h2 className="text-2xl font-bold">이 Take는 서버에서 이미 끝났어요</h2>
          <p className="text-stone">다시 보내도 받지 않아요. 새 Take로 다시 연습해 주세요.</p>
          <div className="flex flex-wrap gap-3">
            <ButtonLink to={`/pitch/${ticket.pitchId}/device-check`}>새 Take 준비</ButtonLink>
            <ButtonLink to="/" variant="secondary">
              내 피치로
            </ButtonLink>
          </div>
        </Card>
      ) : (
        <Card>
          <h2 className="text-2xl font-bold">
            {sending ? '발표 기록을 다시 보내고 있어요' : '발표 기록을 서버로 보내지 못했어요'}
          </h2>
          <p className="text-stone">
            {sending
              ? '잠시만 기다려 주세요. 창을 닫아도 기록은 남아 있어요.'
              : '기록은 이 브라우저에 그대로 남아 있어요. 연결이 돌아오면 다시 보내 주세요.'}
          </p>
          {error && (
            <p role="alert" className="font-bold text-coral-deep">
              {error}
            </p>
          )}
          <div className="flex flex-wrap gap-3">
            <Button onClick={() => resend()} disabled={sending}>
              {sending ? '보내는 중…' : '다시 보내기'}
            </Button>
            <ButtonLink to="/" variant="secondary">
              나중에 할게요
            </ButtonLink>
          </div>
        </Card>
      )}

      {kept && (
        <section aria-label="남아 있는 기록" className="grid gap-4 sm:grid-cols-3">
          <Stat label="발표 길이" value={formatDuration(ending.durationMs)}>
            끝내기를 누른 시각까지
          </Stat>
          <Stat label="시선 기록" value={row.gazeExcluded ? '제외' : `${kept.gazeSec}초`}>
            {row.gazeExcluded ? '이 Take는 시선 측정을 뺐어요' : `1초 기록 ${kept.gazeSec}개`}
          </Stat>
          <Stat label="슬라이드 이동" value={`${kept.slideMoves}회`}>
            {kept.lastSlide !== null && `마지막 장 ${kept.lastSlide}번`}
          </Stat>
        </section>
      )}

      <Card>
        <Mascot who="chichi">
          브라우저 기록(쿠키 · 사이트 데이터)을 지우면 이 기록도 사라져요. 보내기 전에는 지우지 말아
          줘!
        </Mascot>
      </Card>
    </InfoPage>
  );
}
