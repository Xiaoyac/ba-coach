"""Shared deterministic post-generation guard; not a semantic quality judge.

Block explicit contract violations. Questions, conditional explanations and
grounded reflections need different treatment from unqualified declarations.
No model calls, and no claim that a rules pass proves counselling quality.
"""
import re
from time import perf_counter

from .reasoning import contains_internal_protocol

VERSION = "answer-rules-20260924-diagnostics"
SAFE_REPLY = "这条回复生成异常，暂未展示。请重试；不需要重新说明已有信息。"
INTEGRITY_CODES = {
    "empty_reply",
    "oversized_reply",
    "unknown_evidence_id",
    "internal_protocol_leak",
}

# Coaching quality is assessed in diagnostics/evaluation. It cannot replace
# an otherwise valid response with an unrelated module template.
DIAGNOSTIC_CODES = {
    "panel_confirmation_instruction", "unsupported_behavior_label",
    "m3_analysis_boundary", "premature_plan", "goal_replacement",
    "completion_claim",
}


def recovery_reply(module: str) -> str:
    """Technical failure notice; never substitute a scripted coaching question."""
    return SAFE_REPLY


def _declaration(sentence: str, start: int) -> bool:
    """A local negation must qualify the claim, not an unrelated earlier clause."""
    prefix = re.split(r"[，,；;]", sentence[:start])[-1]
    if re.search(r"尚未|还未|没有|并未|不能|不得|不要|不应|不是|无法|不一定|无需|不用|不必|不需要|不会(?:说)?|不意味着|不代表|不等于", prefix):
        return False
    if re.match(r"\s*(?:如果|假如|假设|比如|例如|举例|待.{0,8}确认|确认.{0,4}后)", sentence):
        return False
    if re.search(r"确认.{0,4}后|如果|是否|可能|也许", prefix):
        return False
    local = re.split(r"[，,；;]", sentence[start:])[0]
    if re.search(r"(?:我们|我|你)(?:现在|暂时|还)?(?:不需要|无需|不能|不应|不用|不必).{0,8}(?:设定|制定|确认|改成|进入)", local):
        return False
    return not re.search(r"[？?]|(?:吗|么|对吗|是不是)\s*$", local)


def _user_supports(claim: str, current_user: str) -> bool:
    """Only literal current-turn self-reports support evidence-sensitive claims.

    KB text and historical assistant replies cannot establish a user's action.
    Other paraphrases remain conservative; this is not an entailment model.
    """
    normalized = re.sub(r"^(?:你说|你提到|你)", "", claim).lstrip("我")
    for clause in re.split(r"[，,。；;！!\n]", current_user):
        if re.search(r"[？?‘’“”\"]|如果|假如|不是|并未|没有|没做|没完成|只是|例子|引用|他说|朋友说", clause):
            continue
        if not re.match(r"\s*(?:我|今天|本周|这次|已经|已|完成|按照计划)", clause):
            continue
        source = re.sub(r"^\s*(?:我说|我)", "", clause)
        if normalized and normalized in source:
            return True
    return False


