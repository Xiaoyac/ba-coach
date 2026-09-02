"""The canonical first assistant turn for every BA coaching conversation.

It lives on the server because it is part of the transcript, not decorative
frontend copy.  New conversations persist it before the user can reply, so a
refresh, another device, and the model all see the same opening turn.
"""

from .schemas import Message


OPENING_MESSAGE_TEXT = (
    "你好，我是一个基于行为激活理论工作的 AI 教练。简单来说，我会和你一起观察情绪与行动怎样互相影响，"
    "再找到适合你的身体活动，制定一个可以尝试的小计划，并在执行后一起复盘和调整。"
    "我不能替代医生或心理咨询师，也不会做医学诊断。\n"
    "在制定计划前，我们会先从最近一个具体的困扰或卡住的时刻开始。这样做不是为了追问完整经历，"
    "而是为了让后面的建议真正贴合你。之后的行动由你决定，我不会替你做主。\n"
    "你希望我怎么称呼你？"
)

OPENING_MESSAGE = Message(role="assistant", content=OPENING_MESSAGE_TEXT)

