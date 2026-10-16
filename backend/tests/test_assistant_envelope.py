import json
import pytest
from app.assistant_content import unwrap_assistant_message
from app.context_pipeline import prepare_context
from app.graph.nodes import VisibleReplyBuffer, _finish_stream_channels
from app.reasoning import ThinkingTagStreamGuard, normalize_reasoning_channels
from app.schemas import Message

WRAPPED = '<message><datetime>2026-09-29T22:23:43+08:00</datetime><content>你好，1 &lt; 2 &amp; 3。\n再聊聊。</content></message>'
BODY = '你好，1 < 2 & 3。\n再聊聊。'


def test_assistant_history_unwraps_without_changing_user_evidence():
    history = [Message(role='user', content=WRAPPED), Message(role='assistant',content=WRAPPED)]
    result = prepare_context(system=[],history=history,user_input='好的',max_history_messages=80)
    assert result.messages[1].content == BODY
    assert result.messages[0].source_content == WRAPPED
    assert '&lt;message&gt;' in result.messages[0].content
    assert history[1].content == WRAPPED
    assert normalize_reasoning_channels(WRAPPED,None).reply == BODY
    assert normalize_reasoning_channels(WRAPPED,None,unwrap_message=False).reply == WRAPPED


@pytest.mark.parametrize('text', [WRAPPED, json.dumps({'chat_reply':WRAPPED},ensure_ascii=False)])
@pytest.mark.parametrize('width',[1,7,1000])
def test_stream_never_emits_envelope_including_json_reply(text,width):
    guard=ThinkingTagStreamGuard.create();buffer=VisibleReplyBuffer.create();emitted=[]
    for i in range(0,len(text),width):
        for chunk in guard.push(text[i:i+width]):
            emitted.extend(buffer.push(chunk))
        assert '<message>' not in ''.join(emitted)
        assert '<datetime>' not in ''.join(emitted)
    visible,_,remaining=_finish_stream_channels(guard,buffer,[])
    emitted.extend(remaining)
    assert visible == BODY
    assert ''.join(emitted) == BODY


@pytest.mark.parametrize('text',['普通回复，1 < 2。','这里的 <content> 是示例。','<div>正常正文</div>', '解释：'+WRAPPED])
def test_non_envelope_prose_is_preserved(text):
    assert unwrap_assistant_message(text)==text
    buffer=VisibleReplyBuffer.create();emitted=[]
    for char in text:emitted.extend(buffer.push(char))
    visible,remaining=buffer.finish()
    assert visible==text
    assert ''.join(emitted+remaining)==text


def test_plain_reply_keeps_immediate_streaming():
    buffer=VisibleReplyBuffer.create()
    assert buffer.push('你好')==['你好']


@pytest.mark.parametrize('text', [
    '<datetime>2035-03-10T12:30:00+08:00</datetime>你好',
    '<datetime>2035-03-10</datetime>你好',
    '<datetime>unknown</datetime>你好',
    '<datetime>2035-03-10T12:30:00Z</datetime><content>你好</content>',
    '<message><datetime>2035-03-10T12:30:00+08:00</datetime><content>你好',
    '<message><datetime>unknown</datetime><content>你好</content>',
    '<message><datetime>unknown</datetime><content>你好</content></mess',
    '<message><datetime>unknown</datetime><content>你好</cont',
])
@pytest.mark.parametrize('width', [1, 7, 1000])
def test_generated_time_is_removed_from_complete_or_interrupted_stream(text, width):
    guard = ThinkingTagStreamGuard.create()
    buffer = VisibleReplyBuffer.create()
    emitted = []
    for start in range(0, len(text), width):
        for chunk in guard.push(text[start:start + width]):
            emitted.extend(buffer.push(chunk))
        assert '<datetime>' not in ''.join(emitted)
        assert '2035' not in ''.join(emitted)
    visible, _, remaining = _finish_stream_channels(guard, buffer, [])
    assert visible == '你好'
    assert ''.join(emitted + remaining) == '你好'
    assert normalize_reasoning_channels(text, None).reply == '你好'


def test_interrupted_header_never_becomes_reply_or_history():
    header = '<message><datetime>2035-03-10T12:30:00+08:00</datetime><content>'
    for end in range(1, len(header) + 1):
        text = header[:end]
        buffer = VisibleReplyBuffer.create()
        emitted = [part for char in text for part in buffer.push(char)]
        visible, remaining = buffer.finish()
        assert visible == '', text
        assert ''.join(emitted + remaining) == '', text
        assert normalize_reasoning_channels(text, None).reply == '', text


@pytest.mark.parametrize('text', [
    '这里的 <datetime>2035-03-10</datetime> 只是格式示例。',
    '例如：<message><datetime>2035-03-10</datetime><content>你好</content></message>',
    '<datetime>这里填写日期</datetime> 是示例标签。',
    '<message> 是示例标签，不是消息包装。',
    '```xml\n<datetime>2035-03-10</datetime>\n```',
    '```xml\n' + WRAPPED + '\n```',
    '你说“昨天八点”，我会按你的原话记录。',
])
def test_time_examples_and_event_prose_are_not_modified(text):
    assert unwrap_assistant_message(text) == text
    buffer = VisibleReplyBuffer.create()
    emitted = [part for char in text for part in buffer.push(char)]
    visible, remaining = buffer.finish()
    assert visible == text
    assert ''.join(emitted + remaining) == text


def test_truncated_content_preserves_comparison_character():
    text = '<message><datetime>unknown</datetime><content>比较 x <'
    assert unwrap_assistant_message(text) == '比较 x <'
