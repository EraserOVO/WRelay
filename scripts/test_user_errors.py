"""验证用户错误提示中文化不改变原始日志输入。"""

from plugins.user_errors import localize_user_error


def main():
    provider_error = (
        'AI API 返回 HTTP 500: Error from provider self-deploy: '
        '503 Service Unavailable {"error":{"code":"no_available_workers",'
        '"message":"No available workers (all circuits open or unhealthy)"}}'
    )
    localized = localize_user_error(provider_error)
    assert "服务商暂时不可用" in localized
    assert "没有可用的服务节点" in localized
    assert "No available workers" not in localized
    assert localize_user_error("HTTP 503") == "接口返回 HTTP 503"
    assert localize_user_error("Forbidden") == "禁止访问"
    assert localize_user_error("AI API 返回 HTTP 400：model is not supported") == "AI API 返回 HTTP 400：模型不受支持"
    assert localize_user_error("请求异常: ReadTimeout") == "请求异常: 读取超时"
    assert localize_user_error("执行失败: UnknownProviderError") == "执行失败，请稍后重试"
    assert localize_user_error("") == "操作失败"
    print("PASS - 用户错误提示中文化")


if __name__ == "__main__":
    main()
