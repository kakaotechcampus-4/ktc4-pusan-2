/**
 * `public/`의 파일 주소. 설계서를 하위 경로(GitHub Pages `/ktc4-pusan-2/`)에 올려도 맞도록 빌드 `base`를 앞에 붙인다.
 * 제품은 루트에서 열려 `/onboarding/…`을 그대로 쓰지만, 설계서는 `--base`로 빌드하므로 절대 경로를 적지 않는다.
 * `styles.css`의 `url(/fonts/…)`와 `index.html`은 Vite가 base에 맞게 다시 써 주니 이 함수가 필요 없다.
 */
export function publicUrl(path: string): string {
  return `${import.meta.env.BASE_URL}${path.replace(/^\//, '')}`;
}
