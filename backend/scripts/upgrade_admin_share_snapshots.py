"""Explicit, idempotent upgrade of existing admin-owned share snapshots.

Dry-run by default. --apply requires a new private backup file. Tokens, titles,
transcripts and message boundaries stay fixed; only diagnostic fields change.
No model calls or changes to ordinary users' shares are made.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.db import get_engine, get_sessionmaker
from app.models import AccountSettings, Conversation, ConversationShare, UserAccount
from app.routes.shares import _admin_messages
from app.share_schemas import AdminConversationShareSnapshot, AdminSharedMessage, ConversationShareSnapshot, SharedMessage


def utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


async def upgraded_snapshot(db, share, conversation):
    """Match frozen messages within the owned conversation, never by live ID."""
    original = ConversationShareSnapshot.model_validate(share.snapshot)
    live = await _admin_messages(db, conversation)
    source = conversation.messages
    cutoff = utc(share.created_at)

    def matches(saved, index):
        row = source[index]
        if row.role != saved.role or utc(row.created_at) > cutoff:
            return False
        if saved.created_at is not None and utc(saved.created_at) != utc(row.created_at):
            return False
        visible = SharedMessage(id=0, role=row.role, content=row.content).content
        return saved.content in {visible, live[index].content}

    # A verified prefix disambiguates repeated opening lines and repeated user
    # messages, including historical imports with equal timestamps.
    prefix = len(original.messages) <= len(source) and all(
        matches(saved, index) for index, saved in enumerate(original.messages))
    upgraded = []
    matched = 0
    unmatched_assistants = 0
    for index, saved in enumerate(original.messages):
        candidates = [index] if prefix else [i for i in range(len(source)) if matches(saved, i)]
        details = live[candidates[0]].model_dump() if len(candidates) == 1 else {}
        if len(candidates) == 1:
            matched += 1
        elif saved.role == "assistant":
            unmatched_assistants += 1
        # Older rich snapshots may already hold details no longer present in
        # live telemetry. Keep those fields, but never arbitrary extra fields.
        raw = share.snapshot["messages"][index]
        details.update({key: value for key, value in raw.items()
                        if key in AdminSharedMessage.model_fields and value is not None})
        details.update(saved.model_dump())
        upgraded.append(AdminSharedMessage.model_validate(details))
    result = AdminConversationShareSnapshot(title=original.title, created_at=original.created_at,
                                           messages=upgraded).model_dump(mode="json")
    # Upgrading must never append later turns or change the published text.
    assert ConversationShareSnapshot.model_validate({**result, "snapshot_version": 1}) == original
    return result, {"messages": len(upgraded), "matched_messages": matched,
                    "unmatched_assistants": unmatched_assistants}


async def upgrade(db, *, apply=False, backup_path: Path | None = None):
    if apply and backup_path is None:
        raise ValueError("--apply requires --backup")
    query = select(ConversationShare).join(Conversation, Conversation.id == ConversationShare.conversation_id).join(
        UserAccount, UserAccount.profile_uuid == Conversation.subject_id).join(
        AccountSettings, AccountSettings.account_id == UserAccount.id).where(
        AccountSettings.role == "admin", ConversationShare.revoked_at.is_(None))
    if apply:
        query = query.with_for_update()
    shares = (await db.execute(query.order_by(ConversationShare.id))).scalars().all()
    changes = []
    report = {"eligible_shares": 0, "already_upgraded": 0, "messages": 0,
              "matched_messages": 0, "unmatched_assistants": 0, "applied": False}
    for share in shares:
        if share.snapshot.get("snapshot_version", 1) == 2:
            report["already_upgraded"] += 1
            continue
        if share.snapshot.get("snapshot_version", 1) != 1:
            raise ValueError("unsupported snapshot version")
        conversation = await db.get(Conversation, share.conversation_id)
        candidate, counts = await upgraded_snapshot(db, share, conversation)
        changes.append((share, candidate))
        report["eligible_shares"] += 1
        for key, value in counts.items():
            report[key] += value
    if apply and changes:
        # Stop before publication if a historical assistant cannot be matched.
        # The preview count identifies this without printing private content.
        if report["unmatched_assistants"]:
            raise ValueError("unmatched historical assistant messages; review dry-run before applying")
        backup = {"created_at": datetime.now(timezone.utc).isoformat(), "shares": [
            {"id": share.id, "before": share.snapshot, "after_sha256": digest(candidate)}
            for share, candidate in changes]}
        fd = os.open(backup_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as file:
            json.dump(backup, file, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        for share, candidate in changes:
            share.snapshot = candidate
        await db.commit()
        report["applied"] = True
    else:
        await db.rollback()
    return report


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    try:
        async with get_sessionmaker()() as db:
            print(json.dumps(await upgrade(db, apply=args.apply, backup_path=args.backup)))
    finally:
        await get_engine().dispose()


if __name__ == "__main__":
    asyncio.run(main())
