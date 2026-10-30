/**
 * 미리보기 한 칸. 샘플을 iframe에 띄워 **iframe 폭이 곧 화면 폭**이 되게 한다 — 샘플 · src/ui의
 * sm: · lg: 배치와 stage.css의 미디어 쿼리가 제품처럼 그 화면 크기에 반응한다. 뷰어 안에 바로 그리면
 * 브라우저 전체 폭을 보고 반응해서, 넓은 화면일수록 좁은 미리보기 칸에 2단 배치가 켜진다.
 *
 * iframe은 같은 앱의 `#/frame?screen=&state=` 주소(FramePage)를 열고, 화면 안 이동은 메시지로 받는다.
 * 크기를 고르면 그 화면 크기로 그린 뒤 칸 폭에 맞게 줄여 보인다.
 */
import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react';
import { FRAME_MESSAGE, FRAME_UPDATED_EVENT, frameUrl, type FrameMessage } from './frame';
import type { PreviewNavigate } from './ScreenPreview';

export function PreviewFrame({
  screenId,
  stateId,
  onNavigate,
  size,
  zoom = 1,
  fitHeight,
  title,
  inline = false,
  lazy = false,
  frameRef,
}: {
  screenId: string;
  stateId: string;
  onNavigate: PreviewNavigate;
  /** 그릴 화면 크기(frame.ts previewSize). null이면 칸 폭에 맞춘다 */
  size: { width: number; height: number } | null;
  /**
   * 브라우저 확대 배율(1 = 100%). iframe 안 폭 · 높이를 크기 ÷ 배율로 두고 배율만큼 키워 보여,
   * 크롬에서 확대했을 때처럼 미디어 쿼리 · 배치가 바뀐다
   */
  zoom?: number;
  /** "칸에 맞춤"일 때의 높이(px) */
  fitHeight: number;
  title: string;
  /** 전체 보기처럼 여럿 나란히 둘 때. 창(dialog)을 그 자리에 그린다 */
  inline?: boolean;
  lazy?: boolean;
  frameRef?: RefObject<HTMLIFrameElement>;
}) {
  const ownRef = useRef<HTMLIFrameElement>(null);
  const iframe = frameRef ?? ownRef;
  const box = useRef<HTMLDivElement>(null);
  const [boxWidth, setBoxWidth] = useState(0);
  const url = frameUrl(screenId, stateId, inline);
  // 처음 주소만 src로 준다. 그 뒤로는 hash만 바꿔 끼워(location.replace) 다시 불러오지도,
  // 브라우저 기록을 쌓지도 않는다 — iframe의 이동도 뒤로 가기 기록에 들어가기 때문이다
  const [initialUrl] = useState(url);
  const shown = useRef(initialUrl);
  // 메시지 처리기는 한 번만 붙이고, 그때그때의 onNavigate를 부른다
  const navigate = useRef(onNavigate);
  useEffect(() => {
    navigate.current = onNavigate;
  });

  useEffect(() => {
    const view = iframe.current?.contentWindow;
    if (!view || url === shown.current) return;
    shown.current = url;
    view.location.replace(url);
  }, [iframe, url]);

  useEffect(() => {
    function receive(event: MessageEvent<FrameMessage | undefined>) {
      if (event.origin !== window.location.origin) return;
      if (!iframe.current || event.source !== iframe.current.contentWindow) return;
      const data = event.data;
      if (data?.kind !== FRAME_MESSAGE) return;
      if (data.type === 'navigate') navigate.current(data.screenId, data.stateId);
      else window.dispatchEvent(new Event(FRAME_UPDATED_EVENT));
    }
    window.addEventListener('message', receive);
    return () => window.removeEventListener('message', receive);
  }, [iframe]);

  useLayoutEffect(() => {
    const element = box.current;
    if (!element) return;
    setBoxWidth(element.clientWidth);
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => setBoxWidth(element.clientWidth));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  const scale = size && boxWidth ? Math.min(1, boxWidth / size.width) : 1;
  // 화면에 차지하는 높이. 확대해도 창 크기는 그대로다
  const height = size?.height ?? fitHeight;
  // iframe 안(페이지가 쓰는) 크기는 창 크기 ÷ 배율이다. 칸에 맞춤 · 100%면 칸 폭 그대로 둔다
  let frameWidth: number | string = '100%';
  if (size) frameWidth = size.width / zoom;
  else if (zoom !== 1 && boxWidth) frameWidth = boxWidth / zoom;
  const transform = scale * zoom;

  return (
    <div ref={box} className="min-w-0 overflow-hidden" style={{ height: height * scale }}>
      {/* 칸보다 좁은 화면(휴대폰 등)은 가운데에 둔다 */}
      <div
        className="mx-auto overflow-hidden"
        style={{ width: size ? size.width * scale : '100%', height: height * scale }}
      >
        <iframe
          ref={iframe}
          src={initialUrl}
          title={title}
          loading={lazy ? 'lazy' : undefined}
          className="block border-0 bg-panel"
          style={{
            width: frameWidth,
            height: height / zoom,
            transform: transform !== 1 ? `scale(${transform})` : undefined,
            transformOrigin: 'top left',
          }}
        />
      </div>
    </div>
  );
}
