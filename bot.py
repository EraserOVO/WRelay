import asyncio
import json
import os
import sys

import nonebot

from nonebot import get_driver
from nonebot import logger

from nonebot.adapters.qq import Adapter as QQAdapter
from nonebot.adapters.discord import Adapter as DiscordAdapter


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# =====================================================
# 首次配置检查（安装后引导）
#
# 全新安装后若 .env.prod 缺失或仍填着 .env.example 的示例占位符，
# 直接启动只会让 QQ / Discord 适配器反复对官方服务器发起认证并失败
# （示例占位符还会触发编码错误）。这里在 nonebot.init() 之前做一次
# 只读检查：发现缺失/占位就打印清晰的配置路径与字段提示后退出，
# 避免无意义的对外认证，也绝不打印真实凭据（占位符本就非真实）。
# 用户改好 .env.prod 后重新启动即可，本检查只拦一次。
#
# 解析方式：复用 NoneBot 与适配器自身的配置解析能力，而不是自己另写
# 一套 BOTS JSON parser。适配器是通过 get_plugin_config() 构建配置的，
# 其底层是 BaseSettings._settings_build_values()（对 env 值先 json.loads、
# 失败时静默保留为字符串）+ type_validate_python()。这里在 init 之前
# 用完全相同的手段解析 .env.prod，行为与 NoneBot 实际加载一致，且
# 不调用 init()、不实例化 adapter、不建立任何网络连接、无副作用。
# =====================================================

ENV_PROD = os.path.join(BASE_DIR, ".env.prod")

# .env.example 中明确给出的示例占位字符串（精确匹配，不做宽泛中文词判断）
_QQ_PLACEHOLDER_APPID = "你的AppID"
_QQ_PLACEHOLDER_APPSECRET = "你的AppSecret"
_DC_PLACEHOLDER_TOKEN = "在这里粘贴你的DiscordBotToken"


def _build_adapter_config(config_model):
    """用与 NoneBot get_plugin_config() 相同的机制解析 .env.prod 的配置。

    不调用 nonebot.init()：直接从 dotenv 文件取原始键值，走
    BaseSettings._settings_build_values()（含 json.loads 失败时保留字符串
    的宽松处理）+ type_validate_python()，与 NoneBot 实际解析行为一致，
    且无任何副作用（不注册适配器、不建连接、不触发初始化）。
    """
    from dotenv import dotenv_values
    from nonebot.compat import type_validate_python
    from nonebot.config import BaseSettings

    vals = dict(dotenv_values(ENV_PROD))
    built = BaseSettings._settings_build_values(
        config_model,
        vals,
        env_file=".env.prod",
        env_file_encoding="utf-8",
        env_nested_delimiter=None,
    )
    return type_validate_python(config_model, built)


def _first_run_config_check():
    """检测首次配置是否就绪；未就绪时打印指引并 sys.exit(2)。

    只在 .env.prod 不存在或必填凭据缺失/为空/仍为示例占位符时拦停。
    已填真实配置（哪怕是伪造的 ASCII 值）则原样返回，继续走正常启动
    流程，后续认证失败交给 QQ / Discord 原有机制处理。
    """
    exists = os.path.exists(ENV_PROD)

    # 仅当 .env.prod 存在时才尝试解析；缺失时直接按未配置处理
    qq_missing = []
    dc_missing = []
    if exists:
        try:
            qq_cfg = _build_adapter_config(
                __import__("nonebot.adapters.qq.config", fromlist=["Config"]).Config
            )
            dc_cfg = _build_adapter_config(
                __import__(
                    "nonebot.adapters.discord.config", fromlist=["Config"]
                ).Config
            )
        except Exception:
            # 解析异常按保守处理：不拦停，让 NoneBot 原有机制去报错
            return

        def _field(bot, name):
            """从 bot 元素取字段值，兼容 dict 与 pydantic 模型对象"""
            return bot.get(name) if isinstance(bot, dict) else getattr(bot, name, None)

        def _is_placeholder(value, *exact):
            """字段缺失/为空/精确等于某个示例占位字符串时判为占位"""
            if not isinstance(value, str) or not value.strip():
                return True
            return any(value.strip() == e for e in exact)

        # QQ：至少一个 bot 同时含非空 id / token / secret（都不是示例占位）
        qq_ok = any(
            not _is_placeholder(_field(b, "id"), _QQ_PLACEHOLDER_APPID)
            and not _is_placeholder(_field(b, "token"), _QQ_PLACEHOLDER_APPID)
            and not _is_placeholder(_field(b, "secret"), _QQ_PLACEHOLDER_APPSECRET)
            for b in qq_cfg.qq_bots
        )
        if not qq_ok:
            qq_missing = ["id", "token", "secret"]

        # Discord：至少一个 bot 含非空 token（不是示例占位）
        dc_ok = any(
            not _is_placeholder(_field(b, "token"), _DC_PLACEHOLDER_TOKEN)
            for b in dc_cfg.discord_bots
        )
        if not dc_ok:
            dc_missing = ["token"]

    if exists and not qq_missing and not dc_missing:
        return  # 已配置完成，正常启动

    lines = []
    lines.append("")
    lines.append("=" * 66)
    lines.append("  WRelay 首次配置检查：尚未完成首次配置")
    lines.append("=" * 66)
    if not exists:
        lines.append("  尚未生成配置文件（.env.prod 不存在）。")
        lines.append("  请先运行 install.bat，或手动复制 .env.example 为 .env.prod：")
        lines.append("      copy .env.example .env.prod")
    lines.append("")
    lines.append("  配置文件位置：%s" % ENV_PROD)
    lines.append("")
    if qq_missing:
        lines.append("  [QQ 官方机器人] 缺少、为空或仍是示例占位符的字段：%s"
                     % ", ".join(qq_missing))
        lines.append("    - 在 https://q.qq.com 创建机器人，获得 AppID 与 AppSecret")
        lines.append("    - 在 .env.prod 的 QQ_BOTS 中：id 与 token 都填 AppID，secret 填 AppSecret")
    if dc_missing:
        lines.append("  [Discord] 缺少、为空或仍是示例占位符的字段：%s"
                     % ", ".join(dc_missing))
        lines.append("    - 在 https://discord.com/developers/applications 创建应用获取 Bot Token")
        lines.append("    - 在 .env.prod 的 DISCORD_BOTS 中：token 填你的 Bot Token")
        lines.append("    - 记得在 Bot 设置里开启 Message Content Intent（特权网关意图）")
    lines.append("")
    lines.append("  国内网络访问 Discord 还需在 .env.prod 配置 HTTP_PROXY（例如：")
    lines.append("      HTTP_PROXY=http://127.0.0.1:7890")
    lines.append("  详细说明见 docs/SETUP.md。")
    lines.append("")
    lines.append("  请用记事本编辑 .env.prod 填好以上字段后，重新启动即可。")
    lines.append("=" * 66)
    lines.append("")

    sys.stderr.write("\n".join(lines) + "\n")
    sys.exit(2)


