"""Safe per-turn retrieval timings, including honest cache reuse semantics."""
import math


def timing_rows(metrics):
    if metrics.get('ranking_mode') != 'astrbot_hybrid':
        return []
    cache = metrics.get('exact_cache') or {}
    details = cache.get('retriever_details') or {}
    stages = [('query_embedding', 'embedding_duration_ms'),
              ('knowledge_search', 'recall_fusion_duration_ms'),
              ('knowledge_reranker', 'rerank_duration_ms')]
    if cache.get('cache') in {'hit', 'coalesced'}:
        detail = ('命中检索结果缓存，本轮未重新执行' if cache['cache'] == 'hit'
                  else '复用并发检索结果，等待计入检索总耗时')
        return [dict(stage=stage, status='skipped', duration_ms=0, calls=0, detail=detail)
                for stage, _ in stages]
    rows = []
    for stage, field in stages:
        duration = details.get(field)
        if type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
            if stage == 'knowledge_reranker' and details.get('rerank_api_calls') == 0:
                rows.append(dict(stage=stage, status='skipped', duration_ms=0, calls=0,
                                 detail='本轮未调用重排'))
            continue  # Missing measurements are never invented for old turns.
        row = dict(stage=stage, status='completed', duration_ms=duration, calls=1)
        if stage == 'knowledge_reranker':
            row['model'] = details.get('rerank_model')
            if details.get('rerank') in {'failed', 'failed_retained_fusion'}:
                row['status'] = 'failed'
            count = details.get('rerank_candidate_count')
            if type(count) is int and count >= 0:
                row['detail'] = f'重排候选：{count} 条（上限 20 条）'
        rows.append(row)
    return rows


def publish_timings(metrics, *, local_only=False):
    from .execution_status import update
    for row in timing_rows(metrics):
        if local_only and row['stage'] == 'knowledge_reranker':
            continue
        update(row['stage'], row['status'], model=row.get('model'),
               detail=row.get('detail'), duration_ms=row['duration_ms'])
