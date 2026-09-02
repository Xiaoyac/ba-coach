"""Small live diagnostic for thinking-capable provider streams.

Prints timing and character counts only; API keys and model output are never
written to the terminal. Usage from ``backend``::

    python -m scripts.check_reasoning_stream doubao
    python -m scripts.check_reasoning_stream deepseek
"""

from __future__ import annotations

import argparse
import asyncio
from time import perf_counter

from app.providers import get_provider
from app.schemas import Message


async def check(name: str) -> None:
    provider = get_provider(name)
    started = perf_counter()
    first_reasoning: float | None = None
    first_content: float | None = None
    reasoning_chars = 0
    content_chars = 0

    async with asyncio.timeout(120):
        async for delta in provider.stream(
            system="Answer the user accurately and briefly.",
            messages=[Message(role="user", content="1+1 等于多少？只需简短回答。")],
        ):
            elapsed = perf_counter() - started
            if delta.kind == "reasoning":
                first_reasoning = first_reasoning or elapsed
                reasoning_chars += len(delta.text)
            else:
                first_content = first_content or elapsed
                content_chars += len(delta.text)

    print(
        f"{name}: model={provider.model} "
        f"first_reasoning={first_reasoning!r}s reasoning_chars={reasoning_chars} "
        f"first_content={first_content!r}s content_chars={content_chars} "
        f"total={perf_counter() - started:.2f}s"
    )

    router_started = perf_counter()
    routed = await provider.route(
        system="Return exactly OK and nothing else.", user="check", max_tokens=8
    )
    print(
        f"{name}: non-thinking router_ok={routed.strip().upper() == 'OK'} "
        f"chars={len(routed)} total={perf_counter() - router_started:.2f}s"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("provider", choices=("doubao", "deepseek"))
    args = parser.parse_args()
    asyncio.run(check(args.provider))


if __name__ == "__main__":
    main()
