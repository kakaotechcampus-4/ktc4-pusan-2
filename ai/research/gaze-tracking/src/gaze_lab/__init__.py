"""시선 판정의 Python 기준 구현 · 평가 (research 전용, 배포하지 않는다).

브라우저에서 실제로 도는 것은 같은 판정을 옮긴 TS 엔진(web/src/engine/)이다.
이 패키지는 데이터로 실험 · 평가하고, TS 엔진의 임계값(configs/*.yaml)과
parity 기준 답을 만드는 기준이다.

원본: ai/archive/workspaces/jewon-kim/gaze-tracking/v1/local/ai/src/vision/
"""
