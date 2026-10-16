import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock
import pytest
from app.graph import builder, nodes
from app.provisional_reply import with_natural_lead
from app.providers.base import Completion


@pytest.mark.parametrize('failure', ['timeout','empty','invalid','out_of_range','truncated','disabled'])
async def test_risk_failure_blocks_every_ordinary_stage_and_keeps_module(context, provider, monkeypatch, failure):
    async def screen(*args, **kwargs):
        if failure == 'timeout': raise TimeoutError('synthetic timeout')
        if failure == 'empty': return {},Completion(text='',model='stub')
        if failure == 'out_of_range': return {'risk_status':2},Completion(text='{"risk_status":2}',model='stub',finish_reason='stop')
        if failure == 'truncated': return {'risk_status':0},Completion(text='{"risk_status":0}',model='stub',finish_reason='length')
        return {},Completion(text='invalid',model='stub')
    monkeypatch.setattr(nodes,'assess_risk_detailed',screen)
    router=AsyncMock(side_effect=AssertionError('Router must not run'))
    tools=AsyncMock(side_effect=AssertionError('PA tools must not run'))
    monkeypatch.setattr(builder,'pre_reply_router_node',router)
    monkeypatch.setattr(builder,'prepare_pa_reply_node',tools)
    provider.complete=AsyncMock(side_effect=AssertionError('Lead or main must not run'))
    ctx=replace(context,stream=True,settings=context.settings.model_copy(update={'risk_gate_enabled':failure!='disabled'}))
    await ctx.store.adopt('safe-test',[], 'module_3', {'preserve':'original'})
    graph=builder.build_graph().compile()
    state={'session_id':'safe-test','user_input':'合成测试','telemetry':{'reply_mode':'ack_deep'}}
    output=[x async for x in with_natural_lead(graph.astream(state,context=ctx,stream_mode=['custom','values']),
        provider=provider,user_input=state['user_input'],generation_id='g',session_id='safe-test',
        subject_id=None,maker=None,metrics={},context=ctx)]
    values=[e for mode,e in output if mode=='values'];final=values[-1]
    assert final['final_response']==nodes.RISK_CHECK_RETRY_REPLY
    assert final['next_module']=='module_3' and not final['routing_pending']
    assert final['reply_held'] and final['telemetry']['error_code']=='risk_check_failed'
    assert not any(e.get('phase')=='lead' for mode,e in output if mode=='custom')
    router.assert_not_awaited();tools.assert_not_awaited();provider.complete.assert_not_awaited()
    live=await ctx.store.get('safe-test')
    assert live.module=='module_3' and live.memory=={'preserve':'original'}
    assert live.messages[-1].content==nodes.RISK_CHECK_RETRY_REPLY


def test_failure_notice_is_persisted_and_next_request_rescreens(client,auth_headers,provider):
    provider.risk_result='invalid'
    sid=client.post('/api/conversations/current',headers=auth_headers).json()['session_id']
    response=client.post('/api/chat/stream',headers=auth_headers,json={'session_id':sid,'message':'合成测试'})
    assert response.status_code==200 and nodes.RISK_CHECK_RETRY_REPLY in response.text
    assert '"saved": true' in response.text
    assert not provider.seen
    detail=client.get('/api/conversations/'+sid,headers=auth_headers).json()
    assert detail['messages'][-1]['content']==nodes.RISK_CHECK_RETRY_REPLY
    risk_calls=len([s for s in provider.route_systems if '风险信号检测器' in s])
    provider.risk_result='{"risk_status":0}'
    response=client.post('/api/chat/stream',headers=auth_headers,json={'session_id':sid,'message':'现在再试一下'})
    assert response.status_code==200 and nodes.RISK_CHECK_RETRY_REPLY not in response.text
    assert len([s for s in provider.route_systems if '风险信号检测器' in s])>risk_calls
    assert provider.seen
