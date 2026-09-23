/**
 * 이번 Take 의 과제 한 장.
 *
 * 지난 Take 가 남긴 것이고, **한 번에 하나만** 줍니다 — 두 개를 같이 주면
 * 발표자가 무엇을 고쳤는지 다음 리포트에서 가릴 수 없습니다.
 *
 * 없으면 아무것도 그리지 않습니다 (첫 Take). 빈 상자를 두면 "받아야 할 것을
 * 못 받았나" 로 읽힙니다.
 */
export function MissionCard({ description }: { description: string | null }) {
  if (description === null) return null;

  return (
    <section className="rounded-xl bg-coral p-4 text-panel">
      <p className="text-xs font-bold opacity-80">이번 TAKE 미션</p>
      <p className="mt-2 text-lg font-bold leading-snug">{description}</p>
      <p className="mt-2 text-xs leading-relaxed opacity-80">
        지난 Take 에서 걸린 것 하나입니다. 한 번에 하나만 바꿔야 다음 리포트에서 무엇이 나아졌는지
        가려집니다.
      </p>
    </section>
  );
}
