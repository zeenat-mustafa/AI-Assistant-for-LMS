"""add embedded and embedding_error to lecture_chunks and unsolved_files

Revision ID: b0bae8c363e6
Revises: e15a0ce634e1
Create Date: 2026-09-12 21:51:25.577068

Phase 7.2 — tracks per-chunk / per-file embedding status into the shared
Chroma vector store, mirroring LectureFile's existing extracted/
extraction_error pattern. embedded defaults to False for every existing row:
this is the honest true state (nothing has been embedded yet before this
sub-feature), not a fabricated value — server_default is required because
SQLite rejects adding a NOT NULL column to a non-empty table without one.

NOTE: autogenerate also detected unrelated pre-existing drift (lms_sessions
title index/unique constraint, resource_files.uploaded_at nullability) —
deliberately excluded here, same as e15a0ce634e1. This migration is purely
additive and does not touch any Phase 1-5/7.1 model; that drift predates
this sub-feature and is out of scope.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b0bae8c363e6'
down_revision: Union[str, None] = 'e15a0ce634e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('lecture_chunks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('embedded', sa.Boolean(), server_default=sa.text('0'), nullable=False))
        batch_op.add_column(sa.Column('embedding_error', sa.Text(), nullable=True))

    with op.batch_alter_table('unsolved_files', schema=None) as batch_op:
        batch_op.add_column(sa.Column('embedded', sa.Boolean(), server_default=sa.text('0'), nullable=False))
        batch_op.add_column(sa.Column('embedding_error', sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('unsolved_files', schema=None) as batch_op:
        batch_op.drop_column('embedding_error')
        batch_op.drop_column('embedded')

    with op.batch_alter_table('lecture_chunks', schema=None) as batch_op:
        batch_op.drop_column('embedding_error')
        batch_op.drop_column('embedded')
