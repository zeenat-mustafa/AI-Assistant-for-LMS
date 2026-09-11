"""backfill submission_uploads for pre-migration submissions

Revision ID: 4bd3acedefb5
Revises: 04a4089f157b
Create Date: 2026-09-11 13:52:53.517469

Data migration, not a schema change (same shape as 1e98ad501108's own
upgrade comment on the assignment side: "existing rows predate this
feature"). Every Submission row with no linked SubmissionUpload row was
created before the submission_uploads table existed (migration
04a4089f157b), so the student's own upload list/download UI -- which reads
SubmissionRead.uploads exclusively -- shows nothing for them even though
the real files are still on disk and grading (which reads submission_files/
grades, not submission_uploads) was never affected. Confirmed against the
real dev DB before writing this: 19/19 Submission rows (100%) were
orphaned, spanning sessions 1-7, covering all 55 existing submission_files
rows.

Grouping decision: ONE submission_uploads row per pre-existing Submission
row (1:1 at the SUBMISSION level), not per SubmissionFile -- this is where
it differs from the assignment-side backfill, which was 1:1 per
UnsolvedFile/ResourceFile row. The reason is structural, not a judgment
call: before this feature existed, a Submission WAS one upload event by
construction -- the pre-rework upload_submission() endpoint accepted
exactly one file/zip per call and stored its own single
original_filename/uploaded_file_path directly on the Submission row itself.
A zip could still explode into several SubmissionFile rows (one per
notebook inside it), but all of those notebooks came from that one real
upload. So every SubmissionFile row belonging to a given Submission is
linked to the SAME new SubmissionUpload row, not one each.

Field provenance (nothing fabricated):
    submission_id  -- the source Submission row's own id.
    original_filename / file_path  -- copied verbatim from the source
        Submission row's own original_filename / uploaded_file_path;
        existence on disk is verified before any row is written (fails
        loudly, per-row, rather than silently skipping or pointing a new
        row at a file that isn't really there).
    uploaded_at  -- copied verbatim from the source Submission row's own
        real submitted_at column, not reconstructed from filesystem mtimes.
    content_type  -- left NULL. The original HTTP request's Content-Type
        header was never recorded anywhere for these rows (Submission has
        no content_type column at all), and guessing one from the file
        extension would be exactly the kind of plausible-but-invented value
        this migration is explicit about avoiding.

downgrade() reverses cleanly: it re-nulls source_upload_id on every
submission_files row this migration linked (identifiable by it), THEN
deletes the submission_uploads rows it created (content_type IS NULL is a
safe marker -- at the time this migration was written there were zero
genuine post-rework submission_uploads rows in the database at all, so
every content_type IS NULL row is, without ambiguity, one this migration
created; same caveat as 1e98ad501108's identical marker choice). Nulling
the link first matters even though SQLite's FK enforcement is off in
Alembic's own connection (env.py builds its own engine, never touching
database.py's PRAGMA foreign_keys=ON listener): deleting straight through
the ON DELETE CASCADE from submission_files to submission_uploads would be
silently correct here but relies on that pragma being off, which is not
something a migration should depend on.
"""
from pathlib import Path
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4bd3acedefb5'
down_revision: Union[str, None] = '04a4089f157b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Lightweight, metadata-free table handles -- deliberately not importing the
# real ORM models, so this migration keeps working unchanged even if those
# models are refactored later.
submissions = sa.table(
    'submissions',
    sa.column('id', sa.Integer),
    sa.column('session_id', sa.Integer),
    sa.column('student_id', sa.Integer),
    sa.column('original_filename', sa.String),
    sa.column('uploaded_file_path', sa.String),
    sa.column('submitted_at', sa.DateTime),
)

submission_uploads = sa.table(
    'submission_uploads',
    sa.column('id', sa.Integer),
    sa.column('submission_id', sa.Integer),
    sa.column('original_filename', sa.String),
    sa.column('content_type', sa.String),
    sa.column('file_path', sa.String),
    sa.column('uploaded_at', sa.DateTime),
)

submission_files = sa.table(
    'submission_files',
    sa.column('id', sa.Integer),
    sa.column('submission_id', sa.Integer),
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

    # Every Submission with no linked SubmissionUpload row at all.
    existing_upload_submission_ids = {
        r[0] for r in conn.execute(sa.select(submission_uploads.c.submission_id)).fetchall()
    }
    rows = conn.execute(
        sa.select(
            submissions.c.id, submissions.c.session_id, submissions.c.original_filename,
            submissions.c.uploaded_file_path, submissions.c.submitted_at,
        )
    ).fetchall()
    orphaned = [row for row in rows if row.id not in existing_upload_submission_ids]

    counts: dict[int, int] = {}

    for row in orphaned:
        full_path = root / row.uploaded_file_path
        if not full_path.exists():
            raise RuntimeError(
                f"Backfill aborted: submissions.id={row.id} "
                f"(session {row.session_id}, {row.original_filename!r}) "
                f"points to {full_path}, which does not exist. Refusing "
                f"to create a submission_uploads row for a file that "
                f"isn't actually there."
            )

        result = conn.execute(
            submission_uploads.insert().values(
                submission_id=row.id,
                original_filename=row.original_filename,
                content_type=None,
                file_path=row.uploaded_file_path,
                uploaded_at=row.submitted_at,
            )
        )
        new_upload_id = result.lastrowid

        # Every SubmissionFile under this Submission came from this one real
        # upload event (see the module docstring) -- link all of them, not
        # just one.
        conn.execute(
            submission_files.update()
            .where(submission_files.c.submission_id == row.id)
            .values(source_upload_id=new_upload_id)
        )

        counts[row.session_id] = counts.get(row.session_id, 0) + 1

    for session_id in sorted(counts):
        print(f"  backfilled {counts[session_id]} submission_uploads row(s) for session {session_id}")


def downgrade() -> None:
    conn = op.get_bind()

    backfilled_ids = [
        r[0] for r in conn.execute(
            sa.select(submission_uploads.c.id).where(
                submission_uploads.c.content_type.is_(None)
            )
        ).fetchall()
    ]

    conn.execute(
        submission_files.update()
        .where(submission_files.c.source_upload_id.in_(backfilled_ids))
        .values(source_upload_id=None)
    )

    conn.execute(
        submission_uploads.delete().where(
            submission_uploads.c.content_type.is_(None)
        )
    )
