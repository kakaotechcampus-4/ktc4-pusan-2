"""답 초안 스트림 추적 — 흘러오는 구조화 출력(JSON)에서 최상위 값 · 판단 목록 · 문장 원소가 닫히는 때를 잡는다
(기능 문서 5-4 '문장 줄' · 6-1 5번).

- 누가 쓰나: answer.py 의 답 쓰기. 모델 응답 조각을 `feed()`에 그대로 넣으면, 닫힌 것만 사건으로 돌려준다.
- 하는 일: 글자를 하나씩 따라가며 문자열 · 이스케이프 · 중첩 깊이를 센다. SDK 의 부분 파싱 스냅샷은 쓰지 않는다(원소가
  닫혔다는 보장이 없기 때문).
  - 최상위 객체의 문자열 값(예: `route`)이 닫히면 `ValueDone`
  - 최상위 키 `assessments` 배열이 닫히면 `AssessmentsDone`
  - 최상위 키 `claims` 배열의 원소(객체)가 닫히면 그 원소를 `json.loads`로 읽어 `ClaimClosed`
  읽는 중인 구간(키 · 값 · 원소 · 판단 목록)만 모아 두므로 조각이 많아도 받은 글 전체를 다시 잇지 않는다.
- 하지 않는 것: 원소의 뜻 검사 · 스키마 검사(호출자가 한다). 원소를 읽지 못하면(깨진 JSON) `item=None`으로 알리고, 최종
  판단은 응답 전체를 다 받은 뒤의 파싱이 한다. 화면에 나간 글이 최종 답과 달라지지 않게 하는 것은 호출자의 '앞 문장이
  확정되기 전에 뒤 문장을 내지 않는다' 규칙과 최종 대조다.
- 고칠 때: 답 초안 스키마(prompts)의 키 이름이 바뀌면 `ASSESSMENTS_KEY` · `CLAIMS_KEY`를 맞춘다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

ASSESSMENTS_KEY = "assessments"
CLAIMS_KEY = "claims"


@dataclass(frozen=True)
class ValueDone:
    """최상위 객체의 `key` 값(문자열)이 닫혔다."""

    key: str
    value: str


@dataclass(frozen=True)
class AssessmentsDone:
    """최상위 `assessments` 배열이 닫혔다. `items`는 그 배열(읽지 못하면 None)."""

    items: list[Any] | None


@dataclass(frozen=True)
class ClaimClosed:
    """최상위 `claims` 배열의 `index`번째 원소가 닫혔다. `item`은 읽은 원소(읽지 못하면 None)."""

    index: int
    item: dict[str, Any] | None


Event = ValueDone | AssessmentsDone | ClaimClosed


@dataclass
class StreamTracker:
    """흘러오는 JSON 글자를 받아 닫힌 최상위 값 · 판단 목록 · 문장 원소를 사건으로 낸다. 한 응답에 하나씩 만든다."""

    _depth: int = 0
    _in_str: bool = False
    _esc: bool = False
    #: 최상위 객체 안(깊이 1)에서 마지막으로 읽은 키와, ':' 뒤 값을 기다리는 중인가
    _last_key: str | None = None
    _await_value: bool = False
    #: 지금 따라가는 최상위 배열('assessments' · 'claims')
    _array: str | None = None
    #: 읽는 중인 구간의 글자와 그 종류('key' · 'value' · 'array' · 'elem'). 구간은 겹치지 않는다
    _cap: list[str] | None = None
    _cap_kind: str | None = None
    _next_index: int = 0
    assessments_done: bool = False
    #: 닫힌 claims 원소 수
    claims_seen: int = field(default=0)

    def feed(self, text: str) -> list[Event]:
        """조각을 더하고 이번 조각으로 새로 닫힌 사건들을 순서대로 돌려준다."""
        events: list[Event] = []
        for ch in text:
            if self._cap is not None:
                self._cap.append(ch)
            if self._in_str:
                if self._esc:
                    self._esc = False
                elif ch == "\\":
                    self._esc = True
                elif ch == '"':
                    self._in_str = False
                    if self._cap_kind in ("key", "value"):
                        self._string_closed(events)
                continue
            if ch == '"':
                self._in_str = True
                if self._depth == 1:
                    # 최상위 객체 안에서 값을 기다리지 않을 때 나오는 문자열은 키, 기다릴 때는 값이다
                    self._begin("value" if self._await_value else "key", ch)
                continue
            if ch == ":" and self._depth == 1:
                self._await_value = True
                continue
            if ch == "," and self._depth == 1:
                self._await_value = False
                continue
            if ch in "{[":
                if (
                    self._depth == 1
                    and self._await_value
                    and ch == "["
                    and self._last_key in (ASSESSMENTS_KEY, CLAIMS_KEY)
                ):
                    self._array = self._last_key
                    if self._array == ASSESSMENTS_KEY:
                        self._begin("array", ch)
                elif self._depth == 2 and self._array == CLAIMS_KEY and ch == "{":
                    self._begin("elem", ch)
                self._depth += 1
                continue
            if ch in "}]":
                self._depth -= 1
                if self._depth == 2 and self._cap_kind == "elem" and ch == "}":
                    events.append(self._claim(self._end()))
                elif self._depth == 1 and ch == "]" and self._array is not None:
                    if self._cap_kind == "array":
                        events.append(AssessmentsDone(_loads_list(self._end())))
                        self.assessments_done = True
                    self._array = None
                    self._await_value = False
                elif self._depth == 1:
                    self._await_value = False
        return events

    def _begin(self, kind: str, ch: str) -> None:
        self._cap, self._cap_kind = [ch], kind

    def _end(self) -> str:
        raw = "".join(self._cap or [])
        self._cap, self._cap_kind = None, None
        return raw

    def _string_closed(self, events: list[Event]) -> None:
        kind = self._cap_kind
        try:
            value = json.loads(self._end())
        except ValueError:
            value = None
        if kind == "key":
            self._last_key = value if isinstance(value, str) else None
        elif self._last_key is not None and isinstance(value, str):
            events.append(ValueDone(self._last_key, value))

    def _claim(self, raw: str) -> ClaimClosed:
        idx = self._next_index
        self._next_index += 1
        self.claims_seen = self._next_index
        try:
            item = json.loads(raw)
        except ValueError:
            item = None
        return ClaimClosed(idx, item if isinstance(item, dict) else None)


def _loads_list(raw: str) -> list[Any] | None:
    try:
        v = json.loads(raw)
    except ValueError:
        return None
    return v if isinstance(v, list) else None
