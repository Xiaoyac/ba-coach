"""Opt-in Web Push tables. Separate metadata: never implicit startup DDL."""
from sqlalchemy import MetaData, Table, Column as C, String, Integer, DateTime, Boolean, Text, Index

metadata = MetaData()
devices = Table('pa_push_devices', metadata,
    C('id', String(36), primary_key=True), C('user_id', String(36), nullable=False),
    C('session_id', Integer, nullable=False),
    C('endpoint_hash', String(64), nullable=False, unique=True),
    C('endpoint', Text, nullable=False), C('p256dh', String(128), nullable=False),
    C('auth', String(64), nullable=False), C('enabled', Boolean, nullable=False),
    C('enabled_at', DateTime, nullable=False), C('updated_at', DateTime, nullable=False),
    Index('ix_pa_push_user', 'user_id', 'enabled'))

deliveries = Table('pa_push_deliveries', metadata,
    # Stable goal + execution start + device identity, independent of review cycles.
    C('id', String(64), primary_key=True), C('user_id', String(36), nullable=False),
    C('device_id', String(36), nullable=False), C('goal_id', String(36), nullable=False),
    C('plan_id', String(36), nullable=False), C('start_at', DateTime, nullable=False),
    C('due_at', DateTime, nullable=False), C('state', String(32), nullable=False),
    C('http_status', Integer), C('created_at', DateTime, nullable=False),
    C('updated_at', DateTime, nullable=False),
    Index('ix_pa_push_delivery_user', 'user_id', 'created_at'))

# User-initiated diagnostics, kept separate from real PA goals/deliveries.
checks = Table('pa_push_checks', metadata,
    C('id', String(64), primary_key=True), C('user_id', String(36), nullable=False),
    C('device_id', String(36), nullable=False), C('state', String(24), nullable=False),
    C('due_at', DateTime, nullable=False), C('expires_at', DateTime, nullable=False),
    C('created_at', DateTime, nullable=False), C('http_status', Integer),
    C('receipt_digest', String(64)), C('displayed_at', DateTime),
    C('had_open_window', Boolean),
    Index('ix_pa_push_checks_due', 'state', 'due_at'),
    Index('ix_pa_push_checks_user', 'user_id', 'created_at'))

# No foreign keys across separate business/auth metadata: worker always checks
# ownership, live session, current plan, and source evidence immediately before send.
# Retention cleanup removes disabled/deleted-user endpoints and old delivery rows.
