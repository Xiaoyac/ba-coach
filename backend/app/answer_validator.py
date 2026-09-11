"""Shared deterministic post-generation validation, not an LLM quality judge."""
import re
from time import perf_counter

VERSION = "answer-rules-v2"
SAFE_REPLY = "刚才的回复未通过完整性检查，暂时无法展示。你可以补充一下最希望讨论的具体问题，我们再继续。"
MODULE_RULES = {
    "module_1": ("premature_plan", r"(?:从明天开始|你必须|你应该每天).{0,24}(?:跑步|散步|运动|锻炼)"),
    "module_2": ("confirmation_claim", r"(?:我们已经确定|你已经确认|目标已确定|你已同意)"),
    "module_3": ("goal_replacement", r"(?:改成|换成|改为).{0,15}(?:目标|跑步|散步|游泳|运动)"),
    "module_4": ("completion_claim", r"(?:你已经完成|你本周坚持了|你已连续).{0,12}(?:天|次|目标)"),
}


def validate_answer(*, reply: str, module: str, evidence_ids: list[str], workflow: dict | None = None) -> dict:
    started = perf_counter()
    findings = []
    def flag(code, severity):
        findings.append({"code": code, "severity": severity})
    if not reply.strip():
        flag("empty_reply", "block")
    if len(reply) > 50000:
        flag("oversized_reply", "block")
    # Only explicit canonical KB identifiers are machine-verifiable. This
    # does NOT prove factual entailment or judge uncited natural-language claims.
    citations = set(re.findall(r"\bkb:\d+\b", reply))
    if citations - set(evidence_ids):
        flag("unknown_evidence_id", "block")
    rule = MODULE_RULES.get(module)
    if rule and re.search(rule[1], reply):
        flag(rule[0], "review")
    if re.search(r"(?:你患有|你被诊断为|你需要服用)", reply):
        flag("clinical_claim_needs_review", "review")
    if workflow is not None:
        # Check declarations, not conditional explanations or explicit denials.
        for sentence in re.split(r'[。！？\n]', reply):
            if re.search(r'尚未|还未|未曾|没有|不能|不得|并未|确认.{0,4}后|如果|待.{0,8}确认|确认成功后', sentence):
                continue
            committed = re.search(r'(?:目标卡片?|计划|目标|记录).{0,8}(?:已(?:经)?(?:被)?(?:锁定|保存|提交|确认)|保存成功|提交成功)|(?:已(?:经)?(?:锁定|保存|提交)).{0,8}(?:目标卡|计划)', sentence)
            moved = re.search(r'(?:接下来|现在|正式|已经|已|我们).{0,6}(?:进入|切换到|转入|来到)\s*(?:模块\s*([一二三四1234])|M([1234]))', sentence)
            target = None
            if moved:
                token = moved.group(1) or moved.group(2)
                target = 'module_' + str({'一':1,'二':2,'三':3,'四':4}.get(token, token))
            if (committed and not workflow.get('plan_confirmed')) or (target and (not workflow.get('available') or target != workflow.get('current_module'))):
                flag('uncommitted_workflow_claim','block')
                break
    blocked = any(f["severity"] == "block" for f in findings)
    return {"version": VERSION, "module": module,
        "status": "blocked" if blocked else "review" if findings else "passed",
        "findings": findings, "duration_ms": round((perf_counter()-started)*1000, 3),
        "llm_calls": 0, "semantic_verified": False}
