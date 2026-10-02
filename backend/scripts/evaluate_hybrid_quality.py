"""Fixed-corpus, labeled retrieval evaluation; no business DB or user transcripts.

Default is development cases only. Optional baseline file must be an explicitly
exported repository source revision (git show REF:backend/app/hybrid_knowledge_base.py).
Do not equate these seed target hits with answer accuracy or clinical quality.
"""
import argparse
import asyncio
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from time import perf_counter
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.hybrid_knowledge_base import HybridDatabaseKnowledgeBase
from app.retrieval_intent import decide_retrieval
from scripts.evaluate_retrieval import load_corpus, relevant_ids, measure, summarize

DEFAULT_CASES = Path(__file__).resolve().parents[1] / 'tests/fixtures/knowledge_quality_cases.json'


def baseline_type(path):
    spec = importlib.util.spec_from_file_location('app._quality_baseline', path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.HybridDatabaseKnowledgeBase


async def evaluate(args):
    corpus, manifest = load_corpus()
    data = json.loads(args.cases.read_text())
    cases = [c for c in data['cases'] if c['split'] == args.split]
    # Validate judgments before any retrieval; a missing target is a dataset error.
    labels = {c['id']: relevant_ids(c, corpus) for c in cases}
    async def load(**kw):
        return corpus
    current = HybridDatabaseKnowledgeBase()
    current.settings = current.settings.model_copy()
    current._load_index = load
    await current.warmup()
    engines = {'candidate': current}
    if args.baseline_source:
        old = baseline_type(args.baseline_source)(embedder=current._embedder)
        old._load_index = load
        old.settings = SimpleNamespace(**{**current.settings.model_dump(),
            'knowledge_hybrid_min_cosine': .5, 'knowledge_hybrid_sparse_min_coverage': .5,
            'knowledge_hybrid_candidates': 20, 'knowledge_rerank_model': None})
        await old.warmup()
        engines['hybrid_v1_same_embedding'] = old
    report = {'label_status': data['label_status'], 'split': args.split, 'chunks': len(corpus),
        'manifest': manifest, 'dataset_sha256': hashlib.sha256(args.cases.read_bytes()).hexdigest(),
        'baseline_source_sha256': hashlib.sha256(args.baseline_source.read_bytes()).hexdigest() if args.baseline_source else None,
        'embedding_identity': current._embedder.identity, 'embedding_model': current.settings.knowledge_embedding_model,
        'rerank_model': current.settings.knowledge_rerank_model, 'k': args.top_k, 'database_writes': 0,
        'cases': [], 'limitations': 'Seed target judgments are incomplete; precision/nDCG are label-based only. Not an answer-quality or clinical-safety evaluation.'}
    for case in cases:
        row = {'id': case['id'], 'query': case['query'], 'relevant_ids': sorted(labels[case['id']])}
        gate = decide_retrieval({'user_input': case['query']})
        row['gate'] = gate.as_dict()
        for name, kb in engines.items():
            start = perf_counter()
            hits, metrics = await kb.search_with_diagnostics(module=case['module'], query=case['query'], top_k=args.top_k) if gate.retrieve else ([], {'status': 'gate_skipped'})
            trace = metrics.get('retriever_details', {})
            stages = {stage: bool(labels[case['id']] & set(trace.get(key, [])))
                      for stage, key in [('dense_target_hit', 'dense_ids'), ('sparse_target_hit', 'sparse_ids'), ('fusion_target_hit', 'fused_ids')]} if trace.get('version', '').endswith('v2') and not case['expect_empty'] else {}
            row[name] = {**measure(hits, labels[case['id']], expect_empty=case['expect_empty'], k=args.top_k),
                **stages, 'elapsed_ms': round((perf_counter()-start)*1000, 3),
                'metrics': metrics, 'hits': [asdict(h) for h in hits]}
        report['cases'].append(row)
        print(json.dumps({'case': case['id'], **{name: {'target_hit': row[name]['hit_at_k'],
            'correct_empty': row[name]['correct_empty']} for name in engines}}, ensure_ascii=False), flush=True)
    report['summary'] = {name: summarize([r[name] for r in report['cases']]) for name in engines}
    report['quality_ready'] = bool(current.settings.knowledge_rerank_model) and all(
        row['candidate']['metrics']['status'] in {'hybrid_completed','gate_skipped'}
        and (row['candidate']['metrics']['status'] == 'gate_skipped' or row['candidate']['metrics'].get('retriever_details',{}).get('rerank') == 'completed')
        and (row['candidate']['hit_at_k'] == 1 or row['candidate']['correct_empty'] == 1)
        for row in report['cases'])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'summary': report['summary'], 'quality_ready': report['quality_ready']}, ensure_ascii=False, indent=2))
    if args.require_acceptance and not report['quality_ready']:
        raise SystemExit('Quality acceptance failed; do not deploy this configuration')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, default=DEFAULT_CASES)
    parser.add_argument('--split', choices=['dev', 'holdout'], default='dev')
    parser.add_argument('--baseline-source', type=Path)
    parser.add_argument('--top-k', type=int, default=5)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--require-acceptance', action='store_true')
    asyncio.run(evaluate(parser.parse_args()))
