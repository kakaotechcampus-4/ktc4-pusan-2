"""Pitch Coach 실시간 코치 v1.

    from coach import decide, finalize, build_review_evidence, plan_coaching

입출력은 ai/research/coach-agent/INTERFACE.md, 구조와 판단 흐름은 같은 폴더의 README.md 를 보세요.
"""

from .core import decide, decide_safe, finalize
from .judges import Judges
from .planner import plan_coaching
from .review import build_review_evidence
from .state import initial_state

__all__ = [
    "Judges",
    "build_review_evidence",
    "decide",
    "decide_safe",
    "finalize",
    "initial_state",
    "plan_coaching",
]
