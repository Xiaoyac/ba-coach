from types import SimpleNamespace
from sqlalchemy import select
from app.context_epochs import estimate_tokens, source_digest
from app.context_usage import history_usage
from app.models import ConversationContextCheckpoint, ConversationMessage
from test_main_prefix_cache import epoch_db
from test_conversations import other_headers

SETTINGS = SimpleNamespace(main_history_token_budget=24000, max_history_messages=80)


async def test_usage_counts_valid_summary_and_remaining_history_without_writes(epoch_db):
    db, rows = epoch_db
    source = list((await db.execute(select(ConversationMessage.id, ConversationMessage.role,
        ConversationMessage.content, ConversationMessage.created_at).order_by(ConversationMessage.position))).all())
    summary = '有意愿但尚未执行。'
    checkpoint = ConversationContextCheckpoint(conversation_id=rows[0].conversation_id,
        through_message_id=rows[39].id, source_digest=source_digest(source[:40]), summary=summary)
    db.add(checkpoint); await db.commit()
    result = await history_usage(db, rows[0].conversation_id, settings=SETTINGS, epoch_enabled=True)
    assert result['summarized_messages'] == 40 and result['retained_messages'] == 61
    assert result['estimated_tokens'] == estimate_tokens(summary) + sum(estimate_tokens(r.content) for r in rows[40:])
    rows[0].content = '早期内容已修改'; await db.commit()
    result = await history_usage(db, rows[0].conversation_id, settings=SETTINGS, epoch_enabled=True)
    assert result['summarized_messages'] == 0 and result['retained_messages'] == 101
    assert await db.get(ConversationContextCheckpoint, rows[0].conversation_id) is checkpoint
    assert not db.dirty and not db.deleted


async def test_legacy_usage_has_no_false_token_capacity(epoch_db):
    db, rows = epoch_db
    result = await history_usage(db, rows[0].conversation_id, settings=SETTINGS, epoch_enabled=False)
    assert result['mode'] == 'window' and result['token_budget'] is None
    assert result['retained_messages'] == 79 and result['message_budget'] == 80


def test_context_endpoint_is_private_owner_scoped_and_contains_only_counts(client, auth_headers, other_headers, monkeypatch):
    from app.config import get_settings
    monkeypatch.setattr('app.config.get_settings', lambda: get_settings().model_copy(update={
        'deepseek_model':'kimi-k3','deepseek_base_url':'https://ark.cn-beijing.volces.com/api/coding/v3'}))
    created = client.post('/api/conversations', headers=auth_headers).json()
    url = '/api/conversations/' + created['session_id'] + '/context'
    assert client.get(url).status_code == 401
    assert client.get(url, headers=other_headers).status_code == 404
    response = client.get(url, headers=auth_headers)
    assert response.status_code == 200 and response.headers['cache-control'] == 'private, no-store'
    assert response.json()['token_budget'] == 24000
    assert set(response.json()) == {'estimated_tokens','token_budget','retained_messages',
        'summarized_messages','total_messages','message_budget','mode'}
