"""Owned PA-card operations. Native calls are proposals, never trusted state.

No account/session IDs, SQL, completion flags or module setters are exposed to
models. Existing source, version, plan-time and clinical validators own writes.
"""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
import logging
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

from sqlalchemy import select, update, insert
from fastapi import HTTPException

from .database_v2_schema import metadata as schema
from .models import Conversation, ConversationMessage
from .v2_workflow import runtime_for
from .v2_repository import V2Conflict

POLICY = '''PA 卡片通过工具操作，不从普通回复推测数据库写入。
先查询 get_pa_card 获取真实版本、卡片和可用来源；只有需要保存/确认/收尾时调用写工具。
save_pa_card 只保存用户真实表达的草稿；完整目标卡用 present_pa_card 展示，展示不等于确认。
用户对展示版本明确同意才 confirm_pa_card；用户修改时先更新草稿并重新展示，不确认旧版。
M4 按原模块 Prompt 完成必要复盘；save_pa_review 保存已知事实，close_pa_card 才结束本轮。
执行前暂时犹豫不等于最终未执行；保持/更换目标仍由下一轮 M2 与用户讨论。
工具返回 blocked/failed 不等于用户没说，先检查已有消息或版本，不机械重复提问。
只有工具返回成功才可声称保存/确认/结束。工具失败不得用普通文本假装成功。
present_pa_card/close_pa_card 返回的 display_text 由界面自动展示，不要重复抄写。
查询、修正参数和保存工具调用期间不输出面向用户的操作解说；待结果返回后自然回应。不得声称blocked操作成功，不得把未通过present_pa_card的草稿手工冒充正式确认卡。工具字段不是干预清单，不为填满参数增设流程。
'''


def enabled(settings, state):
    return bool(getattr(settings, "pa_card_tools_enabled", False) and settings.database_schema_version == 'v2'
        and state.get('subject_id') and state.get('user_message_id')
        and not (state.get('memory') or {}).get('sandbox_mode') and not state.get('forced_module'))


def specs_for(module):
    from .clinical_fields import MODULE_SPECS
    from .goal_contract import GOAL_SPEC, CHOICE_SPEC, PLAN_SPEC, M2_CONTEXT_SPEC, ACTIVITY_SPEC, CORRECTION_SPEC
    from .m4_contract import SPEC
    return MODULE_SPECS[module] + (ACTIVITY_SPEC, CORRECTION_SPEC) + ((GOAL_SPEC, CHOICE_SPEC, PLAN_SPEC, M2_CONTEXT_SPEC)
        if module == 'module_2' else (SPEC,))


