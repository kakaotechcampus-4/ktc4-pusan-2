"""예시 요청에 요청 모델이 모르는 필드가 없는지 본다.

요청 모델은 모르는 필드를 조용히 무시한다(`architecture.md` 2절). 그래서 필드 이름에 오타가
있으면 기본값으로 돌아 판정이 달라질 뿐 오류가 나지 않는다. BE 가 먼저 개발하는 예시(`examples/`)
에는 그런 필드가 없어야 하므로 JSON 스키마의 필드 목록과 대조한다.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from pitch_coach_ai.features import coach
from pitch_coach_ai.features.coach.schemas import CoachRequest, FinalizeRequest, PlanRequest

from .test_planner import request as plan_request

EXAMPLES = Path(coach.__file__).resolve().parent / "examples"


def _unknown(
    data: Any, schema: dict[str, Any], defs: dict[str, Any], where: str = "$"
) -> list[str]:
    """`data` 에서 `schema` 가 모르는 키의 위치 목록."""
    if "$ref" in schema:
        return _unknown(data, defs[schema["$ref"].rsplit("/", 1)[1]], defs, where)
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            options = [s for s in schema[key] if s.get("type") != "null"]
            found = [_unknown(data, s, defs, where) for s in options]
            return min(found, key=len) if found else []
    out: list[str] = []
    if isinstance(data, dict) and "properties" in schema:
        props = schema["properties"]
        for k, v in data.items():
            if k not in props:
                out.append(f"{where}.{k}")
            else:
                out += _unknown(v, props[k], defs, f"{where}.{k}")
    elif isinstance(data, dict) and isinstance(schema.get("additionalProperties"), dict):
        for k, v in data.items():
            out += _unknown(v, schema["additionalProperties"], defs, f"{where}.{k}")
    elif isinstance(data, list) and isinstance(schema.get("items"), dict):
        for i, v in enumerate(data):
            out += _unknown(v, schema["items"], defs, f"{where}[{i}]")
    return out


def _check(model: type[BaseModel], data: dict[str, Any]) -> list[str]:
    schema = model.model_json_schema()
    return _unknown(data, schema, schema.get("$defs", {}))


@pytest.mark.parametrize(
    ("model", "name"),
    [
        (CoachRequest, "evaluate/request.json"),
        (FinalizeRequest, "finalize/request.json"),
        (FinalizeRequest, "finalize/request_replay.json"),
    ],
)
def test_example_requests_have_no_field_the_model_ignores(model, name) -> None:
    data = json.loads((EXAMPLES / name).read_text(encoding="utf-8"))
    assert _check(model, data) == []


def test_plan_request_used_by_the_tests_has_no_ignored_field() -> None:
    assert _check(PlanRequest, plan_request()) == []


def test_the_check_finds_a_misspelled_field() -> None:
    data = json.loads((EXAMPLES / "evaluate/request.json").read_text(encoding="utf-8"))
    data["inputs"]["stt_staus"] = "ok"
    assert _check(CoachRequest, data) == ["$.inputs.stt_staus"]
