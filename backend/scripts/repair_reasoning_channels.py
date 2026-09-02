"""Repair historical provider thinking/reply channel mix-ups.

Dry-run is the default. Pass ``--apply`` to commit. The migration is
idempotent and only changes rows whose normalized fields differ.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import or_, select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.db import dispose_db, get_sessionmaker  # noqa: E402
from app.models import ConversationMessage  # noqa: E402
from app.reasoning import normalize_reasoning_channels  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit repairs. Without this flag the script only reports them.",
    )
    return parser.parse_args()


async def run(*, apply: bool) -> None:
    async with get_sessionmaker()() as db:
        messages = (
            await db.execute(
                select(ConversationMessage).where(
                    ConversationMessage.role == "assistant",
                    or_(
                        ConversationMessage.content.ilike("%<thinking>%"),
                        ConversationMessage.content.ilike("%<think>%"),
                        ConversationMessage.reasoning_content.ilike("response%"),
                        ConversationMessage.reasoning_content.ilike("%<thinking>%"),
                        ConversationMessage.reasoning_content.ilike("%<think>%"),
                        ConversationMessage.routing_reasoning_content.ilike(
                            "response%"
                        ),
                        ConversationMessage.routing_reasoning_content.ilike(
                            "%<thinking>%"
                        ),
                        ConversationMessage.routing_reasoning_content.ilike(
                            "%<think>%"
                        ),
                    ),
                )
            )
        ).scalars().all()

        changed = 0
        for message in messages:
            main = normalize_reasoning_channels(
                message.content, message.reasoning_content
            )
            routing = normalize_reasoning_channels(
                "", message.routing_reasoning_content
            )
            next_reasoning = main.reasoning or None
            next_routing = routing.reasoning or None
            if (
                main.reply == message.content
                and next_reasoning == message.reasoning_content
                and next_routing == message.routing_reasoning_content
            ):
                continue
            changed += 1
            print(
                f"message_id={message.id} model={message.model_name or '-'} "
                f"content_chars={len(message.content)}->{len(main.reply)} "
                f"reasoning_chars={len(message.reasoning_content or '')}"
                f"->{len(next_reasoning or '')}"
            )
            if apply:
                message.content = main.reply
                message.reasoning_content = next_reasoning
                message.routing_reasoning_content = next_routing

        if apply:
            await db.commit()
        else:
            await db.rollback()
        print(f"mode={'apply' if apply else 'dry-run'} changed={changed}")
    await dispose_db()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(run(apply=args.apply))
