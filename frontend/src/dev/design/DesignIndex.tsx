import { ComponentsDraft } from './ComponentsDraft';
import type { ReactElement } from 'react';
import { Link, useParams } from 'react-router';
import { Card } from '@/shared/ui/Panel';
import { DraftPage } from './ui';
import { StageDraft } from './stage/StageDraft';
import { LoadingDraft } from './loading/LoadingDraft';
import { PrepareMissionDraft } from './prepare/PrepareMissionDraft';
import { RetryDraft } from './retry/RetryDraft';
import { PitchCreateDraft } from './create/PitchCreateDraft';

/**
 * 디자인 시안 목록 — `/dev/design`, 각 시안은 `/dev/design/:name`.
 * 시안을 추가하면 여기 한 줄만 넣습니다. P 번호는 `app/routes.tsx` 와 같습니다.
 * `note` 에는 실제 화면이 어디까지 왔는지 적습니다 — 구현이 머지되면 고칩니다.
 */
const DRAFTS: { name: string; id: string; title: string; note?: string; element: ReactElement }[] =
  [
    { name: 'components', id: 'UI', title: '공용 컴포넌트', element: <ComponentsDraft /> },
    {
      name: 'pitch-create',
      id: 'P3',
      title: 'Pitch 생성 · 시작 플로우 개편',
      note: '시안만 — 실제 화면은 아직 발표정보부터 시작해요',
      element: <PitchCreateDraft />,
    },
    {
      name: 'prepare-mission',
      id: '09',
      title: '시작 전 세팅 · 이번 Take 미션',
      note: '구현됨 — 위 "시작 전 세팅"이 실제 화면이에요',
      element: <PrepareMissionDraft />,
    },
    {
      name: 'stage',
      id: '07',
      title: '리허설 무대 · 스타일',
      note: '어두운 무대로 정함 · 리뷰 중',
      element: <StageDraft />,
    },
    {
      name: 'processing-loading',
      id: 'P6-0',
      title: '분석 대기 · 8비트 로딩',
      note: '구현됨 — 위 "분석 대기"가 실제 화면이에요',
      element: <LoadingDraft />,
    },
    {
      name: 'retry',
      id: 'P15',
      title: '업로드 실패 · 재시도',
      note: '구현됨 — 위 "업로드 실패 · 재시도"가 실제 화면이에요',
      element: <RetryDraft />,
    },
  ];

/**
 * 이미 구현된 화면 — 시안으로 다시 그리지 않고 **실제 화면으로 바로 갑니다** (복사본은 금방 어긋납니다).
 * 접근성 점검도 실제 화면을 봅니다. 목 데이터(`mocks/handlers.ts`)의 피치 `p1` · Take `t3` 으로 열립니다.
 * 리포트의 상태별 Take id 는 `mocks/report.ts` 에 있습니다.
 */
