# WRelay

WRelay 是一个基于 NoneBot2 的 Windows 消息转发服务。当前代码包含四条相互独立的消息链路：

- Discord → QQ：实时转发和离线补发
- X/Twitter RSS → QQ：通过 RSSHub 轮询 X 账号并转发
- Bilibili RSS → QQ：通过 RSSHub 按 UP 主、标签或关键词轮询新投稿并转发
- QQ → QQ：授权用户通过私聊命令手动转发文字或 Discord 消息

项目不提供 QQ → Discord 自动转发，也不做双向同步。

## 功能

### Discord → QQ

- 实时监听已启用的 Discord 频道并转发到已启用的 QQ 群
- 支持多个 Discord 频道、多个 QQ 群和独立的 Discord 转发组
- 多个 QQ Bot 时可在面板左上角切换 Bot 工作区，分别配置各自发现的 QQ 群目标
- 支持文字、图片、音频、文件、链接、Discord 自定义 Emoji 和 Embed 内容
- 支持 Discord 回复消息，转换为 QQ 可读的引用文本
- 清理 Markdown 标记，并将用户、角色、频道、`@everyone` 等提及转换为可读文本
- 支持按 Discord 用户名、关键词和消息类型筛选
- 频道与 QQ 群分别维护发送游标，避免重复转发
- QQ 群发送失败不会影响其他群，失败群会在重试或补发时继续处理
- 失败消息会持久化到 `data/retry_pending.json`，进程重启后继续重试；媒体字节不写入队列
- 支持媒体并发下载、大小限制、发送重试、429 限流退避和链接降级
- 视频媒体优先以 QQ 视频消息发送，下载或上传失败时自动降级为文字链接
- Discord Bot 重连后自动补发离线期间的未转发消息，可限制每个频道单次补发数量
- 可选失败通知：按冷却时间向管理员反馈投递失败，避免重复刷屏
- 每条投递会记录最终结果、目标群和重试次数，供面板消息追踪查看

### X/Twitter RSS → QQ

- 通过 RSSHub 定时轮询 X/Twitter 账号
- 支持多个 X 来源和独立的 X 转发组
- 支持原创内容、转发、引用内容、图片和视频
- 支持 RSS ETag/304 缓存，减少重复请求
- 对 RSSHub 的 429、5xx 和连接异常做同轮退避重试（最多 3 次）；其他 4xx 快速失败
- RSSHub 暂时不可用时回退到 X 公开时间线，保持订阅轮询不中断
- 使用独立的来源游标去重
- QQ 未连接、发送失败或媒体处理失败时保留游标并在后续轮询中补投
- 管理面板提供 RSS 运行状态、最近轮询结果和失败原因
- 支持向选定的 QQ 接收群定期发送固定消息，可在面板或白名单私聊中控制

### Bilibili RSS → QQ

- 通过 RSSHub 轮询 Bilibili 新投稿
- 支持按 UP 主 UID、Bilibili 标签或关键词注册来源
- 支持多个来源和独立的 Bilibili 转发组，可分别选择 QQ 接收群
- 支持来源级标签、关键词、标题必须包含、标题排除和作者白名单筛选；可在面板编辑筛选条件
- 支持可调的最短发送间隔、每小时上限和每来源积压上限；积压超限可选择保留最新或保留最旧
- 限速队列持久化保存，RSS 突发投稿不会在一次轮询中集中发送；Bilibili RSS 请求按来源串行访问，并对 429/5xx/连接异常做退避重试
- Bilibili 标签/关键词 RSSHub 路由依赖 Chromium 或有效的 `BILIBILI_COOKIE_1`；上游 412 通常会被 RSSHub 转成 503
- 保留“自动转发来自 Bilibili 中[UP 主名称]的投稿”来源头（优先使用投稿作者，RSS 缺少作者时回退到注册来源）；只发送标题和纯文本投稿地址，再发送封面图片；不发送作者行、标签、其他分类、简介及播放统计
- 首次启用来源或 QQ 群只建立游标基线，不批量刷出 RSS 历史窗口
- RSSHub 暂时不可用、QQ 未连接或发送失败时不推进游标，恢复后继续补投

