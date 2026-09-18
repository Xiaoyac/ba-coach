from datetime import datetime, timezone

import pytest

from app.models import Conversation, ConversationMessage
from app.routes.conversations import _detail


def project(**fields):
    message = ConversationMessage(role='assistant',content='回复',reasoning_content='思考',**fields)
    conversation = Conversation(session_id='synthetic',title='test',updated_at=datetime.now(timezone.utc),
                                pinned=False,revision=1,messages=[message])
    return _detail(conversation).model_dump()['messages'][0]


def test_project_only_safe_recorded_timing_and_keep_channels_separate():
    row = project(time_to_first_reasoning_token_ms=952,time_to_first_content_token_ms=59766,
                  main_generation_duration_ms=61614,router_duration_ms=17000,
                  provider_request_id='must-not-expose',input_tokens=2790)
    assert row['timing'] == {'reply_thinking_ms':58814,'reply_generation_ms':61614,'router_processing_ms':17000}
    assert 'provider_request_id' not in row and 'input_tokens' not in row


def test_historical_missing_metrics_stay_unknown_not_zero():
    assert project()['timing'] is None


@pytest.mark.parametrize('first,content', [(None,200), (200,None), (300,200), (-1,200)])
def test_incomplete_or_invalid_interval_never_becomes_thinking_duration(first,content):
    assert project(time_to_first_reasoning_token_ms=first,time_to_first_content_token_ms=content)['timing'] is None


def test_real_zero_is_preserved():
    assert project(time_to_first_reasoning_token_ms=0,time_to_first_content_token_ms=0,
                   main_generation_duration_ms=0,router_duration_ms=0)['timing'] == {
                       'reply_thinking_ms':0,'reply_generation_ms':0,'router_processing_ms':0}
