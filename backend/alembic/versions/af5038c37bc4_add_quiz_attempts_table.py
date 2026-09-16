"""add quiz_attempts table

Revision ID: af5038c37bc4
Revises: 9d9376585d4a
Create Date: 2026-09-13 15:11:19.588512

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'af5038c37bc4'
down_revision: Union[str, None] = '9d9376585d4a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('quiz_attempts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('student_id', sa.Integer(), nullable=False),
    sa.Column('scope_type', sa.String(length=32), nullable=False),
    sa.Column('scope_detail', sa.Text(), nullable=False),
    sa.Column('questions_json', sa.Text(), nullable=False),
    sa.Column('student_answers_json', sa.Text(), nullable=True),
    sa.Column('score', sa.Integer(), nullable=True),
    sa.Column('max_score', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['student_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_quiz_attempts_id'), 'quiz_attempts', ['id'], unique=False)
    op.create_index(op.f('ix_quiz_attempts_student_id'), 'quiz_attempts', ['student_id'], unique=False)
    # NOTE: autogenerate also detected the same unrelated pre-existing drift
    # 7.1 (e15a0ce634e1) and 7.4 (9d9376585d4a) excluded — lms_sessions title
    # index/unique constraint and resource_files.uploaded_at nullability.
    # Deliberately excluded here too: 7.6 is purely additive. No backfill: no
    # quiz feature has ever existed, so no pre-existing attempts can exist.


def downgrade() -> None:
    op.drop_index(op.f('ix_quiz_attempts_student_id'), table_name='quiz_attempts')
    op.drop_index(op.f('ix_quiz_attempts_id'), table_name='quiz_attempts')
    op.drop_table('quiz_attempts')
