"""Take 상태 값."""

# 아직 끝나지 않은 Take. READY 는 첫 연결 전, RUNNING 은 발표 중이다.
# 나머지(ANALYZING · COMPLETED · FAILED)는 이미 끝난 연습이다
IN_PROGRESS_STATUSES = ("READY", "RUNNING")
