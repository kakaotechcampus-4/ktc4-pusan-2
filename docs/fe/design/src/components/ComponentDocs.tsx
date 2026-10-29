/**
 * 컴포넌트 · 대화 상자 견본. 화면 샘플이 쓰는 src/ui를 그대로 쓴다 — 견본용으로 따로 만들면
 * 고칠 곳이 둘이 된다. 상태 ID가 보기(변형)다. 쓰는 규칙 · 사용 예 코드는 entries.ts에 있다.
 */
import { useState, type ReactNode } from 'react';
import { AppHeader } from '@/ui/AppHeader';
import { Badge, type Tone } from '@/ui/Badge';
import { Button, ButtonLink } from '@/ui/Button';
import { Mascot } from '@/ui/Mascot';
import { Card, Stat } from '@/ui/Panel';
import { PitchCoachWordmark } from '@/ui/PitchCoachWordmark';
import { ScriptModeChoice, type ScriptMode } from '@/ui/ScriptModeChoice';
import { SuggestionCategorySelect, type SuggestionCategory } from '@/ui/SuggestionCategorySelect';
import { TakeStatusBadge } from '@/ui/TakeStatusBadge';
import { TAKE_STATUSES } from '@/lib/takeStatus';
import { HomeHelpDialog } from '@/ui/dialogs/HomeHelpDialog';
import { ReplaceTakeDialog } from '@/ui/dialogs/ReplaceTakeDialog';
import { ServiceReviewDialog } from '@/ui/dialogs/ServiceReviewDialog';
import { QueryNotice, PITCH } from '../screens/shared';

/** 견본 한 장. 밝은 지면이 기본인 조각은 light로 감싼다 */
function Specimen({
  title,
  light = false,
  children,
}: {
  title: string;
  light?: boolean;
  children: ReactNode;
}) {
  return (
    <div className={`${light ? 'pitch-create' : ''} min-h-[40rem] bg-greige px-6 py-6 text-ink`}>
      <div className="mx-auto flex max-w-page flex-col gap-6">
        <h1 className="sr-only">{title}</h1>
        {children}
      </div>
    </div>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid gap-3 border-b border-line pb-5 sm:grid-cols-[10rem_minmax(0,1fr)]">
      <p className="font-mono text-xs font-bold text-stone">{label}</p>
      <div className="flex min-w-0 flex-wrap items-center gap-3">{children}</div>
    </div>
  );
}

function ButtonDoc({ stateId }: { stateId: string }) {
  if (stateId === 'disabled')
    return (
      <Specimen title="버튼 · 막힘">
        <Row label="disabled">
          <Button disabled>Take 3 시작하기</Button>
          <Button variant="secondary" disabled>
            다시 불러오기
          </Button>
        </Row>
        <Row label="진행 중 문구">
          <Button disabled>끝내는 중…</Button>
          <Button variant="secondary" disabled>
            삭제하는 중…
          </Button>
        </Row>
      </Specimen>
    );
  return (
    <Specimen title="버튼 · 종류">
      {(['primary', 'secondary', 'quiet'] as const).map((variant) => (
        <Row key={variant} label={variant}>
          <Button variant={variant}>새 피치 만들기</Button>
          <Button variant={variant} size="sm">
            작은 버튼
          </Button>
        </Row>
      ))}
      <Row label="ButtonLink">
        <ButtonLink to="/takes">Take 기록</ButtonLink>
        <ButtonLink to="/" variant="secondary">
          내 피치로
        </ButtonLink>
      </Row>
    </Specimen>
  );
}

const TONES: [Tone, string][] = [
  ['bad', '늦음'],
  ['good', '좋아요'],
  ['strong', '이번 미션'],
  ['plain', '완료'],
  ['muted', '측정 안 함'],
];

function BadgeDoc({ stateId }: { stateId: string }) {
  if (stateId === 'take-status')
    return (
      <Specimen title="배지 · Take 상태">
        {TAKE_STATUSES.map((status) => (
          <Row key={status.status} label={status.status}>
            <TakeStatusBadge status={status} />
          </Row>
        ))}
      </Specimen>
    );
  return (
    <Specimen title="배지 · tone">
      {TONES.map(([tone, label]) => (
        <Row key={tone} label={tone}>
          <Badge tone={tone}>{label}</Badge>
        </Row>
      ))}
    </Specimen>
  );
}

function CardDoc({ stateId }: { stateId: string }) {
  if (stateId === 'stat')
    return (
      <Specimen title="지표 칸">
        <div className="grid gap-4 sm:grid-cols-3">
          <Stat label="발표 시간" value="05:12">
            목표 05:00
          </Stat>
          <Stat label="청중 응시" value="68%">
            지난 Take보다 +6%p
          </Stat>
          <Stat label="군더더기" value="—">
            측정하지 않았어요
          </Stat>
        </div>
      </Specimen>
    );
  return (
    <Specimen title="카드">
      <Card title="이번 Take 미션" action={<Badge tone="strong">Take 2에서 정함</Badge>}>
        <p className="text-sm">문장 끝에서 청중을 바라보기</p>
      </Card>
      <Card>
        <p className="text-sm">제목 없는 카드. 안내 · 빈 화면 한 덩어리에 쓴다.</p>
      </Card>
    </Specimen>
  );
}

