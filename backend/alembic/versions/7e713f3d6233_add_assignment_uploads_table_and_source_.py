"""add assignment_uploads table and source_upload_id links

Revision ID: 7e713f3d6233
Revises: 4a13a49d0ad4
Create Date: 2026-09-10 14:28:03.462744

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7e713f3d6233'
down_revision: Union[str, None] = '4a13a49d0ad4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # bugfix-original-upload-preservation: one row per actual assignment
    # upload event (the exact file the instructor submitted), independent of
    # whatever UnsolvedFile/ResourceFile rows extraction produces from it.
    op.create_table(
        'assignment_uploads',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.Integer(), nullable=False),
        sa.Column('original_filename', sa.String(length=255), nullable=False),
        sa.Column('content_type', sa.String(length=255), nullable=True),
        sa.Column('file_path', sa.String(length=500), nullable=False),
        sa.Column('uploaded_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['lms_sessions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_assignment_uploads_id'), 'assignment_uploads', ['id'], unique=False)
    op.create_index(
        op.f('ix_assignment_uploads_session_id'), 'assignment_uploads', ['session_id'], unique=False
    )

    # Provenance links, nullable (existing rows predate this feature) — used
    # solely so deleting an AssignmentUpload cascades to what it produced.
    # SQLite can't ADD a FK via plain ALTER TABLE — batch mode rebuilds the
    # table under the hood, same pattern as b824df1246e5.
    with op.batch_alter_table('unsolved_files', schema=None) as batch_op:
        batch_op.add_column(sa.Column('source_upload_id', sa.Integer(), nullable=True))
        batch_op.create_index(
            batch_op.f('ix_unsolved_files_source_upload_id'), ['source_upload_id'], unique=False
        )
        batch_op.create_foreign_key(
            'fk_unsolved_files_source_upload_id_assignment_uploads',
            'assignment_uploads', ['source_upload_id'], ['id'], ondelete='CASCADE',
        )

    with op.batch_alter_table('resource_files', schema=None) as batch_op:
        batch_op.add_column(sa.Column('source_upload_id', sa.Integer(), nullable=True))
        batch_op.create_index(
            batch_op.f('ix_resource_files_source_upload_id'), ['source_upload_id'], unique=False
        )
        batch_op.create_foreign_key(
            'fk_resource_files_source_upload_id_assignment_uploads',
            'assignment_uploads', ['source_upload_id'], ['id'], ondelete='CASCADE',
        )


def downgrade() -> None:
    with op.batch_alter_table('resource_files', schema=None) as batch_op:
        batch_op.drop_constraint(
            'fk_resource_files_source_upload_id_assignment_uploads', type_='foreignkey'
        )
        batch_op.drop_index(batch_op.f('ix_resource_files_source_upload_id'))
        batch_op.drop_column('source_upload_id')

    with op.batch_alter_table('unsolved_files', schema=None) as batch_op:
        batch_op.drop_constraint(
            'fk_unsolved_files_source_upload_id_assignment_uploads', type_='foreignkey'
        )
        batch_op.drop_index(batch_op.f('ix_unsolved_files_source_upload_id'))
        batch_op.drop_column('source_upload_id')

    op.drop_index(op.f('ix_assignment_uploads_session_id'), table_name='assignment_uploads')
    op.drop_index(op.f('ix_assignment_uploads_id'), table_name='assignment_uploads')
    op.drop_table('assignment_uploads')
