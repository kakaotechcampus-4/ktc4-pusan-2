"""Pitch Coach 실시간 코치 v1.

    from coach import decide, finalize, build_review_evidence

전체 계약과 로직은 coach-agent/v1/README.md 를 보세요.
"""

from .engine import decide, decide_safe, finalize
from .review import build_review_evidence
from .state import initial_state

__all__ = ["build_review_evidence", "decide", "decide_safe", "finalize", "initial_state"]
