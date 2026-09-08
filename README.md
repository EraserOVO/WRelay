# WRelay

WRelay 是一个基于 NoneBot2 的 Windows 消息转发服务。当前代码包含三条相互独立的消息链路：

- Discord → QQ：实时转发和离线补发
- X/Twitter RSS → QQ：通过 RSSHub 轮询 X 账号并转发
- QQ → QQ：授权用户通过私聊命令手动转发文字或 Discord 消息

项目不提供 QQ → Discord 自动转发，也不做双向同步。

## 功能

### Discord → QQ

- 实时监听已启用的 Discord 频道并转发到已启用的 QQ 群
- 支持多个 Discord 频道、多个 QQ 群和独立的 Discord 转发组
- 支持文字、图片、音频、文件、链接、Discord 自定义 Emoji 和 Embed 内容
- 支持 Discord 回复消息，转换为 QQ 可读的引用文本
- 清理 Markdown 标记，并将用户、角色、频道、`@everyone` 等提及转换为可读文本
- 支持按 Discord 用户名、关键词和消息类型筛选
- 频道与 QQ 群分别维护发送游标，避免重复转发
- QQ 群发送失败不会影响其他群，失败群会在重试或补发时继续处理
- 失败消息会持久化到 `data/retry_pending.json`，进程重启后继续重试；媒体字节不写入队列
- 支持媒体并发下载、大小限制、发送重试、429 限流退避和链接降级
- Discord Bot 重连后自动补发离线期间的未转发消息，可限制每个频道单次补发数量

### X/Twitter RSS → QQ

- 通过 RSSHub 定时轮询 X/Twitter 账号
- 支持多个 X 来源和独立的 X 转发组
- 支持原创内容、转发、引用内容、图片和视频
- 支持 RSS ETag/304 缓存，减少重复请求
- 使用独立的来源游标去重
- QQ 未连接、发送失败或媒体处理失败时保留游标并在后续轮询中补投
- 管理面板提供 RSS 运行状态、最近轮询结果和失败原因

### QQ 私聊手动转发

授权 QQ 用户可以使用：

```text
relay-list       查看可用的 QQ 接收群
relay 1          选择第 1 个 QQ 群
relay all        选择全部已启用的 QQ 群
```

选择目标后发送下一条文字即可转发。下一条消息如果是 Discord 消息链接，WRelay 会从 Discord API 抓取原消息，并复用正常的内容解析、媒体下载和发送逻辑。

### QQ 权限与注册审核

- 自动记录发现的 QQ 用户 OpenID 和群 OpenID
- 私聊注册：`register QQ号 昵称`
- 群内注册：`register-group 群号 群名称`
- 管理员可在面板审核用户和群注册申请
- 审核通过只会加入配置，默认仍为禁用状态，必须由管理员手动启用
- 保存用户昵称、群名、账号和最后活动时间，供管理面板展示
- 身份资料与权限列表分开保存，身份识别不会自动授权

### QQ 私聊管理命令

授权用户可以使用：

```text
status                         查看机器人状态和统计
groups                         查看 QQ 群列表
channels                       查看 Discord 频道列表
users                          查看私聊权限列表
backfill                       查看离线补发状态
backfill on/off                开关离线补发
backfill run/clear/refresh     触发、清除或刷新补发状态
restart                        重启机器人
list                           查看命令帮助
```

### 本地管理面板

面板默认地址为 `http://127.0.0.1:8090`，提供：

- 启动、停止、重启机器人
- 机器人异常退出后的自动拉起
- Windows 开机自启
- 管理 Discord 频道、X 来源、QQ 接收群和私聊权限
- 创建、重命名、排序和删除 Discord/X 转发组
- 配置频道筛选条件
- 注册新的可读 Discord 频道和 X 来源
- 审核 QQ 用户/群注册申请
- 查看频道权限审计结果和真实频道名称
- 查看、清空和动态切换机器人日志级别
- 查看转发统计
- 查看和操作 Discord 离线补发
- 查看 X RSS 轮询状态
- 编辑 QQ Bot 与 Discord Bot 配置

## 快速开始（Windows）

当前安装脚本和管理面板仅支持 Windows，Python 版本要求为 3.10 及以上。

