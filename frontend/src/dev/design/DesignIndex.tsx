import { ComponentsDraft } from './ComponentsDraft';
import type { ReactElement } from 'react';
import { Link, useParams } from 'react-router';
import { Card } from '@/shared/ui/Panel';
import { DraftPage } from './ui';
import { StageDraft } from './stage/StageDraft';
import { LoadingDraft } from './loading/LoadingDraft';
import { PrepareMissionDraft } from './prepare/PrepareMissionDraft';
import { RetryDraft } from './retry/RetryDraft';
import { ComparisonDraft } from './comparison/ComparisonDraft';
import { BestTakeDraft } from './best/BestTakeDraft';
import { TakesDraft } from './takes/TakesDraft';

/**
 * 디자인 시안 목록 — `/dev/design`, 각 시안은 `/dev/design/:name`.
 * 시안을 추가하면 여기 한 줄만 넣습니다. P 번호는 `app/routes.tsx` 와 같습니다.
 */
const DRAFTS: { name: string; id: string; title: string; element: ReactElement }[] = [
  { name: 'components', id: 'UI', title: '공용 컴포넌트', element: <ComponentsDraft /> },
  {
    name: 'prepare-mission',
    id: '09',
    title: '시작 전 세팅 · 이번 Take 미션',
    element: <PrepareMissionDraft />,
  },
  { name: 'stage', id: '07', title: '리허설 무대 · 스타일', element: <StageDraft /> },
  {
    name: 'processing-loading',
    id: 'P6-0',
    title: '분석 대기 · 8비트 로딩',
    element: <LoadingDraft />,
  },
  { name: 'comparison', id: 'P7', title: 'Take 비교', element: <ComparisonDraft /> },
  { name: 'best', id: 'P8', title: 'Best Take 선택', element: <BestTakeDraft /> },
  { name: 'retry', id: 'P15', title: '업로드 실패 · 재시도', element: <RetryDraft /> },
  { name: 'takes', id: 'P16', title: 'Take 기록', element: <TakesDraft /> },
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
  { path: '/pitch/p1/device-check', id: '09', title: '시작 전 세팅 (장치 점검)' },
  { path: '/takes/t3/rehearsal', id: '07', title: '리허설 · 실시간 코칭' },
  { path: '/takes/t3/exam', id: '07', title: '리허설 · 실전 모드' },
  {
    path: '/takes/t4/processing',
    id: 'P6-0',
    title: '분석 대기',
    note: '몇 초마다 단계가 넘어가고 리포트로 가요',
  },
  { path: '/takes/t-failed/processing', id: 'P6-0', title: '분석 대기 · 분석 실패' },
  { path: '/takes/t-held/processing', id: 'P6-0', title: '분석 대기 · 리뷰 보류' },
  { path: '/takes/t3', id: 'P6', title: 'Take 리포트' },
  { path: '/takes/t-fallback', id: 'P6', title: 'Take 리포트 · AI 문장 실패' },
  { path: '/takes/t-review-failed', id: 'P6', title: 'Take 리포트 · 리뷰 실패' },
  { path: '/takes/t2', id: 'P6', title: 'Take 리포트 · 시선 측정 제외' },
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
        '피치 수정(P13)은 Kjun03이 feat/fe-pitch-edit 에서 작업 중이라 시안에서 뺐어요.',
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

      <Card title="아직 구현하지 않은 화면 (시안)">
        <ul className="grid gap-x-8 sm:grid-cols-2">
          {DRAFTS.map((d) => (
            <li key={d.name} className="border-b border-line">
              <Link
                to={`/dev/design/${d.name}`}
                className="flex items-center gap-4 py-3 hover:text-coral-deep"
              >
                <span className="tabular w-12 font-mono text-sm text-stone">{d.id}</span>
                <span className="font-bold">{d.title}</span>
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
