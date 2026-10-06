"""대본 전달 평가 코어.

- `script_analysis/` — 대본 → 슬라이드별 평가 기준 (대본 등록 · 수정 시)
- `stt_evaluation/` — 슬라이드별 STT → 평가 결과 (발표 연습마다)
- `shared/` — 둘이 같이 쓰는 것. 두 폴더는 서로 import 하지 않고 shared 만 import 한다

코어는 파일 · DB · 네트워크를 직접 쓰지 않는다. LLM 과 캐시는 인자로 받는다.
패키지 안에서는 상대 import 만 쓴다 — service 로 폴더째 옮겨도 고칠 곳이 없게 하기 위해서다.
"""
