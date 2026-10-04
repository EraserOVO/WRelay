"""首次配置与面板共用的凭据就绪判断，不读取文件或连接服务。"""

QQ_ID_PLACEHOLDER = "你的AppID"
QQ_SECRET_PLACEHOLDER = "你的AppSecret"
DISCORD_PLACEHOLDER = "在这里粘贴你的DiscordBotToken"


def field(bot, name):
    return bot.get(name) if isinstance(bot, dict) else getattr(bot, name, None)


def present(value, placeholder):
    return isinstance(value, str) and bool(value.strip()) and value.strip() != placeholder


def qq_ready(bot):
    return (present(field(bot, "id"), QQ_ID_PLACEHOLDER)
            and present(field(bot, "token"), QQ_ID_PLACEHOLDER)
            and present(field(bot, "secret"), QQ_SECRET_PLACEHOLDER))


def discord_ready(bot):
    return present(field(bot, "token"), DISCORD_PLACEHOLDER)


def needs_discord(settings):
    # 已启用频道意味着用户要求使用 Discord；保留旧配置的启动检查。
    return any(isinstance(item, dict) and item.get("enabled")
               for item in settings.get("discord_channels", []))
