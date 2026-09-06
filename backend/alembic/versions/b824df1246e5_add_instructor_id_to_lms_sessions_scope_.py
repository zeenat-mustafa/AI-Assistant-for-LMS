"""add instructor_id to lms_sessions, scope title uniqueness per instructor

Revision ID: b824df1246e5
Revises: a2ea273de1f2
Create Date: 2026-09-06 18:02:05.604866

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b824df1246e5'
down_revision: Union[str, None] = 'a2ea273de1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite can't ADD/DROP CONSTRAINT via plain ALTER TABLE — batch mode
    # rebuilds the table under the hood so the FK + unique constraint apply.
    with op.batch_alter_table('lms_sessions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('instructor_id', sa.Integer(), nullable=True))
        batch_op.drop_index('ix_lms_sessions_title')
        batch_op.create_index(batch_op.f('ix_lms_sessions_title'), ['title'], unique=False)
        batch_op.create_index(batch_op.f('ix_lms_sessions_instructor_id'), ['instructor_id'], unique=False)
        batch_op.create_unique_constraint('uq_lms_sessions_instructor_title', ['instructor_id', 'title'])
        batch_op.create_foreign_key(
            'fk_lms_sessions_instructor_id_users', 'users', ['instructor_id'], ['id'], ondelete='SET NULL'
        )

    # Backfill: at the time of writing there is exactly one instructor
    # (id=1, "Demo Instructor") in every known environment's data, and every
    # pre-existing session was created by them — assign existing rows to
    # that instructor rather than leaving them ownerless.
    op.execute(
        "UPDATE lms_sessions SET instructor_id = 1 WHERE instructor_id IS NULL "
        "AND EXISTS (SELECT 1 FROM users WHERE users.id = 1)"
    )


def downgrade() -> None:
    with op.batch_alter_table('lms_sessions', schema=None) as batch_op:
        batch_op.drop_constraint('fk_lms_sessions_instructor_id_users', type_='foreignkey')
        batch_op.drop_constraint('uq_lms_sessions_instructor_title', type_='unique')
        batch_op.drop_index(batch_op.f('ix_lms_sessions_instructor_id'))
        batch_op.drop_index(batch_op.f('ix_lms_sessions_title'))
        batch_op.create_index('ix_lms_sessions_title', ['title'], unique=True)
        batch_op.drop_column('instructor_id')
