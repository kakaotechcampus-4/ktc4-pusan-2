"""서명된 실행 상태 시험 (기능 문서 5-6 · 7-3 L1-26 · L1-27 · L1-28 · L1-33).

- 시험 이름의 `test_fm_L1_xx_`는 7-3 실패 행 ID다(10-3).
- 키는 고정값 `b"k" * 32`를 쓴다. 시각은 모두 인자로 넘기므로 시계에 기대지 않는다.
"""

from __future__ import annotations

import base64
import json

import pytest
from pydantic import ValidationError

from chat_agent.config import load_config
from chat_agent.state import (
    RunVersions,
    StateInvalid,
    StatePayload,
    StateTooLarge,
    _b64e,
    _mac,
    issue_times,
    json_depth_ok,
    kid_of,
    sign,
    strict_json_loads,
    verify,
)
from chat_agent.version import FEATURE_VERSION, TOOL_SCHEMA_VERSION

SECRET = b"k" * 32
OTHER = b"o" * 32
NOW = 1_000_000
CFG = load_config()
IDS = {"run_id": "r1", "conversation_id": "c1", "user_id": "u1", "state_revision": 3}


def make_payload(**kw) -> StatePayload:
    base = {
        **IDS,
        "scope_pitch_id": "p1",
        "trigger": "USER_MESSAGE",
        "original_user_message": "목표 잡아 줘",
        "versions": RunVersions(
            feature=FEATURE_VERSION, prompt="p-1", tool_schema=TOOL_SCHEMA_VERSION, corpus="c-1"
        ),
        "issued_at": NOW,
        "expires_at": NOW + 600_000,
        "turn_count": 1,
        "loop": {"messages": []},
    }
    base.update(kw)
    return StatePayload(**base)


def resign(raw_payload: bytes | str, secret: bytes = SECRET, *, v: int = 1) -> dict:
    """payload 문자열을 그대로 서명한다(변조된 payload도 서명이 맞게 만들어 decode 단계까지 보내려고)"""
    payload_b64 = raw_payload if isinstance(raw_payload, str) else _b64e(raw_payload)
    kid = kid_of(secret)
    return {"v": v, "kid": kid, "payload": payload_b64, "signature": _mac(secret, kid, payload_b64)}


PROMPT = "p-1"


def check(
    signed, *, now_ms=NOW, previous=None, ids=IDS, cfg=CFG, secret=SECRET, prompt=PROMPT
) -> StatePayload:
    return verify(
        signed,
        secret=secret,
        previous_secret=previous,
        now_ms=now_ms,
        current_prompt_version=prompt,
        expected_ids=ids,
        cfg=cfg,
    )


def reason_of(signed, **kw) -> str:
    with pytest.raises(StateInvalid) as exc:
        check(signed, **kw)
    return exc.value.reason_code


def good() -> dict:
    return sign(make_payload(), SECRET, CFG)


def payload_obj() -> dict:
    return make_payload().model_dump(mode="json")


def compact(obj) -> bytes:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# ── 기본 왕복 ──────────────────────────────────────────────────────────────


def test_roundtrip_and_kid():
    signed = good()
    assert signed["v"] == 1 and len(signed["kid"]) == 8 and signed["kid"] == kid_of(SECRET)
    assert "=" not in signed["payload"] + signed["signature"]
    out = check(signed)
    assert out == make_payload()


def test_kid_of_differs_per_key():
    assert kid_of(SECRET) != kid_of(OTHER)


def test_sign_rejects_short_secret():
    with pytest.raises(ValueError):
        sign(make_payload(), b"short", CFG)


def test_payload_top_level_strict():
    with pytest.raises(ValidationError):
        StatePayload(**{**make_payload().model_dump(), "extra": 1})


# ── L1-27 변조 ─────────────────────────────────────────────────────────────


def test_fm_L1_27_tamper_payload_byte():
    signed = good()
    p = signed["payload"]
    signed["payload"] = p[:-2] + ("A" if p[-2] != "A" else "B") + p[-1]
    assert reason_of(signed) == "SIGNATURE"


def test_fm_L1_27_tamper_signature():
    signed = good()
    s = signed["signature"]
    signed["signature"] = ("A" if s[0] != "A" else "B") + s[1:]
    assert reason_of(signed) == "SIGNATURE"


def test_fm_L1_27_signature_non_ascii_does_not_crash():
    signed = good()
    signed["signature"] = "한글" * 20
    assert reason_of(signed) == "SIGNATURE"


def test_fm_L1_27_kid_swap_to_previous_key_name_fails():
    # kid 를 이전 키 이름으로 바꿔도 서명 입력에 kid 가 들어 있어 통과하지 못한다
    signed = good()
    signed["kid"] = kid_of(OTHER)
    assert reason_of(signed, previous=OTHER) == "SIGNATURE"


