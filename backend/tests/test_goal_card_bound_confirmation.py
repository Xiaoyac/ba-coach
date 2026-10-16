"""Bound button across unrelated turns, with real isolated persistence."""
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from sqlalchemy import delete, update
from app.database_v2_schema import metadata as schema
from app.goal_card_interaction import bind_card_action, confirmation_button_text, validate_card_action
from app.goal_card_workspace import read_card_row
from app.models import ConversationMessage
from app.pre_reply_routing import route_before_reply
from app.structured_goal_confirmation import confirm_goal_button
from app.v2_workflow import runtime_for
from test_goal_overview import goal_api
from test_goal_card_interaction import enable_form, message
from test_structured_goal_confirmation import ready_button

async def interpose(goal_api,context,kind,text,modern=True):
    db,state,ctx,card,anchor=await ready_button(goal_api,context,kind)
    user=await db.get(ConversationMessage,state['user_message_id']);user.content=text
    logs=schema.tables['ai_decision_logs']
    await db.execute(delete(logs).where(logs.c.conversation_id==1,logs.c.turn_id==str(user.id),logs.c.decision_type=='goal_card_user_action'))
    await db.commit()
    await message(db,'请核对当前卡片；普通交流不会改变卡片内容。','assistant')
    status=(await goal_api[0].get('/api/program/chat-a/goal-card')).json()['card']['confirmation']
    label='核心目标' if kind=='primary' else '次要目标'
    text=f'确认这张{label}卡，就按这个安排。' if modern else confirmation_button_text(kind,card['revision'])
    user=await message(db,text);conversation,_=await runtime_for(db,'chat-a')
    metadata={'goal_card_action':'confirm','goal_card_id':card['id'],'goal_card_revision':str(card['revision']),'goal_card_display_message_id':str(anchor.id)}
    await bind_card_action(db,conversation,user.id,metadata);await db.commit()
    return db,{**state,'user_message_id':user.id,'user_input':text},ctx,card,status,metadata

@pytest.mark.parametrize('kind',['primary','secondary'])
@pytest.mark.parametrize('modern',[False,True])
@pytest.mark.parametrize('aside',['我刷新网页之后，更新了卡片','今天这里下雨了，确认按钮在哪里？','先等等，网页正在加载。','我撤回了发给朋友的一条消息。'])
async def test_interlude_keeps_exact_card_confirmable_and_receipt_is_durable(goal_api,context,monkeypatch,kind,modern,aside):
    db,state,ctx,card,status,metadata=await interpose(goal_api,context,kind,aside,modern)
    assert status['allowed'],status
    await validate_card_action(db,session_id='chat-a',user_id='a',metadata=metadata,text=state['user_input'])
    forbidden=AsyncMock(side_effect=AssertionError('button needs no model'))
    monkeypatch.setattr('app.pre_reply_routing.decide_target_module_with_reasoning',forbidden)
    monkeypatch.setattr('app.confirmation_intent.semantic_confirmation',forbidden)
    events=[];monkeypatch.setattr('app.pre_reply_routing._emit',events.append)
    result=await route_before_reply(state,ctx)
    assert result['confirmation_receipt']['status']==('confirmed' if kind=='primary' else 'secondary_confirmed'),result
    await db.rollback();row=await read_card_row(db,1,'a')
    assert row['phase']=='confirmed' and row['confirmation_message_id']==state['user_message_id']
    assert result['extracted_intent']==('module_3' if kind=='primary' else 'module_2')
    receipt=next(e['goal_card_confirmation'] for e in events if 'goal_card_confirmation' in e)
    assert receipt['saved'] and receipt['card_id']==card['id'] and receipt['revision']==card['revision']
    assert receipt['user_message_id']==state['user_message_id'] and '保存' in receipt['message']
    assert receipt['card']['phase']=='confirmed' and receipt['card']['revision']>card['revision']
    forbidden.assert_not_called()

@pytest.mark.parametrize('kind',['primary','secondary'])
@pytest.mark.parametrize('text',['先不要保存，我想改成明天。','这张计划先不确认。','我想修改这个安排。'])
async def test_pending_amendment_or_withdrawal_blocks_ui_and_commit(goal_api,context,kind,text):
    db,state,ctx,card,status,metadata=await interpose(goal_api,context,kind,text)
    assert not status['allowed'] and status['code']=='goal_card_unresolved_change'
    with pytest.raises(HTTPException) as exc:
        await validate_card_action(db,session_id='chat-a',user_id='a',metadata=metadata,text=state['user_input'])
    assert exc.value.status_code==409 and '修改' in exc.value.detail
    result=await confirm_goal_button(state,ctx)
    assert result['reason']==status['code'] and result['status']=='blocked'
    await db.rollback();assert (await read_card_row(db,1,'a'))['phase']=='ready'

