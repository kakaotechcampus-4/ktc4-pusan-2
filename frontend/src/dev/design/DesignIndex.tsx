import { ComponentsDraft } from './ComponentsDraft';
import type { ReactElement } from 'react';
import { Link, useParams } from 'react-router';
import { Card } from '@/shared/ui/Panel';
import { DraftPage } from './ui';
import { ReportDraft } from './report/ReportDraft';
import { ProcessingDraft } from './processing/ProcessingDraft';
import { PrepareMissionDraft } from './prepare/PrepareMissionDraft';
import { RetryDraft } from './retry/RetryDraft';
import { ComparisonDraft } from './comparison/ComparisonDraft';
import { BestTakeDraft } from './best/BestTakeDraft';
import { TakesDraft } from './takes/TakesDraft';
import { MyPageDraft } from './me/MyPageDraft';
import { AboutDraft, NotFoundDraft, UnsupportedDraft } from './static/StaticDrafts';

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
  { name: 'processing', id: 'P6-0', title: '분석 대기', element: <ProcessingDraft /> },
  { name: 'report', id: 'P6', title: 'Take 리포트', element: <ReportDraft /> },
  { name: 'comparison', id: 'P7', title: 'Take 비교', element: <ComparisonDraft /> },
  { name: 'best', id: 'P8', title: 'Best Take 선택', element: <BestTakeDraft /> },
  { name: 'me', id: 'P9', title: '마이페이지', element: <MyPageDraft /> },
  { name: 'about', id: 'P14', title: '분석 방식 설명', element: <AboutDraft /> },
  { name: 'retry', id: 'P15', title: '업로드 실패 · 재시도', element: <RetryDraft /> },
  { name: 'takes', id: 'P16', title: 'Take 기록', element: <TakesDraft /> },
  { name: 'unsupported', id: 'P18', title: '미지원 브라우저', element: <UnsupportedDraft /> },
  { name: 'not-found', id: '404', title: '찾을 수 없음', element: <NotFoundDraft /> },
];

export function DesignIndex() {
  return (
    <DraftPage
      title="디자인 시안"
      eyebrow="아직 구현하지 않은 화면"
      questions={['피치 수정(P13)은 Kjun03이 feat/fe-pitch-edit 에서 작업 중이라 시안에서 뺐어요.']}
    >
      <Card>
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
