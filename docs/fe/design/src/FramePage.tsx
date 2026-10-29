/**
 * 화면 하나만 그리는 주소(`#/frame?screen=&state=`). 뷰어의 미리보기 iframe이 이 주소를 연다.
 * iframe 안이면 화면 안 이동을 뷰어에 메시지로 넘기고, 새 탭에서 바로 열었으면 이 주소에서 이동한다.
 */
import { useEffect } from 'react';
import { useSearchParams } from 'react-router';
import { screens } from './catalog';
import { FRAME_MESSAGE, type FrameMessage } from './frame';
import { ScreenPreview } from './ScreenPreview';

function post(message: FrameMessage) {
  window.parent.postMessage(message, window.location.origin);
}

export function FramePage() {
  const [params, setParams] = useSearchParams();
  const screenId = params.get('screen') ?? '';
  const stateId = params.get('state') ?? '';
  const inline = params.get('inline') === '1';
  const embedded = window.parent !== window;

  // 그린 뒤와 개발 서버가 모듈 · CSS를 바꿔 끼운 뒤에 알린다. 뷰어의 디자인 값이 이때 다시 읽는다
  useEffect(() => {
    if (!embedded) return;
    post({ kind: FRAME_MESSAGE, type: 'updated' });
    const hot = import.meta.hot;
    const updated = () => post({ kind: FRAME_MESSAGE, type: 'updated' });
    hot?.on('vite:afterUpdate', updated);
    // Vitest가 넣는 hot 대역에는 off가 없다
    return () => hot?.off?.('vite:afterUpdate', updated);
  }, [embedded, screenId, stateId]);

  function navigate(id: string, next?: string) {
    if (embedded) {
      post({ kind: FRAME_MESSAGE, type: 'navigate', screenId: id, stateId: next });
      return;
    }
    const target = screens.find((screen) => screen.id === id);
    const state = target?.states.find((item) => item.id === next)?.id ?? target?.states[0]?.id;
    setParams({ screen: id, state: state ?? '' }, { replace: id === screenId });
  }

  return (
    <ScreenPreview
      key={`${screenId}:${stateId}`}
      screenId={screenId}
      stateId={stateId}
      onNavigate={navigate}
      inline={inline}
    />
  );
}
