import { autoEndText, type AutoEndNotice } from '@/shared/lib/autoEnd';

/**
 * 서버가 Take 를 자동으로 끝냈다는 안내 (#925) — 분석 대기 · 리포트가 같이 씁니다.
 *
 * 무대가 라우터 state 로 넘긴 이유를 띄웁니다 (`readAutoEnd`). 사용자는 발표 목표 시간보다 일찍
 * 끝난 것만 보게 되므로, 화면이 열리자마자 화면 읽기 프로그램이 읽도록 `role="alert"` 를 겁니다.
 * 색이 아니라 "자동 종료" 이름표와 문구로 구분합니다.
 */
export function AutoEndAlert({
  notice,
  tone,
}: {
  notice: AutoEndNotice;
  /** `dusk` 노을 대화 상자 안(분석 대기) · `page` 밝은 지면(리포트) */
  tone: 'dusk' | 'page';
}) {
  const { message, after } = autoEndText(notice);
  return (
    <div
      role="alert"
      className={[
        'w-full border-2 px-4 py-3 text-left text-sm leading-relaxed',
        tone === 'dusk' ? 'border-dusk-accent' : 'border-ink bg-coral-wash text-ink',
      ].join(' ')}
    >
      {/* 밝은 지면에서는 coral 글자가 coral-wash 위에서 4.5:1 이 안 나와 먹색으로 둡니다 */}
      <p className={`text-xs font-bold ${tone === 'dusk' ? 'text-dusk-accent' : ''}`}>자동 종료</p>
      <p className="mt-1 font-bold">{message}</p>
      <p className="mt-0.5">{after}</p>
    </div>
  );
}
