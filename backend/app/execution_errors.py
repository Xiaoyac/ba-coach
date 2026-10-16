"""Safe, structured failure explanations; never expose provider bodies or prompts."""
import re


def failure_info(code=None, *, status=None, request_id=None):
    # Error codes are identifiers, not arbitrary exception messages.
    code = str(code or 'unknown')
    code = code if re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', code) else 'unknown'
    low = code.lower()
    status = status if type(status) is int and 400 <= status <= 599 else None
    if 'timeout' in low or 'timed_out' in low or status in {408, 504}:
        reason, suggestion = '请求超过此阶段的等待时限，未能及时完成。', '查看该阶段耗时及服务端日志，确认上游响应或网络是否超时。'
    elif status == 429 or 'ratelimit' in low or 'rate_limit' in low:
        reason, suggestion = '上游拒绝了请求（限流或配额限制）。', '结合请求编号查看上游错误子码和额度；仅凭 429 无法判定是并发、用量还是服务拥堵。'
    elif status in {401, 403} or 'authentication' in low or 'permission' in low:
        reason, suggestion = '上游身份验证或访问权限检查未通过。', '检查服务端 API Key、模型权限和账号状态。'
    elif status is not None and status >= 500:
        reason, suggestion = '上游服务返回异常。', '使用请求编号查询服务商日志，确认服务是否恢复。'
    elif 'connection' in low or 'network' in low:
        reason, suggestion = '未能与模型服务建立或保持连接。', '检查服务器到模型服务的网络连接。'
    elif any(part in low for part in ('json', 'validation', 'parse', 'invalid_protocol')):
        reason, suggestion = '模型返回内容未通过结构或格式校验。', '检查该阶段返回格式及校验日志。'
    elif 'empty' in low:
        reason, suggestion = '模型没有返回可用内容。', '检查完成原因和该阶段输出记录。'
    elif 'superseded' in low or 'module_changed' in low or 'stale' in low:
        reason, suggestion = '对话或模块状态已经变化，本次结果无法继续写入。', '查看最新一轮的运行状态。'
    elif 'persistence' in low:
        reason, suggestion = '对话归档或写入未完成。', '检查服务端数据库及保存日志。'
    else:
        reason, suggestion = '该阶段未成功完成，现有记录未提供更具体的原因。', '结合错误码和服务端日志排查；不据此推断模型限流或网络超时。'
    result = {'code': code, 'reason': reason, 'suggestion': suggestion}
    if status is not None:
        result['http_status'] = status
    if request_id and re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', str(request_id)):
        result['request_id'] = str(request_id)
    return result


def exception_info(exc):
    # Providers wrap SDK errors: follow explicit causes without reading raw messages.
    seen = set()
    while exc.__cause__ is not None and id(exc) not in seen:
        seen.add(id(exc))
        exc = exc.__cause__
    return failure_info(type(exc).__name__, status=getattr(exc, 'status_code', None),
                        request_id=getattr(exc, 'request_id', None))


def safe_failure(value):
    if not isinstance(value, dict):
        return None
    return failure_info(value.get('code'), status=value.get('http_status'), request_id=value.get('request_id'))
