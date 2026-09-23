"""Run real-model acceptance in a separate, ignored post-release evidence set.

Never changes modules/confirmation/DB directly; only synthetic registration,
ordinary chat and read-only progress APIs. Previous failures are immutable.
"""
import argparse
import json
import sys
from pathlib import Path

import full_cycle_acceptance_0920 as cycle

BASE_OUT = cycle.OUT
OUT = BASE_OUT / 'post-release-20260920T031500Z'
cycle.OUT = OUT


def main():
    global OUT
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['batch', 'summary', 'say'])
    parser.add_argument('--actor', default='D')
    parser.add_argument('--count', type=int, default=1)
    parser.add_argument('--offset', type=int, default=0)
    parser.add_argument('--label')
    parser.add_argument('--text')
    parser.add_argument('--release', default='20260920T031500Z',
                        choices=['20260920T030000Z', '20260920T031500Z'])
    args = parser.parse_args()
    OUT = BASE_OUT / ('post-release-' + args.release)
    cycle.OUT = OUT
    if args.action == 'batch':
        assert 1 <= args.count <= 10 and 0 <= args.offset < 10
        cycle.CASES = cycle.CASES[args.offset:] + cycle.CASES[:args.offset]
        cycle.run_actor(args.actor, args.count, canonical=False)
    elif args.action == 'say':
        client = cycle.Client(args.actor)
        data = json.loads((client.folder / (args.label + '.json')).read_text(encoding='utf-8'))
        client.say(data, args.text)
    else:
        import summarize_full_cycle_0920 as stats
        stats.OUT = OUT
        result = stats.summarize()
        cycle.save(OUT / 'summary.json', result)
        # Full text evidence stays private; aggregate output has no credentials.
        print(json.dumps({k: v for k, v in result.items() if k not in {'findings', 'scenario_details'}},
                         ensure_ascii=False, indent=2))


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    main()
