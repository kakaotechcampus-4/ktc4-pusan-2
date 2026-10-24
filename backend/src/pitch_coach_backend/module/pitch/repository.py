import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.orm import Session, defer

from pitch_coach_backend.module.pitch.entity import (
    CoachingPlan,
    Pitch,
    PresentationVersion,
    ScriptParseStatus,
    ScriptSlide,
    ScriptVersion,
    StandardAnalyses,
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

    def get_all_by_user(self, user_id: uuid.UUID, today: date) -> list[Pitch]:
        """홈 목록 순서. 다가오는 발표(오늘 포함) → 날짜 없는 발표 → 지난 발표.

        다가오는 발표는 가까운 날짜부터, 지난 발표는 최근에 지난 것부터다.
        같은 날짜면 최근에 만든 발표가 위다.
        """
        upcoming = Pitch.presentation_date >= today
        return self.db.execute(
            select(Pitch).where(Pitch.user_id == user_id)
            .order_by(
                case((upcoming, 0), (Pitch.presentation_date.is_(None), 1), else_=2),
                case((upcoming, Pitch.presentation_date)).asc(),
                Pitch.presentation_date.desc(),
                Pitch.created_at.desc(),
                Pitch.id,
            )
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
            .options(defer(Take.event_logs))
            .order_by(Take.pitch_id, Take.take_number)
        ).all()

    def save(self, pitch: Pitch) -> Pitch:
        self.db.add(pitch)
        self.db.flush()
        return pitch

    def get_take_ids(self, pitch_id: uuid.UUID) -> list[uuid.UUID]:
        return list(self.db.scalars(select(Take.id).where(Take.pitch_id == pitch_id)))

    def get_ids_by_user(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        return list(self.db.scalars(select(Pitch.id).where(Pitch.user_id == user_id)))

    def get_take_ids_by_user(self, user_id: uuid.UUID) -> list[uuid.UUID]:
        return list(
            self.db.scalars(
                select(Take.id)
                .join(Pitch, Pitch.id == Take.pitch_id)
                .where(Pitch.user_id == user_id)
            )
        )

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
            service.upload_presentation_service (next_presentation_version),
            service.add_pitch_standard_service (next_standard_version),
            take/service.create_take_service (next_take_number)
        pitch 안에서 max + 1 로 번호를 매기는 경로에 잠금을 걸면 위 목록도 같이 고친다.

        FOR UPDATE 가 아니라 FOR NO KEY UPDATE (key_share=True) 다. 자식 행 INSERT 는
        FK 검사로 pitch 행에 KEY SHARE 를 거는데, FOR UPDATE 는 그것과 충돌해 잠금을 쥔 동안
        같은 pitch 의 자식 행 쓰기(번호를 안 매기는 것까지)를 전부 멈춘다.
        NO KEY UPDATE 는 위 목록처럼 이 잠금을 잡는 요청끼리만 막는다.
        그래서 Take 생성은 같은 pitch 의 발표자료 업로드(S3 업로드 동안 잠금을 쥠)가
        끝날 때까지 기다린다.
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

    def next_standard_version(self, pitch_id: uuid.UUID) -> int:
        current = self.db.scalar(
            select(func.max(Standards.version)).where(Standards.pitch_id == pitch_id)
        )
        return (current or 0) + 1

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
            select(ScriptVersion)
            .where(ScriptVersion.pitch_id == pitch_id)
            .options(defer(ScriptVersion.content), defer(ScriptVersion.terms))
            .order_by(ScriptVersion.version.asc())
        ).all()

    def get_latest_presentation(
        self, pitch_id: uuid.UUID, page_count: int | None = None
    ) -> PresentationVersion | None:
        """가장 최근 발표자료. page_count 를 주면 그 쪽수인 것 중에서 고른다."""
        query = select(PresentationVersion).where(PresentationVersion.pitch_id == pitch_id)
        if page_count is not None:
            query = query.where(PresentationVersion.page_count == page_count)
        return self.db.scalar(query.order_by(PresentationVersion.version.desc()).limit(1))

    def get_latest_script(self, pitch_id: uuid.UUID) -> ScriptVersion | None:
        return self.db.scalar(
            select(ScriptVersion)
            .where(ScriptVersion.pitch_id == pitch_id)
            .options(defer(ScriptVersion.content), defer(ScriptVersion.terms))
            .order_by(ScriptVersion.version.desc())
            .limit(1)
        )

    def get_latest_parsed_script(self, pitch_id: uuid.UUID) -> ScriptVersion | None:
        """파싱이 끝난(DONE) 가장 최근 대본."""
        return self.db.scalar(
            select(ScriptVersion)
            .where(
                ScriptVersion.pitch_id == pitch_id,
                ScriptVersion.parse_status == ScriptParseStatus.DONE,
            )
            .options(defer(ScriptVersion.content), defer(ScriptVersion.terms))
            .order_by(ScriptVersion.version.desc())
            .limit(1)
        )

    def get_evaluations(self, pitch_id: uuid.UUID) -> list[Standards]:
        return self.db.scalars(
            select(Standards).where(Standards.pitch_id == pitch_id)
            .distinct(Standards.version)
            .order_by(Standards.version.asc())
        ).all()

    def get_standards_in_version(self, pitch_id: uuid.UUID, version: int) -> list[Standards]:
        return self.db.scalars(
            select(Standards).where(
                Standards.pitch_id == pitch_id,
                Standards.version == version
            )
            .order_by(Standards.position.asc())
        ).all()

    def get_standard_analysis(self, pitch_id: uuid.UUID, version: int) -> StandardAnalyses | None:
        return self.db.scalar(
            select(StandardAnalyses).where(
                StandardAnalyses.pitch_id == pitch_id,
                StandardAnalyses.version == version,
            )
        )

    def get_evaluations_by_version(
            self, pitch_id: uuid.UUID, version: int) -> list[Standards] | None:
        return self.db.scalars(
            select(Standards).where(
                Standards.pitch_id == pitch_id,
                Standards.version == version
            )
            .order_by(Standards.position.asc())
        ).all()

    def get_latest_standard_version(self, pitch_id: uuid.UUID) -> int | None:
        return self.db.scalar(
            select(func.max(Standards.version)).where(Standards.pitch_id == pitch_id)
        )

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

    def count_slides(self, script_version_id: uuid.UUID) -> int:
        return self.db.scalar(
            select(func.count()).where(ScriptSlide.script_version_id == script_version_id)
        ) or 0

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
        parse_meta: dict[str, Any] | None,
    ) -> bool:
        return self._finish_parse(
            script_version_id,
            requested_at,
            expired_before,
            parse_status=ScriptParseStatus.DONE,
            segmented=segmented,
            terms=terms,
            parse_meta=parse_meta,
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

    def get_latest_analysis_version(self, pitch_id: uuid.UUID) -> int | None:
        """분석까지 끝난 평가 기준의 가장 최근 버전. Take 를 시작할 때 그 Take 에 고정한다."""
        return self.db.scalar(
            select(func.max(StandardAnalyses.version)).where(StandardAnalyses.pitch_id == pitch_id)
        )

    def get_script_used(self, pitch_id: uuid.UUID, version: int) -> bool | None:
        return self.db.scalar(
            select(StandardAnalyses.script_used).where(
                StandardAnalyses.pitch_id == pitch_id, StandardAnalyses.version == version
            )
        )

    def get_script_version(self, script_version_id: uuid.UUID) -> ScriptVersion | None:
        return self.db.get(ScriptVersion, script_version_id)

    def get_chosen_coaching_plan(
        self,
        pitch_id: uuid.UUID,
        *,
        source_take_id: uuid.UUID | None,
        script_version_id: uuid.UUID | None,
        target_ms: int | None,
        min_ms: int | None,
        max_ms: int | None,
    ) -> CoachingPlan | None:
        """바탕 값이 모두 같은 계획 중 요청 시각이 가장 늦은 것 (coach-agent.md 12-2 계획 고르기).
        null 끼리 같다."""
        return self.db.scalar(
            select(CoachingPlan)
            .where(
                CoachingPlan.pitch_id == pitch_id,
                CoachingPlan.source_take_id.is_not_distinct_from(source_take_id),
                CoachingPlan.script_version_id.is_not_distinct_from(script_version_id),
                CoachingPlan.target_ms.is_not_distinct_from(target_ms),
                CoachingPlan.min_ms.is_not_distinct_from(min_ms),
                CoachingPlan.max_ms.is_not_distinct_from(max_ms),
            )
            .order_by(CoachingPlan.requested_at.desc(), CoachingPlan.id.desc())
            .limit(1)
        )

    def add_coaching_plan(self, plan: CoachingPlan) -> bool:
        """계획 행 하나를 더한다. 발표가 그 사이 지워졌으면 넣지 않고 False."""
        if self.db.scalar(select(Pitch.id).where(Pitch.id == plan.pitch_id)) is None:
            return False
        self.db.add(plan)
        self.db.flush()
        return True
