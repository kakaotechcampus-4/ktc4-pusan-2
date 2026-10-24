import uuid
from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.core.database import get_db
from pitch_coach_backend.module import hooks
from pitch_coach_backend.module.auth.dependencies import CurrentUser
from pitch_coach_backend.module.pitch.exception import NonExistentPitch
from pitch_coach_backend.module.pitch.repository import PitchRepository
from pitch_coach_backend.module.pitch.script_parse_runner import ScriptParseRunner
from pitch_coach_backend.module.pitch.script_parser import HttpScriptParser

# 상태가 없다 — AI 를 부를 때마다 HTTP 클라이언트를, 저장할 때마다 DB 세션을 새로 연다.
# 테스트는 get_script_parse_runner 를 가짜 파서를 끼운 것으로 바꿔 끼운다


async def _plan_after_parse(pitch_id: uuid.UUID) -> None:
    # 첫 Take 의 코칭 계획은 대본 파싱 직후에 만든다 (coach-agent.md 5-1). 다른 계획 요청처럼
    # 백그라운드로 띄운다
    hooks.coaching().plan_next_take(pitch_id)


_script_parse_runner = ScriptParseRunner(
    HttpScriptParser(settings.ai_base_url), on_parsed=_plan_after_parse
)


def get_owned_pitch(
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    pitch_id: uuid.UUID,
) -> uuid.UUID:
    pitch = PitchRepository(db).get_owned(pitch_id, current_user.id)
    if pitch is None:
        raise NonExistentPitch()
    return pitch.id


OwnedPitch = Annotated[uuid.UUID, Depends(get_owned_pitch)]


def get_script_parse_runner() -> ScriptParseRunner:
    return _script_parse_runner


ScriptParseRunnerDep = Annotated[ScriptParseRunner, Depends(get_script_parse_runner)]