function MascotDoc({ stateId }: { stateId: string }) {
  return (
    <Specimen title="마스코트">
      {stateId === 'pico' ? (
        <Mascot who="pico">
          점수를 매기려는 게 아니에요. 지난번보다 무엇이 나아졌는지 보려는 거예요.
        </Mascot>
      ) : (
        <Mascot who="chichi">어라, 여기는 아무것도 없어. 길을 잃었나 봐!</Mascot>
      )}
    </Specimen>
  );
}

function WordmarkDoc() {
  return (
    <Specimen title="워드마크">
      <Row label="기본">
        <PitchCoachWordmark />
      </Row>
      <Row label="어두운 바탕">
        <span className="bg-stage p-4 text-ink-stage">
          <PitchCoachWordmark />
        </span>
      </Row>
    </Specimen>
  );
}

function HeaderDoc({ stateId }: { stateId: string }) {
  const current = stateId === 'takes' || stateId === 'me' ? stateId : 'home';
  return (
    <div className="min-h-[40rem] bg-greige">
      <h1 className="sr-only">상단바</h1>
      <AppHeader current={current} />
    </div>
  );
}

const field =
  'w-full min-w-0 border-2 border-ink bg-white p-3 text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink';

function InputsDoc({ stateId }: { stateId: string }) {
  const disabled = stateId === 'disabled';
  const [category, setCategory] = useState<SuggestionCategory>('OTHER');
  const [mode, setMode] = useState<ScriptMode>('HIGHLIGHT');
  return (
    <Specimen title="입력" light>
      <div className="grid gap-6 lg:grid-cols-2">
        <div className="flex flex-col gap-3">
          <SuggestionCategorySelect value={category} onChange={setCategory} disabled={disabled} />
          <label htmlFor="sample-opinion" className="text-sm font-bold">
            의견 (필수)
          </label>
          <textarea
            id="sample-opinion"
            rows={4}
            readOnly={disabled}
            defaultValue={disabled ? '보내는 중에는 고칠 수 없어요' : ''}
            className={`resize-y ${field}`}
          />
          <p className="text-sm text-ink/80">0 / 2,000자</p>
          <label className="flex min-h-11 items-center gap-3 text-sm font-bold">
            <input type="checkbox" disabled={disabled} className="size-4 shrink-0 accent-ink" />
            연습 기록을 남겨 다시 분석하는 데 동의해요
          </label>
        </div>
        <ScriptModeChoice value={mode} onChange={setMode} disabled={disabled} />
      </div>
    </Specimen>
  );
}

function NoticesDoc({ stateId }: { stateId: string }) {
  if (stateId === 'messages')
    return (
      <Specimen title="안내 줄" light>
        <Row label='role="status"'>
          <p role="status" className="text-sm">
            의견을 보냈어요. 감사합니다.
          </p>
        </Row>
        <Row label='role="alert"'>
          <p role="alert" className="text-sm text-coral-deep">
            동의 설정을 바꾸지 못했어요. 다시 시도해 주세요.
          </p>
        </Row>
        <Row label="빈 자리">
          <p className="border-2 border-dashed border-line-strong p-4 text-sm text-stone">
            아직 연습한 Take가 없어요
          </p>
        </Row>
      </Specimen>
    );
  return (
    <Specimen title="불러오기 상태" light>
      <QueryNotice stateId={stateId === 'error' ? 'error' : 'loading'} onRetry={() => undefined} />
    </Specimen>
  );
}

/** 대화 상자 견본. 처음에 열려 있고, 닫으면 "다시 열기"로 초점이 돌아온다 */
function DialogDoc({ title, render }: { title: string; render: (close: () => void) => ReactNode }) {
  const [open, setOpen] = useState(true);
  return (
    <Specimen title={title} light>
      <p className="text-sm text-stone">
        이 미리보기 안에서만 열리는 실제 창이다. Esc · 닫기로 닫고, 아래 버튼으로 다시 연다.
      </p>
      <div>
        <Button variant="secondary" onClick={() => setOpen(true)}>
          {title} 다시 열기
        </Button>
      </div>
      {open && render(() => setOpen(false))}
    </Specimen>
  );
}

export function ComponentDoc({ id, stateId }: { id: string; stateId: string }) {
  switch (id) {
    case 'button':
      return <ButtonDoc stateId={stateId} />;
    case 'badge':
      return <BadgeDoc stateId={stateId} />;
    case 'card':
      return <CardDoc stateId={stateId} />;
    case 'mascot':
      return <MascotDoc stateId={stateId} />;
    case 'wordmark':
      return <WordmarkDoc />;
    case 'app-header':
      return <HeaderDoc stateId={stateId} />;
    case 'inputs':
      return <InputsDoc stateId={stateId} />;
    case 'notices':
      return <NoticesDoc stateId={stateId} />;
    case 'confirm-dialog':
      return (
        <DialogDoc
          title="확인 창"
          render={(close) => (
            <ReplaceTakeDialog
              pitchTitle={PITCH}
              takeNumber={2}
              busy={stateId === 'busy'}
              onConfirm={close}
              onCancel={close}
            />
          )}
        />
      );
    case 'help-dialog':
      return (
        <DialogDoc title="도움말 창" render={(close) => <HomeHelpDialog onDismiss={close} />} />
      );
    case 'review-dialog':
      return (
        <DialogDoc
          title="리뷰 창"
          render={(close) => (
            <ServiceReviewDialog onContinue={close} initialRating={stateId === 'rated' ? 4 : 0} />
          )}
        />
      );
    default:
      return null;
  }
}