### 自动翻译

- Discord → QQ 与 X/Twitter RSS → QQ 可分别启用自动翻译，并按链路设置首选服务和后备服务；Bilibili 首版默认发送原文
- 支持腾讯 TMT 与 OpenAI 兼容 AI API；所有翻译服务失败时仍发送原文
- AI API 遇到模型不存在、模型并发受限，或明确的模型容量/节点不可用错误时，会自动查询可用模型并逐个轮换尝试，直到成功或全部候选模型失败
- 面板「翻译服务配置」显示最近一次真实翻译成功使用的工作模型；AI 检测记录只表示独立检测结果，两者可能不同
- 检测记录只展示检测类型和原因，不包含 API 地址或密钥

### QQ 私聊手动转发

授权 QQ 用户可以使用：

```text
timer            查看定时消息状态
timer <on/off/clear> 开/关/清空定时消息
relay list       查看可用的 QQ 接收群
relay 1          选择第 1 个 QQ 群
relay 1 2        同时选择第 1、2 个 QQ 群
relay all        选择全部已启用的 QQ 群
register {QQ号} {昵称} 用户私聊提交注册申请
list             查看命令清单
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

### Windows 桌面管理程序

界面沿用深色风格，导航图标与“打开”按钮使用统一尺寸。网页配置、诊断、备份等二级窗口共用分级宽度、固定标题/操作栏和内部滚动区；具体规范见 [界面设计说明](docs/DESIGN.md)。Windows 原生系统对话框保留系统样式。

桌面窗口通过 WebView2 显示本地管理界面，管理服务仍监听 `http://127.0.0.1:18090`，提供：

- 启动、停止、重启机器人
- 机器人异常退出后的自动拉起
- Windows 开机自启
- 管理 Discord 频道、X/Bilibili 来源、QQ 接收群和私聊权限
- 创建、重命名、排序和删除 Discord/X/Bilibili 转发组
- 配置频道筛选条件
- 注册新的可读 Discord 频道、X 来源和 Bilibili 来源
- 审核 QQ 用户/群注册申请
- 配置腾讯 TMT、AI API 和自动翻译路由，查看工作模型与 AI 检测记录
- 查看频道权限审计结果和真实频道名称
- 查看、清空和动态切换机器人日志级别
- 查看转发统计
- 导出和恢复配置备份（ZIP 上限 5 MB）；运行状态可选，恢复前自动保存回退快照
- 查看和操作 Discord 离线补发
- 查看 X RSS 和 Bilibili RSS 轮询状态
- 在“设置 → 启动与维护 → 运行日志”窗口中打开消息追踪二级窗口，查看最近 50 条投递结果
- 使用连接诊断工具查看机器人、Discord、QQ 和待重试投递状态
- 在机器人状态卡片中开关失败通知
- 编辑 QQ Bot 与 Discord Bot 配置
- 多个 QQ Bot 时通过左上角 QQ Bot 下拉框切换当前工作区；Discord → QQ 与 X → QQ 页面只显示该 Bot 的目标群，
  不需要在每个群行单独选择 Bot
- 桌面窗口使用 38px 自定义 Windows 标题栏，保留系统缩放、双击最大化/还原和 Snap；窗口按钮统一尺寸，关闭悬停显示红色；窗口位置、尺寸及最大化状态会保存在 `data/desktop_window.json`，并在显示器变化后回到可见区域。应用图标、托盘与 Windows 快捷方式统一使用 WRelay 主标识；侧栏图标采用统一的线性样式

