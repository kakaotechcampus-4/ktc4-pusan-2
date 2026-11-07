"""지워진 피치 · Take 의 id 를 기록하고 정리 작업이 그 id 로 행을 찾을 색인을 만든다.

Revision ID: c81300000009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic_git_revisions import get_down_revision

revision: str = "c81300000009"
down_revision: str | Sequence[str] | None = get_down_revision(revision)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB_INDEXES = (
    ("ix_chat_messages_source_pitch_ids", "chat_messages", ["source_pitch_ids"]),
    ("ix_chat_messages_source_take_ids", "chat_messages", ["source_take_ids"]),
    ("ix_chat_runs_read_pitches", "chat_runs", [sa.text("(read_sources -> 'pitches')")]),
    ("ix_chat_runs_read_takes", "chat_runs", [sa.text("(read_sources -> 'takes')")]),
    ("ix_chat_actions_source_take_ids", "chat_actions", ["source_take_ids"]),
    ("ix_chat_summaries_source_pitch_ids", "chat_summaries", ["source_pitch_ids"]),
    ("ix_chat_summaries_source_take_ids", "chat_summaries", ["source_take_ids"]),
)

CREATE_TRIGGERS = """
CREATE FUNCTION chat_record_deleted_source() RETURNS trigger AS $$
BEGIN
    INSERT INTO chat_deleted_sources (source_id) VALUES (OLD.id) ON CONFLICT DO NOTHING;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER chat_record_deleted_pitch AFTER DELETE ON pitches
    FOR EACH ROW EXECUTE FUNCTION chat_record_deleted_source();
CREATE TRIGGER chat_record_deleted_take AFTER DELETE ON takes
    FOR EACH ROW EXECUTE FUNCTION chat_record_deleted_source();
"""

# 정리 작업은 근거 칸을 문자열로 찾는다. AI 가 낸 문장에서 온 대문자 · 하이픈 없는 UUID 를 표준
# 형식으로 맞춘다. 행동 근거와 읽은 근거는 BE 가 만든 값이라 이미 표준 형식이다
UUID_TEXT = "[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}"
CANONICAL_UUID_TEXT = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
SOURCE_COLUMNS = (
    ("chat_messages", "source_pitch_ids"),
    ("chat_messages", "source_take_ids"),
    ("chat_summaries", "source_pitch_ids"),
    ("chat_summaries", "source_take_ids"),
)
NORMALIZE = """
UPDATE {table} SET {column} = (
    SELECT jsonb_agg(
        CASE WHEN value ~* '^{uuid}$' THEN value::uuid::text ELSE value END ORDER BY ordinality
    )
    FROM jsonb_array_elements_text({column}) WITH ORDINALITY AS item(value, ordinality)
)
WHERE jsonb_typeof({column}) = 'array' AND jsonb_array_length({column}) > 0
    AND EXISTS (
        SELECT 1 FROM jsonb_array_elements_text({column}) AS item(value)
        WHERE value ~* '^{uuid}$' AND value !~ '^{canonical}$'
    )
"""

# 트리거를 걸기 전에 지워져 아직 정리되지 않은 근거를 채운다. 저장 경로는 형식이 틀린 id 를
# 받지 않는다. 옛 정리 작업이 이미 가린 행이라 형식 검사로 걸러도 놓치는 답이 없다
BACKFILL = """
INSERT INTO chat_deleted_sources (source_id)
SELECT DISTINCT referenced.id::uuid
FROM (
    SELECT jsonb_array_elements_text(source_pitch_ids) AS id FROM chat_messages
        WHERE jsonb_typeof(source_pitch_ids) = 'array'
    UNION ALL
    SELECT jsonb_array_elements_text(source_take_ids) FROM chat_messages
        WHERE jsonb_typeof(source_take_ids) = 'array'
    UNION ALL
    SELECT jsonb_array_elements_text(source_pitch_ids) FROM chat_summaries
        WHERE jsonb_typeof(source_pitch_ids) = 'array'
    UNION ALL
    SELECT jsonb_array_elements_text(source_take_ids) FROM chat_summaries
        WHERE jsonb_typeof(source_take_ids) = 'array'
    UNION ALL
    SELECT jsonb_array_elements_text(source_take_ids) FROM chat_actions
        WHERE jsonb_typeof(source_take_ids) = 'array'
    UNION ALL
    SELECT jsonb_array_elements_text(read_sources -> 'pitches') FROM chat_runs
        WHERE jsonb_typeof(read_sources -> 'pitches') = 'array'
    UNION ALL
    SELECT jsonb_array_elements_text(read_sources -> 'takes') FROM chat_runs
        WHERE jsonb_typeof(read_sources -> 'takes') = 'array'
    UNION ALL
    SELECT base_take_id::text FROM chat_goals WHERE base_take_id IS NOT NULL
) AS referenced
WHERE referenced.id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    AND NOT EXISTS (SELECT 1 FROM pitches WHERE pitches.id::text = referenced.id)
    AND NOT EXISTS (SELECT 1 FROM takes WHERE takes.id::text = referenced.id)
"""


def upgrade() -> None:
    op.create_table(
        "chat_deleted_sources",
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column(
            "deleted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("source_id"),
    )
    op.execute(CREATE_TRIGGERS)
    for table, column in SOURCE_COLUMNS:
        op.execute(
            NORMALIZE.format(
                table=table, column=column, uuid=UUID_TEXT, canonical=CANONICAL_UUID_TEXT
            )
        )
    op.execute(BACKFILL)
    for name, table, columns in JSONB_INDEXES:
        op.create_index(name, table, columns, postgresql_using="gin")


def downgrade() -> None:
    for name, table, _ in JSONB_INDEXES:
        op.drop_index(name, table_name=table, postgresql_using="gin")
    op.execute("DROP TRIGGER chat_record_deleted_take ON takes")
    op.execute("DROP TRIGGER chat_record_deleted_pitch ON pitches")
    op.execute("DROP FUNCTION chat_record_deleted_source()")
    op.drop_table("chat_deleted_sources")
