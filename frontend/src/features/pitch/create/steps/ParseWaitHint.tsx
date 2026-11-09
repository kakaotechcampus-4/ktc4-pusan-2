import { useEffect, useState } from 'react';
import { PARSE_WAIT_SLOW_MS, parseWaitText } from '../lib/parseWait';

/**
 * 나누는 동안 기다린 시간에 따른 안내 (`lib/parseWait.ts`). 값은 매번 `performance.now()` 에서 다시 계산하고,
 * 1초 타이머는 다시 재기만 합니다 — 가려진 탭에서 타이머가 늦어져도 단계가 틀리지 않습니다 (`shared/lib/clock.ts`).
 * 다른 대본으로 바뀌면 쓰는 쪽이 `key={startedAt}` 으로 다시 마운트합니다 — 앞 대본의 기다린 시간이 남지 않습니다
 */
export function ParseWaitHint({ startedAt }: { startedAt: number }) {
  // 처음 그릴 때 한 번 재고(다른 화면에 다녀왔으면 이미 지난 시간부터), 그 뒤로는 1초마다 다시 잽니다
  const [elapsedMs, setElapsedMs] = useState(() => performance.now() - startedAt);
  // 마지막 단계에 닿으면 문구가 더 바뀌지 않으니 타이머를 멈춥니다
  const settled = elapsedMs >= PARSE_WAIT_SLOW_MS;
  useEffect(() => {
    if (settled) return;
    const id = window.setInterval(() => setElapsedMs(performance.now() - startedAt), 1_000);
    return () => window.clearInterval(id);
  }, [startedAt, settled]);

  const text = parseWaitText(elapsedMs);
  // 자리를 미리 두어 안내가 바뀔 때 화면 낭독기가 읽습니다 — 빈 동안에는 아무것도 보이지 않습니다
  return (
    <p role="status" className="mb-3 text-sm text-ink empty:mb-0">
      {text}
    </p>
  );
}