@pytest.mark.parametrize(
    ("field", "value"),
    [("run_id", "r2"), ("conversation_id", "c2"), ("user_id", "u2"), ("state_revision", 4)],
)
def test_fm_L1_27_id_mismatch(field, value):
    assert reason_of(good(), ids={**IDS, field: value}) == "ID_MISMATCH"


def test_fm_L1_27_request_scope_old_value_is_allowed():
    # 범위는 payload 안의 값을 믿는다: expected_ids 에 scope 가 없어도 통과, payload 범위가 쓰인다
    out = check(sign(make_payload(scope_pitch_id="new"), SECRET, CFG))
    assert out.scope_pitch_id == "new"


# ── L1-28 해석 ─────────────────────────────────────────────────────────────


def test_fm_L1_28_bad_padding():
    raw = compact(payload_obj())
    padded = base64.urlsafe_b64encode(raw).decode("ascii")
    if not padded.endswith("="):
        padded += "=="
    assert reason_of(resign(padded)) == "DECODE"


def test_fm_L1_28_non_canonical_base64_trailing_bits():
    raw = compact(payload_obj())
    enc = _b64e(raw)
    # 길이가 4n+2 이면 마지막 글자의 남는 4비트가 0 이어야 정규형이다. 값을 바꿔 비정규형을 만든다
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    if len(enc) % 4 == 2 or len(enc) % 4 == 3:
        bumped = enc[:-1] + alphabet[(alphabet.index(enc[-1]) + 1) % 64]
    else:
        raw = raw + b" "  # 4n+2 / 4n+3 길이가 되게 바이트를 하나 늘린다
        enc = _b64e(raw)
        bumped = enc[:-1] + alphabet[(alphabet.index(enc[-1]) + 1) % 64]
    assert bumped != enc
    assert reason_of(resign(bumped)) == "DECODE"


def test_fm_L1_28_non_alphabet_char():
    assert reason_of(resign("ab+/cd")) == "DECODE"


def test_fm_L1_28_length_mod4_is_1():
    assert reason_of(resign("abcde")) == "DECODE"


def test_fm_L1_28_invalid_utf8():
    assert reason_of(resign(b"\xff\xfe\xfa")) == "DECODE"


def test_fm_L1_28_not_json():
    assert reason_of(resign(b"{not json")) == "DECODE"


def test_fm_L1_28_nan_constant():
    assert reason_of(resign(b'{"a": NaN}')) == "DECODE"


def test_fm_L1_28_duplicate_keys():
    text = json.dumps(payload_obj(), separators=(",", ":"))
    dup = text[:-1] + ',"run_id":"other"}'
    assert reason_of(resign(dup.encode())) == "DUPLICATE_KEY"


def test_fm_L1_28_version_not_1():
    signed = good()
    signed["v"] = 2
    assert reason_of(signed) == "VERSION"


@pytest.mark.parametrize("bad", [True, "1", 1.0, None])
def test_fm_L1_28_version_wrong_type_is_format(bad):
    signed = good()
    signed["v"] = bad
    assert reason_of(signed) == "FORMAT"


def test_fm_L1_28_format_errors():
    base = good()
    assert reason_of("nope") == "FORMAT"
    assert reason_of({k: v for k, v in base.items() if k != "kid"}) == "FORMAT"
    assert reason_of({**base, "extra": 1}) == "FORMAT"
    assert reason_of({**base, "payload": 5}) == "FORMAT"
    assert reason_of({**base, "kid": "short"}) == "FORMAT"
    assert reason_of({**base, "payload": {1, 2}}) == "FORMAT"


def test_fm_L1_28_missing_field_is_schema_error():
    obj = payload_obj()
    del obj["turn_count"]
    assert reason_of(resign(compact(obj))) == "SCHEMA"


def test_fm_L1_28_extra_top_level_field_is_schema_error():
    obj = payload_obj()
    obj["surprise"] = 1
    assert reason_of(resign(compact(obj))) == "SCHEMA"


def test_fm_L1_28_wrong_field_type_is_schema_error():
    obj = payload_obj()
    obj["turn_count"] = "1"
    assert reason_of(resign(compact(obj))) == "SCHEMA"


def nested(depth_inside_loop: int) -> dict:
    """payload(깊이 1) 안의 loop(깊이 2)에 더 중첩해 전체 깊이를 맞춘다"""
    inner: dict | list = {}
    for _ in range(depth_inside_loop):
        inner = {"x": inner}
    obj = payload_obj()
    obj["loop"] = {"deep": inner}
    return obj


def total_depth(obj) -> int:
    lo, hi = 1, 200
    while lo < hi:
        mid = (lo + hi) // 2
        if json_depth_ok(obj, mid):
            hi = mid
        else:
            lo = mid + 1
    return lo


