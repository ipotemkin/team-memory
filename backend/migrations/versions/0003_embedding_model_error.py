"""embedding_models.error + 'failed' status (persisted reindex failures)

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ADD VALUE inside a transaction is fine on PG12+ as long as the value is not used in the same transaction
    op.execute("ALTER TYPE app.embedding_model_status ADD VALUE IF NOT EXISTS 'failed'")
    op.execute("ALTER TABLE app.embedding_models ADD COLUMN IF NOT EXISTS error TEXT")


def downgrade() -> None:
    # enum values cannot be dropped; map 'failed' back to 'indexing' and drop the column
    op.execute("UPDATE app.embedding_models SET status = 'indexing' WHERE status::text = 'failed'")
    op.execute("ALTER TABLE app.embedding_models DROP COLUMN IF EXISTS error")
