"""코치의 버전 값. 응답과 리뷰 근거에 실려 나가고, BE 가 결과와 함께 저장한다.

- `SCHEMA_VERSION`: 요청 · 응답 · 이벤트 · 리뷰 근거의 모양이 바뀔 때 올린다
- `POLICY_VERSION`: 판단 규칙의 의미가 바뀔 때 올린다 (기능 버전).
  같은 입력에 다른 판단이 나오게 바꾸면 올린다
- `STATE_VERSION`: `coach_state` 모양이 바뀔 때 올린다

기준값 · 가중치 같은 설정 조정은 버전을 올리지 않는다.
응답의 `config_hash` 가 어떤 설정으로 판단했는지 남긴다.
"""

#: 1.1: 시선 1초 기록 입력(gaze.records)
SCHEMA_VERSION = "1.1"
#: coach-v1.1: 원자료 입력, 측정하지 못한 1초 · Take 시작 직후의 시선 판단
POLICY_VERSION = "coach-v1.1"
#: coach_state 모양이 바뀌면 올린다. 다른 버전의 state 가 오면 버리고 새로 시작한다.
STATE_VERSION = 1
