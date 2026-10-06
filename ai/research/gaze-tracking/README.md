# gaze-tracking

웹캠으로 발표자가 **카메라 · 화면 · 대본 · 그 밖** 중 어디를 보는지 판정하는 시선 기능입니다.
판정은 사용자 브라우저에서(온디바이스) 돌고, 서버는 1초 기록으로 코치 이슈와 리뷰 요약을 만듭니다.

담당: `jewon-kim`

> `ai/archive/workspaces/jewon-kim/gaze-tracking/v1/local/`을 옮기는 중입니다 (#101).
> 폴더 구조 · 코드 역할 · 실행 흐름은 이전이 끝나면 이 문서에 정리합니다.

## 준비

```bash
cd ai/research/gaze-tracking
uv sync                      # Python 3.12 (.python-version), 라이브러리 + 개발 도구
```

Windows에서는 `pytest.exe`가 앱 제어로 막혀 있을 수 있어 `uv run python -m pytest`로 부릅니다.
