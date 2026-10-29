// 제품 업로드 실패 · 재시도(`frontend/src/features/rehearsal/retry/RetryPage.tsx`)의 설계서 샘플.
import { Button } from '@/ui/Button';
import { Card, Stat } from '@/ui/Panel';
import { Mascot } from '@/ui/Mascot';
import { Frame, PITCH, type Navigate } from './shared';

export function RetryScreen({ stateId, onNavigate }: { stateId: string; onNavigate: Navigate }) {
  if (stateId === 'checking')
    return (
      <Frame title="업로드 실패 · 재시도">
        <p role="status">이 브라우저의 발표 기록을 확인하고 있어요.</p>
      </Frame>
    );
  if (stateId === 'empty')
    return (
      <Frame title="업로드 실패 · 재시도">
        <Card>
          <h2 className="text-2xl font-bold">다시 보낼 발표 기록이 없어요</h2>
          <p className="text-ink/80">
            이 브라우저에 이 Take의 기록이 남아 있지 않아요. 발표한 브라우저에서 다시 열거나, 새
            Take로 연습해 주세요.
          </p>
          <div>
            <Button onClick={() => onNavigate('home')}>내 피치로</Button>
          </div>
        </Card>
      </Frame>
    );
  const sending = stateId === 'sending';
  return (
    <Frame title="업로드 실패 · 재시도">
      <p className="border-b border-line pb-4 font-bold">
        {PITCH} <span className="mx-2 text-line-strong">/</span> TAKE 3
      </p>
      {stateId === 'gone' ? (
        <Card>
          <h2 className="text-2xl font-bold">이 Take는 서버에서 이미 끝났어요</h2>
          <p className="text-ink/80">다시 보내도 받지 않아요. 새 Take로 다시 연습해 주세요.</p>
          <div className="flex flex-wrap gap-3">
            <Button onClick={() => onNavigate('prepare-mission')}>새 Take 준비</Button>
            <Button variant="secondary" onClick={() => onNavigate('home')}>
              내 피치로
            </Button>
          </div>
        </Card>
      ) : (
        <Card>
          <h2 className="text-2xl font-bold">
            {sending ? '발표 기록을 다시 보내고 있어요' : '발표 기록을 서버로 보내지 못했어요'}
          </h2>
          <p className="text-ink/80">
            {sending
              ? '잠시만 기다려 주세요. 다 보낼 때까지 이 창을 열어 두세요.'
              : '기록은 이 브라우저에 남아 있지만, 서버는 몇 분 뒤 이 Take를 끝내요. 가능하면 지금 다시 보내 주세요.'}
          </p>
          <div className="flex flex-wrap gap-3">
            <Button disabled={sending} onClick={() => onNavigate('processing-loading')}>
              {sending ? '보내는 중…' : '다시 보내기'}
            </Button>
            <Button variant="secondary" onClick={() => onNavigate('home')}>
              나중에 할게요
            </Button>
          </div>
        </Card>
      )}
      <section aria-label="남아 있는 기록" className="grid gap-4 sm:grid-cols-3">
        <Stat label="발표 길이" value="04:38">
          끝내기를 누른 시각까지
        </Stat>
        <Stat label="시선 기록" value="271초">
          1초 기록 271개
        </Stat>
        <Stat label="슬라이드 이동" value="14회">
          마지막 장 12번
        </Stat>
      </section>
      <Card>
        <Mascot who="chichi">
          브라우저 기록(쿠키 · 사이트 데이터)을 지우면 이 기록도 사라져요. 보내기 전에는 지우지 말아
          줘!
        </Mascot>
      </Card>
    </Frame>
  );
}
