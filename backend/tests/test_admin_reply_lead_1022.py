"""Admins switch the 承接 opening for their own chats; members follow the server default."""
import pytest

from test_admin_accounts import admin_headers  # noqa: F401


@pytest.fixture
def lead_calls(monkeypatch):
    from app import provisional_reply
    calls = []
    async def wrapped(events, **kwargs):
        calls.append(kwargs['session_id'])
        async for event in events:
            yield event
    monkeypatch.setattr(provisional_reply, 'with_natural_lead', wrapped)
    return calls


def stream(client, headers, sid, reply_lead):
    body = {'session_id': sid, 'message': '我想开始散步'}
    if reply_lead is not None:
        body['reply_lead'] = reply_lead
    response = client.post('/api/chat/stream', headers=headers, json=body)
    assert response.status_code == 200, response.text
    assert 'event: done' in response.text


def admin_ack_deep(client, headers):
    response = client.post('/api/conversations', headers=headers, json={'reply_mode': 'ack_deep'})
    assert response.status_code in (200, 201), response.text
    assert response.json()['reply_mode'] == 'ack_deep'
    return response.json()['session_id']


def test_member_cannot_turn_the_opening_on(client, auth_headers, lead_calls):
    sid = client.post('/api/conversations/current', headers=auth_headers).json()['session_id']
    stream(client, auth_headers, sid, True)
    assert lead_calls == []


def test_admin_turns_the_opening_on_for_their_own_chat(client, admin_headers, lead_calls):
    sid = admin_ack_deep(client, admin_headers)
    stream(client, admin_headers, sid, None)
    assert lead_calls == []
    stream(client, admin_headers, sid, True)
    assert lead_calls == [sid]


def test_admin_turning_it_off_wins_over_the_server_default(client, admin_headers, auth_headers, lead_calls, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), 'reply_lead_enabled', True)
    sid = admin_ack_deep(client, admin_headers)
    stream(client, admin_headers, sid, False)
    assert lead_calls == []
    member = client.post('/api/conversations/current', headers=auth_headers).json()['session_id']
    stream(client, auth_headers, member, False)
    assert lead_calls == [member]
