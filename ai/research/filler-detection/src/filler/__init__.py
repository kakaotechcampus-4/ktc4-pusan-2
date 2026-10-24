"""군더더기(filler) 판정 모듈. 설계: docs/ai/features/filler.md.

밖에서 쓰는 이름만 내보낸다. 입출력 모델은 `filler.schemas` 에 있다.
"""

from .config import DEFAULT, FillerConfig
from .core import judge, normalize
from .version import FEATURE_VERSION

__all__ = ["DEFAULT", "FEATURE_VERSION", "FillerConfig", "judge", "normalize"]
