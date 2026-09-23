"""Production preflight: pure logic only, no database or provider calls."""
from app.m1_contract import consent_is_current, normalize, reconcile_router_completion
from app.router_agent import RouterDecision, format_routing_reasoning
from app.workflow_contract import MODULE_STEP_KEYS

invitation = '把“定个读书目标、完成再睡”慢慢落成小目标。你愿意试试这个方法，进入目标设定的部分吗？'
assert consent_is_current([('assistant', invitation), ('user', '愿意')], 1)
assert not consent_is_current([('assistant', '比如“你愿意开始目标设定吗？”只是例子。'), ('user', '愿意')], 1)
assert not consent_is_current([('assistant', invitation), ('user', '不愿意')], 1)
education = ['情绪精力行动相互影响。', '活动反馈减少可能维持困扰。', '可控行动可能带来反馈。',
             '行动不保证立刻开心。', '尝试观察调整，不要求意志力。']
turns = [('user', '我不想披露个人经历。')] + [('assistant', text) for text in education[:4]]
turns += [('user', '贴合'), ('assistant', education[4] + '到这里有还没说清或有顾虑的地方吗？'),
          ('user', '没有'), ('assistant', invitation), ('user', '愿意'), ('assistant', '收到你的意愿。')]
raw = {'path': 'low_disclosure', 'refusal_quote': {'turn': 0}, 'education_quotes': education,
       'understanding_quote': {'turn': 5}, 'consent_quote': {'turn': 9}, 'core_questions_resolved': True}
contract = normalize(raw, {}, turns, 'probe')
contract['assistant_message_id'] = 10
assert contract['missing_fields'] == ['ba_understanding', 'goal_setting_consent']
fixed = reconcile_router_completion(contract, turns, session_id='probe', assistant_message_id=10)
assert fixed and fixed['missing_fields'] == [] and fixed['evidence']['understanding']['turn'] == 7
assert reconcile_router_completion({**contract, 'core_questions_resolved': False}, turns,
    session_id='probe', assistant_message_id=10) is None
decision = RouterDecision('module_2', '', 'probe', list(MODULE_STEP_KEYS['module_1']), {})
assert '本轮未跳转原因：' in format_routing_reasoning(decision, 'module_1', 'module_1',
    diagnostics={'block_reasons': ['尚有疑问']})
print('ROUTER_PRIORITY_PREFLIGHT_OK: actual invitation, evidence reconciliation, unresolved-question block, diagnostics')
