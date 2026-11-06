"""Take 의 상태 · 분석 단계 · AI 결과 종류 값."""

from typing import Literal

# takes.status
READY = "READY"
RUNNING = "RUNNING"
ANALYZING = "ANALYZING"
COMPLETED = "COMPLETED"
FAILED = "FAILED"
# 아직 끝나지 않은 Take. READY 는 첫 연결 전, RUNNING 은 발표 중이다.
# 나머지(ANALYZING · COMPLETED · FAILED)는 이미 끝난 연습이다
IN_PROGRESS_STATUSES = (READY, RUNNING)

# takes.analysis_stage. 리뷰가 끝나면 DONE 이다 (takes.review_status 의 DONE 과 같은 값)
FINALIZING = "FINALIZING"
SCORING = "SCORING"
AWAITING_CONFIRMATION = "AWAITING_CONFIRMATION"
REVIEWING = "REVIEWING"
DONE = "DONE"

# take_ai_results.kind
TAKE_RESULT = "TAKE_RESULT"
REVIEW = "REVIEW"
COVERAGE_KIND = "SCRIPT_COVERAGE"
COVERAGE_CANDIDATE_KIND = "SCRIPT_COVERAGE_PENDING"

# 채점 작업 하나의 결과 (`service/coverage.py` 의 `analyze_script_coverage`)
type CoverageOutcome = Literal[
    "SKIPPED", "RUBRIC_PENDING", "HELD", "AWAITING_CONFIRMATION", "NO_COVERAGE", "READY_FOR_REVIEW"
]
