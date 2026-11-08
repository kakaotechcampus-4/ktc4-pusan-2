"""First Take 튜토리얼 샘플 발표의 미리 만든 AI 결과를 다시 만든다.

튜토리얼 샘플 발표(`module/pitch/service/tutorial.py`)는 AI 를 부르지 않고 이 결과를 복사해 만든다.
FE 의 샘플 대본 · PDF(`firstTakeMaterials.ts` · `firstTakePdf.ts`)가 바뀌면 다시 돌린다.
AI 응답 형식(평가 기준 · 코칭 계획)이 바뀌어도 다시 돌린다. 앱이 서빙하지 않는다.

실제 파이프라인을 그대로 돈다. 임시 사용자 · 발표 · 대본을 만들고 BE 코드로 대본 파싱 → 장별 평가
기준 → 첫 Take 코칭 계획을 부른 뒤 저장된 행을 읽어 쓴다. 끝나면 임시 사용자를 지운다 (CASCADE).

실행 (AI 서버를 로컬에 띄우고, 마이그레이션을 적용한 빈 DB 를 쓴다)
    DATABASE_URL=postgresql+psycopg://localhost:5432/<빈 DB> AI_BASE_URL=http://localhost:8001 \\
        uv run python dev/build-first-take-sample.py <PDF> <source.json>

source.json 은 FE 의 샘플 값이다.
    {"title": ..., "time_limit_sec": 180, "lower_deviation": 30, "upper_deviation": 60,
     "slides": ["1장 대본", ...]}

쓰는 파일 (`SAMPLE_DIR`): first_take.pdf · first_take.webp(첫 장 썸네일) · first_take.json
"""

import argparse
import asyncio
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select

from pitch_coach_backend.coach import planner
from pitch_coach_backend.coach.client import HttpCoachClient
from pitch_coach_backend.coach.store import DbCoachStore
from pitch_coach_backend.core.config import settings
from pitch_coach_backend.core.database import SessionLocal
from pitch_coach_backend.module.pitch.adapter.presentation_file import (
    count_pdf_pages,
    render_first_page,
)
from pitch_coach_backend.module.pitch.adapter.sample import SAMPLE_DIR
from pitch_coach_backend.module.pitch.adapter.script_parser import HttpScriptParser
from pitch_coach_backend.module.pitch.adapter.script_rubric import HttpRubricClient
from pitch_coach_backend.module.pitch.entity import (
    CoachingPlan,
    Pitch,
    ScriptParseStatus,
    ScriptRubricStatus,
    ScriptSlide,
    ScriptVersion,
)
from pitch_coach_backend.module.pitch.service.script import create_script_service
from pitch_coach_backend.module.pitch.service.script_parse_runner import ScriptParseRunner
from pitch_coach_backend.module.pitch.service.script_rubric_runner import ScriptRubricRunner
from pitch_coach_backend.module.user import service as user_service
from pitch_coach_backend.module.user.entity import User

# 평가 기준 meta.script_name 은 대본 버전 id 다. 임시 대본의 id 대신 이 값을 남기고, 샘플 발표를
# 만들 때 새 대본 버전 id 로 바꾼다
SCRIPT_NAME_PLACEHOLDER = "first-take-sample"


def join_script_blocks(blocks: list[str]) -> str:
    """장별 대본을 한 원문으로 잇는다. FE `joinScriptBlocks`(create/lib/draft.ts)와 같다."""
    return "\n\n".join(f"[슬라이드 {i}]\n{block}" for i, block in enumerate(blocks, start=1))


def _prepare(source: dict[str, Any]) -> tuple[uuid.UUID, uuid.UUID, Any]:
    with SessionLocal() as db:
        user = user_service.create(
            db, email=f"first-take-sample-{uuid.uuid4()}@example.invalid", name="샘플 생성"
        )
        pitch = Pitch(
            user_id=user.id,
            title=source["title"],
            time_limit_sec=source["time_limit_sec"],
            lower_deviation=source["lower_deviation"],
            upper_deviation=source["upper_deviation"],
        )
        db.add(pitch)
        db.commit()
        _, ticket = create_script_service(db, pitch.id, join_script_blocks(source["slides"]))
        return user.id, pitch.id, ticket


