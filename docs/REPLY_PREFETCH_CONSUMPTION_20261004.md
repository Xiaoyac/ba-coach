# Consume-time validation for prepared reply evidence

This follow-up depends on b52bda3. That patch validates prepared catalog evidence
when the Router finishes, but the graph can yield before the reply node starts.
A knowledge revision published in that interval could otherwise reach the
mediator as stale prepared evidence.

The reply node now checks the corpus token immediately before consuming prepared
evidence. A changed index, revision or epoch, or a failed version read discards
that result and uses the existing ordinary retrieval path. Cancellation continues
to propagate. The request-owned slot is cleared before checking, so the prepared
result cannot be consumed twice. The Router's earlier check remains in place.

Only reply_retrieval.py and graph/nodes.py change at runtime. This adds one version
read when a matching prepared result reaches the reply node; production database
latency for that extra read has not been measured. It does not lock the knowledge
base for the whole turn or alter catalog ranking, mediator policy, model budgets,
user confirmation, persistence or production defaults.

The new 26-case regression file test_reply_retrieval_boundaries_1004.py covers
consumer-time revision changes and failures, cancellation during the version
read, pending/completed prefetch invalidation, request changes, both completion
orders, and 20 concurrent request contexts. The stale-evidence case reproduced
in the unmodified parent in all three controlled runs. The isolated corrected
tree passed 314 targeted cases in three rounds and 2645 full-suite cases with two
optional skips. Integration verification is recorded separately against the final
commit; no deployment or external-model probe is part of these tests.

Deployments using b52bda3 should include this follow-up. It is independent of the
experimental hybrid/K3 retrieval alternative, which remains unadopted.
