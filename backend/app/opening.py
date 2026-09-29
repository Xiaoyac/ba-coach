"""The canonical first assistant turn for every BA coaching conversation.

It lives on the server because it is part of the transcript, not decorative
frontend copy.  New conversations persist it before the user can reply, so a
refresh, another device, and the model all see the same opening turn.
"""

from .schemas import Message


OPENING_MESSAGE_TEXT = """你好！很高兴认识你。你可能是第一次来，我先自我介绍一下：

我是基于行为激活理论工作的AI教练，简单来说就是帮你通过行动来改善情绪。

我不能替代医生或心理咨询师，但我可以帮助你理解自己的行为和情绪的关系，和你一起找到适合的身体活动，制定可行的运动计划，陪伴支持你准备、行动、复盘和调整。我是你的伙伴，而你是自己生活的专家，我们一起讨论，你来决定要不要尝试、怎么调整。

这里是一个安全、不被评判的空间，你可以真实地表达自己。

在开始之前，你希望我怎么称呼你呢？"""

OPENING_MESSAGE = Message(role="assistant", content=OPENING_MESSAGE_TEXT)
