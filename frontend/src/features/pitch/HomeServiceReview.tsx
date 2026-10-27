import { useState } from 'react';
import { useLocation } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import { useSession } from '@/shared/api/useSession';
import { ServiceReviewDialog } from './ServiceReviewDialog';
import { requestServiceReview } from '@/shared/api/serviceReview';
import { ApiFailure } from '@/shared/api/tokenStore';

export function HomeServiceReview({ ready, blocked }: { ready: boolean; blocked: boolean }) {
  const { data: user } = useSession();
  const location = useLocation();
  if (!user || location.pathname !== '/') return null;
  return (
    <VisitReview
      key={`${user.id}:${location.key}`}
      userId={user.id}
      ready={ready}
      blocked={blocked}
    />
  );
}

function VisitReview({
  userId,
  ready,
  blocked,
}: {
  userId: string;
  ready: boolean;
  blocked: boolean;
}) {
  const [requestId] = useState(() => crypto.randomUUID());
  const [closed, setClosed] = useState(false);
  const approval = useQuery({
    queryKey: ['service-feedback-impression', userId, requestId],
    queryFn: ({ signal }) => requestServiceReview(requestId, signal),
    enabled: ready && !blocked,
    // 승인 POST는 방문당 한 ID로만 재시도하며 포커스·재연결로 다시 열지 않습니다.
    staleTime: Infinity,
    gcTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: (count, error) => count < 1 && !(error instanceof ApiFailure && error.status < 500),
  });
  if (closed || blocked || !approval.data?.should_open) return null;
  return <ServiceReviewDialog onContinue={() => setClosed(true)} />;
}
