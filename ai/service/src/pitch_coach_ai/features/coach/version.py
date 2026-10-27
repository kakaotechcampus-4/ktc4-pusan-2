"""코치의 버전 값. 응답과 리뷰 근거에 실려 나가고, BE 가 결과와 함께 저장한다.

- `SCHEMA_VERSION`: 요청 · 응답 · 이벤트 · 리뷰 근거의 모양이 바뀔 때 올린다
- `FEATURE_VERSION`: 코치 기능 버전. 출력의 뜻이 바뀔 때 올린다.
  같은 입력에 다른 판단이 나오게 바꾸면 올린다
- `STATE_VERSION`: `coach_state` 모양이 바뀔 때 올린다

기준값 · 가중치 같은 설정 조정은 버전을 올리지 않는다.
응답 `meta.criteria_versions` 의 `coach` 가 어떤 설정으로 판단했는지 남긴다.
"""

#: 1.0: 판정 모듈을 받는 요청 · 응답 · 이벤트 계약 (응답 meta, 이벤트 id 는 종류와 대상으로)
SCHEMA_VERSION = "1.0"
#: coach-1.1: 원자료 입력, 측정하지 못한 1초 · Take 시작 직후의 시선 판단.
#: coach-1.2: Take 시작 전 LLM 코칭 계획 (계획이 없으면 coach-1.1 과 같은 판단)
#: coach-1.3: 서비스 탑재 전 맞춤 — 기록 단위 입력 검사 · 장 전환 기록을 시각으로 · STT 가 끊긴
#:   요청부터 진행도를 머문 시간으로 · Take 도중 기준 변경을 한 번이라도 보면 criteria_changed
FEATURE_VERSION = "coach-1.3"
#: coach_state 모양이 바뀌면 올린다. 다른 버전의 state 가 오면 버리고 새로 시작한다.
#: 2: 판정 모듈 커서 · tally 합계 · 기준 음량 · 센 구간을 두고, 옛 평가기 상태를 뺐다.
#: 코칭 계획도 뺐다 (요청의 coaching_plan 으로 받는다). 군더더기 시각 · 시선 짧은 평균 기록을 뒀고,
#: 장별 누적은 아직 내지 않은 장 방문(visits)으로 바꿨다
STATE_VERSION = 2
#: 시간 판정(timing) 기능 버전. 출력의 뜻이 바뀌면 올린다
#: timing-1.1: 잰 시간이 없으면 measured_ratio 0, 이번 요청의 STT 신뢰(stt_trusted)로 진행도를 고름
TIMING_VERSION = "timing-1.1"
