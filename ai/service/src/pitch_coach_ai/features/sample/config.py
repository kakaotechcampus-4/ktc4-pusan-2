"""대역의 기준값. 해시가 `criteria_version`(`sample-0.1+<해시 12자리>`)에 들어간다.

- 값을 바꾸면 해시가 바뀐다. `examples/judge_response.json` 의 `criteria_version` 도 같이
  고친다(계약 테스트가 어긋나면 실패한다)
- 해시는 `common/meta.py` 의 `criteria_version` 과 같은 방식이다(코치 연구 코드
  `coach/config.py` 와도 같다). 기능 폴더는 `common/` 을 import 하지 않으므로 여기 따로 둔다.
  두 값이 같은지는 계약 테스트가 본다
"""

import hashlib
import json

from pydantic import BaseModel, ConfigDict, Field


class SampleConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    #: 판정 창에서 기록이 온 시간이 이 비율 이상이면 잴 수 있다고 본다 (`measurable`)
    min_measured_ratio: float = Field(0.5, ge=0, le=1)


DEFAULT = SampleConfig()


def criteria_version(feature_version: str, config: BaseModel) -> str:
    """`<feature_version>+<설정 해시 12자리>`. 기준값을 바꾸면 해시가 바뀐다."""
    blob = json.dumps(config.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return f"{feature_version}+{hashlib.sha256(blob.encode('utf-8')).hexdigest()[:12]}"
