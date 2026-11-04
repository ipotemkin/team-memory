"""embedding_models.error + статус 'failed' (сохранение ошибок переиндексации).

Revision ID: 0003
Revises: 0002
"""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ADD VALUE в транзакции допустим с PG12+, если значение не используется в той же транзакции
    op.execute("ALTER TYPE app.embedding_model_status ADD VALUE IF NOT EXISTS 'failed'")
    op.execute("ALTER TABLE app.embedding_models ADD COLUMN IF NOT EXISTS error TEXT")


def downgrade() -> None:
    # значения enum удалить нельзя: возвращаем 'failed' в 'indexing' и удаляем колонку
    op.execute("UPDATE app.embedding_models SET status = 'indexing' WHERE status::text = 'failed'")
    op.execute("ALTER TABLE app.embedding_models DROP COLUMN IF EXISTS error")