桌面程序首次打开进入运行总览，集中查看机器人状态、各转发链路状态与配置数量，并可直接进入 Discord → QQ、X → QQ、Bilibili → QQ、QQ → QQ 管理页。设置页按“应用、外观与窗口、机器人与服务、启动与维护”四个 Card 组织，统一使用左侧说明、右侧控件的设置行，提供桌面主题、当前版本和手动检查 GitHub 稳定版更新；发现新版本后由用户打开官方发布页下载安装，不会自动下载或替换程序。机器人凭据、翻译服务、开机启动、失败通知、备份恢复、连接诊断、重启管理程序和关于作者统一从设置页进入。配置表会按可用宽度调整列，长 ID 使用省略号、悬停查看完整值并提供复制按钮；窄窗口会收起为图标导航并调整为单栏布局。桌面窗口使用自定义标题栏、原生缩放边框与系统 Snap；位置、尺寸和最大化状态保存在 `data/desktop_window.json`，如果上次显示器不可用则重新放到当前可用屏幕。关闭窗口默认隐藏到托盘，也可在设置选择“退出桌面端”或“每次询问”；偏好保存于 `data/desktop_preferences.json`，立即生效，普通关闭/退出均不停止机器人。托盘提供打开窗口、总览、设置、随运行状态变化的启停/重启，以及退出桌面端和需确认的“停止机器人并退出…”；退出桌面端后不再保留托盘与看护。浏览器备用入口仍可访问上述本地地址，并保留原 Discord → QQ 默认入口。全局运行日志统一从“设置 → 启动与维护 → 运行日志”打开；使用标准二级窗口，等宽控制台最高 320px 并内部滚动，过滤/追踪/自动滚动位于正文下方，复制/清空位于底部右侧。四个转发页不再显示日志卡片。根文档预留稳定滚动条空间，避免切页与折叠时宽度跳动。机器人启动、停止和重启的主入口为“总览 → 机器人状态”，托盘提供同功能快捷入口：运行时显示停止/重启，停止时显示启动。四个功能页不再提供运行工具卡片、全局维护或重复日志入口。布局参考见 [docs/DESIGN.md](docs/DESIGN.md)。

## 快速开始（Windows）

当前安装脚本和桌面程序仅支持 Windows，Python 版本要求为 3.10 及以上。安装包会在缺少 WebView2 Runtime 时安装该组件。

