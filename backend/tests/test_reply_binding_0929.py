"""Reply identity survives gaps, repeated text and standalone assistant rows."""
from datetime import datetime, timezone
from app.models import Conversation, ConversationMessage
from app.routes.conversations import _detail


def test_projection_binds_reserved_positions_not_list_adjacency():
    conv = Conversation(id=1, session_id='test', subject_id='a', title='test',
                        revision=1, pinned=False, updated_at=datetime.now(timezone.utc))
    conv.messages = [ConversationMessage(id=i, conversation_id=1, position=p, role=r, content=t)
                     for i,p,r,t in [
                         (1,-1,'assistant','opening'), (2,0,'user','好的'),
                         (3,2,'user','好的'), (4,3,'assistant','第二条的回复'),
                         (5,5,'assistant','独立提醒'), (6,6,'user','好的'),
                     ]]
    detail = _detail(conv)
    assert [m.reply_to_message_id for m in detail.messages] == [None,None,None,3,None,None]
    assert detail.messages[-1].id == 6


def test_legacy_ambiguous_position_is_not_guessed():
    conv = Conversation(id=1, session_id='test', subject_id='a', title='test',
                        revision=1, pinned=False, updated_at=datetime.now(timezone.utc))
    conv.messages = [ConversationMessage(id=i, conversation_id=1, position=p, role=r, content='好的')
                     for i,p,r in [(1,0,'user'),(2,0,'user'),(3,1,'assistant')]]
    assert _detail(conv).messages[-1].reply_to_message_id is None


def test_unpersisted_message_has_no_reply_binding():
    conv = Conversation(session_id='test', title='test', revision=1, pinned=False,
                        updated_at=datetime.now(timezone.utc))
    conv.messages = [ConversationMessage(role='assistant', content='unpersisted')]
    assert _detail(conv).messages[0].reply_to_message_id is None


def test_stream_identity_matches_persisted_reply(client, auth_headers):
    import json
    conversation = client.post('/api/conversations', headers=auth_headers).json()
    response = client.post('/api/chat/stream', headers=auth_headers,
        json={'session_id':conversation['session_id'], 'message':'好的'})
    assert response.status_code == 200
    meta = next(json.loads(frame.split('data: ',1)[1]) for frame in response.text.split('\n\n')
                if frame.startswith('event: meta') and 'user_message_id' in frame)
    detail = client.get('/api/conversations/'+conversation['session_id'], headers=auth_headers).json()
    assert detail['messages'][-2]['id'] == meta['user_message_id']
    assert detail['messages'][-1]['reply_to_message_id'] == meta['user_message_id']
