"""footprint reads coverage geometry from vocabulary

Revision ID: 6162f8bcaab5
Revises: 508b998c3871
Create Date: 2026-09-01 02:21:12.264313

"""
from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op
from app.core.coverage_sql import CAMERA_FOOTPRINT


# revision identifiers, used by Alembic.
revision: str = '6162f8bcaab5'
down_revision: str | Sequence[str] | None = '508b998c3871'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # CREATE OR REPLACE, so this replays cleanly. The definition lives in
    # app/core/coverage_sql.py, which is the single source this migration and the
    # test fixture both install from.
    op.execute(CAMERA_FOOTPRINT)
    # Minimal INSERTs are the whole point of vocabulary-as-data; NOT NULL with no
    # default made an operator supply attributes just to add a camera type.
    op.execute("ALTER TABLE vocabulary_terms ALTER COLUMN attributes SET DEFAULT '{}'::jsonb")


def downgrade() -> None:
    # The function is replaceable and holds no data, so rolling back the schema
    # does not require restoring the previous body.
    op.execute("ALTER TABLE vocabulary_terms ALTER COLUMN attributes DROP DEFAULT")
