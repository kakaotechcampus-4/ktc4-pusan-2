# 피치코치 공용 UI

현재 디자인 시안에서 검토 중입니다. 실제 제품 화면에 일괄 적용하지 않습니다.
미리보기: `/dev/design/components`, 적용 예시: `/dev/design/report`.

| 컴포넌트 | 용도 | 주요 옵션 |
| --- | --- | --- |
| Button | 실행하는 동작 (기본 `type="button"`) | variant: primary / secondary / quiet, size: sm / md |
| ButtonLink | 라우터 화면 이동 | to, variant, size |
| Card | 제목과 내용을 묶는 패널 | title, action |
| Stat | 표시용 숫자 지표 | label, value |
| Badge | 짧은 상태 표시 | tone: bad / good / strong / plain / muted |
| Mascot | 마스코트 한마디 (안내 · 빈 화면) | who: chichi / pico |

## 규칙

- 색은 `index.css` 토큰만 씁니다. 컴포넌트 안에 화면별 색을 넣지 않습니다 (밝은 지면은 `pitch-create` 클래스가 토큰을 바꿉니다).
- Badge는 색만으로 의미를 전하지 않습니다. 상태 문구를 꼭 넣습니다.
- 측정 못 한 값은 `—`로 넘깁니다. 0으로 채우지 않습니다.
- 리허설 무대에는 쓰지 않습니다 (`stage.css`).
- 새 컴포넌트는 두 화면 이상에서 반복될 때 추가하고, 미리보기에도 예시를 넣습니다.