def definitions(module):
    def tool(name, description, properties=None, required=()):
        return {'type':'function','function':{'name':name,'description':description,
            'parameters':{'type':'object','properties':properties or {},
                          'required':list(required),'additionalProperties':False}}}
    version = {'state_version': {'type':'integer','description':'get_pa_card 最近返回的 state_version；禁止猜测。'}}
    defs=[tool('continue_pa_conversation','本轮无需改卡片/确认/收尾，继续当前模块自然对话。用户已有需要保存的明确安排、已确认展示版本或复盘已达收尾时不能用它代替写操作。'),tool('get_pa_card','查询本用户当前 PA 卡、版本、历史核心和真实消息来源。无副作用。')]
    if module not in {'module_2','module_4'}:
        return defs
    properties={}
    for spec in specs_for(module):
        types = {'text':['string','null'],'varchar':['string','null'],'datetime':['string','null'],
                 'int':['integer','null'],'level':['integer','null'],'flag':['boolean','null'],
                 'json':['object','array','null']}
        properties[spec.name]={'type':types[spec.kind],'description':spec.prompt}
    if module=='module_2':
        score_source={'type':['object','null'],'properties':{
            'message_id':{'type':'integer'},'quote':{'type':'string','minLength':1},
            'score_text':{'type':'string','enum':[str(n) for n in range(11)]+list('零〇一二两三四五六七八九十'),
                          'description':'只填原文中的数字，例如4或四；不含“分”。'}},
            'required':['quote','score_text'],'additionalProperties':False}
        properties['difficulty_evidence']={'type':['object','null'],'properties':{
            'rating':score_source,'original':score_source},'additionalProperties':False,
            'description':'用户评分原文来源；不能把助手代评当作来源。'}
        properties['goal_proposal']={'type':['object','null'],'properties':{
            'selection_status':{'type':'string','enum':['selected','ambiguous','not_expressed','retracted']},
            'selection_role':{'type':'string','enum':['core','secondary','trial']},
            'goal_kind':{'type':'string','enum':['primary','secondary']},
            'selection_message_id':{'type':'integer'},'selection_quote':{'type':'string'},
            'activity_quote':{'type':'string'},'long_term_direction':{'type':['string','null']},
            'direction_quote':{'type':['string','null']}},
            'required':['selection_status','selection_role','goal_kind','selection_quote','activity_quote'],
            'additionalProperties':False,'description':properties['goal_proposal']['description']}
    if module=='module_4':
        properties['m4_contract']['description']+=' 工具查询中的证据对象可原样回传（message_id、quote）；后台核对来源后转换。也可直接填写逐字quote字符串。'
    data={'type':'object','properties':properties,'additionalProperties':False,
          'required':['target_activity_content','goal_proposal'] if module=='module_2' else [],
          'description':'创建新核心时必须在同一次调用中同时给target_activity_content和goal_proposal。更新已有草稿时带上现有活动、goal_proposal可null；其他字段只提交有依据的值。未知null不是追问任务。'}
    save='save_pa_card' if module=='module_2' else 'save_pa_review'
    defs.append(tool(save,'保存本轮真实信息到草稿，返回真实保存结果；不代表用户确认或本轮结束。',
                     {**version,'data':data},('state_version','data')))
    if module=='module_2':
        defs.extend([
            tool('present_pa_card','展示当前完整草稿供用户核对。必须先保存；数据不全会返回原因，不会冒充确认。',version,('state_version',)),
            tool('confirm_pa_card','用户明确同意上一条展示的同一版本卡片时确认；修改、提问、拒绝均不能调用确认。',version,('state_version',)),
        ])
    else:
        defs.append(tool('close_pa_card','本轮实际结果已确定、ABC已核对、必要教育和处理完成后结束本轮。下一轮目标选择留给M2。',
            {**version,'data':data,'summary':{'type':'string','minLength':1,'maxLength':2000,
                'description':'依据本轮已确认事实撰写的自然复盘总结。该文本作为助手工具产出显示，不能伪称用户原话。'}},
            ('state_version','data','summary')))
    return defs


class ToolRejected(ValueError):
    pass


def _json(value):
    return json.loads(json.dumps(value,ensure_ascii=False,default=str))