1. 安装包版本直接使用随包提供的 `python\` 运行时（仅供运行 WRelay，不含 pip、venv 等通用开发工具），并在安装时从 `.env.example` 生成 `.env.prod`；源码目录运行 `install.bat` 时，如果没有内置 Python，则创建 `.venv` 并安装项目依赖。
2. 源码版本如果不存在 `.env.prod`，安装脚本会从 `.env.example` 生成配置模板。
3. 编辑 `.env.prod`，填写 QQ 官方机器人和 Discord Bot 配置。
4. 如需访问 Discord，在 `.env.prod` 中配置 `HTTP_PROXY`。
5. 双击 `启动面板.bat`，打开 WRelay 桌面窗口；安装包用户可用桌面或开始菜单快捷方式。
6. 在 WRelay 桌面端登记并启用 Discord 频道、X/Bilibili 来源、QQ 群和私聊用户，然后启动机器人。

详细的 Bot 创建、权限、`group_openid`、频道登记、代理和 RSSHub 配置方式见 [docs/SETUP.md](docs/SETUP.md)。

## 配置与权限说明

- Discord 频道、X/Bilibili 来源、QQ 群和 QQ 用户是否启用，以 `data/settings.json` 为准。
- Discord、X 和 Bilibili 分别使用独立的转发组配置；全局启用和转发组成员关系必须同时满足，消息才会发送。
- QQ 官方机器人使用 `group_openid`，不能从普通群号直接推算；同一个实体群被多个 Bot 发现时，
  每个 Bot 会保存自己的 OpenID 和转发目标关系。
- 旧配置中没有 Bot 归属的群继续由第一个已连接 QQ Bot 处理；面板状态卡片会显示当前工作区的连接状态。
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
├─ 启动面板.bat              # 启动桌面窗口
├─ WRelay.iss                # Inno Setup 安装包定义
├─ scripts/
│  ├─ run_tests.py           # 回归测试统一入口
│  ├─ prepare_runtime.ps1    # 准备安装包内置 Python
│  ├─ build_installer.ps1    # 构建 Windows 安装包
│  └─ test_installer_smoke.ps1 # 安装包冒烟检查
├─ docs/
│  ├─ DESIGN.md              # 主标识与桌面端界面概念图
│  ├─ SETUP.md               # 安装与配置指南
│  └─ CHANNELS.md            # Discord 频道权限记录
├─ plugins/
│  ├─ relay.py               # Discord 实时转发
│  ├─ x_rss.py               # X RSS 轮询和投递
│  ├─ x_rss_parser.py        # RSS 内容解析
│  ├─ bilibili.py            # Bilibili RSS 轮询和投递
│  ├─ bilibili_parser.py     # Bilibili RSS 内容解析
│  ├─ bilibili_rate_limit.py # Bilibili 限速、积压裁剪纯逻辑
│  ├─ command.py             # QQ relay 命令、OpenID 自动发现和身份记录
│  ├─ manage.py              # QQ 私聊管理命令
│  ├─ registration.py        # QQ 用户/群注册申请
│  ├─ fetch.py               # Discord 消息抓取、归一化和渲染
│  ├─ media.py               # Markdown、提及、Emoji 和媒体处理
│  ├─ sender.py              # QQ 消息发送、分片、重试和降级
│  ├─ retry.py               # 失败消息延迟重试
│  ├─ message_trace.py       # 投递结果追踪与持久化
│  ├─ failure_notification.py # 管理员失败通知与冷却
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
│  ├─ desktop_app.pyw        # WebView2 桌面窗口与原生托盘
│  ├─ desktop_controls.py    # 关闭偏好与托盘操作模型
│  ├─ windows_titlebar.py    # 自定义标题栏装载
│  ├─ windows_titlebar.cs    # 原生窗口命中测试与系统命令
│  ├─ 管理面板.pyw            # FastAPI 管理服务与启停协调
│  ├─ backup_restore.py      # 配置备份、校验和恢复回滚
│  ├─ process_manager.py     # 机器人进程与 PID 管理
│  ├─ autostart.pyw           # 开机自启入口
│  ├─ wrelay_panel.ico       # Windows 安装器与快捷方式图标
│  └─ web/                    # 桌面界面、窗口桥与品牌 SVG
└─ data/                      # 配置、游标、日志和运行时数据
```

## 技术栈

- [NoneBot2](https://nonebot.dev/)
- `nonebot-adapter-discord`
- `nonebot-adapter-qq`
- FastAPI / Uvicorn
- PyWebView / WebView2 / PyStray（Windows 桌面窗口与托盘）
- httpx
- RSSHub（X/Twitter、Bilibili RSS 来源）

## 运行数据

主要运行时文件包括：

- `data/settings.json`：频道、来源、群、用户、转发组和补发配置
- `data/discord_last.json`：Discord/X/Bilibili 按目标群保存的去重游标
- `data/bilibili.json`：Bilibili RSS 的 ETag 和轮询缓存
- `data/bilibili_protection.json`：Bilibili 限速时间窗、待发送积压和延后跳过记录
- `data/stats.json`：成功、失败和当日转发统计
- `data/message_trace.json`：最近投递结果追踪（自动限制保留数量）
- `data/qq_identities.json`：QQ 用户和群身份资料
- `data/qq_registrations.json`：待审核注册申请
- `data/channels_audit.json`：Discord 频道权限审计结果
- `data/logs/bot.log`：机器人运行日志

这些文件属于运行时数据，不应提交到版本库。

## 相关文档

- [安装与配置指南](docs/SETUP.md)
- [Discord 频道权限记录](docs/CHANNELS.md)
- [项目架构说明](ARCHITECTURE.md)
