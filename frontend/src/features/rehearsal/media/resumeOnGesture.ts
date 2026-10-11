/**
 * 멈춘(`suspended`) AudioContext 를 사용자의 첫 클릭·키 입력에서 다시 켭니다.
 *
 * 제스처 밖에서 만든 AudioContext 는 브라우저가 멈춘 채로 시작합니다. 준비 화면의 CTA 를
 * 누르고 들어오면 그 클릭이 남아 있어 만들 때 resume() 이 통하지만, **리허설 화면을
 * 새로고침하면 페이지에 상호작용이 하나도 없어서** 음량도 STT 도 소리 없이 멈춰 있습니다.
 * 발표자가 → 키로 슬라이드를 넘기거나 화면을 누르는 순간 풀리게 합니다.
 *
 * 돌려주는 함수로 리스너를 뗍니다. 다시 흐르면 저절로 뗍니다.
 */
export function resumeOnGesture(context: AudioContext): () => void {
  if (context.state !== 'suspended' || typeof window === 'undefined') return () => undefined;

  const events = ['pointerdown', 'keydown'] as const;
  const detach = () => {
    events.forEach((e) => window.removeEventListener(e, onGesture, true));
    context.removeEventListener('statechange', onStateChange);
  };
  // 제스처 없이도 풀릴 수 있습니다 (브라우저가 허용하면 곧 running 이 됩니다). 그러면 바로 뗍니다
  const onStateChange = () => {
    if (context.state !== 'suspended') detach();
  };
  const onGesture = () => {
    if (context.state !== 'suspended') {
      detach();
      return;
    }
    // 제스처 안에서 불러야 통합니다. 실패하면 다음 제스처에서 다시 해 봅니다
    context
      .resume()
      .then(() => {
        if (context.state !== 'suspended') detach();
      })
      .catch(() => undefined);
  };

  // capture 단계에서 받습니다 — 슬라이드 키 처리가 preventDefault 해도 여기는 옵니다
  events.forEach((e) => window.addEventListener(e, onGesture, true));
  context.addEventListener('statechange', onStateChange);
  return detach;
}
