import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session

from pitch_coach_backend.module.pitch.entity import (
    Pitch,
    PresentationVersion,
    ScriptParseStatus,
    ScriptSlide,
    ScriptVersion,
    Standards,
)
from pitch_coach_backend.module.take.entity import Take, TakeSummary


class PitchRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, pitch_id: uuid.UUID) -> Pitch | None:
        return self.db.get(Pitch, pitch_id)

    def get_owned(self, pitch_id: uuid.UUID, user_id: uuid.UUID) -> Pitch | None:
        return self.db.scalar(
            select(Pitch).where(Pitch.id == pitch_id, Pitch.user_id == user_id)
        )

    def get_all_by_user(self, user_id: uuid.UUID) -> list[Pitch]:
        return self.db.execute(
            select(Pitch).where(Pitch.user_id == user_id)
            .order_by(Pitch.presentation_date.desc()) # 우선 마감일이 빠른 순서대로 정렬
        ).scalars().all()

    def get_takes_with_scores_in_pitch(self, pitch_id: uuid.UUID) -> list[tuple[Take, int | None]]:
        return self.db.execute(
            select(Take, TakeSummary.score)
            .outerjoin(TakeSummary, TakeSummary.take_id == Take.id)
            .where(Take.pitch_id == pitch_id)
            ).all()
    
    def get_takes_with_scores_in_pitches(
        self, pitch_ids: list[uuid.UUID]
    ) -> list[tuple[Take, int | None]]:
        if not pitch_ids:
            return []
        return self.db.execute(
            select(Take, TakeSummary.score)
            .outerjoin(TakeSummary, TakeSummary.take_id == Take.id)
            .where(Take.pitch_id.in_(pitch_ids))
            .order_by(Take.pitch_id, Take.take_number)
        ).all()

    def save(self, pitch: Pitch) -> Pitch:
        self.db.add(pitch)
        self.db.flush()
        return pitch

    def delete(self, pitch: Pitch) -> None:
        self.db.delete(pitch)
        self.db.flush()

    def lock_for_new_version(self, pitch_id: uuid.UUID) -> None:
        """이 pitch 에 새 버전을 만드는 요청을 한 줄로 세운다 (트랜잭션이 끝날 때까지 잠금).

        버전은 max(version) + 1 로 계산한다. 같은 pitch 에 요청 둘이 동시에 오면 둘 다 같은
        번호를 받아 unique(pitch_id, version) 에 걸리고 하나가 500 이 된다. pitch 행을 잠그면
        두 번째 요청은 첫 번째가 커밋할 때까지 기다렸다가 다음 번호를 받는다.

        보장 범위는 이 잠금을 먼저 잡는 경로까지다. 같은 번호를 매기는 경로가 잠금을 건너뛰면
        그 경로끼리는 여전히 같은 번호를 받을 수 있다.
          - 잡는 곳: service.create_script_service (next_script_version),
            service.upload_presentation_service (next_presentation_version)
          - 아직 안 잡는 곳: take/service.create_take_service (next_take_number)
        pitch 안에서 max + 1 로 번호를 매기는 경로에 잠금을 걸면 위 목록도 같이 고친다.

        FOR UPDATE 가 아니라 FOR NO KEY UPDATE (key_share=True) 다. 자식 행 INSERT(Take 생성 등)는
        FK 검사로 pitch 행에 KEY SHARE 를 거는데, FOR UPDATE 는 그것과 충돌해 잠금을 쥔 동안
        같은 pitch 의 Take 생성까지 멈춘다. NO KEY UPDATE 는 버전을 만드는 요청끼리만 막는다.
        """
        # key_share=True 는 이름과 달리 KEY SHARE 가 아니라 FOR NO KEY UPDATE 로 나간다
        # (KEY SHARE 는 read=True 를 같이 줄 때). tests/test_script_parse.py 가 확인한다
        self.db.execute(
            select(Pitch.id).where(Pitch.id == pitch_id).with_for_update(key_share=True)
        )

    def next_presentation_version(self, pitch_id: uuid.UUID) -> int:
        current = self.db.scalar(
            select(func.max(PresentationVersion.version)).where(
                PresentationVersion.pitch_id == pitch_id
            )
        )
        return (current or 0) + 1

    def save_presentation(self, presentation_version: PresentationVersion) -> PresentationVersion:
        self.db.add(presentation_version)
        self.db.flush()
        return presentation_version

    def next_script_version(self, pitch_id: uuid.UUID) -> int:
        current = self.db.scalar(
            select(func.max(ScriptVersion.version)).where(ScriptVersion.pitch_id == pitch_id)
        )
        return (current or 0) + 1

    def save_script(self, script_version: ScriptVersion) -> ScriptVersion:
        self.db.add(script_version)
        self.db.flush()
        return script_version

    def get_presentation_detail(
        self, pitch_id: uuid.UUID, presentation_version_id: uuid.UUID
    ) -> PresentationVersion | None:
        return self.db.scalar(
            select(PresentationVersion).where(
                PresentationVersion.pitch_id == pitch_id,
                PresentationVersion.id == presentation_version_id
            )
        )

    def save_standard(self, standard: Standards) -> Standards:
        self.db.add(standard)
        self.db.flush()
        return standard
 
    def get_presentations(self, pitch_id: uuid.UUID) -> list[PresentationVersion]:
        return self.db.scalars(
            select(PresentationVersion).where(PresentationVersion.pitch_id == pitch_id)
            .order_by(PresentationVersion.version.asc())
        ).all()

    def get_scripts(self, pitch_id: uuid.UUID) -> list[ScriptVersion]:
        return self.db.scalars(
            select(ScriptVersion).where(ScriptVersion.pitch_id == pitch_id)
            .order_by(ScriptVersion.version.asc())
        ).all()

    def get_evaluations(self, pitch_id: uuid.UUID) -> list[Standards]:
        return self.db.scalars(
            select(Standards).where(Standards.pitch_id == pitch_id)
            .order_by(Standards.version.asc())
        ).all()

    def get_evaluations_by_version(
            self, pitch_id: uuid.UUID, version: int) -> list[Standards] | None:
        return self.db.scalars(
            select(Standards).where(
                Standards.pitch_id == pitch_id,
                Standards.version == version
            )
            .order_by(Standards.position.asc())
        ).all()

    # ── 대본 파싱 ────────────────────────────────────────────────────
    # 상태 전이는 전부 조건부 UPDATE 한 번으로 한다. "읽고 → 검사하고 → 쓰기" 로 나누면
    # 그 사이에 다른 요청(재시도 버튼 두 번)이나 늦게 끝난 옛 작업이 끼어들 수 있다.
    # 조건을 WHERE 에 넣으면 DB 가 행 잠금으로 줄을 세워 주고, 바뀐 행 수로 이겼는지 안다.

    def get_script_in_pitch(
        self, pitch_id: uuid.UUID, script_version_id: uuid.UUID
    ) -> ScriptVersion | None:
        return self.db.scalar(
            select(ScriptVersion).where(
                ScriptVersion.id == script_version_id, ScriptVersion.pitch_id == pitch_id
            )
        )

    def get_slides(self, script_version_id: uuid.UUID) -> list[ScriptSlide]:
        return self.db.scalars(
            select(ScriptSlide)
            .where(ScriptSlide.script_version_id == script_version_id)
            .order_by(ScriptSlide.slide_number)
        ).all()

    def restart_parse(
        self, script_version_id: uuid.UUID, now: datetime, expired_before: datetime
    ) -> bool:
        """FAILED 이거나 만료된 PENDING 이면 새 PENDING 으로 바꾼다. 바꿨으면 True."""
        result = self.db.execute(
            update(ScriptVersion)
            .where(
                ScriptVersion.id == script_version_id,
                or_(
                    ScriptVersion.parse_status == ScriptParseStatus.FAILED,
                    and_(
                        ScriptVersion.parse_status == ScriptParseStatus.PENDING,
                        ScriptVersion.parse_requested_at < expired_before,
                    ),
                ),
            )
            .values(
                parse_status=ScriptParseStatus.PENDING,
                parse_requested_at=now,
                parse_error=None,
            )
        )
        return result.rowcount == 1

    def mark_parse_done(
        self,
        script_version_id: uuid.UUID,
        requested_at: datetime,
        *,
        expired_before: datetime,
        segmented: bool,
        terms: list[str],
    ) -> bool:
        return self._finish_parse(
            script_version_id,
            requested_at,
            expired_before,
            parse_status=ScriptParseStatus.DONE,
            segmented=segmented,
            terms=terms,
            parse_error=None,
        )

    def mark_parse_failed(
        self,
        script_version_id: uuid.UUID,
        requested_at: datetime,
        error_code: str,
        *,
        expired_before: datetime,
    ) -> bool:
        return self._finish_parse(
            script_version_id,
            requested_at,
            expired_before,
            parse_status=ScriptParseStatus.FAILED,
            parse_error=error_code,
        )

    def _finish_parse(
        self,
        script_version_id: uuid.UUID,
        requested_at: datetime,
        expired_before: datetime,
        **values: Any,
    ) -> bool:
        """이 작업(requested_at)이 아직 유효한 PENDING 일 때만 결과를 쓴다. 썼으면 True.

        유효하지 않은 경우는 셋이고 모두 0행이 바뀌어 False 다.
          - 재시도로 parse_requested_at 이 바뀜 (옛 작업)
          - 이미 DONE/FAILED
          - 만료됨. FE 는 이미 FAILED(PARSE_EXPIRED) 를 봤으니 뒤늦게 DONE 으로 바꾸지 않는다 —
            FAILED → DONE 으로 뒤집히면 재시도 버튼이 409 를 받는다
        """
        result = self.db.execute(
            update(ScriptVersion)
            .where(
                ScriptVersion.id == script_version_id,
                ScriptVersion.parse_status == ScriptParseStatus.PENDING,
                ScriptVersion.parse_requested_at == requested_at,
                ScriptVersion.parse_requested_at >= expired_before,
            )
            .values(**values)
        )
        return result.rowcount == 1

    def add_slides(self, slides: list[ScriptSlide]) -> None:
        self.db.add_all(slides)
        self.db.flush()
