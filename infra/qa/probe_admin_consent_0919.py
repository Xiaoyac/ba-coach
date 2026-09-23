"""Pure-function probe of the reported invitation; no database access."""
from app.m1_contract import consent_is_current, is_goal_discussion_invitation
import json

invitation = '好，那我们就说到这。接下来我想和你一起，把“定个读书目标、完成再睡”这类想法，慢慢落成具体可执行的小目标，再根据实际反馈调整。\n\n你愿意试试这个方法，进入目标设定的部分吗？'
question = '你愿意试试这个方法，进入目标设定的部分吗？'
print(json.dumps({
    'full_invitation_accepted': is_goal_discussion_invitation(invitation),
    'question_only_accepted': is_goal_discussion_invitation(question),
    'actual_willing_reply_accepted': consent_is_current([('assistant', invitation), ('user', '愿意')], 1),
}, ensure_ascii=False))
