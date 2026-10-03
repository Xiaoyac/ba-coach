const NO_BARRIERS = "暂无需要应对的困难";

function object(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown> : null;
}

function visiblePlanValue(value: unknown): unknown {
  if (Array.isArray(value)) {
    // Invalid or mixed N/A markers must not leak evidence metadata or turn
    // an unknown state into a statement that there are no difficulties.
    const items = value.map(visiblePlanValue).filter(item => item !== null);
    return items.length ? items : null;
  }
  const item = object(value);
  if (item) {
    if (item.status === "not_applicable") return null;
    const visible = Object.fromEntries(Object.entries(item).filter(([key]) =>
      !["source_message_id", "source_quote", "status"].includes(key)));
    return Object.keys(visible).length ? visible : null;
  }
  return value == null || (typeof value === "string" && !value.trim()) ? null : value;
}

/** Only the backend's sourced, single-item N/A record asserts no barriers. */
export function copingPlanDisplayValue(value: unknown): unknown {
  const item = Array.isArray(value) && value.length === 1 ? object(value[0]) : null;
  if (item?.status === "not_applicable" && !("barrier" in item) && !("plan" in item)
    && Number.isInteger(item.source_message_id) && Number(item.source_message_id) > 0
    && typeof item.source_quote === "string" && item.source_quote.trim()) return NO_BARRIERS;
  return visiblePlanValue(value);
}
