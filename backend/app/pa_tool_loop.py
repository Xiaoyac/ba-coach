"""Bounded native tool loop; keep real role=tool messages and visible streaming."""
import asyncio
import json
import hashlib
from .providers.deadline import timeout
from time import perf_counter
from .providers.base import Completion, StreamDelta, ProviderError
from .pa_card_tools import POLICY
from .prompts import SystemPromptSegment


FINAL_REPLY_POLICY = """工具操作阶段已经结束。请根据本轮用户发言和上述真实工具结果，输出非空、自然的回复正文。
已有开头时继续回应用户，不重复开头。草稿保存不等于最终确认；只可说明工具确实完成的操作。
若确认卡尚未展示，应依据具体缺项继续讨论或如实说明当前未完成，不承诺下一轮会自动推送或放行，也不要让用户反复发送同意来触发后台。
不输出工具名、内部字段名或调用过程；不要把系统未整理成功说成用户没回答。"""


class PAToolReply:
    def __init__(self, provider, executor, *, max_rounds, telemetry, timeout_seconds=90, transition_prompt=None):
        self.provider,self.executor,self.max_rounds=provider,executor,max_rounds
        self.transition_prompt=transition_prompt
        self.timeout_seconds=timeout_seconds
        self.telemetry=telemetry
        telemetry['pa_tools']={'enabled':True,'calls':executor.trace,'requests':[]}

    async def stream(self, *, system, messages):
        try:
            async with timeout(self.timeout_seconds):
                async for delta in self._stream(system=system,messages=messages):yield delta
        except (TimeoutError, asyncio.TimeoutError) as exc:
            self.telemetry['pa_tools']['error']='total_timeout'
            raise ProviderError('PA tool generation timed out') from exc

    async def _stream(self, *, system, messages):
        from .providers.base import as_text, as_segments
        system=[*as_segments(system),SystemPromptSegment(POLICY)]
        self.telemetry['pa_tools'].update(system=as_text(system),tools=self.executor.definitions)
        self.telemetry['main_input']={'system':as_text(system),'messages':[{'role':m.role,'content':m.content} for m in messages],
                                      'tools':self.executor.definitions,'tool_choice':'auto'}
        from .providers.prompt_cache import ordered_messages
        self.telemetry['main_input']['wire_messages']=ordered_messages(system,
            [{'role':m.role,'content':m.content} for m in messages])
        self.telemetry['prompt_version']=hashlib.sha256(as_text(system).encode()).hexdigest()[:16]
        history=[{'role':m.role,'content':m.content} for m in messages]
        totals={}; visible=[]; last_rid=None
        decision_mode=any(t['function']['name']=='continue_pa_conversation' for t in self.executor.definitions)
        query_allowed=True
        stop=False
        last_call=None
        same_call_streak=0
        force_final=False
        for round_no in range(self.max_rounds+1):
            started=perf_counter();text=[];thinking=[];calls=[];finish=None;rid=None;usage={}
            # Last call may only explain actual results. Never fabricate a tool
            # success or silently switch to legacy persistence on exhaustion.
            choice='none' if round_no==self.max_rounds else 'auto'
            if not decision_mode and round_no>0 and any(t['function']['name']=='continue_pa_conversation' for t in self.executor.definitions):choice='none'
            if decision_mode and choice!='none':choice='required'
            if force_final:choice='none'
            if round_no==0 and any(t['function']['name']=='get_pa_card' for t in self.executor.definitions):
                choice={'type':'function','function':{'name':'get_pa_card'}}
            available_tools = (self.executor.available_tools() if hasattr(self.executor, 'available_tools')
                               else self.executor.definitions)
            available=[t for t in available_tools if query_allowed or t['function']['name']!='get_pa_card']
            request_system = [*as_segments(system), SystemPromptSegment(FINAL_REPLY_POLICY)] if choice == 'none' else system
            for attempt in range(2):
                started=perf_counter();text=[];thinking=[];calls=[];finish=None;rid=None;usage={}
                request={'round':round_no,'system':as_text(request_system),'input_messages':list(history),'tool_choice':choice,
                         'wire_messages':ordered_messages(request_system,history),'tools':available}
                if attempt:
                    request['recovery_attempt'] = attempt
                self.telemetry['pa_tools']['requests'].append(request)
                if round_no==0:self.telemetry['main_input'].update(tool_choice=choice,tools=available)
                try:
                    async for delta in self.provider.stream_tools(system=request_system,messages=history,
                            tools=available,tool_choice=choice):
                        if delta.kind=='tool_calls':calls.extend(delta.tool_calls or [])
                        elif delta.kind=='usage':usage=delta.usage or {};rid=delta.request_id;finish=delta.finish_reason
                        elif delta.kind=='reasoning':thinking.append(delta.text);yield delta
                        else:
                            text.append(delta.text)
                            # Tool decisions are internal. Only the final
                            # response streams; partial output is never retried.
                            if choice in ('auto','none'):
                                visible.append(delta.text);yield delta
                except ProviderError as exc:
                    request.update(request_id=getattr(exc,'request_id',None) or rid,
                                   error_code='provider_error',duration_ms=int((perf_counter()-started)*1000),
                                   output_text=''.join(text),tool_calls=calls,usage=usage)
                    raise
                last_rid=rid or last_rid
                for k,v in usage.items():totals[k]=totals.get(k,0)+v
                request.update(request_id=rid,usage=usage,finish_reason=finish,duration_ms=int((perf_counter()-started)*1000),
                               tool_calls=calls,output_text=''.join(text))
                # Retry empty final wording ONCE with unchanged native tool
                # history. Never repeat mutations or synthesize user consent.
                if (attempt == 0 and choice == 'none' and not calls
                        and not ''.join(visible).strip() and finish in {'stop','length'}):
                    self.telemetry['pa_tools']['final_reply_recovery'] = {
                        'attempted':True,'original_finish_reason':finish,'original_request_id':rid}
                    request_system = [*as_segments(request_system), SystemPromptSegment(
                        '上一请求没有输出回复正文。请现在只生成给用户的正文；工具结果仍然有效，不重复执行操作，不要求用户重发。')]
                    continue
                if attempt:
                    self.telemetry['pa_tools']['final_reply_recovery'].update(
                        status='recovered' if ''.join(text).strip() else 'empty_again',request_id=rid)
                break
            if not calls:
                if choice not in ('auto','none'):raise ProviderError('Provider did not return the required PA tool decision')
                break
            if choice=='none':raise ProviderError('Provider returned tools after tool budget was exhausted')
            if len(calls)>8 or len({c['id'] for c in calls})!=len(calls):raise ProviderError('Invalid native tool batch')
            assistant={'role':'assistant','content':''.join(text) or None,'tool_calls':calls}
            if thinking:assistant['reasoning_content']=''.join(thinking)
            history.append(assistant)
            # Sequential database actions prevent two mutations from validating
            # the same stale version. The second gets a conflict and must reread.
            for call in calls:
                function=call['function']
                try:
                    args=json.dumps(json.loads(function['arguments']),ensure_ascii=False,sort_keys=True,separators=(',',':'))
                except (ValueError,TypeError):
                    args=str(function.get('arguments'))
                signature=(function['name'],args)
                same_call_streak=same_call_streak+1 if signature==last_call else 1
                last_call=signature
                if force_final or same_call_streak>=3:
                    # Do not repeat a mutation just to obtain another model response.
                    result={'status':'blocked','reason':'repeated_identical_call_limit',
                            'guidance':'同一工具和参数已重复调用。停止执行，依据已有实际结果回复，说明未完成事项。'}
                    force_final=True
                    self.telemetry['pa_tools']['repetition_blocked']=True
                else:
                    result=await self.executor.execute(call)
                    if same_call_streak==2:
                        result={**result,'loop_notice':'已连续重复相同调用；如状态没有改变，请使用已有结果，避免继续重复。'}
                history.append({'role':'tool','tool_call_id':call['id'],'content':json.dumps(result,ensure_ascii=False)})
                status=result.get('status')
                if call['function']['name']=='get_pa_card' and status=='ok':query_allowed=False
                if status in {'blocked','failed'}:query_allowed=True
                if status in {'ready_to_display','closed'}:stop=True
                if status in {'continue_conversation','saved_activity_context','confirmed','secondary_confirmed','goal_card_paused'}:
                    decision_mode=False
                    if status=='confirmed' and self.transition_prompt:
                        system=await self.transition_prompt(result['next_module'])
                        system=[*as_segments(system),SystemPromptSegment(POLICY)]
            if stop:break
        for display in self.executor.displays:
            if display not in ''.join(visible):
                yield StreamDelta(kind='content',text=('\n\n' if visible else '')+display)
                visible.append(display)
        self.telemetry['pa_tools']['budget_exhausted']=round_no==self.max_rounds
        self.telemetry['pa_tools']['tools']=self.executor.definitions
        yield StreamDelta(kind='usage',usage=totals,request_id=last_rid,finish_reason=finish)

    async def complete(self, *, system, messages):
        text=[];thought=[];usage={};rid=None;finish=None
        async for delta in self.stream(system=system,messages=messages):
            if delta.kind=='content':text.append(delta.text)
            elif delta.kind=='reasoning':thought.append(delta.text)
            elif delta.kind=='usage':usage=delta.usage;rid=delta.request_id;finish=delta.finish_reason
        return Completion(text=''.join(text),model=self.provider.model,reasoning_content=''.join(thought),
                          usage=usage,request_id=rid,finish_reason=finish)
