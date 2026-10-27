# 기능 버전. 같은 입력에 대한 결과의 뜻(판정 규칙 · 출력 필드의 뜻)이 바뀔 때만 올린다
# (gaze-tracking.md 9절)
# gaze-1.2: 1초 판정 결과(judge · summarize · criteria)와 대본 의존.
# 1초 기록 형식과 엔진 짝(gaze_v1.1.0)은 그대로
# gaze-1.3: GAZE_UNMEASURABLE · GAZE_LOW_EYE_CONTACT 의 persistence_sec 를 지금까지 이어진 길이로
#   (architecture.md 7절)
FEATURE_VERSION = "gaze-1.3"
