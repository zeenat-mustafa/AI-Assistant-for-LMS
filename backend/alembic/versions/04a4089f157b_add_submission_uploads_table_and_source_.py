"""add submission_uploads table and source_upload_id link

Revision ID: 04a4089f157b
Revises: 1e98ad501108
Create Date: 2026-09-11 12:57:38.796647

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '04a4089f157b'
down_revision: Union[str, None] = '1e98ad501108'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Submission-side rework: one row per actual student upload event (the
    # exact file they submitted), independent of whatever SubmissionFile rows
    # extraction produces from it. Mirrors assignment_uploads (7e713f3d6233).
    op.create_table(
        'submission_uploads',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('submission_id', sa.Integer(), nullable=False),
        sa.Column('original_filename', sa.String(length=255), nullable=False),
        sa.Column('content_type', sa.String(length=255), nullable=True),
        sa.Column('file_path', sa.String(length=500), nullable=False),
        sa.Column('uploaded_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['submission_id'], ['submissions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_submission_uploads_id'), 'submission_uploads', ['id'], unique=False)
    op.create_index(
        op.f('ix_submission_uploads_submission_id'), 'submission_uploads', ['submission_id'], unique=False
    )

    # Provenance link, nullable (existing rows predate this feature) — used
    # solely so deleting a SubmissionUpload cascades to what it produced.
    # SQLite can't ADD a FK via plain ALTER TABLE — batch mode rebuilds the
    # table under the hood, same pattern as 7e713f3d6233.
    with op.batch_alter_table('submission_files', schema=None) as batch_op:
        batch_op.add_column(sa.Column('source_upload_id', sa.Integer(), nullable=True))
        batch_op.create_index(
            batch_op.f('ix_submission_files_source_upload_id'), ['source_upload_id'], unique=False
        )
        batch_op.create_foreign_key(
            'fk_submission_files_source_upload_id_submission_uploads',
            'submission_uploads', ['source_upload_id'], ['id'], ondelete='CASCADE',
        )


def downgrade() -> None:
    with op.batch_alter_table('submission_files', schema=None) as batch_op:
        batch_op.drop_constraint(
            'fk_submission_files_source_upload_id_submission_uploads', type_='foreignkey'
        )
        batch_op.drop_index(batch_op.f('ix_submission_files_source_upload_id'))
        batch_op.drop_column('source_upload_id')

    op.drop_index(op.f('ix_submission_uploads_submission_id'), table_name='submission_uploads')
    op.drop_index(op.f('ix_submission_uploads_id'), table_name='submission_uploads')
    op.drop_table('submission_uploads')
