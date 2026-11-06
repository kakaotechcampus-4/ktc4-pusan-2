"""서명된 실행 상태 `state` — 만들기 · 검증 (기능 문서 5-6 · 7-3 L1-26 · L1-27 · L1-28 · L1-33).

- 누가 부르나: loop.py / turn.py. `NEEDS_ACTION`을 낼 때 `sign()`으로 상태를 서명하고, 이어 부를 때
  `verify()`로 검증한 뒤에야 payload 필드를 쓴다. 키와 현재 시각은 호출자가 넘긴다(이 모듈은 환경변수 ·
  파일 · 시계 · 난수를 읽지 않는다).
- 입력 → 출력
  - `sign(payload, secret, cfg)` → `{"v": 1, "kid", "payload", "signature"}` (JSON으로 보낼 수 있는 dict).
  - `verify(signed, ...)` → 검증을 통과한 `StatePayload`. 하나라도 어긋나면 `StateInvalid(reason_code)`.
    BE에는 409 `AGENT_STATE_INVALID`로만 보이고, `reason_code`는 진단 기록(`reason_code`)에만 쓴다.
- 검증 순서(5-6, 정확히 이 순서): 외곽 크기 → `v` · 문자열 형식 → `kid`로 키 고르기(지금 키 · 바로 전 키)
  → 서명(상수 시간 비교) → 엄격한 base64url decode → UTF-8 → JSON(중복 키 거절, 깊이 상한)
  → decode 결과 크기 → 엄격한 스키마 → 만료 → 지원하는 실행 판(프롬프트 판 포함) → 요청 `run`의 식별자.
  서명이 통과하기 전에는 payload 안의 어떤 필드도 읽지 않는다(L1-28).
- 고칠 때
  - 오류 메시지 · 예외에 payload 내용 · 키를 넣지 않는다(P8). `reason_code` 문자열만 담는다.
  - 서명 형식(`v`)이 바뀌면 `version.STATE_V`를 올리고 이 모듈의 형식 검사를 같이 고친다.
  - `StatePayload`의 최상위 필드는 엄격(`extra="forbid"`)하다. 반복 루프가 소유한 내부 데이터는 `loop`
    dict 안에 두고, 최상위에 필드를 더하면 `version.TOOL_SCHEMA_VERSION`을 올린다.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from .config import DEFAULT_CONFIG, ChatAgentConfig
from .version import STATE_V, SUPPORTED_RUN_VERSIONS

#: 이 모듈이 내는 `StateInvalid.reason_code` 목록(진단 기록용 enum, `[A-Z_]`)
REASON_CODES = (
    "SIZE",
    "FORMAT",
    "VERSION",
    "UNKNOWN_KID",
    "SIGNATURE",
    "DECODE",
    "DUPLICATE_KEY",
    "DEPTH",
    "DECODED_TOO_LARGE",
    "SCHEMA",
    "EXPIRED",
    "UNSUPPORTED_RUN_VERSION",
    "ID_MISMATCH",
)

_KID_RE = re.compile(r"[A-Za-z0-9_-]{8}")
_B64URL_RE = re.compile(r"[A-Za-z0-9_-]*")


class StateInvalid(Exception):
    """실행 상태를 믿을 수 없다 → 스트림 전에 409 `AGENT_STATE_INVALID`. 메시지는 `reason_code`뿐이다(P8)."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class StateTooLarge(Exception):
    """직렬화한 state가 크기 한도를 넘었다 → 호출자는 `actions=[]` · `state=null`로 PARTIAL/오류를 낸다(L1-26)."""


class RunVersions(BaseModel):
    """실행이 시작될 때 고정한 판(5-6 '진행 중 실행은 서명한 판을 고정')"""

    model_config = ConfigDict(extra="forbid")

    feature: StrictStr
    prompt: StrictStr
    tool_schema: StrictStr
    corpus: StrictStr


class StatePayload(BaseModel):
    """서명 payload의 모양(5-6 '담는 것'). 최상위는 엄격하고, 루프 내부 데이터는 `loop`에 둔다."""

    model_config = ConfigDict(extra="forbid")

    run_id: StrictStr
    conversation_id: StrictStr
    user_id: StrictStr
    state_revision: StrictInt = Field(ge=0)
    scope_pitch_id: StrictStr | None
    #: 처음 trigger (재개 때 허용 근거 검사용)
    trigger: StrictStr
    #: 처음 사용자 말 원문 (재개 때 허용 근거 검사용). 모델 입력에는 넣지 않는다
    original_user_message: StrictStr | None
    versions: RunVersions
    #: 발급 · 만료 시각 (ms, 호출자가 넘긴 `now_ms` 기준)
    issued_at: StrictInt = Field(ge=0)
    expires_at: StrictInt = Field(ge=0)
    #: `/agent/turn` 호출 횟수 (L1-31)
    turn_count: StrictInt = Field(ge=1)
    #: loop.py가 소유한 내부 데이터(모델 메시지 · 도구 캐시 · 카운터 · 계획 · 근거 등록부 · 판단 · 대기 행동)
    loop: dict[str, Any]


