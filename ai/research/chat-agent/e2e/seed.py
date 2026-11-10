"""E2E 시드 — 테스트마다 새 사용자 · 피치 · Take(+코치 결과) 를 DB 에 넣고 쿠키 값을 만든다.

- 부르는 곳: `tests/*.e2e.ts` 가 테스트마다 `new <종류>` 를 부른다(테스트끼리 데이터를 나누지 않는다).
  `sweep` 은 시나리오 6 이 자동 목표 확인을 바로 돌릴 때 부른다.
- 실행: BE 의 uv 환경에서, BE 와 같은 환경변수(DATABASE_URL 등)로 돈다.
  `uv run --project backend python ai/research/chat-agent/e2e/seed.py new base|review|goal`
  · `base`: Take 1(3장 시선 28%, 코치 결과만).  · `review`: base + 공식 리뷰(AI `/takes/review` 를 불러 받은 실제 모양).
  · `goal`: review + 열린 대화 목표(Take 1 기준) + 목표 뒤 Take 2(코치 결과 + 리뷰 DONE, 3장 시선 52%).
  · 실제 모델 시연(README '실제 모델 시연')용: `voice` — base + 더 최근에 연습한 피치 'abc'(Take 2 가 점검 때보다 1.4dB
    작고 3장에 작은 목소리 구간). `latest` — base + 다른 피치 '투자 설명회'에 2분 전 시작해 분석이 실패한 Take 하나
    (마지막 연습 시작은 더 늦지만 분석이 끝난 Take 0개). `scripts` — base + 대본 판 2로 한 Take 2(코치 결과, 대본 판이 달라 비교 불가).
- 읽기만 하는 것: BE 엔티티(`pitch_coach_backend`)와 서비스 시험 도우미 `fixtures.take_result`
  (모양이 실제 저장 payload 와 같다). 어느 쪽 파일도 고치지 않는다.
- 결과: 표준 출력에 JSON 한 줄 {user_id, pitch_id, take_id, refresh_token, csrf_token, ...}.
  리프레시 토큰은 쓰면 돌려 바뀌므로(재사용 탐지) 테스트마다 `token` 으로 새로 만든다.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import secrets
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
FIXTURES = ROOT / "ai" / "service" / "tests" / "unit" / "chat_agent" / "fixtures.py"
EMAIL = "e2e@example.com"


def _fixtures():
    spec = importlib.util.spec_from_file_location("chat_agent_fixtures", FIXTURES)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["chat_agent_fixtures"] = mod
    spec.loader.exec_module(mod)
    return mod


def _session():
    from pitch_coach_backend.core.database import SessionLocal

    return SessionLocal()


def mint_token(db, user_id: uuid.UUID) -> dict[str, str]:
    from pitch_coach_backend.core.config import settings
    from pitch_coach_backend.module.auth.entity import RefreshToken

    raw = secrets.token_urlsafe(48)
    now = datetime.now(UTC)
    db.add(
        RefreshToken(
            user_id=user_id,
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            device_id=uuid.uuid4(),
            expires_at=now + timedelta(days=settings.refresh_token_expire_days),
            session_expires_at=now + timedelta(days=settings.refresh_session_max_days),
        )
    )
    db.commit()
    return {"refresh_token": raw, "csrf_token": secrets.token_urlsafe(32)}




AI_BASE = "http://127.0.0.1:18001"
GAZE_BASE = [0.62, 0.58, 0.28, 0.6]  # 3장 청중 시선 28% — 가짜 모델 답의 28% 와 맞춘다
GAZE_LATER = [0.62, 0.58, 0.52, 0.6]  # 목표 뒤 Take 2: 3장 52%
GOAL_MIN_DELTA = 0.05  # AI 설정 goal_metrics.audience_ratio.min_delta 와 같다
AI_PYTHON = ROOT / "ai" / "research" / "chat-agent" / ".venv" / "bin" / "python"


def _goal_rule_version() -> str:
    """AI 설정이 지금 쓰는 목표 규칙 판(저장 때 목표에 고정되는 값). BE 환경에는 AI 코어가 없어 연구 venv 로 읽는다."""
    import subprocess

    code = "from pitch_coach_ai.features.chat_agent.config import DEFAULT_CONFIG as C; print(C.goal_rule_version())"
    return subprocess.run([str(AI_PYTHON), "-c", code], capture_output=True, text=True, check=True).stdout.strip()


def _add_take(db, pitch, pres, script, number: int, audience: list[float], *, ago_min: int, **result: object):
    """끝난 Take 하나 + 장 이벤트 + 코치 결과(TAKE_RESULT). `result`는 가상 코치 결과 인자를 바꾼다(예: voice_diff_db · issues)."""
    from pitch_coach_backend.module.take.entity import Take, TakeAIResults, TakeSlideEvent

    now = datetime.now(UTC)
    take = Take(
        pitch_id=pitch.id,
        take_number=number,
        presentation_version_id=pres.id,
        script_version_id=script.id,
        mode="COACHING",
        script_mode="OFF",
        status="COMPLETED",
        goal_time_sec=240,
        started_at=now - timedelta(minutes=ago_min + 5),
        ended_at=now - timedelta(minutes=ago_min + 1),
        duration_sec=240,
        completed_at=now - timedelta(minutes=ago_min),
        raw_data_consent=True,
        standard_version=None,
    )
    db.add(take)
    db.flush()
    db.add_all(
        TakeSlideEvent(take_id=take.id, started_ms=i * 60_000, slide_number=i + 1) for i in range(4)
    )
    kw: dict = {"script_ratio": [0.15, 0.2, 0.55, 0.18], "issues": [("GAZE_ON_SCRIPT", 3, True)], **result}
    result = _fixtures().take_result(take_id=str(take.id), audience=audience, **kw)
    db.add(TakeAIResults(take_id=take.id, kind="TAKE_RESULT", payload=result, revision=1))
    db.flush()
    return take


def _add_voice_pitch(db, user) -> dict:
    """가장 최근에 연습한 다른 피치 'abc'(Take 1 · 2). Take 2 는 점검 때보다 1.4dB 작게 말했고 3장에 작은 목소리 구간이 있다
    (실제 화면에서 본 헷갈리는 두 차례 대화의 재현, Issue #1194)."""
    from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion, ScriptVersion

    pitch = Pitch(user_id=user.id, title="abc", time_limit_sec=240)
    db.add(pitch)
    db.flush()
    pres = PresentationVersion(pitch_id=pitch.id, version=1, file_key=f"{pitch.id}/1.pdf", page_count=4)
    script = ScriptVersion(pitch_id=pitch.id, version=1, content="대본")
    db.add_all([pres, script])
    db.flush()
    _add_take(db, pitch, pres, script, 1, GAZE_BASE, ago_min=20, voice_diff_db=-0.6)
    t2 = _add_take(
        db, pitch, pres, script, 2, GAZE_BASE, ago_min=2, voice_diff_db=-1.4,
        issues=[("VOLUME_LOW", 3, True)],
    )  # fmt: skip
    db.flush()
    return {"voice_pitch_id": str(pitch.id), "voice_take_id": str(t2.id)}


def _add_unfinished_pitch(db, user) -> None:
    """다른 피치 '투자 설명회'와, 2분 전 시작해 분석이 실패한 Take 하나(결과 없음). 마지막 연습 시작은 이쪽이 더 늦다."""
    from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion, ScriptVersion
    from pitch_coach_backend.module.take.entity import Take

    now = datetime.now(UTC)
    pitch = Pitch(user_id=user.id, title="투자 설명회", time_limit_sec=300)
    db.add(pitch)
    db.flush()
    pres = PresentationVersion(pitch_id=pitch.id, version=1, file_key=f"{pitch.id}/1.pdf", page_count=4)
    script = ScriptVersion(pitch_id=pitch.id, version=1, content="대본")
    db.add_all([pres, script])
    db.flush()
    db.add(
        Take(
            pitch_id=pitch.id,
            take_number=1,
            presentation_version_id=pres.id,
            script_version_id=script.id,
            mode="COACHING",
            script_mode="OFF",
            status="FAILED",
            goal_time_sec=300,
            started_at=now - timedelta(minutes=2),
            raw_data_consent=True,
            standard_version=None,
        )
    )
    db.flush()


def _add_review(db, take) -> None:
    """공식 리뷰: BE 가 AI 에 보내는 요청을 그대로 만들어 실제 `/takes/review`(모델 없이 기본 결과)에서 받아 저장한다."""
    import httpx

    from pitch_coach_backend.module.take.repository import TakeRepository
    from pitch_coach_backend.module.take.service.review import build_review_request

    body = build_review_request(db, take.id).model_dump(mode="json")
    reply = httpx.post(f"{AI_BASE}/takes/review", json=body, timeout=30)
    reply.raise_for_status()
    data = reply.json()
    repo = TakeRepository(db)
    repo.save_ai_result(take.id, "REVIEW", {k: v for k, v in data.items() if k != "meta"}, data["meta"])
    repo.complete_take(take.id, "DONE")
    db.flush()


def new(kind: str) -> dict:
    """테스트 하나가 쓸 새 사용자 · 피치 · Take(+종류에 따라 리뷰 · 목표) 를 만들고 ids 와 쿠키 값을 돌려준다."""
    from pitch_coach_backend import entities  # noqa: F401  (모든 테이블 등록)
    from pitch_coach_backend.module.chat.entity import ChatConversation
    from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion, ScriptVersion
    from pitch_coach_backend.module.user.entity import User

    assert kind in ("base", "review", "goal", "latest", "scripts", "voice"), kind
    now = datetime.now(UTC)
    with _session() as db:
        user = User(
            email=f"e2e-{uuid.uuid4().hex[:12]}@example.com",
            name="E2E 사용자",
            raw_data_consent=True,
            test_consent_at=now,
            test_consent_version=1,
        )
        db.add(user)
        db.flush()
        pitch = Pitch(user_id=user.id, title="졸업 발표", time_limit_sec=240)
        db.add(pitch)
        db.flush()
        pres = PresentationVersion(pitch_id=pitch.id, version=1, file_key=f"{pitch.id}/1.pdf", page_count=4)
        script = ScriptVersion(pitch_id=pitch.id, version=1, content="대본")
        db.add_all([pres, script])
        db.flush()
        take1 = _add_take(db, pitch, pres, script, 1, GAZE_BASE, ago_min=30)
        db.commit()
        info: dict = {
            "user_id": str(user.id),
            "pitch_id": str(pitch.id),
            "take_id": str(take1.id),
            "pitch_title": pitch.title,
            "audience_ratio_slide3": 0.28,
        }
        if kind in ("review", "goal"):
            _add_review(db, take1)
            db.commit()
        if kind == "latest":
            _add_unfinished_pitch(db, user)
            db.commit()
        if kind == "voice":
            info.update(_add_voice_pitch(db, user))
            db.commit()
        if kind == "scripts":
            script2 = ScriptVersion(pitch_id=pitch.id, version=2, content="고친 대본")
            db.add(script2)
            db.flush()
            take2 = _add_take(db, pitch, pres, script2, 2, GAZE_LATER, ago_min=5)
            db.commit()
            info.update(take2_id=str(take2.id))
        if kind == "goal":
            take2 = _add_take(db, pitch, pres, script, 2, GAZE_LATER, ago_min=5)
            db.commit()
            _add_review(db, take2)
            db.commit()
            conversation = ChatConversation(
                user_id=user.id, scope_pitch_id=pitch.id, title="3장 시선 목표", goal_version=1
            )
            db.add(conversation)
            db.flush()
            info.update(conversation_id=str(conversation.id), take2_id=str(take2.id))
        info.update(mint_token(db, user.id))
        if kind == "goal":
            _add_goal(db, info, take1, script, pitch)
    return info


def _add_goal(db, info: dict, take1, script, pitch) -> None:
    """Take 1 기준 열린 목표(판 1). 모양은 BE `save_goal` 이 저장하는 것과 같다. Take 2 가 이 목표 뒤(first_take_number=2)다."""
    import uuid as _uuid

    from sqlalchemy import select

    from pitch_coach_backend.module.chat.entity import ChatGoal
    from pitch_coach_backend.module.take.entity import TakeAIResults

    result = db.scalar(select(TakeAIResults).where(TakeAIResults.take_id == take1.id, TakeAIResults.kind == "TAKE_RESULT"))
    goal = {
        "pitch_id": str(pitch.id),
        "metric": "audience_ratio",
        "slide_number": 3,
        "operator": "gt_base",
        "script_version_id": str(script.id),
        "standard_version": None,
        "goal_rule_version": _goal_rule_version(),
        "base": {
            "take_id": str(take1.id),
            "take_number": 1,
            "kind": "TAKE_RESULT",
            "revision": result.revision,
            "value": 0.28,
            "metric_rule_version": result.payload["criteria"]["GAZE"]["criteria_version"],
            "range": None,
        },
        "min_delta": GOAL_MIN_DELTA,
    }
    db.add(
        ChatGoal(
            conversation_id=_uuid.UUID(info["conversation_id"]),
            version=1,
            goal=goal,
            status="OPEN",
            base_take_id=take1.id,
            first_take_number=2,
        )
    )
    db.commit()


def sweep() -> dict:
    """자동 목표 확인을 지금 한 번 돌리고, 띄운 실행이 끝나기를 기다린다(BE 의 60초 주기를 기다리지 않으려는 것).

    BE 앱 안의 `goal_sweep.sweep_once()` 를 읽기만 하는 import 로 같은 환경변수 · 같은 DB 에서 부른다.
    """
    import asyncio

    from sqlalchemy import text

    from pitch_coach_backend.module.chat import goal_sweep

    async def go() -> dict:
        started = await goal_sweep.sweep_once()
        end = asyncio.get_running_loop().time() + 40
        while asyncio.get_running_loop().time() < end:
            with _session() as db:
                open_runs = db.execute(
                    text("select count(*) from chat_runs where first_trigger='TAKE_REVIEWED' and status in ('QUEUED','RUNNING')")
                ).scalar_one()
            if open_runs == 0:
                break
            await asyncio.sleep(0.5)
        return {"started": started}

    return asyncio.run(go())


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "new"
    if cmd == "new":
        print(json.dumps(new(sys.argv[2] if len(sys.argv) > 2 else "base")))
    elif cmd == "sweep":
        print(json.dumps(sweep()))
    else:
        raise SystemExit(f"모르는 명령: {cmd}")
