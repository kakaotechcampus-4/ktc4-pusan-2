"""대본 전달 평가 research 도구.

코어(`script_coverage`)를 감싸 실험에 필요한 일을 한다: `.env` 와 LLM 클라이언트, 로컬 SQLite(LLM 응답 캐시 ·
평가 기준 · 평가 결과), 데이터 읽기, 표 · 지표 계산, 정답 라벨 비교, 반복 실행.

research 전용이다. ai/service 로 옮기지 않는다 (파일 · SQLite · pandas · print 를 자유롭게 쓴다).
"""