const LIVE: { path: string; id: string; title: string; note?: string }[] = [
  { path: '/welcome', id: '—', title: '웰컴 · 서비스 소개' },
  { path: '/login', id: 'P11', title: '로그인', note: '목 로그인이 켜져 있으면 홈으로 넘어가요' },
  { path: '/', id: '—', title: '홈 · 내 피치' },
  { path: '/pitch/new', id: '—', title: 'Pitch 생성' },
  { path: '/pitch/10000000-0000-4000-8000-000000000001/edit', id: 'P13', title: 'Pitch 수정' },
  { path: '/pitch/p1/device-check', id: '09', title: '시작 전 세팅 (장치 점검)' },
  { path: '/takes/t3/rehearsal', id: '07', title: '리허설 · 실시간 코칭' },
  { path: '/takes/t3/exam', id: '07', title: '리허설 · 실전 모드' },
  {
    path: '/takes/t4/processing',
    id: 'P6-0',
    title: '분석 대기',
    note: '몇 초마다 단계가 넘어가고 리포트로 가요',
  },
  {
    path: '/takes/t3/retry',
    id: 'P15',
    title: '업로드 실패 · 재시도',
    note: '이 브라우저에 기록이 없으면 안내만 떠요. 콘솔 mockTakeEnd.fail() 뒤 발표를 끝내면 열려요',
  },
  { path: '/takes/t-failed/processing', id: 'P6-0', title: '분석 대기 · 분석 실패' },
  { path: '/takes/t-held/processing', id: 'P6-0', title: '분석 대기 · 리뷰 보류' },
  {
    path: '/takes/t-confirm/processing',
    id: 'P6-0',
    title: '분석 대기 · 대본 확인 대기',
    note: '확인 화면으로 가는 버튼이 떠요',
  },
  {
    path: '/takes/t-confirm/confirm',
    id: 'P6-0',
    title: '대본 확인',
    note: 'API 는 합의 전 FE 제안이에요. 답하거나 건너뛰면 분석 대기를 거쳐 리포트로 가요',
  },
  {
    path: '/takes/t-confirm-timeout/confirm',
    id: 'P6-0',
    title: '대본 확인 · 시간 초과',
    note: '8초 뒤 시간이 끝나요. 그 전에 보내면 409 로 막혀요',
  },
  { path: '/takes/t-confirm-done/confirm', id: 'P6-0', title: '대본 확인 · 이미 넘어감' },
  { path: '/takes/t3', id: 'P6', title: 'Take 리포트' },
  { path: '/takes/t-fallback', id: 'P6', title: 'Take 리포트 · AI 문장 실패' },
  { path: '/takes/t-review-failed', id: 'P6', title: 'Take 리포트 · 리뷰 실패' },
  { path: '/takes/t2', id: 'P6', title: 'Take 리포트 · 시선 측정 제외' },
  { path: '/takes', id: 'P16', title: 'Take 기록' },
  {
    path: '/pitch/10000000-0000-4000-8000-000000000001/comparison',
    id: 'P7',
    title: 'Take 비교',
  },
  {
    path: '/pitch/p2/comparison',
    id: 'P7',
    title: 'Take 비교 · 완료 Take 없음',
  },
  { path: '/about', id: 'P14', title: '분석 방식 설명' },
  {
    path: '/unsupported',
    id: 'P18',
    title: '미지원 브라우저',
    note: '이 브라우저에 맞는 사유 하나만 보여요',
  },
  { path: '/없는-주소', id: '404', title: '찾을 수 없음' },
  { path: '/me', id: 'P9', title: '마이페이지' },
  { path: '/privacy', id: '—', title: '개인정보 처리방침' },
  { path: '/terms', id: '—', title: '이용약관' },
];

export function DesignIndex() {
  return (
    <DraftPage
      title="디자인 시안"
      eyebrow="화면 모아 보기"
      questions={[
        'Best Take 선택(P8)은 팀에서 빼기로 해 화면과 시안을 지웠어요.',
        '구현된 화면은 로그인이 필요해요. 목 모드에서는 브라우저 콘솔에 mockSession.on() 을 입력하면 로그인한 상태가 돼요.',
      ]}
    >
      <Card title="구현된 화면">
        <ul className="grid gap-x-8 sm:grid-cols-2">
          {LIVE.map((d) => (
            <li key={d.path} className="border-b border-line">
              <Link to={d.path} className="flex items-center gap-4 py-3 hover:text-coral-deep">
                <span className="tabular w-12 font-mono text-sm text-stone">{d.id}</span>
                <span>
                  <span className="font-bold">{d.title}</span>
                  {d.note && <span className="block text-xs text-stone">{d.note}</span>}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </Card>

      <Card title="시안">
        <ul className="grid gap-x-8 sm:grid-cols-2">
          {DRAFTS.map((d) => (
            <li key={d.name} className="border-b border-line">
              <Link
                to={`/dev/design/${d.name}`}
                className="flex items-center gap-4 py-3 hover:text-coral-deep"
              >
                <span className="tabular w-12 font-mono text-sm text-stone">{d.id}</span>
                <span>
                  <span className="font-bold">{d.title}</span>
                  {d.note && <span className="block text-xs text-stone">{d.note}</span>}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </Card>
    </DraftPage>
  );
}

export function DesignDraftRoute() {
  const { name } = useParams();
  const draft = DRAFTS.find((d) => d.name === name);
  return draft ? draft.element : <DesignIndex />;
}
