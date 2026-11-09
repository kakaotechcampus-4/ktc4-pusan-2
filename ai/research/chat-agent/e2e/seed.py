"""E2E 시드 — 사용자 · 피치 · Take(+코치 결과) 를 DB 에 넣고 쿠키 값을 만든다.

- 부르는 곳: `stack.py`(처음 한 번, `seed`), `tests/turn.e2e.ts`(테스트마다 `token`).
- 실행: BE 의 uv 환경에서, BE 와 같은 환경변수(DATABASE_URL 등)로 돈다.
  `uv run --project backend python ai/research/chat-agent/e2e/seed.py seed|token`
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


def seed() -> dict:
    from pitch_coach_backend import entities  # noqa: F401  (모든 테이블 등록)
    from pitch_coach_backend.module.pitch.entity import Pitch, PresentationVersion, ScriptVersion
    from pitch_coach_backend.module.take.entity import Take, TakeAIResults, TakeSlideEvent
    from pitch_coach_backend.module.user.entity import User

    fx = _fixtures()
    now = datetime.now(UTC)
    with _session() as db:
        user = User(
            email=EMAIL,
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
        pres = PresentationVersion(
            pitch_id=pitch.id, version=1, file_key=f"{pitch.id}/1.pdf", page_count=4
        )
        script = ScriptVersion(pitch_id=pitch.id, version=1, content="대본")
        db.add_all([pres, script])
        db.flush()
        take = Take(
            pitch_id=pitch.id,
            take_number=1,
            presentation_version_id=pres.id,
            script_version_id=script.id,
            mode="COACHING",
            script_mode="OFF",
            status="COMPLETED",
            goal_time_sec=240,
            started_at=now - timedelta(minutes=10),
            ended_at=now - timedelta(minutes=6),
            duration_sec=240,
            completed_at=now - timedelta(minutes=5),
            raw_data_consent=True,
            standard_version=None,
        )
        db.add(take)
        db.flush()
        db.add_all(
            TakeSlideEvent(take_id=take.id, started_ms=i * 60_000, slide_number=i + 1)
            for i in range(4)
        )
        # 3장 청중 시선이 낮은 Take (가짜 모델 답의 28% 와 맞춘다)
        result = fx.take_result(
            take_id=str(take.id),
            audience=[0.62, 0.58, 0.28, 0.6],
            script_ratio=[0.15, 0.2, 0.55, 0.18],
            issues=[("GAZE_ON_SCRIPT", 3, True)],
        )
        db.add(TakeAIResults(take_id=take.id, kind="TAKE_RESULT", payload=result, revision=1))
        db.commit()
        info = {
            "user_id": str(user.id),
            "pitch_id": str(pitch.id),
            "take_id": str(take.id),
            "pitch_title": pitch.title,
            "audience_ratio_slide3": 0.28,
            **mint_token(db, user.id),
        }
    return info


def token() -> dict:
    from sqlalchemy import select

    from pitch_coach_backend.module.user.entity import User

    with _session() as db:
        user_id = db.scalar(select(User.id).where(User.email == EMAIL))
        assert user_id, "seed 를 먼저 돌려야 한다"
        return mint_token(db, user_id)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "seed"
    print(json.dumps(seed() if cmd == "seed" else token()))
