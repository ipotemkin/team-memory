"""init: расширение pgvector и схема app.

Revision ID: 0001
Revises:
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE SCHEMA IF NOT EXISTS app")


def downgrade() -> None:
    op.execute("DROP SCHEMA IF EXISTS app CASCADE")
    op.execute("DROP EXTENSION IF EXISTS vector")
