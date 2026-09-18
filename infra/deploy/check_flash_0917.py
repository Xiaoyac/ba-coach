"""Synthetic provider-path checks; no business database or user data touched."""
import asyncio
from dataclasses import replace
import json
import logging
from time import perf_counter

from app.config import get_settings
from app.providers.deepseek import DeepSeekProvider
from app.schemas import Message


async def main():
    logging.disable(logging.CRITICAL)
    settings = get_settings().model_copy(update={"deepseek_max_tokens": 1024})
    assert settings.deepseek_model == settings.deepseek_router_model == "deepseek-v4.1-flash"
    provider = DeepSeekProvider(settings)
    checks = []
    try:
        for name in ("route_detailed", "route_with_reasoning", "complete", "stream"):
            started = perf_counter()
            if name.startswith("route"):
                result = await asyncio.wait_for(getattr(provider, name)(system='Return only {"module":"module_2"}.',
                    user="Connection test, no user record.", max_tokens=1024), 60)
                ok = json.loads(result.text).get("module") == "module_2"
                metadata = {"actual_model": result.model, "usage": result.usage, "finish_reason": result.finish_reason,
                            "synthetic_reply": result.text[:200]}
            elif name == "complete":
                result = await asyncio.wait_for(provider.complete(system="Reply only OK.",
                    messages=[Message(role="user", content="Connection test.")]), 60)
                ok = result.text.strip() == "OK"
                metadata = {"actual_model": result.model, "usage": result.usage, "finish_reason": result.finish_reason}
            else:
                pieces, metadata = [], {}
                async def collect():
                    nonlocal metadata
                    async for delta in provider.stream(system="Reply only OK.", messages=[Message(role="user", content="Connection test.")]):
                        if delta.kind == "content":
                            pieces.append(delta.text)
                        elif delta.kind == "usage":
                            metadata = {"usage": delta.usage, "finish_reason": delta.finish_reason}
                await asyncio.wait_for(collect(), 60)
                ok = "".join(pieces).strip() == "OK"
            checks.append({"check": name, "ok": ok, "ms": round((perf_counter()-started)*1000), **metadata})
        print(json.dumps({"checks": checks, "all_passed": all(c["ok"] for c in checks)}))
        if not all(c["ok"] for c in checks):
            raise SystemExit(1)
    except Exception as exc:
        # API exceptions can contain sensitive request details: emit class only.
        print(json.dumps({"checks": checks, "error_type": type(exc).__name__}))
        raise SystemExit(1)
    finally:
        await provider._client.close()


if __name__ == "__main__":
    asyncio.run(main())