# 首次配置检查必须早于 nonebot.init()：init 会读取 .env.prod 并注册
# 适配器，若带着占位符继续会直接对外认证失败
_first_run_config_check()

nonebot.init()


# =====================================================
# 日志轮转 + 运行时日志级别（B4）
#
# 全部日志写入 data/logs/bot.log，按 5MB 轮转、保留最近 3 个，
# 避免日志无限增长（此前 LOG_LEVEL=DEBUG 下一天能到 19MB）。
# 初始级别取 data/runtime_log_level.json（管理面板可改），
# 缺失时回退 .env.prod 的 LOG_LEVEL（当前 INFO，排查时可临时改 DEBUG）。
# 面板写入 runtime_log_level.json 后，后台任务 3 秒内自动生效，无需重启。
# =====================================================

BOT_LOG = os.path.join(
    BASE_DIR,
    "data",
    "logs",
    "bot.log",
)

RUNTIME_LEVEL_FILE = os.path.join(
    BASE_DIR,
    "data",
    "runtime_log_level.json",
)

os.makedirs(
    os.path.dirname(BOT_LOG),
    exist_ok=True
)

VALID_LOG_LEVELS = ("TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL")


def _file_log_level():
    """读取面板写入的运行时日志级别文件；无效/缺失返回 None"""
    try:
        with open(RUNTIME_LEVEL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        level = str(data.get("level", "")).upper()
        if level in VALID_LOG_LEVELS:
            return level
    except Exception:
        pass
    return None


def _resolve_log_level():
    """初始日志级别：优先运行时文件，其次 .env.prod 的 LOG_LEVEL"""
    level = _file_log_level()
    if level:
        return level
    try:
        level = str(getattr(get_driver().config, "log_level", "INFO")).upper()
        if level in VALID_LOG_LEVELS:
            return level
    except Exception:
        pass
    return "INFO"


_handler_id = None


def _setup_log_sink(level):
    """重建文件 sink（运行时切换日志级别用）"""
    global _handler_id
    if _handler_id is not None:
        logger.remove(_handler_id)
    _handler_id = logger.add(
        BOT_LOG,
        level=level,
        rotation="5 MB",
        retention=3,
        encoding="utf-8",
        enqueue=True,
    )
    logger.info(
        "日志级别 -> {}",
        level
    )


_setup_log_sink(
    _resolve_log_level()
)


driver = get_driver()


@driver.on_startup
async def _watch_log_level():
    """监听 runtime_log_level.json 的 mtime 变化，动态切换日志级别。
    注意：只能在这里创建后台任务，不能直接 await 监听循环——
    否则 startup 事件永不完成，后续适配器连接（Discord/QQ）不会执行。"""
    asyncio.create_task(_watch_log_level_loop())


async def _watch_log_level_loop():
    last_mtime = None
    while True:
        await asyncio.sleep(3)
        try:
            mtime = os.path.getmtime(RUNTIME_LEVEL_FILE)
        except OSError:
            mtime = None
        if mtime == last_mtime:
            continue
        last_mtime = mtime
        try:
            _setup_log_sink(
                _resolve_log_level()
            )
        except Exception:
            logger.exception("切换日志级别失败")


driver.register_adapter(
    QQAdapter
)


driver.register_adapter(
    DiscordAdapter
)


nonebot.load_plugins(
    "plugins"
)


nonebot.run()
