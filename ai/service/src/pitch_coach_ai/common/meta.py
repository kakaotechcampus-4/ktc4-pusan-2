"""기능의 성공 응답에 붙는 `meta` (`docs/ai/architecture.md` 5절).

    {"schema_version": "1.0", "feature_version": "sample-0.1",
     "criteria_versions": {"sample": "sample-0.1+3f2a9c1d0b7e"}, "model": null}

- 부르는 곳: 라우터. 기능 결과 옆에 `meta=build_meta(...)` 로 싣는다
- `feature_version`: 기능 `version.py` 의 `FEATURE_VERSION` 그대로. 값 자체가 `<모듈>-<버전>`
  꼴이다(예: `"coach-1.2"`, `architecture.md` 5절). 여기서 접두어를 붙이지 않는다
- `criteria_versions`: 판정이 들어간 응답만(`JudgeMeta`). 모듈마다
  `<feature_version>+<config 해시 12자리>`. 기준값(config.py)을 바꾸면 해시가 바뀐다.
  해시는 코치 연구 코드(`coach/config.py` 의 `criteria_version`)와 같은 방식이다:
  `model_dump(mode="json")` 을 키 정렬 JSON 으로 만들어 sha256 앞 12자리
- `model`: 실제로 응답한 LLM 모델. LLM 을 쓰지 않았거나, 결과가 LLM 출력이 아니라
  기본 결과(코칭 계획 · 리뷰의 `fallback_reason`)면 null 이다.
  응답한 모델 · 토큰 수는 그때도 로그에 남는다
- `schema_version`: 응답 모양의 버전. BE 가 깨지는 변경(필드 이름 변경 · 삭제 · 뜻 변경)을
  할 때만 올린다
"""

import hashlib
import json

from pydantic import BaseModel

from pitch_coach_ai.common.llm import StructuredLlm

SCHEMA_VERSION = "1.0"


class Meta(BaseModel):
    schema_version: str = SCHEMA_VERSION
    feature_version: str
    model: str | None


class JudgeMeta(Meta):
    """판정이 들어간 응답의 meta. `criteria_versions` 가 반드시 있다."""

    criteria_versions: dict[str, str]


def config_hash(config: BaseModel) -> str:
    """기준값 설정의 해시 12자리. 같은 해시면 같은 기준이다."""
    blob = json.dumps(config.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def criteria_version(feature_version: str, config: BaseModel) -> str:
    """예: `criteria_version("sample-0.1", SampleConfig())` → `"sample-0.1+3f2a9c1d0b7e"`."""
    return f"{feature_version}+{config_hash(config)}"


def responded_model(llm: StructuredLlm | None, *, fallback: bool = False) -> str | None:
    """`meta.model` 에 실을 값. LLM 을 안 썼거나 기본 결과면 None."""
    if llm is None or fallback:
        return None
    return llm.responded_model


def build_meta(
    feature_version: str,
    *,
    llm: StructuredLlm | None = None,
    fallback: bool = False,
) -> Meta:
    """판정이 없는 응답의 meta. `fallback=True` 는 기본 결과를 낸 응답(`fallback_reason` 있음).

    예: `build_meta(FEATURE_VERSION, llm=llm)` (FEATURE_VERSION = "script_parser-1.0")
    """
    return Meta(
        feature_version=feature_version,
        model=responded_model(llm, fallback=fallback),
    )


def build_judge_meta(
    feature_version: str,
    criteria_versions: dict[str, str],
    *,
    llm: StructuredLlm | None = None,
    fallback: bool = False,
) -> JudgeMeta:
    """판정이 들어간 응답의 meta. `criteria_versions` 는 모듈 → `criteria_version(...)`."""
    return JudgeMeta(
        feature_version=feature_version,
        criteria_versions=criteria_versions,
        model=responded_model(llm, fallback=fallback),
    )
