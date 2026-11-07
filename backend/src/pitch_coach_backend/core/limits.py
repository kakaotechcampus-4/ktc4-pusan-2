"""여러 모듈이 같이 쓰는 한도와 시간대."""

from datetime import timedelta, timezone

# PostgreSQL integer(int4) 칸의 상한. 넘으면 저장에서 터진다
INT4_MAX = 2**31 - 1

# Take 시각 `t_ms` 의 상한. Take 최대 1시간 + 60초 여유로, 코치 AI 가 받는 상한과 같다
# (docs/ai/architecture.md 1-3 · 1-4). 넘으면 AI 가 422 를 준다.
# `AutoEndPolicy.from_settings` 가 앱 시작 때 `take_max_duration_sec` 과 맞는지 본다
MAX_T_MS = 3_660_000

KST = timezone(timedelta(hours=9), "KST")
