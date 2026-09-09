"""add resource_files table

Revision ID: 4a13a49d0ad4
Revises: b824df1246e5
Create Date: 2026-09-09 18:30:47.833222

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4a13a49d0ad4'
down_revision: Union[str, None] = 'b824df1246e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A separate table for supporting/resource files (datasets, PDFs, slides)
    # uploaded alongside a session's assignment notebooks. Structurally
    # distinct from unsolved_files so resources can never enter grading paths.
    op.create_table(
        "resource_files",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("file_path", sa.String(length=500), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["session_id"], ["lms_sessions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_resource_files_id"), "resource_files", ["id"], unique=False
    )
    op.create_index(
        op.f("ix_resource_files_session_id"),
        "resource_files",
        ["session_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_resource_files_session_id"), table_name="resource_files")
    op.drop_index(op.f("ix_resource_files_id"), table_name="resource_files")
    op.drop_table("resource_files")
