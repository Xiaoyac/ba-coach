"""The canonical first assistant turn for every BA coaching conversation.

It lives on the server because it is part of the transcript, not decorative
frontend copy.  New conversations persist it before the user can reply, so a
refresh, another device, and the model all see the same opening turn.
"""

from .schemas import Message


OPENING_MESSAGE_TEXT = (
    "你好，我是一个基于行为激活（BA）的 AI 教练。接下来我会先和你一起了解近期一次具体的困扰，"
    "观察行动如何影响情绪和状态，再逐步把这种理解落实到可尝试的改变中，并根据反馈一起调整。"
    "我不能替代医生或心理咨询师，也不会做医学诊断。你的真实体验最重要，是否尝试、如何调整由你决定。\n"
    "开始前，你希望我怎么称呼你？"
)

OPENING_MESSAGE = Message(role="assistant", content=OPENING_MESSAGE_TEXT)
