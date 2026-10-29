"""소리 크기 · 침묵 판정의 기능 버전.

판정 규칙 · 출력의 뜻이 바뀔 때만 올린다. 기준값 조정은 버전을 올리지 않고 config 해시로 남는다
(``criteria_version`` = ``<FEATURE_VERSION>+<config 해시 12자리>``).
"""

#: volume-1.0: 첫 판. 연구용 대역(volume-0.2)과 달리 음량은 에너지 평균(Leq), 말한 1초는
#: voiced_ms 기준 · audio_live, 침묵은 오디오가 다시 흐른 첫 1초의 시작부터 센다
#: volume-1.1: 판정 시각에 끝나는 1초가 없으면 두 영역 UNKNOWN, 기록이 없는 시간의 tally 는
#: 1초씩(끝 조각 포함) 낸다 (#589)
FEATURE_VERSION = "volume-1.1"
