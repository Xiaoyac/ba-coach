"""Black-box negative choice cases; these are not full-cycle attempts."""
import sys
from full_cycle_acceptance_0920 import Client,save

sys.stdout.reconfigure(encoding='utf-8')
c=Client('A')
cases=[
    ('12_转述不建目标','我朋友说“我选择跑步作为目标”，那是他的决定，不是我的。我自己还没想好，也不想现在设目标，只想先聊聊。'),
    ('13_假设不建目标','如果以后我想尝试游泳，可能会每周一次。但现在只是举例，我还没有决定做这件事，请不要把它当成我的目标。'),
    ('14_撤回选择','我选择散步，不过算了，我还没决定，先不要创建目标，我想再想想。'),
]
for label,text in cases:
    if (c.folder/(label+'.json')).exists():continue
    d=c.new(label)
    d['suite']='negative_choice'
    r=c.say(d,text)
    d['result']={'outcome':'negative_pass' if r.get('ok') and not r.get('program',{}).get('runtime',{}).get('active_goal_id') else 'negative_fail'}
    save(c.folder/(label+'.json'),d)
