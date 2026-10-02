# Main reply prefix cache experiment

Local implementation and isolated Ark K3 tests; **not deployed**.

`main_prefix_cache_enabled` enables a verified Ark K3 layout only. Stable policies and a frozen context baseline precede immutable history. Exact replacement/deletion patches, current committed workflow facts, mediator guidance, and the server clock precede the current user message. Module/policy changes intentionally rebuild the prefix. Tool continuations preserve the final user/tool ordering.

`ConversationContextCheckpoint` is a new app-owned table created by the existing startup schema mechanism on a future authorized deployment. It holds a summary with its source boundary/digest and a reference-context baseline, never authoritative business state. Both legacy and V2 deletion paths erase it. Disabling the feature leaves existing checkpoints unused.

Main history uses conservative byte-based token estimates (not billing tokens), with a default 24000-token epoch and 8000-token recent tail. Summaries are created only on overflow. Original messages remain stored. Source edits invalidate summaries; owner/message-boundary checks prevent cross-user and queued-turn leakage. Compaction requests have separate telemetry. First compaction may add material latency; background precompaction remains future work.

## AstrBot-inspired context handling (local, not deployed)

Reviewed AstrBot commit `9d4f523464644554e0e8e50fa2a65f146e320cd1`, especially its [round-preserving compressor](https://github.com/AstrBotDevs/AstrBot/blob/9d4f523464644554e0e8e50fa2a65f146e320cd1/astrbot/core/agent/context/compressor.py) and [dynamic context guidance](https://github.com/AstrBotDevs/AstrBot/blob/9d4f523464644554e0e8e50fa2a65f146e320cd1/docs/zh/dev/star/guides/listen-message-event.md#L307-L340). These are independently implemented adaptations to our existing database/provider boundaries.

- Recent history retains complete user/assistant rounds. The latest historical round stays verbatim even when it alone exceeds the target tail budget; the current user message is outside compaction entirely. Oversized retained history is reported in telemetry.
- Compression uses its own input budget (48000 estimated tokens by default), rather than the reply history threshold. It can summarize a larger old segment in one call instead of repeatedly folding summaries at a small reply budget. Larger histories still use bounded, round-aligned batches; this does not eliminate all recursive summarization.
- `HistoryCompressor` scopes model, effort, output budget and deadline independently of member/admin reply settings and the shared Router. It shares the existing transport without mutating that client's configuration. Provider resolution is lazy; an unused optional provider cannot break ordinary requests.
- Retrieved knowledge, recalled long-term-memory snippets and request metadata stay in the current system suffix. They never enter a new frozen state baseline or stored chat messages. Profile/clinical reference state still uses exact baseline/delta records; authoritative workflow state stays current.
- Empty, truncated, oversized or failed summaries do not update the checkpoint. If the original history fits the configured compression input headroom, the reply continues with the entire previously valid summary and unmodified remaining history. No partially generated batch is committed. If it does not fit, an explicit error is retained rather than silently deleting user facts. Cancellation propagates. Failed calls are recorded as failures, not successful `stop` completions.

Configuration (environment keys are the uppercase versions):

| Setting | Default | Meaning |
| --- | --- | --- |
| `main_history_compression_provider` | unset | Use the current reply provider; optionally choose an existing configured provider. |
| `main_history_compression_model` | unset | Use that provider's main model, independently of its Router model. |
| `main_history_compression_effort` | `low` | Explicit `low/high/max` is verified on DeepSeek-compatible Ark K3 only. Other transports keep their own auxiliary conventions; unsupported high/max fail observably. |
| `main_history_compression_max_tokens` | `4096` | Compression output budget, including native reasoning where applicable. |
| `main_history_compression_timeout_seconds` | `60` | Whole compression-call deadline, separate from Router/reply deadlines. |
| `main_history_compression_input_tokens` | `48000` | Conservative input budget and lossless failure headroom for history; not a provider-advertised context limit. Leave room for policies/current input within the actual model capacity. |

We keep the existing cost-oriented 24000/8000 reply budgets rather than copying an 82%-of-model-capacity trigger. PA tools already return bounded business records, so no general filesystem/tool-overflow store is introduced. We do not copy AstrBot's task-continuation instruction or its lossy halving fallback. These changes do not establish source-verified factual memory or fix greeting-to-task resumption. The earlier real K3 tests found both errors, including with Max compression; those remain separate acceptance concerns.

Validation on October 2: 141 regression tests passed, followed by 20 focused tests after a query projection optimization. Controlled provider replay produced 91.95% token-weighted cache reuse across 36 requests, including first requests and a forced compaction boundary; the 33 subsequent requests achieved 94.72%. This is not a production traffic or guaranteed cold-cache result. Ordinary replay used 12 short-history and 12 long-history turns; a further 12 tested compaction with a deliberately smaller 6000/2000 budget. Clinical/temporal checks retained current safety limits and date interpretation; some coaching-style issues persist.

Detailed scripts, response usage/request IDs, matched controls, scoped patch, and limitations are in the adjacent workspace diagnostic directory `诊断导出/K3缓存改造-20261002`.
