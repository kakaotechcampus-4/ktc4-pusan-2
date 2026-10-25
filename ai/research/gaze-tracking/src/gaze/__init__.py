"""시선 서버 판정 모듈: 1초 기록 → 1초 판정 결과 · 장 / Take 시선 지표 · 판정 기준.

- `core.py` — `judge`(1초 판정 결과, 영역 GAZE) · `summarize`(장 · Take 지표) · `criteria`(다섯 문제의 기준)
- `schemas.py` — 입력(1초 기록 · 발화 시간 · 커서) · 출력(JudgmentResult · 요약 · 기준) 계약 (pydantic)
- `config.py` — 판정 기준값 `GazeConfig`. 해시가 criteria_version 에 들어간다
- `version.py` — 기능 버전
- `examples/` — 입력 · 출력 예시 (계약 테스트가 읽는다)

시선 판정의 앞부분(카메라 → 프레임 판정 → 1초 기록)은 사용자 브라우저에서 돈다. 서버는 얼굴 영상 ·
랜드마크를 받지 않고 1초 기록만 받는다. 코치가 `Judges` 로 이 모듈의 세 함수를 받아 부른다
(docs/ai/features/gaze-tracking.md 3 · 4-2절).

코어는 파일 · DB · 네트워크 · 환경변수 · 시계를 쓰지 않고, 패키지 안에서는 상대 import 만 쓴다.
ai/service 의 features/gaze/ 로 폴더째 옮겨도 고칠 곳이 없게 하기 위해서다.
"""