def _read(pitch_id: uuid.UUID, page_count: int, source: dict[str, Any]) -> dict[str, Any]:
    with SessionLocal() as db:
        script = db.scalars(select(ScriptVersion).where(ScriptVersion.pitch_id == pitch_id)).one()
        if script.parse_status != ScriptParseStatus.DONE or not script.segmented:
            raise SystemExit(f"대본 파싱 실패 {script.parse_status} {script.parse_error}")
        if script.rubric_status != ScriptRubricStatus.DONE:
            raise SystemExit(f"장별 평가 기준이 다 만들어지지 않았다 {script.rubric_status}")
        slides = db.scalars(
            select(ScriptSlide)
            .where(ScriptSlide.script_version_id == script.id)
            .order_by(ScriptSlide.slide_number)
        ).all()
        if [slide.slide_number for slide in slides] != list(range(1, page_count + 1)):
            raise SystemExit(f"장 번호가 PDF {page_count}쪽과 맞지 않다")
        plan = db.scalars(select(CoachingPlan).where(CoachingPlan.pitch_id == pitch_id)).first()
        if plan is None or plan.payload.get("fallback_reason") is not None:
            raise SystemExit("첫 Take 코칭 계획을 받지 못했다 (기본 계획이면 다시 돌린다)")

        return {
            "title": source["title"],
            "time_limit_sec": source["time_limit_sec"],
            "lower_deviation": source["lower_deviation"],
            "upper_deviation": source["upper_deviation"],
            "page_count": page_count,
            "script": {
                "content": script.content,
                "segmented": script.segmented,
                "terms": script.terms,
                "parse_meta": script.parse_meta,
            },
            "slides": [
                {
                    "slide_number": slide.slide_number,
                    "full_content": slide.full_content,
                    "highlights": slide.highlights,
                    "keywords": slide.keywords,
                    "script_chars": slide.script_chars,
                    "expected_ms": slide.expected_ms,
                    "rubric": {
                        **slide.rubric,
                        "meta": {**slide.rubric["meta"], "script_name": SCRIPT_NAME_PLACEHOLDER},
                    },
                    "rubric_meta": slide.rubric_meta,
                }
                for slide in slides
            ],
            "coaching_plan": {
                "target_ms": plan.target_ms,
                "min_ms": plan.min_ms,
                "max_ms": plan.max_ms,
                "payload": plan.payload,
                "meta": plan.meta,
            },
        }


def _cleanup(user_id: uuid.UUID) -> None:
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is not None:
            user_service.delete(db, user)


async def _generate(pitch_id: uuid.UUID, ticket: Any) -> None:
    runner = ScriptParseRunner(
        HttpScriptParser(settings.ai_base_url),
        rubric_runner=ScriptRubricRunner(HttpRubricClient(settings.ai_base_url)),
    )
    # 파싱 → 평가 기준까지 기다린다
    await runner.run(ticket)
    await planner.run(pitch_id, client=HttpCoachClient(settings.ai_base_url), store=DbCoachStore())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("pdf", type=Path, help="FE 가 만든 샘플 PDF")
    parser.add_argument("source", type=Path, help="FE 샘플 값 JSON (위 설명)")
    args = parser.parse_args()

    source = json.loads(args.source.read_text())
    with args.pdf.open("rb") as pdf:
        page_count = count_pdf_pages(pdf)
        thumbnail = render_first_page(pdf)
    if page_count != len(source["slides"]):
        raise SystemExit(f"PDF {page_count}쪽과 대본 {len(source['slides'])}장이 다르다")
    if thumbnail is None:
        raise SystemExit("PDF 첫 장 썸네일을 그리지 못했다")

    user_id, pitch_id, ticket = _prepare(source)
    try:
        asyncio.run(_generate(pitch_id, ticket))
        sample = _read(pitch_id, page_count, source)
    finally:
        _cleanup(user_id)

    shutil.copyfile(args.pdf, SAMPLE_DIR / "first_take.pdf")
    (SAMPLE_DIR / "first_take.webp").write_bytes(thumbnail)
    (SAMPLE_DIR / "first_take.json").write_text(
        json.dumps(sample, ensure_ascii=False, indent=2) + "\n"
    )
    print(f"{SAMPLE_DIR} 에 썼다: 장 {page_count}개")


if __name__ == "__main__":
    main()
