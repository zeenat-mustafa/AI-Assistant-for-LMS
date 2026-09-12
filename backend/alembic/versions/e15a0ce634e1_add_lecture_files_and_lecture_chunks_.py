"""add lecture_files and lecture_chunks tables

Revision ID: e15a0ce634e1
Revises: 9816e8e0fb88
Create Date: 2026-09-12 12:01:56.009715

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e15a0ce634e1'
down_revision: Union[str, None] = '9816e8e0fb88'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('lecture_files',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('session_id', sa.Integer(), nullable=False),
    sa.Column('instructor_id', sa.Integer(), nullable=True),
    sa.Column('original_filename', sa.String(length=255), nullable=False),
    sa.Column('content_type', sa.String(length=255), nullable=True),
    sa.Column('file_path', sa.String(length=500), nullable=False),
    sa.Column('extracted', sa.Boolean(), nullable=False),
    sa.Column('extraction_error', sa.Text(), nullable=True),
    sa.Column('uploaded_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['instructor_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['session_id'], ['lms_sessions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_lecture_files_id'), 'lecture_files', ['id'], unique=False)
    op.create_index(op.f('ix_lecture_files_instructor_id'), 'lecture_files', ['instructor_id'], unique=False)
    op.create_index(op.f('ix_lecture_files_session_id'), 'lecture_files', ['session_id'], unique=False)
    op.create_table('lecture_chunks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('lecture_file_id', sa.Integer(), nullable=False),
    sa.Column('slide_number', sa.Integer(), nullable=False),
    sa.Column('source', sa.Enum('slide_text', 'notes', name='chunksource'), nullable=False),
    sa.Column('chunk_index', sa.Integer(), nullable=False),
    sa.Column('chunk_text', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['lecture_file_id'], ['lecture_files.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_lecture_chunks_id'), 'lecture_chunks', ['id'], unique=False)
    op.create_index(op.f('ix_lecture_chunks_lecture_file_id'), 'lecture_chunks', ['lecture_file_id'], unique=False)
    op.create_index(op.f('ix_lecture_chunks_slide_number'), 'lecture_chunks', ['slide_number'], unique=False)
    # NOTE: autogenerate also detected unrelated pre-existing drift between
    # the models and this dev DB (lms_sessions title index/unique constraint,
    # resource_files.uploaded_at nullability) — deliberately excluded here.
    # Phase 7.1 is purely additive and does not touch any Phase 1-5 model or
    # migration; that drift predates this sub-feature and is out of scope.


def downgrade() -> None:
    op.drop_index(op.f('ix_lecture_chunks_slide_number'), table_name='lecture_chunks')
    op.drop_index(op.f('ix_lecture_chunks_lecture_file_id'), table_name='lecture_chunks')
    op.drop_index(op.f('ix_lecture_chunks_id'), table_name='lecture_chunks')
    op.drop_table('lecture_chunks')
    op.drop_index(op.f('ix_lecture_files_session_id'), table_name='lecture_files')
    op.drop_index(op.f('ix_lecture_files_instructor_id'), table_name='lecture_files')
    op.drop_index(op.f('ix_lecture_files_id'), table_name='lecture_files')
    op.drop_table('lecture_files')
