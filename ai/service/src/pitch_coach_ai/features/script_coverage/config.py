"""기능 전체의 기준값과 기준 버전.

판정 임계값 · 점수표는 하위 `script_analysis/config.py` · `stt_evaluation/config.py`에 두고,
둘이 함께 쓰는 값(역할 → 중요도, 중요도 가중치)과 실행 값(장 동시 처리 수)은 여기에 둔다.

`CRITERIA_HASH` 는 결과를 바꾸는 기준값(하위 설정 두 개의 대문자 상수 + `ROLE_IMPORTANCE` ·
`SCORE_WEIGHT`)을 모아 낸 해시이고, `CRITERIA_VERSION` 은 응답 `meta.criteria_versions` 에 들어간다.
기준값이 하나라도 바뀌면 해시가 달라져, 저장된 채점이 어떤 기준으로 나왔는지 구분할 수 있다.
"""

import hashlib
import json
from types import ModuleType

from .script_analysis import config as _script_config
from .stt_evaluation import config as _stt_config
from .version import FEATURE_VERSION

# Key Point 의 중요도 = 근거 문장 역할 중 가장 높은 것 (순서 정의 IMPORTANCE_RANK 는 shared/rubric.py)
ROLE_IMPORTANCE = {"claim": "critical", "evidence": "high", "detail": "normal", "skip": "normal"}
# 채점할 때 Key Point · 사실의 가중치
SCORE_WEIGHT = {"critical": 3, "high": 2, "normal": 1}

# 동시에 도는 LLM 호출 수 (기능 문서 8절). 결과에 영향이 없는 실행 값이라 CRITERIA_HASH 에 넣지 않는다
MAX_WORKERS = 4  # 채점 (`/takes/analyze`): 장마다 1번 + 어긋난 장 1번
# 평가 기준 만들기 (`/scripts/rubric`): 장마다 3번을 이어서. 호출 20초 제한에서 30장 77초, 4개 동시 20장 87초 (8절)
RUBRIC_MAX_WORKERS = 12


def _constants(module: ModuleType) -> dict:
    """모듈의 대문자 공개 상수. 집합은 정렬한 list 로 바꿔 JSON 으로 항상 같게 만든다."""
    return {
        name: sorted(value) if isinstance(value, set | frozenset) else value
        for name, value in vars(module).items()
        if name.isupper() and not name.startswith("_")
    }


def criteria_hash() -> str:
    """기준값으로 만든 해시 (sha256 앞 12자리). 호출할 때의 값으로 다시 계산한다."""
    payload = {
        "script_analysis": _constants(_script_config),
        "stt_evaluation": _constants(_stt_config),
        "importance": ROLE_IMPORTANCE,
        "weights": SCORE_WEIGHT,
    }
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def criteria_version() -> str:
    return f"{FEATURE_VERSION}+{criteria_hash()}"


CRITERIA_HASH = criteria_hash()
CRITERIA_VERSION = criteria_version()
