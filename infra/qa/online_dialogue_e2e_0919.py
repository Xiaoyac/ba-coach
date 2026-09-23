"""Run a real, isolated ten-turn dialogue against production.

This script uses only the public API, creates a disposable account, and never
touches the local database or uploads any test data.  It intentionally leaves
the module unset so the production router is exercised.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import date
from pathlib import Path

import requests


BASE = os.environ.get("BA_ONLINE_BASE", "https://bacoach.xyz").rstrip("/")
OUT = Path(os.environ.get("BA_ONLINE_OUT", "work/online-dialogue-0919"))
OUT.mkdir(parents=True, exist_ok=True)


def call(session: requests.Session, method: str, path: str, **kwargs):
    url = f"{BASE}{path}"
    started = time.perf_counter()
    response = session.request(method, url, timeout=240, **kwargs)
    elapsed_ms = round((time.perf_counter() - started) * 1000)
    return response, elapsed_ms


def main() -> int:
    stamp = uuid.uuid4().hex[:8]
    username = f"e2e0919{stamp}"
    # ``EmailStr`` rejects reserved special-use TLDs such as ``.invalid``;
    # the production mailer is disabled in this environment, so example.com
    # keeps the account isolated without targeting a real inbox.
    email = f"e2e0919{stamp}@example.com"
    password = "E2eOnline0919!" + stamp
    register_payload = {
        "username": username,
        "password": password,
        "email": email,
        "nickname": "线上验收",
        "tag": str(int(stamp, 16) % 100000).zfill(5),
        "birth_date": "1995-06-15",
        "living_status": "和家人",
        "communication_preference": "温柔引导",
        "physical_condition": [],
        "behavior_taboo": [],
    }
    turns = [
        "最近我经常想做些运动，但下班后总是拖延，心里也因此有点自责。我想先聊聊这个问题。",
        "上周二我本来打算下班后去小区走走，结果回到家已经很累了，就躺在沙发上刷手机，最后没有出门。",
        "当时身体有点疲惫，脑子里想的是‘今天太累了明天再说’，心情也从期待变成了自责和烦躁。",
        "我发现自己不是完全不想运动，而是下班回家这个时间点很容易被疲惫和刷手机打断，做完小行动后心情可能会好一点。",
        "这个思路和我的经历很贴合。我理解了行为激活不是等有动力才行动，而是先做一个小行动，再观察真实感受。",
        "我愿意继续了解，也愿意开始讨论一个实际可行的行动目标。",
        "我想把晚饭后散步作为主要目标，先安排每周三次，选周一、周三和周五。",
        "具体可以是晚饭后八点左右，在小区里走十五分钟；如果当天特别累，就先走五分钟作为最低版本。",
        "我确认这个计划是我自己愿意尝试的，地点、时间、时长和频率都比较现实，我也知道加班和疲惫可能是障碍。",
        "今天我完成了一次散步，大约十五分钟，开始前不太想动，但走完以后感觉轻松了一些。",
        "昨天因为加班没有完成十五分钟，不过回家路上我多走了十分钟，这算是一次替代行动。",
        "我觉得替代版本有帮助：没有完成原计划时不把它当成全盘失败，而是先做五分钟或在路上多走一段。",
        "接下来我想继续这个目标，把加班日的最低版本保留为五分钟，其他日子仍然尝试十五分钟。",
        "这轮执行让我更清楚什么时候容易拖延，也看到了小行动对心情的影响。我愿意结束这次回顾并在下一周期继续观察。",
    ]

    s = requests.Session()
    s.headers.update({"Accept": "application/json"})
    reg, reg_ms = call(s, "POST", "/api/auth/register", json=register_payload)
    if reg.status_code not in (200, 201):
        print(json.dumps({"stage": "register", "status": reg.status_code, "body": reg.text}, ensure_ascii=False))
        return 2
    auth = reg.json()
    token = auth["token"]
    s.headers["Authorization"] = f"Bearer {token}"
    account = auth.get("account", {})

    created, create_ms = call(s, "POST", "/api/conversations")
    if created.status_code not in (200, 201):
        print(json.dumps({"stage": "create_conversation", "status": created.status_code, "body": created.text}, ensure_ascii=False))
        return 3
    conversation = created.json()
    session_id = conversation["session_id"]

    records = []
    for index, user_text in enumerate(turns, 1):
        # Use the renewed production Doubao account for this run.  The default
        # DeepSeek router is separately recorded below if it is unavailable.
        payload = {"message": user_text, "session_id": session_id, "provider": "doubao"}
        response, elapsed_ms = call(s, "POST", "/api/chat", json=payload)
        item = {
            "turn": index,
            "user": user_text,
            "http_status": response.status_code,
            "elapsed_ms": elapsed_ms,
        }
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text[:2000]}
        if response.status_code >= 400:
            item["error_body"] = body
        if isinstance(body, dict):
            for key in (
                "reply", "reasoning_content", "routing_reasoning_content", "router_model_name",
                "provider", "model", "reply_module", "next_module", "routing_pending", "routed_by", "usage",
            ):
                if key in body:
                    item[key] = body[key]
        records.append(item)
        print(json.dumps(item, ensure_ascii=False)[:4000])
        if response.status_code >= 400:
            break
        # The router runs after the visible reply.  Let the next request use
        # its durable module pointer, while keeping the wait bounded.
        time.sleep(1.0)

    detail, detail_ms = call(s, "GET", f"/api/conversations/{session_id}")
    detail_body = detail.json() if detail.headers.get("content-type", "").startswith("application/json") else {"raw": detail.text[:2000]}
    result = {
        "base": BASE,
        "account": {"username": username, "email": email, "profile_uuid": account.get("profile_uuid")},
        "session_id": session_id,
        "register_ms": reg_ms,
        "create_ms": create_ms,
        "detail_ms": detail_ms,
        "turns_requested": len(turns),
        "turns_completed": len(records),
        "records": records,
        "final_detail": detail_body,
    }
    (OUT / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUT / "credentials.local-only.txt").write_text(
        f"username={username}\npassword={password}\nemail={email}\n", encoding="utf-8"
    )
    print(json.dumps({"session_id": session_id, "turns_completed": len(records), "output": str(OUT / 'result.json')}, ensure_ascii=False))
    return 0 if len(records) == len(turns) else 4


if __name__ == "__main__":
    raise SystemExit(main())