def test_fm_L1_28_depth_boundary_32_ok_33_rejected():
    # 전체 깊이 32 는 통과, 33 은 DEPTH
    obj = nested(0)
    base = total_depth(obj)
    ok = nested(32 - base)
    too_deep = nested(33 - base)
    assert total_depth(ok) == 32 and total_depth(too_deep) == 33
    assert check(resign(compact(ok))).loop is not None
    assert reason_of(resign(compact(too_deep))) == "DEPTH"


def test_fm_L1_28_extreme_depth_does_not_crash():
    text = ("[" * 100_000 + "]" * 100_000).encode()
    assert reason_of(resign(text)) == "DEPTH"


def test_json_depth_ok_basic():
    assert json_depth_ok(1, 0)
    assert json_depth_ok({}, 1) and not json_depth_ok({}, 0)
    assert json_depth_ok([[1]], 2) and not json_depth_ok([[1]], 1)


def test_strict_json_loads_rejects_duplicates_and_depth():
    with pytest.raises(StateInvalid) as e:
        strict_json_loads('{"a":1,"a":2}')
    assert e.value.reason_code == "DUPLICATE_KEY"
    with pytest.raises(StateInvalid) as e:
        strict_json_loads("[[[1]]]", max_depth=2)
    assert e.value.reason_code == "DEPTH"
    assert strict_json_loads('{"a":[1,2]}') == {"a": [1, 2]}


def padded_payload(total_bytes: int) -> bytes:
    """decode 결과가 정확히 total_bytes 인 payload JSON"""
    obj = payload_obj()
    obj["loop"] = {"pad": ""}
    size = len(compact(obj))
    obj["loop"]["pad"] = "a" * (total_bytes - size)
    raw = compact(obj)
    assert len(raw) == total_bytes
    return raw


def test_fm_L1_28_decoded_size_boundary():
    # 외곽 한도는 넉넉히 키워 decode 한도(768 KiB)만 본다
    cfg = load_config(max_state_bytes=4 * 1024 * 1024)
    limit = cfg.max_state_decoded_bytes
    assert limit == 768 * 1024
    assert check(resign(padded_payload(limit)), cfg=cfg).loop["pad"]
    assert reason_of(resign(padded_payload(limit + 1)), cfg=cfg) == "DECODED_TOO_LARGE"


def test_fm_L1_28_order_signature_before_payload_fields():
    # 서명이 틀리면 payload 가 깨져 있어도 SIGNATURE 다(검증 전 payload 를 읽지 않는다)
    signed = resign(b"{not json")
    signed["signature"] = "A" * 43
    assert reason_of(signed) == "SIGNATURE"


# ── L1-26 크기 ─────────────────────────────────────────────────────────────


