"""add conversation_threads and conversation_messages tables

Revision ID: 9d9376585d4a
Revises: b0bae8c363e6
Create Date: 2026-09-13 01:48:00.232851

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9d9376585d4a'
down_revision: Union[str, None] = 'b0bae8c363e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('conversation_threads',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('student_id', sa.Integer(), nullable=False),
    sa.Column('lms_session_id', sa.Integer(), nullable=False),
    sa.Column('rolling_summary', sa.Text(), nullable=True),
    sa.Column('summarized_through_message_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['lms_session_id'], ['lms_sessions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['student_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('student_id', 'lms_session_id', name='uq_conversation_threads_student_lms_session')
    )
    op.create_index(op.f('ix_conversation_threads_id'), 'conversation_threads', ['id'], unique=False)
    op.create_index(op.f('ix_conversation_threads_lms_session_id'), 'conversation_threads', ['lms_session_id'], unique=False)
    op.create_index(op.f('ix_conversation_threads_student_id'), 'conversation_threads', ['student_id'], unique=False)
    op.create_table('conversation_messages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('thread_id', sa.Integer(), nullable=False),
    sa.Column('role', sa.Enum('user', 'assistant', name='messagerole'), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['thread_id'], ['conversation_threads.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_conversation_messages_id'), 'conversation_messages', ['id'], unique=False)
    op.create_index(op.f('ix_conversation_messages_thread_id'), 'conversation_messages', ['thread_id'], unique=False)
    # NOTE: autogenerate also detected the same unrelated pre-existing drift
    # 7.1's migration (e15a0ce634e1) excluded — lms_sessions title index/unique
    # constraint and resource_files.uploaded_at nullability. Deliberately
    # excluded here too: 7.4 is purely additive. No backfill: no chatbot has
    # ever existed, so no pre-existing conversation data can exist.


def downgrade() -> None:
    op.drop_index(op.f('ix_conversation_messages_thread_id'), table_name='conversation_messages')
    op.drop_index(op.f('ix_conversation_messages_id'), table_name='conversation_messages')
    op.drop_table('conversation_messages')
    op.drop_index(op.f('ix_conversation_threads_student_id'), table_name='conversation_threads')
    op.drop_index(op.f('ix_conversation_threads_lms_session_id'), table_name='conversation_threads')
    op.drop_index(op.f('ix_conversation_threads_id'), table_name='conversation_threads')
    op.drop_table('conversation_threads')
