"""Pitch Coach 리뷰 에이전트 v1.

    from pitch_coach_ai.features.review import review_take

계약 · 판단 흐름은 docs/ai/features/review-agent.md,
실행법은 ai/research/review-agent/README.md 를 보세요.

코어는 파일 · DB · 네트워크 · 환경변수 · 시계 · 난수를 쓰지 않고,
패키지 안에서는 상대 import 만 쓴다.
연구 폴더 src/review/ 에서 폴더째 옮겨 올 때 고칠 곳이 없게 하기 위해서였다.
"""

from .config import DEFAULT_CONFIG, ReviewConfig, load_config
from .core import RuleResult, apply_rules, review_take
from .schemas import ReviewRequest, ReviewResponse

__all__ = [
    "DEFAULT_CONFIG",
    "ReviewConfig",
    "ReviewRequest",
    "ReviewResponse",
    "RuleResult",
    "load_config",
    "apply_rules",
    "review_take",
]
