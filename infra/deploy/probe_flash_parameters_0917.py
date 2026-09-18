"""Small synthetic compatibility probe, allowlisted output only, no DB access."""
import asyncio
import json
import logging
import openai
from app.config import get_settings


async def main():
    logging.disable(logging.CRITICAL)
    cfg = get_settings()
    client = openai.AsyncOpenAI(api_key=cfg.deepseek_api_key, base_url=cfg.deepseek_base_url, max_retries=0, timeout=25)
    rows = []
    variants = [
        ("temperature_0", {"temperature": 0, "extra_body": {"thinking": {"type": "disabled"}}}),
        ("temperature_01", {"temperature": .1, "extra_body": {"thinking": {"type": "disabled"}}}),
        ("default_temperature", {"extra_body": {"thinking": {"type": "disabled"}}}),
        ("enable_thinking_false", {"temperature": 0, "extra_body": {"enable_thinking": False}}),
    ]
    try:
        for name, params in variants:
            for prompt in ('Return exactly this JSON: {"module":"module_2"}.', 'Reply only SAFE.'):
                try:
                    result = await client.chat.completions.create(model=cfg.deepseek_router_model,
                        max_tokens=128, messages=[{"role":"system","content":prompt},
                                                {"role":"user","content":"Synthetic connection test."}], **params)
                    message = result.choices[0].message
                    rows.append({"variant":name,"prompt":prompt,"text":(message.content or "")[:200],
                        "reasoning_present":bool(getattr(message,"reasoning_content",None)),
                        "finish_reason":result.choices[0].finish_reason,"actual_model":result.model})
                except Exception as exc:
                    rows.append({"variant":name,"error_type":type(exc).__name__})
        print(json.dumps(rows))
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
