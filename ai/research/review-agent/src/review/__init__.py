"""Pitch Coach 리뷰 에이전트 v1.

    from review import review_take

입출력은 ai/research/review-agent/INTERFACE.md, 구조와 판단 흐름은 같은 폴더의 README.md 를 보세요.

코어는 파일 · DB · 네트워크 · 환경변수 · 시계 · 난수를 쓰지 않고,
패키지 안에서는 상대 import 만 쓴다.
나중에 ai/service 의 features/review/ 로 폴더째 옮겨도 고칠 곳이 없게 하기 위해서다.
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
