import { Navigate, useLocation, useParams } from 'react-router';

/** 기존 공유 링크를 같은 피치의 통합 비교 탭으로 보냅니다. (#795) */
export function TakeComparisonRedirect() {
  const { id = '' } = useParams();
  const { search, hash } = useLocation();
  const params = new URLSearchParams(search);
  params.set('pitch', id);
  params.set('view', 'compare');
  return <Navigate to={{ pathname: '/takes', search: '?' + params.toString(), hash }} replace />;
}
