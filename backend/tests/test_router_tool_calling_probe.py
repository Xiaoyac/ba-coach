"""Validate experimental tool arguments without making paid API calls."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace as NS
import pytest

path=Path(__file__).resolve().parents[2]/'infra/qa/router_tool_calling_probe.py'
spec=importlib.util.spec_from_file_location('router_tool_probe',path)
probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)

def choice(body=None, *, finish='tool_calls', name=probe.NAME, count=1, id='call-test'):
    args=json.dumps(body if body is not None else {'target_module':'2','knowledge_task':'general'})
    call=NS(id=id,type='function',function=NS(name=name,arguments=args))
    return NS(finish_reason=finish,message=NS(tool_calls=[call]*count,content=None))


def test_valid_native_arguments():
    assert probe.validate_tool_choice(choice())=={'target_module':'2','knowledge_task':'general'}


@pytest.mark.parametrize('kwargs',[
    {'finish':'length'}, {'finish':'stop'}, {'count':0}, {'count':2}, {'name':'write_goal'}, {'id':''},
    {'body':{'target_module':2,'knowledge_task':'general'}},
    {'body':{'target_module':'5','knowledge_task':'general'}},
    {'body':{'target_module':'2','knowledge_task':'invented'}},
    {'body':{'target_module':'2','knowledge_task':'general','confirmed':True}},
    {'body':{'target_module':'2'}},
])
def test_rejects_incomplete_duplicate_and_invalid_calls(kwargs):
    with pytest.raises(ValueError):probe.validate_tool_choice(choice(**kwargs))


def test_no_prose_or_reasoning_fallback():
    response=choice(count=0)
    response.message.content='{"target_module":"2","knowledge_task":"general"}'
    response.message.reasoning_content=response.message.content
    with pytest.raises(ValueError):probe.validate_tool_choice(response)


def test_schema_has_fixed_required_keys_and_no_business_actions():
    schema=probe.TOOL['function']['parameters']
    assert schema['additionalProperties'] is False
    assert set(schema['required'])==set(schema['properties'])=={'target_module','knowledge_task'}


@pytest.mark.asyncio
async def test_both_arms_match_member_wire_policy():
    import httpx
    import openai
    from app.config import Settings
    from app.providers.deepseek import DeepSeekProvider
    cfg=Settings(_env_file=None, deepseek_api_key='synthetic',
        deepseek_base_url='https://ark.cn-beijing.volces.com/api/coding/v3',
        deepseek_model='kimi-k3',deepseek_router_model='kimi-k3',module_router_reasoning_effort='provider_default')
    requests=[]
    def respond(request):
        body=json.loads(request.content);requests.append(body)
        message={'role':'assistant','content':'{"target_module":"2","knowledge_task":"general"}'}
        finish='stop'
        if 'tools' in body:
            message={'role':'assistant','content':None,'tool_calls':[{'id':'synthetic-call','type':'function',
                'function':{'name':probe.NAME,'arguments':'{"target_module":"2","knowledge_task":"general"}'}}]}
            finish='tool_calls'
        return httpx.Response(200,json={'id':'synthetic','object':'chat.completion','created':0,'model':'kimi-k3',
            'choices':[{'index':0,'finish_reason':finish,'message':message}],
            'usage':{'prompt_tokens':100,'completion_tokens':50,'total_tokens':150}})
    for arm in ['json','tool']:
        provider=DeepSeekProvider(cfg).with_thinking(False)
        await provider._client.close()
        provider._client=openai.AsyncOpenAI(api_key='synthetic',base_url=cfg.deepseek_base_url,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),max_retries=0)
        try:
            completion=await probe.RecordedProvider(provider,arm).route_with_reasoning(system='router',user='case',max_tokens=1024)
            assert json.loads(completion.text)['target_module']=='2'
        finally:await provider._client.close()
    for request in requests:
        assert request['temperature']==0
        assert request['reasoning_effort']=='low'
        assert request['max_tokens']==2048
        assert request['model']=='kimi-k3'
    assert requests[0]['messages'][-1]==requests[1]['messages'][-1]
    assert requests[1]['tool_choice']=='required' and requests[1]['parallel_tool_calls'] is False