# ── 열쇠 이름 · 서명 ───────────────────────────────────────────────────────


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def kid_of(secret: bytes) -> str:
    """키 이름 = 키의 SHA-256을 base64url(패딩 없음)로 쓴 앞 8글자 (5-6). 따로 설정하지 않아 틀릴 일이 없다."""
    return _b64e(hashlib.sha256(secret).digest())[:8]


def _mac(secret: bytes, kid: str, payload_b64: str) -> str:
    # 서명 입력에 kid를 넣어 키 이름만 바꾸는 위조도 막는다(5-6)
    msg = ("1." + kid + "." + payload_b64).encode("utf-8")
    return _b64e(hmac.new(secret, msg, hashlib.sha256).digest())


def _compact(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def sign(
    payload: StatePayload, secret: bytes, cfg: ChatAgentConfig = DEFAULT_CONFIG
) -> dict[str, Any]:
    """payload를 서명한 외곽 dict를 만든다. 키가 짧으면 `ValueError`, 크기 한도를 넘으면 `StateTooLarge`.

    payload는 공백 없는 UTF-8 JSON → base64url(패딩 없음). 외곽을 직렬화한 크기가 `max_state_bytes`
    (1,048,576바이트)를 넘거나 decode 결과가 `max_state_decoded_bytes`를 넘으면 잘라 내지 않고 실패한다(5-6).
    """
    if len(secret) < cfg.min_secret_bytes:
        raise ValueError("서명 키가 너무 짧다")
    raw = _compact(payload.model_dump(mode="json")).encode("utf-8")
    if len(raw) > cfg.max_state_decoded_bytes:
        raise StateTooLarge
    kid = kid_of(secret)
    payload_b64 = _b64e(raw)
    signed = {
        "v": STATE_V,
        "kid": kid,
        "payload": payload_b64,
        "signature": _mac(secret, kid, payload_b64),
    }
    if len(_compact(signed).encode("utf-8")) > cfg.max_state_bytes:
        raise StateTooLarge
    return signed


# ── JSON 엄격 읽기 ─────────────────────────────────────────────────────────


def json_depth_ok(obj: Any, max_depth: int) -> bool:
    """중첩 깊이가 `max_depth` 이하인가. 최상위 객체 · 배열이 깊이 1이다(스칼라는 0). 반복문이라 재귀 한도에 안 걸린다."""
    stack: list[tuple[Any, int]] = [(obj, 0)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, dict):
            children: Any = item.values()
        elif isinstance(item, list):
            children = item
        else:
            continue
        depth += 1
        if depth > max_depth:
            return False
        stack.extend((child, depth) for child in children)
    return True


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise StateInvalid("DUPLICATE_KEY")
        out[key] = value
    return out


def _reject_constant(_: str) -> Any:
    raise StateInvalid("DECODE")


def strict_json_loads(text: str, max_depth: int = 32) -> Any:
    """중복 키 · NaN/Infinity · 깊이 초과를 거절하는 JSON 읽기. 실패는 `StateInvalid`(DECODE · DUPLICATE_KEY · DEPTH).

    state 해석이 쓰고, turn.py가 요청 본문의 깊이를 볼 때도 이 함수(또는 `json_depth_ok`)를 쓴다.
    """
    try:
        obj = json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=_reject_constant)
    except StateInvalid:
        raise
    except RecursionError:
        raise StateInvalid("DEPTH") from None
    except ValueError:
        raise StateInvalid("DECODE") from None
    if not json_depth_ok(obj, max_depth):
        raise StateInvalid("DEPTH")
    return obj


def _strict_b64decode(text: str) -> bytes:
    """패딩 · 비정규 글자 · 남는 비트가 있는 base64url을 거절하는 decode (L1-28)."""
    if not text or not _B64URL_RE.fullmatch(text) or len(text) % 4 == 1:
        raise StateInvalid("DECODE")
    try:
        raw = base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (binascii.Error, ValueError):
        raise StateInvalid("DECODE") from None
    # 다시 인코딩해 같아야 정규형이다(마지막 글자의 남는 비트가 0이 아니면 걸린다)
    if _b64e(raw) != text:
        raise StateInvalid("DECODE")
    return raw


# ── 검증 ───────────────────────────────────────────────────────────────────


