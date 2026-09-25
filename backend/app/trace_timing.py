"""Observed spans only: never infer missing timings for historical runs."""
from time import perf_counter


def record_span(telemetry, stage, started, *, origin=None, status="completed", after_display=False):
    ended = perf_counter()
    if origin is None:
        origin = started
    spans = list(telemetry.get("execution_timeline") or [])
    spans.append({"stage": stage, "start_ms": round(max(0, started - origin) * 1000, 3),
                  "duration_ms": round(max(0, ended - started) * 1000, 3),
                  "status": status, "after_display": after_display})
    telemetry["execution_timeline"] = spans


def prompt_source(source, text):
    import hashlib
    return {"source": source, "version": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]}
