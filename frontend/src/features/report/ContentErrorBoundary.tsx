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

interface BoundaryProps {
  /** 결과가 바뀐 표시(리포트는 `content_status` 의 상태 · 판). 던진 뒤 이 값이 바뀌면 새 결과로 다시 그려 봅니다 */
  resetKey: string;
  children: ReactNode;
}

interface BoundaryState {
  failed: boolean;
  /** 마지막으로 본 `resetKey` */
  resetKey: string;
}

/**
 * 내용 전달 카드만 감싸는 오류 경계 (#1157 리뷰 P1). 모양 검사(`readCoverageSummary`)를 지나고도 카드가 렌더 중에
 * 던지면 이 카드만 안내로 바꾸고, 측정값 · 리뷰 같은 리포트의 다른 칸은 그대로 둡니다.
 * 같은 응답이면 다시 그려도 또 던지므로 다시 시도 버튼은 두지 않고, `resetKey` 가 바뀔 때만 다시 그려 봅니다.
 *
 * `key` 로 경계를 통째로 새로 만들지 않습니다 — 그러면 정상일 때도 판이 바뀔 때마다(확인 답 반영 → 409) 카드가
 * 새로 만들어져, 열어 둔 장별 목록과 펼친 장이 닫힙니다(E2E 에서 발견)
 */
export class ContentErrorBoundary extends Component<BoundaryProps, BoundaryState> {
  constructor(props: BoundaryProps) {
    super(props);
    this.state = { failed: false, resetKey: props.resetKey };
  }

  static getDerivedStateFromError(): Partial<BoundaryState> {
    return { failed: true };
  }

  static getDerivedStateFromProps(
    props: BoundaryProps,
    state: BoundaryState,
  ): Partial<BoundaryState> | null {
    // 결과가 바뀌면 실패 표시를 지워 새 결과로 다시 그려 봅니다. 아이들은 그대로 두어 상태를 잃지 않습니다
    if (props.resetKey === state.resetKey) return null;
    return { failed: false, resetKey: props.resetKey };
  }

  render() {
    return this.state.failed ? <ContentBroken /> : this.props.children;
  }
}
