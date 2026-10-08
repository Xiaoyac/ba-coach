"""Durable in-chat reminder receipts. Explicit migration, no startup DDL."""
from sqlalchemy import Column as C, DateTime, Index, Integer, MetaData, String, Table

metadata = MetaData()
reminders = Table('pa_chat_reminders', metadata,
    # One occurrence per goal, independent of worker, device and review cycle.
    C('id', String(64), primary_key=True),
    C('user_id', String(36), nullable=False),
    C('conversation_id', Integer, nullable=False),
    C('goal_id', String(36), nullable=False),
    C('plan_id', String(36), nullable=False),
    C('start_at', DateTime, nullable=False),
    C('due_at', DateTime, nullable=False),
    C('state', String(24), nullable=False),
    C('message_id', Integer),
    C('created_at', DateTime, nullable=False),
    Index('ix_pa_chat_reminder_user', 'user_id', 'created_at'))
