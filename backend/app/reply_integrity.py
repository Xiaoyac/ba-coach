"""Hold persistence claims until checked; regenerate wording, never user data."""
import asyncio
import json
import re
from time import perf_counter

from .answer_validator import validate_answer
from .providers.base import as_segments
from .prompts import SystemPromptSegment
from .reasoning import normalize_reasoning_channels, contains_internal_protocol


def inconsistent_save_claim(reply, authority, module):
    return any(item['code'] == 'uncommitted_workflow_claim' for item in
        validate_answer(reply=reply, module=module, evidence_ids=[], workflow=authority)['findings'])


async def verify_reply(*, reply, read_authority, module, provider, system, messages,
                       elapsed_seconds, total_timeout_seconds, allow_recovery=True, current_turn_committed=False, visible_prefix=""):
    """One bounded wording retry; no extraction, tools or write operations here."""
    from .graph.nodes import unwrap_chat_reply
    try:
        authority = await read_authority()
    except Exception:
        authority = {'available': False}
    audit = {'status': 'passed', 'recovery_attempted': False}
    def claim_state(actual):
        # A prior confirmed plan is not a receipt for this turn's edit/save.
        return {**(actual or {}), 'last_operation_failed': not current_turn_committed}
    if not inconsistent_save_claim(reply, claim_state(authority), module):
        return reply, audit
    audit['status'] = 'blocked'
    remaining = min(8.0, max(0, total_timeout_seconds - elapsed_seconds))
    if not allow_recovery or remaining <= 0:
        audit['reason'] = 'generation_failed' if not allow_recovery else 'budget_exhausted'
        return None, audit
    facts = {key: (authority or {}).get(key) for key in (
        'available', 'current_module', 'goal_selected', 'plan_confirmed', 'recording_status',
        'draft_for_dialogue_summary', 'readiness')}
    facts['current_turn_committed'] = current_turn_committed
    corrected_system = [*as_segments(system), SystemPromptSegment(
        '上一候选回复关于保存、确认或模块的声明与数据库状态不一致，尚未展示。'
        '请依据下列真实状态和原有对话重新回答；生成文字不会保存数据。'
        '区分草稿、已经正式确认的旧计划与本轮尚未提交的操作；旧计划已确认不表示本轮写入成功。'
        '不能用对话中的同意替代成功事务；需要确认时说明真实待确认版本，'
        '缺项是系统核验结果，不等于用户没回答，应先核对现有对话。'
        '缺项用自然语言说明，不输出内部字段名。不要编造成功、权限限制、按钮或让用户重复说明；只改本轮正文，不改变用户计划。\n'
        + json.dumps(facts, ensure_ascii=False, default=str), cacheable=False)]
    if visible_prefix:
        corrected_system.append(SystemPromptSegment(
            '以下正文前缀已经展示，不能改写或撤回。仅输出接续正文，不重复这个前缀：\n'
            + json.dumps(visible_prefix, ensure_ascii=False), cacheable=False))
    audit.update(recovery_attempted=True, budget_seconds=remaining)
    started = perf_counter()
    try:
        generate = getattr(provider, 'complete_without_reasoning', None) or provider.complete
        result = await asyncio.wait_for(generate(system=corrected_system, messages=messages), timeout=remaining)
        audit.update(request_id=result.request_id, usage=result.usage)
        candidate = normalize_reasoning_channels(unwrap_chat_reply(result.text), result.reasoning_content).reply
        if visible_prefix and candidate.startswith(visible_prefix):
            candidate = candidate[len(visible_prefix):].lstrip()
        # A transaction in another chat may have changed during generation.
        authority = await read_authority()
        if (candidate.strip() and not contains_internal_protocol(result.text)
                and not inconsistent_save_claim(candidate, claim_state(authority), module)):
            audit['status'] = 'recovered'
            return candidate, audit
        audit['reason'] = 'inconsistent_again' if candidate.strip() else 'empty_recovery'
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        audit['reason'] = type(exc).__name__
    finally:
        audit['duration_ms'] = round((perf_counter() - started) * 1000)
    return None, audit


class SentenceSaveGuard:
    """Publish safe complete sentences; retain a suspect sentence and its tail.

    This shares the claim validator with final verification. It never releases
    an unfinished sentence, so splitting 保存成功 across tokens cannot evade it.
    """
    def __init__(self, authority, module, *, current_turn_committed=False):
        self.authority = {**(authority or {}), 'last_operation_failed': not current_turn_committed}
        self.module = module
        self.pending = ''
        self.blocked = False

    def push(self, text):
        self.pending += text
        ready = []
        while not self.blocked:
            boundary = re.search(r'[。！？!?\n]', self.pending)
            if boundary is None:
                break
            sentence = self.pending[:boundary.end()]
            if inconsistent_save_claim(sentence, self.authority, self.module):
                self.blocked = True
                break
            ready.append(sentence)
            self.pending = self.pending[boundary.end():]
        return ready
