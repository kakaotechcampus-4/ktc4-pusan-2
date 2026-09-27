import { Navigate, useParams } from 'react-router';

/**
 * 준비 화면(P4)이 **시작 전 세팅(09)에 합쳐지기 전**의 주소.
 *
 * 시안 09 가 장치 점검과 리허설 준비를 한 화면으로 그리면서 두 화면을 합쳤습니다.
 * 이 경로를 북마크했거나 지난 링크를 들고 오는 경우가 있어 404 대신 보냅니다.
 *
 * `replace` 인 이유 — 뒤로 가기를 누르면 여기로 돌아와 다시 튕겨 나가는 고리가 생깁니다.
 */
export function PrepareRedirect() {
  const { pitchId = '' } = useParams();
  return <Navigate to={`/pitch/${pitchId}/device-check`} replace />;
}
