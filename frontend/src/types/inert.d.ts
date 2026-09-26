import 'react';

declare module 'react' {
  interface HTMLAttributes<T> {
    /**
     * 서브트리를 통째로 비활성화합니다 — 포커스(Tab)·포인터·접근성 트리에서
     * 모두 빠집니다. `pointer-events: none` 은 포인터만 막고, `aria-hidden` 은
     * 스크린 리더만 막습니다. 키보드까지 막는 것은 이것뿐입니다.
     *
     * ★ 값이 `boolean` 이 아니라 `''` 인 이유
     *   react-dom 18.3.1 은 `inert` 를 모릅니다. `inert={true}` 로 쓰면
     *   "Received `true` for a non-boolean attribute" 경고와 함께 속성을
     *   **아예 렌더하지 않습니다.** HTML 불리언 속성이라 빈 문자열이 곧 "켬"
     *   이므로 `inert=""` 로 씁니다.
     *
     *   React 19 로 올리면 `inert` 가 boolean 으로 정식 지원됩니다.
     *   그때 이 파일을 지우고 `inert` 로 되돌리면 됩니다.
     */
    inert?: '';
  }
}
