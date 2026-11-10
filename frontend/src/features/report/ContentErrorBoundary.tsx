import { Component, type ReactNode } from 'react';
import { Card } from '@/shared/ui/Panel';

/** 내용 전달 결과를 그리지 못했을 때의 문구. 다른 칸은 그대로라는 것을 함께 알립니다 */
export const CONTENT_BROKEN_TEXT =
  '내용 전달 결과를 보여 드리지 못했어요. 측정값과 다른 리포트 내용은 그대로 볼 수 있어요.';

/** 응답 모양이 어긋나 내용 전달을 그릴 수 없을 때의 카드 */
export function ContentBroken() {
  return (
    <Card title="내용 전달">
      <p className="text-sm text-ink/80">{CONTENT_BROKEN_TEXT}</p>
    </Card>
  );
}

/**
 * 내용 전달 카드만 감싸는 오류 경계 (#1157 리뷰 P1). 모양 검사(`readCoverageSummary`)를 지나고도 카드가 렌더 중에
 * 던지면 이 카드만 안내로 바꾸고, 측정값 · 리뷰 같은 리포트의 다른 칸은 그대로 둡니다.
 * 같은 응답이면 다시 그려도 또 던지므로 다시 시도 버튼은 두지 않습니다. 새 결과로 다시 그리려면 부르는 쪽이
 * `key` 를 바꿉니다(리포트는 `content_status.revision`)
 */
export class ContentErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    return this.state.failed ? <ContentBroken /> : this.props.children;
  }
}
