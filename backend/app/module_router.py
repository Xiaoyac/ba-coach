"""All module-routing entry points use an isolated Flash/low transport."""
from contextlib import suppress
from dataclasses import replace
from functools import wraps
import json
from time import perf_counter

from .execution_status import routing_model
from .router_trace import RouterRequest, RouterResponse

MODEL = 'deepseek-v4-1-flash-261017'
EFFORT = 'low'


def module_router_model(provider):
    return MODEL if hasattr(provider, '_settings') else routing_model(provider)


def use_module_router(function):
    @wraps(function)
    async def call(provider, *args, **kwargs):
        calls = kwargs.pop('_request_calls', None)
        calls = calls if calls is not None else []
        if not hasattr(provider, '_settings'):
            return await function(provider, *args, **kwargs)
        from .providers import get_provider
        from .providers.deepseek import DeepSeekProvider
        source = provider if provider.name == 'deepseek' else get_provider('deepseek')
        settings = source._settings.model_copy(update={
            'deepseek_model': MODEL, 'deepseek_router_model': MODEL,
            'module_router_reasoning_effort': EFFORT, 'provider_max_retries': 0,
        })
        scoped = DeepSeekProvider(settings)
        # Preserve the old K3 channel's effective minimum output allowance.
        kwargs['max_tokens'] = max(kwargs.get('max_tokens') or settings.router_reasoning_max_tokens, 2048)
        started = {}

        async def sent(request):
            body = json.loads(request.content)
            record = {'request': RouterRequest.model_validate(body).model_dump(exclude_none=True)}
            calls.append(record)
            started[id(request)] = (perf_counter(), record)

        async def received(response):
            began, record = started.pop(id(response.request))
            record.update(http_status=response.status_code,
                          request_id=response.headers.get('x-request-id'))
            # Router calls are non-streaming. Read the actual response, never
            # reconstruct a request from UI preferences or expose auth headers.
            await response.aread()
            record['duration_ms'] = round((perf_counter()-began)*1000)
            try:
                body = response.json()
                choice = (body.get('choices') or [{}])[0]
                message = choice.get('message') or {}
                record['response'] = RouterResponse(model=body.get('model'),
                    content=message.get('content'),
                    reasoning_content=message.get('reasoning_content') or message.get('reasoning'),
                    finish_reason=choice.get('finish_reason'),
                    error_code=str((body.get('error') or {}).get('code') or response.status_code) if response.is_error else None,
                ).model_dump(exclude_none=True)
            except (ValueError, TypeError, AttributeError, IndexError):
                record['response'] = {'error_code': 'unreadable_provider_response'}
        scoped._client._client.event_hooks['request'].append(sent)
        scoped._client._client.event_hooks['response'].append(received)
        try:
            decision = await function(scoped, *args, **kwargs)
            return replace(decision, request_calls=calls)
        finally:
            for began, record in started.values():
                record["duration_ms"] = round((perf_counter()-began)*1000)
                record["response"] = {"error_code": "router_call_interrupted"}
            with suppress(Exception):
                await scoped._client.close()
    return call
