"""add graded_by_instructor_id to grades

Revision ID: 605f94589b73
Revises: 1195b0afae4d
Create Date: 2026-09-10 20:56:38.186374

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '605f94589b73'
down_revision: Union[str, None] = '1195b0afae4d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable, SET NULL on delete — same pattern as LMSSession.instructor_id
    # (b824df1246e5). Nullable so pre-existing grades from before this column
    # existed correctly show no attribution rather than a fabricated one.
    with op.batch_alter_table('grades', schema=None) as batch_op:
        batch_op.add_column(sa.Column('graded_by_instructor_id', sa.Integer(), nullable=True))
        batch_op.create_index(
            batch_op.f('ix_grades_graded_by_instructor_id'), ['graded_by_instructor_id'], unique=False
        )
        batch_op.create_foreign_key(
            'fk_grades_graded_by_instructor_id_users', 'users', ['graded_by_instructor_id'], ['id'],
            ondelete='SET NULL',
        )


def downgrade() -> None:
    with op.batch_alter_table('grades', schema=None) as batch_op:
        batch_op.drop_constraint('fk_grades_graded_by_instructor_id_users', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_grades_graded_by_instructor_id'))
        batch_op.drop_column('graded_by_instructor_id')
