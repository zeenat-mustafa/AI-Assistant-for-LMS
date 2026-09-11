"""add summary column to grades

Revision ID: 9816e8e0fb88
Revises: 4bd3acedefb5
Create Date: 2026-09-12 02:24:34.601467

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9816e8e0fb88'
down_revision: Union[str, None] = '4bd3acedefb5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable: historical grades predate this field and never get one
    # fabricated for them — display falls back to the existing rationale/
    # feedback_text rendering instead. SQLite can't ADD a column with the
    # rest of an existing table's DDL cleanly via plain ALTER TABLE for some
    # backends, so batch mode is used here for consistency with every other
    # migration in this project, even though a bare ADD COLUMN would work.
    with op.batch_alter_table('grades', schema=None) as batch_op:
        batch_op.add_column(sa.Column('summary', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('grades', schema=None) as batch_op:
        batch_op.drop_column('summary')
