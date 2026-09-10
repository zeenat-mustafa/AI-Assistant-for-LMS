"""backfill assignment_uploads for pre-migration files

Revision ID: 1e98ad501108
Revises: 605f94589b73
Create Date: 2026-09-11 03:15:59.921769

Data migration, not a schema change (see 7e713f3d6233's own upgrade comment:
"existing rows predate this feature"). Every UnsolvedFile/ResourceFile row
with source_upload_id IS NULL was created before the assignment_uploads
table existed, so the "Assignment files" UI panel -- which reads
SessionRead.assignment_uploads exclusively -- shows nothing for them even
though the real files are still on disk and grading (which reads
unsolved_files, not assignment_uploads) was never affected.

Grouping decision: ONE assignment_uploads row per pre-existing row (1:1),
not one row per original upload batch. Neither unsolved_files nor
resource_files records any batch/zip provenance for rows predating this
column, so there is no reliable signal to reconstruct "these N files came
from one .zip" versus "these were N individually-selected files uploaded
in one multi-file form submission" -- and even today, an ordinary
(non-zip) multi-file upload already produces one assignment_uploads row
per file, never one shared row. 1:1 is therefore not just the safe
default, it's what the current system would itself have produced for a
non-zip multi-file upload of the same files.

Field provenance (nothing fabricated):
    session_id / original_filename / file_path  -- copied verbatim from
        the source row; existence on disk is verified before any row is
        written (fails loudly, per-row, rather than silently skipping or
        pointing a new row at a file that isn't really there).
    uploaded_at  -- copied verbatim from the source row's own real
        uploaded_at column (both tables have had this since they were
        created; it is not reconstructed from filesystem mtimes).
    content_type  -- left NULL. The original HTTP request's Content-Type
        header was never recorded anywhere for these rows, and guessing
        one from the file extension would be exactly the kind of
        plausible-but-invented value this migration is explicit about
        avoiding. The column is nullable for precisely this reason.

downgrade() reverses cleanly: it re-nulls source_upload_id on every row
this migration linked (identifiable by it), THEN deletes the
assignment_uploads rows it created (content_type IS NULL is a safe marker
-- the one pre-existing real upload, session 6's INSTRUCTOR.zip, has a
real content_type). Nulling the link first matters even though SQLite's
FK enforcement is off in Alembic's own connection (env.py builds its own
engine, never touching database.py's PRAGMA foreign_keys=ON listener):
deleting straight through the ON DELETE CASCADE from
unsolved_files/resource_files to assignment_uploads would be silently
correct here but relies on that pragma being off, which is not something
a migration should depend on.
"""
from pathlib import Path
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1e98ad501108'
down_revision: Union[str, None] = '605f94589b73'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Lightweight, metadata-free table handles -- deliberately not importing the
# real ORM models, so this migration keeps working unchanged even if those
# models are refactored later.
assignment_uploads = sa.table(
    'assignment_uploads',
    sa.column('id', sa.Integer),
    sa.column('session_id', sa.Integer),
    sa.column('original_filename', sa.String),
    sa.column('content_type', sa.String),
    sa.column('file_path', sa.String),
    sa.column('uploaded_at', sa.DateTime),
)

SOURCE_TABLES = ('unsolved_files', 'resource_files')


def _source_table(name: str):
    return sa.table(
        name,
        sa.column('id', sa.Integer),
        sa.column('session_id', sa.Integer),
        sa.column('original_filename', sa.String),
        sa.column('file_path', sa.String),
        sa.column('uploaded_at', sa.DateTime),
        sa.column('source_upload_id', sa.Integer),
    )


def _storage_root() -> Path:
    """Mirrors app/services/storage.py's default resolution (storage/sessions,
    relative to backend/) without importing the app package."""
    backend_dir = Path(__file__).resolve().parents[2]
    return backend_dir / "storage" / "sessions"


def upgrade() -> None:
    conn = op.get_bind()
    root = _storage_root()
    counts: dict[int, int] = {}

    for table_name in SOURCE_TABLES:
        table = _source_table(table_name)
        rows = conn.execute(
            sa.select(
                table.c.id, table.c.session_id, table.c.original_filename,
                table.c.file_path, table.c.uploaded_at,
            ).where(table.c.source_upload_id.is_(None))
        ).fetchall()

        for row in rows:
            full_path = root / row.file_path
            if not full_path.exists():
                raise RuntimeError(
                    f"Backfill aborted: {table_name}.id={row.id} "
                    f"(session {row.session_id}, {row.original_filename!r}) "
                    f"points to {full_path}, which does not exist. Refusing "
                    f"to create an assignment_uploads row for a file that "
                    f"isn't actually there."
                )

            result = conn.execute(
                assignment_uploads.insert().values(
                    session_id=row.session_id,
                    original_filename=row.original_filename,
                    content_type=None,
                    file_path=row.file_path,
                    uploaded_at=row.uploaded_at,
                )
            )
            new_upload_id = result.lastrowid

            conn.execute(
                table.update()
                .where(table.c.id == row.id)
                .values(source_upload_id=new_upload_id)
            )

            counts[row.session_id] = counts.get(row.session_id, 0) + 1

    for session_id in sorted(counts):
        print(f"  backfilled {counts[session_id]} assignment_uploads row(s) for session {session_id}")


def downgrade() -> None:
    conn = op.get_bind()

    backfilled_ids = [
        r[0] for r in conn.execute(
            sa.select(assignment_uploads.c.id).where(
                assignment_uploads.c.content_type.is_(None)
            )
        ).fetchall()
    ]

    for table_name in SOURCE_TABLES:
        table = _source_table(table_name)
        conn.execute(
            table.update()
            .where(table.c.source_upload_id.in_(backfilled_ids))
            .values(source_upload_id=None)
        )

    conn.execute(
        assignment_uploads.delete().where(
            assignment_uploads.c.content_type.is_(None)
        )
    )
