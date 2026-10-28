import { useId } from 'react';
import './ComfortScene.css';

/** All layers share one coordinate system, so resizing never moves tears away from the eyes. */
export function ComfortScene() {
  const titleId = useId();
  return (
    <svg
      viewBox="0 0 1050 764"
      role="img"
      aria-labelledby={titleId}
      className="comfort-scene block w-full"
    >
      <title id={titleId}>피코 교수가 눈물을 흘리는 치치의 머리를 부드럽게 쓰다듬습니다</title>
      <svg width="1050" height="764" viewBox="0 0 1050 764" overflow="hidden" aria-hidden="true">
        <image href="/onboarding/comfort-layers.png" width="2057" height="764" />
      </svg>
      <g className="comfort-scene__wing" aria-hidden="true">
        <svg x="305" y="365" width="260" height="136" viewBox="1300 345 325 170" overflow="hidden">
          <image href="/onboarding/comfort-layers.png" width="2057" height="764" />
        </svg>
      </g>
      {[
        { x: 347, y: 488, delay: '0s' },
        { x: 397, y: 499, delay: '.5s' },
        { x: 347, y: 488, delay: '.9s' },
      ].map(({ x, y, delay }) => (
        <svg key={delay} x={x} y={y} width="12" height="64" viewBox="0 0 3 16" aria-hidden="true">
          <g
            className="comfort-scene__tear"
            style={{ animationDelay: delay }}
            shapeRendering="crispEdges"
          >
            <path d="M1 0h1v1h1v3H0V1h1z" fill="currentColor" />
            <rect x="1" y="1" width="1" height="1" fill="white" />
          </g>
        </svg>
      ))}
    </svg>
  );
}
