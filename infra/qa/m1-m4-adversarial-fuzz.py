"""Adversarial local fuzzing for evidence gates and agent-response rules.

No network, .env, production DB, browser account or real user data.
"""
from __future__ import annotations
import asyncio
import copy
import json
import runpy
import sys
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
from app.answer_validator import validate_answer
from app.goal_contract import proposal_evidence
from app.m1_contract import normalize as normalize_m1
from app.m4_contract import normalize as normalize_m4
from app.workflow_contract import MODULE_STEP_KEYS

stress = runpy.run_path(str(ROOT / 'infra/qa/m1-m4-stress-1000.py'))


def m4_valid(i):
    data, messages = stress['m4_fixture'](i)
    return data, messages


def check_m1_mutations(rounds):
    failures = []
    for i in range(rounds):
        raw, data, turns = stress['m1_fixture'](i)
        field = ('summary_quote', 'approval_quote', 'methods_quote', 'understanding_quote', 'consent_quote')[i % 5]
        mutated = copy.deepcopy(raw)
        if field == 'summary_quote': mutated[field] = '不是任何真实助手消息'
        elif field == 'approval_quote': mutated[field] = '这句认可发生在总结之前'
        elif field == 'methods_quote': mutated[field] = '助手替用户说有方法'
        elif field == 'understanding_quote': mutated[field] = '用户没有说过的理解'
        else: mutated[field] = '用户没有同意目标'
        result = normalize_m1(mutated, data, turns, f'fuzz-{i}')
        if len(result['completed_steps']) == len(MODULE_STEP_KEYS['module_1']):
            failures.append({'kind': 'm1_false_completion', 'round': i, 'field': field})
    return failures


def check_m2_mutations(rounds):
    failures = []
    cases = [
        ('可能去散步，也许吧', '散步', 'primary'),
        ('我还没决定要不要游泳', '游泳', 'secondary'),
        ('我不想跑步', '跑步', 'secondary'),
        ('助手说你可以站桩', '站桩', 'secondary'),
        ('我做过散步，今天也只是想起它', '散步', 'secondary'),
    ]
    for i in range(rounds):
        body, activity, kind = cases[i % len(cases)]
        msg = SimpleNamespace(id=i, position=1, role='user', content=body, conversation_id=f'fuzz-{i}')
        raw = {'goal_kind': kind, 'long_term_direction': '改善生活' if kind == 'primary' else None,
               'selection_quote': body, 'activity_quote': activity,
               'direction_quote': body if kind == 'primary' else None}
        if proposal_evidence(raw, [msg], activity) is not None:
            # Retain the original buckets for comparison, but both categories
            # fail the final run. A product bug is not a passing harness run.
            if '助手说' in body or '做过' in body:
                failures.append({'kind': 'product_m2_false_goal_creation', 'round': i, 'case': body})
            else:
                failures.append({'kind': 'm2_false_goal_creation', 'round': i, 'case': body})
    # Assistant-only suggestion must never be sufficient evidence.
    for i in range(rounds):
        user = SimpleNamespace(id=i, position=2, role='user', content='我还没想好')
        assistant = SimpleNamespace(id=i + 1, position=1, role='assistant', content='你可以散步十分钟')
        raw = {'goal_kind': 'secondary', 'selection_quote': '我还没想好', 'activity_quote': '散步十分钟'}
        if proposal_evidence(raw, [assistant, user], '散步十分钟') is not None:
            failures.append({'kind': 'm2_assistant_suggestion_promoted', 'round': i})
    return failures


def check_m4_mutations(rounds):
    failures = []
    for i in range(rounds):
        data, messages = m4_valid(i)
        mutated = copy.deepcopy(data)
        contract = mutated['m4_contract']
        field = ('phase_a_quote', 'phase_b_quote', 'phase_c_quote', 'summary_quote',
                 'confirmation_quote', 'education_quote', 'understanding_quote',
                 'difficulty_quote', 'decision_quote', 'review_summary_quote')[i % 10]
        contract[field] = '伪造且不存在的引用'
        result = normalize_m4(mutated, messages, session_id=f'fuzz-{i}', cycle_id=f'cycle-{i}', assistant_message_id=i * 100 + 10)
        actual = result['phase_c']['_m4_contract']['completed_steps']
        if len(actual) == len(MODULE_STEP_KEYS['module_4']):
            failures.append({'kind': 'm4_false_completion', 'round': i, 'field': field})
    # Reordering a user approval before the assistant summary must not pass.
    for i in range(rounds):
        data, messages = m4_valid(i)
        messages[3], messages[4] = messages[4], messages[3]
        messages[3].position, messages[4].position = messages[4].position, messages[3].position
        result = normalize_m4(data, messages, session_id=f'order-{i}', cycle_id=f'cycle-{i}', assistant_message_id=i * 100 + 10)
        if len(result['phase_c']['_m4_contract']['completed_steps']) == len(MODULE_STEP_KEYS['module_4']):
            failures.append({'kind': 'm4_order_false_completion', 'round': i})
    return failures


def check_response_rules(rounds):
    failures = []
    templates = [
        ('module_1', '从明天开始你应该每天跑步'),
        ('module_2', '目标已经确定并保存成功'),
        ('module_3', '请去网页目标面板确认保存'),
        ('module_4', '你本周坚持了五天，已经完成目标'),
        ('module_2', '你患有抑郁症，需要服用药物'),
    ]
    for i in range(rounds):
        module, reply = templates[i % len(templates)]
        result = validate_answer(reply=reply, module=module, evidence_ids=[], workflow={
            'available': True, 'current_module': module, 'plan_confirmed': False})
        if result['status'] != 'blocked':
            failures.append({'kind': 'response_rule_not_flagged', 'round': i, 'module': module})
    return failures


async def main():
    started = time.perf_counter()
    rounds = 2000
    failures = []
    failures.extend(check_m1_mutations(rounds))
    failures.extend(check_m2_mutations(rounds))
    failures.extend(check_m4_mutations(rounds))
    failures.extend(check_response_rules(rounds))
    product_findings = [item for item in failures if item['kind'].startswith('product_')]
    harness_failures = [item for item in failures if not item['kind'].startswith('product_')]
    report = {'scope': 'local synthetic only', 'rounds_each': rounds,
              'families': {'m1_quote_mutation': rounds, 'm2_goal_adversarial': rounds * 2,
                           'm4_quote_and_order_mutation': rounds * 2, 'agent_response_rules': rounds},
              'product_finding_count': len(product_findings), 'harness_failure_count': len(harness_failures),
              'product_findings': product_findings[:200], 'harness_failures': harness_failures[:200],
              'status': 'passed' if not failures else 'failed',
              'elapsed_seconds': round(time.perf_counter() - started, 3),
              'notes': ['All mutations are expected to fail closed.', 'No production reads/writes or model calls.']}
    out = ROOT / '.test-tmp' / 'm1-m4-adversarial-fuzz-report.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'status': report['status'], 'rounds_each': rounds,
                      'product_finding_count': len(product_findings), 'harness_failure_count': len(harness_failures), 'report': str(out)}, ensure_ascii=False))
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    asyncio.run(main())
