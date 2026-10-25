"""쪽수(page_count)가 비어 있는 발표자료의 PDF 를 S3 에서 받아 쪽수를 채운다.

page_count 칸이 생기기 전에 올린 발표자료용이다. 한 번 돌리면 되고, 다시 돌려도 비어 있는 행만 본다.
앱이 서빙하지 않는다.

실행
    uv run alembic upgrade head        # page_count 칸이 있어야 한다
    uv run python dev/backfill-presentation-page-count.py --dry-run   # 세어 보기만 한다
    uv run python dev/backfill-presentation-page-count.py

.env 의 DATABASE_URL · S3 설정을 쓴다. 운영 DB 에 돌릴 때는 그 값을 운영 것으로 두고 실행한다.
PDF 로 열리지 않는 파일은 비워 둔 채 id 를 찍는다. 그 발표자료로는 짝 검사를 하지 않는다.
"""

import argparse
import tempfile

from botocore.exceptions import BotoCoreError, ClientError
from sqlalchemy import select

from pitch_coach_backend.core.config import settings
from pitch_coach_backend.core.database import SessionLocal
from pitch_coach_backend.core.s3 import get_s3
from pitch_coach_backend.module.pitch.adapter.presentation_file import count_pdf_pages
from pitch_coach_backend.module.pitch.entity import PresentationVersion

# 이보다 큰 PDF 는 메모리 대신 임시 파일에 받는다
SPOOL_MAX_BYTES = 8 * 1024 * 1024


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="세기만 하고 저장하지 않는다")
    args = parser.parse_args()

    filled = failed = 0
    with SessionLocal() as db:
        rows = db.scalars(
            select(PresentationVersion)
            .where(PresentationVersion.page_count.is_(None))
            .order_by(PresentationVersion.created_at)
        ).all()
        print(f"쪽수가 비어 있는 발표자료 {len(rows)}개")

        for row in rows:
            with tempfile.SpooledTemporaryFile(max_size=SPOOL_MAX_BYTES) as pdf:
                try:
                    get_s3().download_fileobj(settings.s3_bucket_name, row.file_key, pdf)
                except (ClientError, BotoCoreError) as e:
                    print(f"  받지 못함 id={row.id} key={row.file_key} ({e})")
                    failed += 1
                    continue
                pdf.seek(0)
                page_count = count_pdf_pages(pdf)

            if page_count is None:
                print(f"  PDF 로 열리지 않음 id={row.id} key={row.file_key}")
                failed += 1
                continue
            print(f"  {page_count}쪽 id={row.id} key={row.file_key}")
            filled += 1
            if not args.dry_run:
                row.page_count = page_count
                # 한 행씩 커밋한다. 중간에 멈춰도 다시 돌리면 남은 행부터 한다
                db.commit()

    action = "셀 수 있음" if args.dry_run else "채움"
    print(f"{action} {filled}개, 실패 {failed}개")


if __name__ == "__main__":
    main()
