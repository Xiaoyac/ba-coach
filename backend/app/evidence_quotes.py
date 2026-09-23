"""Recover literal source spans across presentation-only whitespace changes."""


def literal_span(quote, source):
    """Return the actual contiguous source span, or None.

    Models often collapse paragraph breaks while quoting a message. Ignore
    whitespace for locating the span, but retain every word, number and
    punctuation mark and return the original text for later provenance checks.
    This does not repair paraphrases, reordered clauses or edited numbers.
    """
    if not isinstance(quote, str) or not isinstance(source, str):
        return None
    needle = quote.strip()
    if not needle:
        return None
    if needle in source:
        return needle
    needle = ''.join(char for char in needle if not char.isspace())
    positions = [index for index, char in enumerate(source) if not char.isspace()]
    haystack = ''.join(source[index] for index in positions)
    start = haystack.find(needle)
    if not needle or start < 0:
        return None
    return source[positions[start]:positions[start + len(needle) - 1] + 1]