def verify(
    signed: Any,
    *,
    secret: bytes,
    previous_secret: bytes | None,
    now_ms: int,
    current_prompt_version: str,
    expected_ids: dict[str, Any],
    cfg: ChatAgentConfig = DEFAULT_CONFIG,
) -> StatePayload:
    """서명된 상태를 검증해 payload를 돌려준다. 어긋나면 `StateInvalid(reason_code)`.

    - `expected_ids`: 요청 `run`의 `run_id` · `conversation_id` · `user_id` · `state_revision`.
      범위(`scope_pitch_id`)는 비교하지 않는다. payload 안의 범위를 믿는다(5-6).
    - `current_prompt_version`: 이 인스턴스가 쓰는 프롬프트 판. 옛 프롬프트는 불러올 수 없으므로 payload가 고정한
      프롬프트 판이 다르면 지원하지 않는 판이다(`UNSUPPORTED_RUN_VERSION`, 5-6 · L1-33). 자료 판(`corpus`)은 여기서
      거절하지 않는다: 판이 달라도 통과시키고 loop가 검색 캐시를 버린다(6-10).
    - `previous_secret`은 검증에만 쓴다(새 서명은 `sign`이 지금 키로만 한다).
    - `now_ms > expires_at`이면 만료다(경계 값은 아직 유효).
    순서와 이유는 모듈 설명 참고. 서명 전에 payload 필드를 읽지 않는다.
    """
    # 1. 외곽 크기
    if not isinstance(signed, dict):
        raise StateInvalid("FORMAT")
    try:
        outer_size = len(_compact(signed).encode("utf-8"))
    except (TypeError, ValueError, UnicodeEncodeError):
        raise StateInvalid("FORMAT") from None
    if outer_size > cfg.max_state_bytes:
        raise StateInvalid("SIZE")
    # 2. v · 문자열 형식
    if set(signed) != {"v", "kid", "payload", "signature"}:
        raise StateInvalid("FORMAT")
    version, kid, payload_b64, signature = (
        signed["v"],
        signed["kid"],
        signed["payload"],
        signed["signature"],
    )
    if isinstance(version, bool) or not isinstance(version, int):
        raise StateInvalid("FORMAT")
    if not all(isinstance(x, str) for x in (kid, payload_b64, signature)):
        raise StateInvalid("FORMAT")
    if version != STATE_V:
        raise StateInvalid("VERSION")
    if not _KID_RE.fullmatch(kid):
        raise StateInvalid("FORMAT")
    # 3. kid로 키 고르기 (지금 키 → 바로 전 키)
    key = None
    for candidate in (secret, previous_secret):
        if candidate is not None and kid_of(candidate) == kid:
            key = candidate
            break
    if key is None:
        raise StateInvalid("UNKNOWN_KID")
    # 4. 서명 (상수 시간 비교)
    try:
        expected = _mac(key, kid, payload_b64).encode("ascii")
        given = signature.encode("utf-8")
    except UnicodeEncodeError:
        raise StateInvalid("FORMAT") from None
    if not hmac.compare_digest(expected, given):
        raise StateInvalid("SIGNATURE")
    # 5~7. decode → UTF-8 → JSON (여기서부터 서명된 내용)
    raw = _strict_b64decode(payload_b64)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise StateInvalid("DECODE") from None
    obj = strict_json_loads(text, cfg.max_json_depth)
    # 8. decode 결과 크기
    if len(raw) > cfg.max_state_decoded_bytes:
        raise StateInvalid("DECODED_TOO_LARGE")
    # 9. 엄격한 스키마
    try:
        payload = StatePayload.model_validate(obj)
    except ValidationError:
        raise StateInvalid("SCHEMA") from None
    # 10. 만료
    if now_ms > payload.expires_at:
        raise StateInvalid("EXPIRED")
    # 11. 지원하는 실행 판 (feature, tool_schema)과 지금 프롬프트 판
    if (
        payload.versions.feature,
        payload.versions.tool_schema,
    ) not in SUPPORTED_RUN_VERSIONS or payload.versions.prompt != current_prompt_version:
        raise StateInvalid("UNSUPPORTED_RUN_VERSION")
    # 12. 요청 run과 같은 식별자
    for name in ("run_id", "conversation_id", "user_id", "state_revision"):
        if name not in expected_ids or getattr(payload, name) != expected_ids[name]:
            raise StateInvalid("ID_MISMATCH")
    return payload


def issue_times(
    now_ms: int, *, ask: bool, cfg: ChatAgentConfig = DEFAULT_CONFIG
) -> tuple[int, int]:
    """(issued_at, expires_at) ms. 발급 + 10분, `ASK` 승인 대기(확인 카드)로 멈춘 상태는 발급 + 35분(5-6)."""
    ttl = cfg.state_ttl_ask_seconds if ask else cfg.state_ttl_seconds
    return now_ms, now_ms + ttl * 1000