class PACardTools:
    def __init__(self, *, maker, session_id, user_id, user_message_id, module, provider=None):
        self.maker,self.session_id,self.user_id=maker,session_id,user_id
        self.boundary,self.module,self.provider=user_message_id,module,provider
        self.definitions=definitions(module)
        self.displays=[]
        self.trace=[]

    async def _owned(self, db):
        # Same lock order as the existing persistence/confirmation paths.
        await db.execute(select(schema.tables['user_profile'].c.uuid).where(
            schema.tables['user_profile'].c.uuid==self.user_id).with_for_update())
        conversation=(await db.execute(select(Conversation).where(Conversation.session_id==self.session_id)
            .with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()
        if not conversation or conversation.subject_id!=self.user_id:
            raise ToolRejected('owned_conversation_unavailable')
        rt=schema.tables['conversation_runtime_states']
        state=(await db.execute(select(rt).where(rt.c.conversation_id==conversation.id).with_for_update())).mappings().one_or_none()
        latest=(await db.execute(select(ConversationMessage).where(ConversationMessage.conversation_id==conversation.id)
            .order_by(ConversationMessage.position.desc(),ConversationMessage.id.desc()).limit(1).with_for_update())).scalar_one_or_none()
        if not state or not latest or latest.id!=self.boundary or latest.role!='user':
            raise ToolRejected('current_user_boundary_changed')
        if (state['memory'] or {}).get('sandbox_mode') or state['flow_status'] in {'paused','completed'}:
            raise ToolRejected('write_window_closed')
        return conversation,dict(state),latest

    async def snapshot(self, db, conversation, state):
        from .program_confirmation import draft
        from .pa_lifecycle import unfinished_core_goals,reviewed_goal
        from .goal_contract import evidence_messages
        pending=await draft(db,state,self.user_id) if state['current_module'] in {'module_2','module_4'} else None
        messages=await evidence_messages(db,conversation.id,self.user_id)
        plans,cycles,goals=(schema.tables[k] for k in ('module_two_record','pa_cycles','pa_goals'))
        confirmed=(await db.execute(select(plans).join(cycles,cycles.c.module_two_record_id==plans.c.id)
            .join(goals,goals.c.id==cycles.c.goal_id).where(cycles.c.id==state['active_cycle_id'],
                plans.c.goal_id==goals.c.id,goals.c.user_id==self.user_id,
                plans.c.record_status=='confirmed',plans.c.confirmation_status=='confirmed'))).mappings().one_or_none()
        return _json({'status':'ok','state_version':state['row_version'],'module':state['current_module'],
            'goal_id':state['active_goal_id'],'cycle_id':state['active_cycle_id'],
            'draft':dict(pending) if pending else None,
            'confirmed_plan':dict(confirmed) if confirmed else None,
            'unfinished_core_goals':await unfinished_core_goals(db,user_id=self.user_id),
            'last_reviewed_goal':await reviewed_goal(db,user_id=self.user_id,memory=state['memory']),
            'recent_sources':[{'message_id':m.id,'role':m.role,'text':m.content} for m in messages[-12:]]})

    async def execute(self, call):
        name=call.get('function',{}).get('name'); raw=call.get('function',{}).get('arguments','')
        try:
            args=json.loads(raw)
            definition=next((d['function'] for d in self.definitions if d['function']['name']==name),None)
            if not definition or not isinstance(args,dict):raise ToolRejected('unknown_tool_or_arguments')
            params=definition['parameters']
            if set(args)-set(params['properties']) or set(params['required'])-set(args):raise ToolRejected('invalid_arguments')
            if name not in {'get_pa_card','continue_pa_conversation'} and type(args.get('state_version')) is not int:raise ToolRejected('invalid_state_version')
            if 'data' in args:
                if not isinstance(args['data'],dict) or set(args['data'])-{s.name for s in specs_for(self.module)}:
                    raise ToolRejected('invalid_data_fields')
            if name=='close_pa_card' and (not isinstance(args['summary'],str) or not 0<len(args['summary'].strip())<=2000):
                raise ToolRejected('invalid_summary')
            key=hashlib.sha256(json.dumps([self.boundary,name,args],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            async with self.maker() as db:
                conversation,state,user=await self._owned(db)
                logs=schema.tables['ai_decision_logs']
                receipts=(await db.execute(select(logs.c.decision_value).where(logs.c.conversation_id==conversation.id,
                    logs.c.turn_id==str(self.boundary),logs.c.decision_type=='pa_native_tool'))).scalars().all()
                prior=next((r['result'] for r in receipts if r.get('operation_key')==key),None)
                if prior is not None:
                    result={**prior,'replayed':True}
                elif name=='continue_pa_conversation':
                    result={'status':'continue_conversation','module':state['current_module'],'state_version':state['row_version']}
                elif name=='get_pa_card':
                    result=await self.snapshot(db,conversation,state)
                else:
                    if state['current_module']!=self.module:raise ToolRejected('module_changed_requery_state')
                    if args['state_version']!=state['row_version']:raise ToolRejected('state_changed_requery_state')
                    # Start the outer write transaction before confirmation's
                    # savepoint (also required by SQLite's deferred BEGIN).
                    receipt=await db.execute(insert(logs),{'conversation_id':conversation.id,'turn_id':str(self.boundary),
                        'goal_id':state['active_goal_id'],'cycle_id':state['active_cycle_id'],'module_name':self.module,
                        'decision_type':'pa_native_tool','decision_value':{'operation_key':key,'status':'pending'},
                        'evidence_message_ids':[self.boundary]})
                    receipt_id=receipt.inserted_primary_key[0]
                    result=await self._mutate(db,conversation,state,user,name,args,call['id'])
                    _,fresh=await runtime_for(db,self.session_id)
                    result=_json({**result,'state_version':fresh['row_version'],'module':fresh['current_module']})
                    await db.execute(update(logs).where(logs.c.id==receipt_id).values(
                        goal_id=fresh['active_goal_id'],cycle_id=fresh['active_cycle_id'],
                        decision_value={'operation_key':key,'tool':name,'tool_call_id':call['id'],'result':result}))
                    conversation.revision+=1
                    await db.commit()
            if name=='save_pa_card' and result.get('status')=='draft_saved':
                self.displays.clear()
            if result.get('display_text') and result['display_text'] not in self.displays:
                self.displays.append(result['display_text'])
        except (ToolRejected,HTTPException,V2Conflict) as exc:
            result={'status':'blocked','reason':str(getattr(exc,'detail',exc)),
                    'guidance':'检查已有消息、当前版本和工具结果；不要把系统未保存解释为用户未回答。'}
        except SQLAlchemyError:
            logger.exception('PA tool transaction failed')
            result={'status':'failed','reason':'storage_error','guidance':'本次操作未提交；不要声称保存成功。'}
        except (ValueError,TypeError,KeyError):
            result={'status':'blocked','reason':'invalid_arguments'}
        self.trace.append({'tool_call_id':call.get('id'),'name':name,'arguments':raw,'result':result})
        return result

    async def _mutate(self, db, conversation, state, user, name, args, call_id):
        from .v2_workflow import persist_record,create_goal_from_agent_dialogue,record_steps
        from .program_confirmation import draft,record_hash,validate_confirmation,commit_confirmation
        from .goal_contract import evidence_messages,save_m2_activity_context,capture_activities
        from .clinical_store import coerce
        from .dialogue_confirmation import render_confirmation_summary,confirmation_fields_complete
        rt=schema.tables['conversation_runtime_states']
        if name in {'save_pa_card','save_pa_review','close_pa_card'}:
            raw=args['data']
            data=coerce(specs_for(self.module),raw)
            invalid=[key for key,value in raw.items() if value is not None and key not in data]
            if invalid:raise ToolRejected('invalid_field_types: '+','.join(invalid))
            if self.module=='module_2' and any(k in data for k in ('difficulty_rating','difficulty_original')):
                from .goal_contract import difficulty_values
                messages=await evidence_messages(db,conversation.id,self.user_id)
                verified=difficulty_values(data,messages)
                invalid=[k for k in ('difficulty_rating','difficulty_original') if k in data and verified.get(k)!=data[k]]
                if invalid:
                    raise ToolRejected('invalid_difficulty_source: 评分须有真实用户原文，score_text只含数字如4，不含分；请修正参数引用，不要重问用户')
            if self.module=='module_4' and isinstance(data.get('m4_contract'),dict):
                from .goal_contract import source_reference
                messages=await evidence_messages(db,conversation.id,self.user_id)
                evidence=dict(data['m4_contract'])
                for key,value in evidence.items():
                    if key.endswith('_quote') and isinstance(value,dict):
                        role='assistant' if key in {'summary_quote','education_quote','review_summary_quote'} else 'user'
                        if source_reference(value,messages,role=role) is None:
                            raise ToolRejected('invalid_review_source: '+key)
                        # Query returns source-bound evidence objects, while
                        # the legacy normalizer accepts literal quote strings.
                        # Adapt only after validating the supplied ID and role.
                        evidence[key]=value['quote']
                data['m4_contract']=evidence
            data.update(_source_session_id=self.session_id,_source_user_message_id=self.boundary)
            if name=='save_pa_card':
                messages=await evidence_messages(db,conversation.id,self.user_id)
                await save_m2_activity_context(db,conversation=conversation,state=state,raw=data.get('m2_activity_context'),messages=messages)
                await capture_activities(db,user_id=self.user_id,conversation=conversation,state=state,
                    raw=data.get('activity_observations'),messages=messages,corrections=data.get('activity_corrections'))
                if not state['active_cycle_id'] or data.get('core_goal_choice'):
                    diagnostics={}
                    created=await create_goal_from_agent_dialogue(db,session_id=self.session_id,user_id=self.user_id,data=data,
                        completed_steps=[],assistant_message_id=self.boundary,diagnostics=diagnostics)
                    if not created:
                        reason=diagnostics.get('goal_creation',{}).get('reason_code')
                        if reason in {'secondary_only','selection_not_core'}:
                            return {'status':'saved_activity_context','core_unchanged':True}
                        raise ToolRejected(reason or 'goal_creation_not_applied')
                    _,state=await runtime_for(db,self.session_id)
                    data.pop('core_goal_choice',None)
            summary={'call_id':call_id,'text':args['summary'].strip()} if name=='close_pa_card' else None
            if not state['active_cycle_id']:raise ToolRejected('no_active_cycle')
            record_id=await persist_record(self.maker,module=self.module,user_id=self.user_id,data=data,
                cycle_id=state['active_cycle_id'],db_session=db,tool_closing_summary=summary)
            if not record_id:raise ToolRejected('record_write_not_applied')
            await record_steps(db,session_id=self.session_id,user_id=self.user_id,module=self.module,
                requested_target=self.module,steps=[],assistant_message_id=self.boundary,allow_transition=False)
            if name!='close_pa_card':
                if name=='save_pa_card':
                    _,fresh=await runtime_for(db,self.session_id)
                    memory=dict(fresh['memory'] or {});memory.pop('pa_tool_display',None)
                    await db.execute(update(rt).where(rt.c.conversation_id==conversation.id).values(memory=memory))
                _,fresh=await runtime_for(db,self.session_id)
                saved=await draft(db,fresh,self.user_id)
                return {'status':'draft_saved','record_id':record_id,'confirmed':False,
                        'saved_draft':_json(dict(saved)) if saved else None}
            _,fresh=await runtime_for(db,self.session_id)
            pending=await draft(db,fresh,self.user_id)
            pending,action=await validate_confirmation(db,conversation=conversation,state=fresh,user_id=self.user_id,
                session_id=self.session_id,payload=SimpleNamespace(record_id=pending['id'],record_hash=record_hash(pending),row_version=fresh['row_version']),
                extraction_assistant_message_id=self.boundary)
            from .m4_contract import contract_for
            source_id=contract_for(pending)['evidence']['confirmation_quote']['message_id']
            source=await db.get(ConversationMessage,source_id)
            await commit_confirmation(db,conversation=conversation,state=fresh,user_id=self.user_id,pending=pending,
                message=source,review_action=None,snapshot_hash=record_hash(pending),source='native_pa_tool',boundary_message_id=self.boundary)
            return {'status':'closed','cycle_id':state['active_cycle_id'],'goal_retained':True,
                    'next_goal_choice':'deferred_to_M2','display_text':summary['text']}
        pending=await draft(db,state,self.user_id)
        if name=='present_pa_card':
            if not pending or not confirmation_fields_complete('module_2',pending):
                raise ToolRejected('plan_not_ready_to_present: 请核对get_pa_card的实际草稿，检查活动、安排、有效用户评分、障碍及应对来源；已有消息中的信息修正参数即可')
            text=render_confirmation_summary('module_2',pending)
            from .dialogue_confirmation import fingerprint
            memory={**(state['memory'] or {}),'pa_tool_display':{'record_id':pending['id'],
                'fingerprint':fingerprint(pending),'user_message_id':self.boundary,'text':text}}
            await db.execute(update(rt).where(rt.c.conversation_id==conversation.id).values(memory=memory,row_version=rt.c.row_version+1))
            return {'status':'ready_to_display','record_id':pending['id'],'display_text':text,'confirmed':False}
        if name=='confirm_pa_card':
            from .dialogue_confirmation import precommit_user_confirmation
            receipt=await precommit_user_confirmation(db,session_id=self.session_id,user_id=self.user_id,
                user_message_id=self.boundary,confirmation_provider=self.provider)
            if not receipt:raise ToolRejected('confirmation_not_verified')
            return {'status':'confirmed','cycle_id':state['active_cycle_id'],'next_module':receipt[0]}
        raise ToolRejected('unknown_tool')


async def finalize_tool_display(db, *, session_id, user_id, assistant_message_id):
    """Bind the actually stored card to its version; no extraction or consent."""
    from .program_confirmation import draft
    from .dialogue_confirmation import fingerprint
    from .v2_workflow import record_steps
    await db.execute(select(schema.tables['user_profile'].c.uuid).where(schema.tables['user_profile'].c.uuid==user_id).with_for_update())
    await db.execute(select(Conversation).where(Conversation.session_id==session_id).with_for_update())
    conversation,state=await runtime_for(db,session_id)
    if not conversation or conversation.subject_id!=user_id or state['current_module']!='module_2':return
    display=(state['memory'] or {}).get('pa_tool_display')
    if not display:return
    latest_id=await db.scalar(select(ConversationMessage.id).where(ConversationMessage.conversation_id==conversation.id)
        .order_by(ConversationMessage.position.desc(),ConversationMessage.id.desc()).limit(1))
    if latest_id!=assistant_message_id:return
    assistant=await db.get(ConversationMessage,assistant_message_id)
    pending=await draft(db,state,user_id)
    if (not assistant or assistant.conversation_id!=conversation.id or assistant.role!='assistant'
        or display['text'] not in assistant.content or not pending or fingerprint(pending)!=display['fingerprint']):return
    previous=(await db.execute(select(ConversationMessage).where(ConversationMessage.conversation_id==conversation.id,
        ConversationMessage.position<assistant.position).order_by(ConversationMessage.position.desc()).limit(1))).scalar_one_or_none()
    if not previous or previous.id!=display['user_message_id']:return
    memory=dict(state['memory']);memory.pop('pa_tool_display',None)
    freshness=dict(memory.get('module_extraction_freshness') or {})
    freshness['module_2']={'assistant_message_id':assistant.id,'cycle_id':state['active_cycle_id']}
    memory['module_extraction_freshness']=freshness
    rt=schema.tables['conversation_runtime_states']
    await db.execute(update(rt).where(rt.c.conversation_id==conversation.id).values(memory=memory))
    await record_steps(db,session_id=session_id,user_id=user_id,module='module_2',requested_target='module_2',steps=[],
                       assistant_message_id=assistant.id,allow_transition=False)