def outer_size(signed: dict) -> int:
    return len(json.dumps(signed, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def test_fm_L1_26_state_size_limit_boundary():
    # base64 는 3바이트 단위로 커지므로 한도를 "지금 크기"에 맞춰 ±1 바이트를 본다.
    # 실제 한도는 1,048,576바이트(1 MiB)다.
    assert CFG.max_state_bytes == 1_048_576
    obj = payload_obj()
    obj["loop"] = {"pad": "a" * 500}
    payload = StatePayload.model_validate(obj)
    signed = sign(payload, SECRET, CFG)
    size = outer_size(signed)
    exact = load_config(max_state_bytes=size)
    assert sign(payload, SECRET, exact) == signed
    assert check(signed, cfg=exact).loop["pad"]
    under = load_config(max_state_bytes=size - 1)
    with pytest.raises(StateTooLarge):
        sign(payload, SECRET, under)
    assert reason_of(signed, cfg=under) == "SIZE"


def _sign_with_pad(n: int, cfg):
    obj = payload_obj()
    obj["loop"] = {"pad": "a" * n}
    return sign(StatePayload.model_validate(obj), SECRET, cfg)


def test_fm_L1_26_real_limit_largest_fitting_payload_and_next_step_fails():
    # 실제 한도(1,048,576바이트)에서, 들어가는 가장 큰 payload와 그 다음 1바이트 키운 payload를 찾는다.
    # base64는 3바이트 단위로 커지므로 다음 한 걸음이 같은 크기일 수 있어 "처음 넘는 지점"을 이분 탐색한다.
    limit = CFG.max_state_bytes
    assert limit == 1_048_576

    def fits(n: int) -> bool:
        try:
            _sign_with_pad(n, CFG)
        except StateTooLarge:
            return False
        return True

    lo, hi = 0, limit  # fits(lo) True, fits(hi) False
    while hi - lo > 1:
        mid = (lo + hi) // 2
        lo, hi = (mid, hi) if fits(mid) else (lo, mid)
    signed = _sign_with_pad(lo, CFG)
    size = outer_size(signed)
    assert size <= limit
    assert check(signed).loop["pad"] == "a" * lo
    with pytest.raises(StateTooLarge):
        _sign_with_pad(lo + 1, CFG)
    # 한도 안에서 가장 큰 크기와 한도의 차이는 base64 한 묶음(≤ 4바이트) 안이다
    assert limit - size < 4


def test_fm_L1_26_korean_text_counts_utf8_bytes():
    # 한글은 3바이트: 글자 수가 아니라 UTF-8 바이트로 잰다
    cfg = load_config(max_state_bytes=2000)
    obj = payload_obj()
    obj["loop"] = {"pad": "가" * 600}
    with pytest.raises(StateTooLarge):
        sign(StatePayload.model_validate(obj), SECRET, cfg)


def test_fm_L1_26_sign_rejects_over_decoded_limit():
    cfg = load_config(max_state_bytes=4 * 1024 * 1024)
    obj = payload_obj()
    obj["loop"] = {"pad": "a" * (cfg.max_state_decoded_bytes + 1)}
    with pytest.raises(StateTooLarge):
        sign(StatePayload.model_validate(obj), SECRET, cfg)


# ── L1-33 배포 호환 ────────────────────────────────────────────────────────


def test_fm_L1_33_issue_times_10min_and_ask_35min():
    assert issue_times(NOW, ask=False, cfg=CFG) == (NOW, NOW + 600_000)
    assert issue_times(NOW, ask=True, cfg=CFG) == (NOW, NOW + 2_100_000)


@pytest.mark.parametrize("ask", [False, True])
def test_fm_L1_33_expiry_boundary(ask):
    issued, expires = issue_times(NOW, ask=ask, cfg=CFG)
    signed = sign(make_payload(issued_at=issued, expires_at=expires), SECRET, CFG)
    assert check(signed, now_ms=expires - 1).run_id == "r1"
    assert check(signed, now_ms=expires).run_id == "r1"  # 만료 시각 자체는 유효
    assert reason_of(signed, now_ms=expires + 1) == "EXPIRED"


def test_fm_L1_33_previous_key_verifies_but_new_signatures_use_current():
    old = sign(make_payload(), OTHER, CFG)
    # 지금 키는 SECRET, 바로 전 키는 OTHER → 검증된다
    assert check(old, previous=OTHER).run_id == "r1"
    # 이전 키가 없으면 모르는 kid
    assert reason_of(old) == "UNKNOWN_KID"
    # 새 서명은 지금 키로만: kid 가 지금 키 이름이다
    assert sign(make_payload(), SECRET, CFG)["kid"] == kid_of(SECRET)


def test_fm_L1_33_previous_key_signature_still_checked():
    old = sign(make_payload(), OTHER, CFG)
    old["signature"] = "A" * 43
    assert reason_of(old, previous=OTHER) == "SIGNATURE"


def test_fm_L1_33_unknown_kid():
    signed = sign(make_payload(), b"z" * 32, CFG)
    assert reason_of(signed, previous=OTHER) == "UNKNOWN_KID"


def test_fm_L1_33_unsupported_versions():
    for versions in (
        RunVersions(
            feature="chat_agent-0.0.1", prompt="p", tool_schema=TOOL_SCHEMA_VERSION, corpus="c"
        ),
        RunVersions(feature=FEATURE_VERSION, prompt="p", tool_schema="tools-0", corpus="c"),
    ):
        assert (
            reason_of(sign(make_payload(versions=versions), SECRET, CFG))
            == "UNSUPPORTED_RUN_VERSION"
        )


def test_fm_L1_33_prompt_version_mismatch_is_unsupported():
    # 옛 프롬프트는 불러올 수 없으므로 프롬프트 판이 다르면 거절한다
    versions = RunVersions(
        feature=FEATURE_VERSION, prompt="old", tool_schema=TOOL_SCHEMA_VERSION, corpus="c-1"
    )
    signed = sign(make_payload(versions=versions), SECRET, CFG)
    assert reason_of(signed) == "UNSUPPORTED_RUN_VERSION"
    assert check(signed, prompt="old").versions.prompt == "old"


def test_fm_L1_33_corpus_version_mismatch_passes_verify():
    # 자료 판이 달라도 verify는 통과시킨다(loop가 검색 캐시를 버리고 자료 조언을 거절한다, 6-10)
    versions = RunVersions(
        feature=FEATURE_VERSION, prompt=PROMPT, tool_schema=TOOL_SCHEMA_VERSION, corpus="old"
    )
    assert check(sign(make_payload(versions=versions), SECRET, CFG)).versions.corpus == "old"


def test_error_message_has_no_payload_content():
    signed = sign(make_payload(original_user_message="비밀 문장"), SECRET, CFG)
    signed["signature"] = "A" * 43
    with pytest.raises(StateInvalid) as e:
        check(signed)
    assert str(e.value) == "SIGNATURE" and "비밀" not in repr(e.value)
