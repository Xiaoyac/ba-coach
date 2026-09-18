"""SSH-only synthetic evaluation worker; stdin/stdout, no files/DB/API-key output."""
import asyncio
import json
import logging
import sys
from time import perf_counter
import openai
from app.config import get_settings


async def main():
    logging.disable(logging.CRITICAL)
    raw = sys.stdin.buffer.read(8_000_001)
    if len(raw) > 8_000_000:
        raise SystemExit("Batch exceeds explicit safety budget")
    data = json.loads(raw)
    if len(data["jobs"]) > 100:
        raise SystemExit("At most 100 jobs per batch")
    settings = get_settings()
    client = openai.AsyncOpenAI(api_key=settings.deepseek_api_key, base_url=settings.deepseek_base_url,
        max_retries=0, timeout=45)
    limiter = asyncio.Semaphore(2)

    async def run(job):
        if len(json.dumps(job, ensure_ascii=False)) > 100_000:
            return {"id": job["id"], "error": "payload_budget"}
        async with limiter:
            started = perf_counter()
            try:
                result = await client.chat.completions.create(model="deepseek-v4.1-flash", temperature=0,
                    messages=[{"role":"system", "content":data["system"]},
                              {"role":"user", "content":json.dumps(job["payload"], ensure_ascii=False)}],
                    max_tokens=256, extra_body={"thinking":{"type":"disabled"}})
                return {"id":job["id"], "text":result.choices[0].message.content or "",
                    "finish_reason":result.choices[0].finish_reason, "model":result.model,
                    "usage":result.usage.model_dump() if result.usage else {},
                    "duration_ms":(perf_counter()-started)*1000}
            except Exception as exc:
                return {"id":job["id"], "error":type(exc).__name__,
                    "duration_ms":(perf_counter()-started)*1000}
    try:
        results = await asyncio.gather(*(run(job) for job in data["jobs"]))
        print(json.dumps({"results":results}, ensure_ascii=False))
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
