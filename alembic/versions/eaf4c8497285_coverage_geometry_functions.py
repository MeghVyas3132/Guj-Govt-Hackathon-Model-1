"""coverage geometry functions

Revision ID: eaf4c8497285
Revises: 8aa586d412ef
Create Date: 2026-08-31 23:05:03.126934

"""
from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op

from app.core.coverage_sql import COVERAGE_FUNCTIONS, DROP_COVERAGE_FUNCTIONS

# revision identifiers, used by Alembic.
revision: str = 'eaf4c8497285'
down_revision: str | Sequence[str] | None = '8aa586d412ef'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # gen_random_uuid(), which the coverage engine uses to key inserted cells, is built
    # into PostgreSQL 13+ and verified present on the 16.4 server this targets, so no
    # pgcrypto extension is required.
    for statement in COVERAGE_FUNCTIONS:
        op.execute(statement)


def downgrade() -> None:
    """Downgrade schema."""
    for statement in DROP_COVERAGE_FUNCTIONS:
        op.execute(statement)