def validate_answer(*, reply: str, module: str, evidence_ids: list[str],
                    workflow: dict | None = None, current_user: str = "") -> dict:
    started = perf_counter()
    findings = []

    def flag(code, severity="block"):
        if code in DIAGNOSTIC_CODES:
            severity = "review"
        if not any(f["code"] == code for f in findings):
            findings.append({"code": code, "severity": severity})

    if not reply.strip():
        flag("empty_reply")
    if len(reply) > 50000:
        flag("oversized_reply")
    # Provider/tool-call envelopes (for example DeepSeek's literal DSML
    # ``invoke`` marker) are transport failures, not assistant prose.  They
    # must never be classified as a successful answer simply because the
    # string is non-empty.  Keep this check shared with the streaming guard so
    # non-streaming and streaming completions fail closed in the same way.
    if contains_internal_protocol(reply):
        flag("internal_protocol_leak")
    citations = set(re.findall(r"\bkb:\d+\b", reply))
    if citations - set(evidence_ids):
        flag("unknown_evidence_id")

    # Preserve question marks so questions cannot become fabricated assertions.
    for sentence in re.findall(r"[^。！？!?\n]+[！？!?]?", reply):
        # Panel confirmation is forbidden even conditionally. Negation must be
        # adjacent to the navigation/action, not elsewhere in the sentence.
        panel = re.search(r"(?:目标面板|目标总览|面板|确认按钮).{0,32}(?:核对|确认|保存|提交)", sentence)
        if panel:
            prefix = re.split(r"[，,；;]", sentence[:panel.start()])[-1]
            negated = re.search(r"不需要|无需|不用|不必|不能|不要|不应|禁止", prefix)
            readonly = re.search(r"只读|只用|仅供|不负责|不能|无需|不用|不需要", panel.group())
            if not negated and not readonly:
                flag("panel_confirmation_instruction")

        for match in re.finditer(r"你(?:可能|已经|已)?(?:患有|被诊断为|需要服用).{0,20}|(?:建议|应该|需要)按.{0,12}(?:症|疾病)来处理", sentence):
            if _declaration(sentence, match.start()) and not _user_supports(match.group(), current_user):
                flag("clinical_claim_needs_review")

        promise = re.search(r"(?:心情|情绪).{0,8}(?:一定|肯定|保证).{0,12}(?:变好|改善|开心)|感觉一定更开心", sentence)
        if promise and not re.search(r"不一定|不是一定|未必", promise.group()) and _declaration(sentence, promise.start()):
            flag("guaranteed_outcome")
        shaming = re.search(r"不要再找借口|别再找借口|你就是懒|都是你不努力", sentence)
        if shaming and _declaration(sentence, shaming.start()):
            flag("shaming_prescription")

        causal = re.search(r"你(?:这是|就是|没有.{0,12}是因为|没.{0,12}是因为|.{0,10}是因为).{0,12}(?:回避|反刍)|你这是在回避", sentence)
        if causal and _declaration(sentence, causal.start()) and not _user_supports(causal.group(), current_user):
            flag("unsupported_behavior_label")
        if module == "module_3" and re.search(r"(?:我们|我来|接下来).{0,8}分析.{0,8}(?:ABC|循环|回避|反刍)", sentence):
            flag("m3_analysis_boundary")

        if module == "module_1":
            plan = re.search(r"(?:从明天开始|你必须|你应该每天).{0,24}(?:跑步|散步|运动|锻炼)|(?:我们|我建议|你可以).{0,32}(?:作为目标|设定目标|定为目标)", sentence)
            if plan and _declaration(sentence, plan.start()):
                flag("premature_plan")
        if module == "module_2":
            decision = re.search(r"(?:我们|我).{0,6}(?:就定成|就定为|替你定|帮你定)|我们已经确定|你已经确认|你已同意", sentence)
            if decision and _declaration(sentence, decision.start()) and not (workflow or {}).get("plan_confirmed"):
                flag("confirmation_claim")
            # Natural finality claims are not all phrased as “已确认”.  Keep
            # them behind the same committed-state gate so a background goal
            # creation failure cannot be presented as a finished plan.
            implicit_decision = re.search(
                r"(?:计划|目标(?:卡片)?|安排).{0,32}(?:就这么定(?:了)?|定下(?:来|了)?|敲定(?:了)?|确定(?:了)?|记下(?:了)?|记录(?:了)?)"
                r"|(?:我们|我).{0,10}(?:就按这个定(?:下来|了)?|就这么安排|按这个定(?:下来|了)?|把.{0,12}(?:安排|计划|目标).{0,8}(?:记下|记录)(?:了)?)"
                r"|(?:按|照)这个.{0,8}定(?:下来|了)?"
                r"|(?:这份|这个)(?:安排|计划|目标).{0,16}(?:照|按).{0,12}(?:开始|执行|做)",
                sentence,
            )
            if implicit_decision and _declaration(sentence, implicit_decision.start()) and not (workflow or {}).get("plan_confirmed"):
                flag("confirmation_claim")
        if module == "module_3":
            replacement = re.search(r"(?:改成|换成|改为).{0,15}(?:目标|跑步|散步|游泳|运动)", sentence)
            if replacement and _declaration(sentence, replacement.start()):
                flag("goal_replacement")
        if module == "module_4":
            for claim in re.finditer(r"你(?:今天|本周|这次)?(?:已经|已)?(?:按照计划)?完成(?:了)?[^，,。；;！？!?]*|你本周坚持了[^，,。；;！？!?]*|你已连续[^，,。；;！？!?]*|这次计划执行得很好|已经完成了安排", sentence):
                if _declaration(sentence, claim.start()) and not _user_supports(claim.group(), current_user):
                    flag("completion_claim")

        # A committed plan/module is a server fact, not a model inference.
        committed = re.search(r"(?:目标卡片?|计划|目标|记录).{0,8}(?:已(?:经)?(?:被)?(?:锁定|保存|提交|确认|确定)|保存成功|提交成功)|(?:已(?:经)?(?:锁定|保存|提交|确定)).{0,8}(?:目标卡|计划|目标)|(?:我们已经确定|你已经确认|你已同意)|(?:已(?:经)?把).{0,12}(?:写入数据库|保存)", sentence)
        moved = re.search(r"(?:接下来|现在|正式|已经|已|我们).{0,10}(?:进入|切换到|转入|来到)\s*(?:模块\s*([一二三四1234])|M([1234]))", sentence)
        if committed and _declaration(sentence, committed.start()) and not (workflow or {}).get("plan_confirmed"):
            flag("uncommitted_workflow_claim")
        if moved and _declaration(sentence, moved.start()):
            token = moved.group(1) or moved.group(2)
            target = "module_" + str({"一": 1, "二": 2, "三": 3, "四": 4}.get(token, token))
            if not (workflow or {}).get("available") or target != workflow.get("current_module"):
                flag("uncommitted_workflow_claim")

    return {"version": VERSION, "module": module,
        "status": "blocked" if any(f["severity"] == "block" for f in findings) else "review" if findings else "passed",
        "findings": findings, "duration_ms": round((perf_counter() - started) * 1000, 3),
        "llm_calls": 0, "semantic_verified": False}
