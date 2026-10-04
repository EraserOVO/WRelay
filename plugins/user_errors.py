"""把面向用户的外部错误转换为中文。

这里只处理用户界面和机器人回复使用的副本；调用方的日志仍使用原始异常，
方便排查问题时保留完整上下文。
"""

import re


_REPLACEMENTS = (
    (r"Error from provider self-deploy:?", "服务商自托管服务错误："),
    (r"No available workers \(all circuits open or unhealthy\)", "没有可用的服务节点（所有线路均已熔断或不健康）"),
    (r"no_available_workers", "没有可用的服务节点"),
    (r"Service Unavailable", "服务不可用"),
    (r"Bad Gateway", "网关错误"),
    (r"Gateway Timeout", "网关超时"),
    (r"Unauthorized", "未授权"),
    (r"Forbidden", "禁止访问"),
    (r"Not Found", "未找到资源"),
    (r"Bad Request", "请求参数错误"),
    (r"model is not supported", "模型不受支持"),
    (r"invalid model", "模型无效"),
    (r"invalid api key", "API 密钥无效"),
    (r"authentication failed", "认证失败"),
    (r"connection refused", "连接被拒绝"),
    (r"connection reset", "连接被重置"),
    (r"read timed out", "读取超时"),
    (r"ReadTimeout", "读取超时"),
    (r"timed out", "请求超时"),
    (r"timeout", "超时"),
    (r"connection error", "连接错误"),
    (r"ConnectError", "连接错误"),
    (r"ConnectionError", "连接错误"),
    (r"JSONDecodeError", "响应 JSON 解析失败"),
    (r"No such file or directory", "文件或目录不存在"),
    (r"Permission denied", "没有权限"),
    (r"Request failed", "请求失败"),
    (r"HTTP Error", "HTTP 错误"),
)


def _http_status(text):
    match = re.search(r"\bHTTP(?:\s+Error)?\s+(\d{3})\b", text, re.IGNORECASE)
    return match.group(1) if match else ""


def _has_untranslated_english(text):
    cleaned = re.sub(r"\b(?:AI|API|HTTP|TMT)\b", "", text, flags=re.IGNORECASE)
    return bool(re.search(r"[A-Za-z]", cleaned))


def localize_user_error(message, fallback="操作失败"):
    """返回用于用户显示的中文错误，不改变传入的原始异常或日志。"""
    text = str(message or "").strip()
    if not text:
        return fallback

    lowered = text.casefold()
    status = _http_status(text)
    if "no_available_workers" in lowered or "no available workers" in lowered:
        if "AI API 返回 HTTP" in text and status:
            prefix = "AI API 返回 HTTP %s：" % status
        elif status:
            prefix = "接口返回 HTTP %s：" % status
        else:
            prefix = ""
        return prefix + "服务商暂时不可用，没有可用的服务节点（所有线路均已熔断或不健康），请稍后重试"

    translated = text
    for pattern, replacement in _REPLACEMENTS:
        translated = re.sub(pattern, replacement, translated, flags=re.IGNORECASE)

    # AI 服务商有时会把整段 JSON 错误直接放入响应。已知词替换后仍残留
    # 英文时，面向用户给出简洁中文说明；原始响应仍由日志保留。
    if "AI API 返回 HTTP" in translated and _has_untranslated_english(translated):
        return "AI API 返回 HTTP %s：服务商返回了无法识别的错误，请检查配置或稍后重试" % (status or "未知")

    # 已知的失败前缀后面如果仍是未知英文异常，不把异常类名直接展示给用户。
    if _has_untranslated_english(translated):
        prefix = re.match(r"^(启动失败|启动重启进程失败|执行失败|请求异常|调用失败|保存失败|恢复失败)", translated)
        if prefix:
            return prefix.group(1) + "，请稍后重试"

    # 仅有英文的未知异常不直接展示英文；HTTP 状态码仍保留，便于用户反馈。
    if not re.search(r"[\u3400-\u9fff]", translated) and re.search(r"[A-Za-z]", translated):
        return ("接口返回 HTTP %s" % status) if status else fallback
    return translated
