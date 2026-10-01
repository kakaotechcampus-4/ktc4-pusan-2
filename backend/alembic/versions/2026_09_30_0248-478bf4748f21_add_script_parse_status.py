"""add script parse status and store script content in db

Revision ID: 478bf4748f21
Revises: 11215228fecd
Create Date: 2026-09-30 02:48:02.506690

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '478bf4748f21'
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # 대본 원문을 S3 대신 DB 에 둔다. 기존 행은 원문이 S3 파일로만 있어 비워 둔다.
    # file_key 는 지운다 — 새 대본은 S3 에 올리지 않고, 옛 파일 위치는 경로 규칙
    # (pitches/{pitch_id}/scripts/{version}.<확장자>) 으로 찾을 수 있어 따로 남길 필요가 없다
    op.add_column('script_versions', sa.Column('content', sa.Text(), nullable=True))
    op.drop_column('script_versions', 'file_key')
    op.add_column('script_versions', sa.Column('parse_status', sa.String(length=16), server_default='PENDING', nullable=False))
    op.add_column('script_versions', sa.Column('parse_requested_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False))
    op.add_column('script_versions', sa.Column('segmented', sa.Boolean(), nullable=True))
    op.add_column('script_versions', sa.Column('terms', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column('script_versions', sa.Column('parse_error', sa.String(length=32), nullable=True))
    op.create_check_constraint(
        'ck_script_versions_parse_status',
        'script_versions',
        "parse_status IN ('PENDING', 'DONE', 'FAILED')",
    )
    # 이 기능 전에 올라온 대본은 슬라이드도 DB 원문도 없다. PENDING 으로 두면 FE 가 "분석 중"을
    # 보다가 만료로 넘어가 원인이 틀리게 보이므로, 처음부터 FAILED 로 두고 다시 올리게 안내한다
    op.execute(
        "UPDATE script_versions SET parse_status = 'FAILED', parse_error = 'LEGACY_UNPARSED'"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('ck_script_versions_parse_status', 'script_versions', type_='check')
    op.drop_column('script_versions', 'parse_error')
    op.drop_column('script_versions', 'terms')
    op.drop_column('script_versions', 'segmented')
    op.drop_column('script_versions', 'parse_requested_at')
    op.drop_column('script_versions', 'parse_status')
    # 지운 file_key 를 되살린다. 값은 복구할 수 없어 빈 문자열로 채운다
    # (원문도 content 와 함께 사라진다 — 되돌리기는 개발 DB 에서만 쓴다)
    op.add_column('script_versions', sa.Column('file_key', sa.String(length=255), server_default='', nullable=False))
    op.alter_column('script_versions', 'file_key', server_default=None)
    op.drop_column('script_versions', 'content')
