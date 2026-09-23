"""Production preflight: pure checks, no database writes or LLM calls."""
from app.dialogue_confirmation import affirmative, fingerprint
from app.goal_contract import _current_activity_choice
from app.reasoning import ThinkingTagStreamGuard, contains_internal_protocol
from app.answer_validator import validate_answer

for text in ('我自己选择散步作为主要目标', '这次自己选择散步作为主要目标'):
    assert _current_activity_choice(text, '散步', '', text)
for text in ('朋友说我自己选择散步', '如果我自己选择散步会怎么样？',
             '我自己选择散步，但先别创建目标'):
    assert not _current_activity_choice(text, '散步', '', text)
for text in ('我确认并同意这个记录方法', '可以，就按刚才这个方式记录',
             '对，你整理的记录方式准确，我确认'):
    assert affirmative(text)
for text in ('不同意', '好的，但是改成20分钟', '我同意，不过下雨就不做了',
             '我不太同意这样记录', '朋友说就按这个来', '我同意这样记录，但每小时一次',
             '我同意这样记录，改为每小时一次'):
    assert not affirmative(text)
plan = {'activity_content': '散步', 'duration_minutes': 10, 'core_values_impact': '原说明'}
assert fingerprint(plan) == fingerprint({**plan, 'core_values_impact': '同义改写'})
assert fingerprint(plan) != fingerprint({**plan, 'duration_minutes': 20})
guard = ThinkingTagStreamGuard.create()
emitted = []
for part in ('好的，', '<｜', 'DSML｜ ', 'invoke name="bash">'):
    emitted.extend(guard.push(part))
emitted.extend(guard.finish_passthrough())
assert guard.invalid_protocol and ''.join(emitted) == '好的，'
assert contains_internal_protocol('<tool_call>{"name":"bash"}</tool_call>')
validation = validate_answer(reply='<｜DSML｜ invoke name="bash">', module='module_2', evidence_ids=[])
assert validation['status'] == 'blocked'
assert any(f['code'] == 'internal_protocol_leak' for f in validation['findings'])
print('CYCLE_REPAIR_PREFLIGHT_OK: natural choices, negative guards, consent, fingerprint, split protocol')