@pytest.mark.parametrize('kind',['primary','secondary'])
@pytest.mark.parametrize('tamper',['display_deleted','display_text','review','paused','revision','metadata_anchor'])
async def test_invalid_card_or_anchor_cannot_confirm(goal_api,context,kind,tamper):
    db,state,ctx,card,anchor=await ready_button(goal_api,context,kind);table=schema.tables['goal_card_workspaces']
    if tamper=='display_deleted': await db.delete(anchor)
    elif tamper=='display_text': anchor.content='这不是目标卡。'
    elif tamper=='review': await db.execute(update(table).values(review={}))
    elif tamper=='paused': await db.execute(update(table).values(phase='paused'))
    elif tamper=='revision': await db.execute(update(table).values(revision=card['revision']+1))
    else:
        logs=schema.tables['ai_decision_logs']
        value={'action':'confirm','card_id':card['id'],'revision':card['revision'],'display_message_id':99999}
        await db.execute(update(logs).where(logs.c.decision_type=='goal_card_user_action').values(decision_value=value))
    await db.commit();result=await confirm_goal_button(state,ctx)
    assert result['status']=='blocked',result
    await db.rollback();assert (await read_card_row(db,1,'a'))['phase']!='confirmed'
    if tamper not in {'revision','metadata_anchor'}:
        status=(await goal_api[0].get('/api/program/chat-a/goal-card')).json()['card']['confirmation']
        assert not status['allowed'],status

@pytest.mark.parametrize('text',['好的','确认这张次要目标卡，就按这个安排。但是改成明天。','不要确认这张次要目标卡，就按这个安排。'])
async def test_metadata_does_not_convert_arbitrary_text_into_consent(goal_api,context,text):
    db,state,ctx,card,anchor=await ready_button(goal_api,context,'secondary',text=text)
    metadata={'goal_card_action':'confirm','goal_card_id':card['id'],'goal_card_revision':str(card['revision'])}
    with pytest.raises(HTTPException):
        await validate_card_action(db,session_id='chat-a',user_id='a',metadata=metadata,text=text)
    result=await confirm_goal_button(state,ctx)
    assert result['status']=='blocked'
    assert (await read_card_row(db,1,'a'))['phase']=='ready'

@pytest.mark.parametrize('kind',['primary','secondary'])
async def test_newer_turn_cannot_be_bypassed_by_an_old_bound_click(goal_api,context,kind):
    db,state,ctx,card,anchor=await ready_button(goal_api,context,kind)
    await message(db,'先不要确认，我想修改这个安排。')
    result=await confirm_goal_button(state,ctx)
    assert result['status']=='blocked' and result['reason']=='current_user_boundary_changed'
    assert (await read_card_row(db,1,'a'))['phase']=='ready'

@pytest.mark.parametrize('kind',['primary','secondary'])
async def test_plain_good_after_an_interlude_still_needs_adjacent_proposal(goal_api,context,kind):
    db,state,ctx,card,status,metadata=await interpose(goal_api,context,kind,'我刷新网页之后，更新了卡片')
    user=await db.get(ConversationMessage,state['user_message_id']);user.content='好的'
    logs=schema.tables['ai_decision_logs']
    await db.execute(delete(logs).where(logs.c.turn_id==str(user.id),logs.c.decision_type=='goal_card_user_action'))
    await db.commit()
    assert await confirm_goal_button(state,ctx) is None
    from test_goal_card_interaction import tools,invoke
    args={} if kind=='primary' else {'card_id':card['id'],'card_revision':card['revision']}
    result=await invoke(tools(db,user.id),'confirm_pa_card' if kind=='primary' else 'confirm_secondary_goal_card',**args)
    assert result['status']=='blocked'
    assert (await read_card_row(db,1,'a'))['phase']=='ready'

async def test_action_receipt_write_failure_is_explicit_not_a_model_confirmation(goal_api,monkeypatch):
    from app.routes.chat import _persist_user_message
    from app.config import get_settings
    monkeypatch.setattr('app.routes.chat.get_settings', get_settings)
    from unittest.mock import AsyncMock
    from sqlalchemy.exc import SQLAlchemyError
    from test_goal_card_interaction import open_primary,submit_form,review,invoke,persist_display
    client,db,_=goal_api
    _,_,card=await open_primary(db,'我想另外做家务。',kind='secondary')
    _,executor,card=await submit_form(client,db,card,{'activity_content':'家务'})
    checked=await review(executor,card,near_term=None,manageable=None)
    shown=await invoke(executor,'present_secondary_goal_card',card_id=card['id'],card_revision=checked['goal_card']['revision'])
    await persist_display(db,shown['display_text']);card=shown['goal_card']
    monkeypatch.setattr('app.goal_card_interaction.bind_card_action',AsyncMock(side_effect=SQLAlchemyError('fixture')))
    with pytest.raises(HTTPException) as exc:
        await _persist_user_message(db,subject_id='a',session_id='chat-a',user_text=confirmation_button_text('secondary',card['revision']),metadata={'goal_card_action':'confirm','goal_card_id':card['id'],'goal_card_revision':str(card['revision'])})
    assert exc.value.status_code==409 and '未能保存' in exc.value.detail
    assert (await read_card_row(db,1,'a'))['phase']=='ready'
