"""Extract provider request identifiers without confusing them with completion IDs."""


def request_id(value) -> str | None:
    candidate = getattr(value, "_request_id", None)
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()[:128]
    response = getattr(value, "response", None)
    headers = getattr(response, "headers", None)
    if headers is not None:
        for name in ("x-request-id", "x-dashscope-request-id", "request-id"):
            candidate = headers.get(name)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()[:128]
    # Some compatible providers include an explicit request_id in the body.
    candidate = getattr(value, "request_id", None)
    return candidate.strip()[:128] if isinstance(candidate, str) and candidate.strip() else None