1. 安装包版本直接使用随包提供的 `python\` 运行时，并在安装时从 `.env.example` 生成 `.env.prod`；源码目录运行 `install.bat` 时，如果没有内置 Python，则创建 `.venv` 并安装项目依赖。
2. 源码版本如果不存在 `.env.prod`，安装脚本会从 `.env.example` 生成配置模板。
3. 编辑 `.env.prod`，填写 QQ 官方机器人和 Discord Bot 配置。
4. 如需访问 Discord，在 `.env.prod` 中配置 `HTTP_PROXY`。
5. 双击 `启动面板.bat`，打开 `http://127.0.0.1:8090`。
6. 在面板中登记并启用 Discord 频道、X 来源、QQ 群和私聊用户，然后启动机器人。

详细的 Bot 创建、权限、`group_openid`、频道登记、代理和 RSSHub 配置方式见 [docs/SETUP.md](docs/SETUP.md)。

## 配置与权限说明

- Discord 频道、X 来源、QQ 群和 QQ 用户是否启用，以 `data/settings.json` 为准。
- Discord 和 X 分别使用独立的转发组配置；全局启用和转发组成员关系必须同时满足，消息才会发送。
- QQ 官方机器人使用 `group_openid`，不能从普通群号直接推算。
- 自动发现 OpenID 只负责记录，不会自动授权。
- `.env.prod` 包含机器人密钥，请勿提交或外传。
- 公域 QQ 机器人可能受到主动消息权限、时段和频率限制。

## 项目结构

```text
WRelay/
├─ bot.py                    # NoneBot 入口、适配器注册、日志轮转和插件加载
├─ pyproject.toml            # 项目依赖和 NoneBot 配置
├─ .env.example              # 环境变量模板
├─ install.bat               # Windows 安装脚本
├─ 启动面板.bat              # 启动管理面板
├─ docs/
│  ├─ SETUP.md               # 安装与配置指南
│  └─ CHANNELS.md            # Discord 频道权限记录
├─ plugins/
│  ├─ relay.py               # Discord 实时转发
│  ├─ x_rss.py               # X RSS 轮询和投递
│  ├─ x_rss_parser.py        # RSS 内容解析
│  ├─ command.py             # QQ relay 命令、OpenID 自动发现和身份记录
│  ├─ manage.py              # QQ 私聊管理命令
│  ├─ registration.py        # QQ 用户/群注册申请
│  ├─ fetch.py               # Discord 消息抓取、归一化和渲染
│  ├─ media.py               # Markdown、提及、Emoji 和媒体处理
│  ├─ sender.py              # QQ 消息发送、分片、重试和降级
│  ├─ retry.py               # 失败消息延迟重试
│  ├─ backfill.py            # Discord 离线补发
│  ├─ backfill_api.py        # 补发和频道审计 HTTP 接口
│  ├─ health_api.py          # Bot 健康状态接口
│  ├─ config.py              # settings.json 配置读取和路由计算
│  ├─ filter.py              # 频道消息筛选
│  ├─ dedup.py               # 去重游标计算
│  ├─ history.py             # 去重记录持久化
│  ├─ identities.py          # QQ OpenID 身份资料
│  ├─ stats.py               # 转发统计
│  └─ json_io.py              # JSON 原子读写
├─ panel/
│  ├─ 管理面板.pyw            # FastAPI 管理面板和机器人进程管理
│  ├─ autostart.pyw           # 开机自启入口
│  └─ web/                    # 管理面板前端
└─ data/                      # 配置、游标、日志和运行时数据
```

## 技术栈

- [NoneBot2](https://nonebot.dev/)
- `nonebot-adapter-discord`
- `nonebot-adapter-qq`
- FastAPI / Uvicorn
- httpx
- RSSHub（X/Twitter RSS 来源）

## 运行数据

主要运行时文件包括：

- `data/settings.json`：频道、来源、群、用户、转发组和补发配置
- `data/discord_last.json`：Discord/X 按目标群保存的去重游标
- `data/stats.json`：成功、失败和当日转发统计
- `data/qq_identities.json`：QQ 用户和群身份资料
- `data/qq_registrations.json`：待审核注册申请
- `data/channels_audit.json`：Discord 频道权限审计结果
- `data/logs/bot.log`：机器人运行日志

这些文件属于运行时数据，不应提交到版本库。

## 相关文档

- [安装与配置指南](docs/SETUP.md)
- [Discord 频道权限记录](docs/CHANNELS.md)
- [项目架构说明](ARCHITECTURE.md)
