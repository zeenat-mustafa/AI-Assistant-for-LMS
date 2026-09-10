"""make session title uniqueness global, not per-instructor

Revision ID: 1195b0afae4d
Revises: 7e713f3d6233
Create Date: 2026-09-10 20:56:00.271775

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1195b0afae4d'
down_revision: Union[str, None] = '7e713f3d6233'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Instructor access is a shared faculty workspace (see README): two
    # instructors having identically-titled sessions is exactly the
    # ambiguity /chat instructions can't resolve, so a session title must
    # now be unique across every instructor, not just within one. The dev
    # DB was checked directly beforehand and has zero duplicate titles —
    # if a real environment somehow does, this migration fails loudly
    # (IntegrityError) rather than silently dropping/renaming anything.
    #
    # SQLite can't ADD/DROP CONSTRAINT via plain ALTER TABLE — batch mode
    # rebuilds the table under the hood, same pattern as b824df1246e5.
    with op.batch_alter_table('lms_sessions', schema=None) as batch_op:
        batch_op.drop_constraint('uq_lms_sessions_instructor_title', type_='unique')
        batch_op.drop_index('ix_lms_sessions_title')
        batch_op.create_index(batch_op.f('ix_lms_sessions_title'), ['title'], unique=True)


def downgrade() -> None:
    with op.batch_alter_table('lms_sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_lms_sessions_title'))
        batch_op.create_index('ix_lms_sessions_title', ['title'], unique=False)
        batch_op.create_unique_constraint(
            'uq_lms_sessions_instructor_title', ['instructor_id', 'title']
        )
