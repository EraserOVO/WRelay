# WRelay 架构说明

> 面向项目维护者，帮助快速理解"每一块是干什么的、以后改东西应该去哪里"。
> 以当前实际代码为准。

---

## 1. 项目整体结构

```
WRelay/
├── bot.py                    机器人入口（NoneBot 启动、适配器注册、日志轮转）
├── plugins/                  NoneBot 插件目录（所有转发逻辑都在这里）
│   ├── config.py             配置读取 + 内存缓存（settings.json 的消费者）
│   ├── relay.py              Discord → QQ 实时转发主逻辑
│   ├── x_rss.py              X/Twitter RSS → QQ 轮询与投递
│   ├── x_rate_limit.py       X RSS 限速、队列裁剪与时间窗纯逻辑
│   ├── bilibili.py           Bilibili RSS → QQ 轮询与投递
│   ├── scheduled_message.py  QQ 定时消息投递
│   ├── x_rss_parser.py       RSS 内容解析
│   ├── bilibili_parser.py    Bilibili RSS 内容解析
│   ├── command.py            QQ 私聊 relay 命令（手动转发）
│   ├── manage.py             QQ 私聊管理命令（status/restart/mode 等）
│   ├── registration.py       QQ 注册指令（register / register-group）+ 申请存储（异步处理器在线程池写入）
│   ├── fetch.py              Discord 消息抓取 + 渲染（统一解析层；REST 使用 DISCORD_PROXY）
│   ├── sender.py             QQ 群发送（重试、分片、降级）
│   ├── retry.py              失败消息延迟重试与持久化队列
│   ├── delivery_dedup.py     X/Bilibili 重叠来源的跨来源投递去重
│   ├── message_trace.py      投递结果追踪与持久化
│   ├── failure_notification.py 管理员失败通知与冷却
│   ├── media.py              文本美化 + Emoji 解析 + 媒体下载（Discord CDN 使用 DISCORD_PROXY）
│   ├── history.py            去重记录读写（discord_last.json；异步读写在线程池执行并串行保护取消中的操作）
│   ├── dedup.py              去重判断逻辑（纯函数，不碰 I/O）
│   ├── filter.py             频道消息筛选（纯函数，relay/backfill 共用）
│   ├── backfill.py           启动后自动补发离线期间缺失的消息
│   ├── backfill_api.py       补发相关的 HTTP 接口（供面板调用）
│   ├── health_api.py         Bot 健康状态接口（供面板显示连接状态）
│   ├── identities.py         OpenID 身份资料库（昵称/群名/备注）
│   ├── stats.py              转发统计（成功/失败/当日条数）
│   └── json_io.py            JSON 原子读写工具
├── panel/
│   ├── desktop_app.pyw       桌面入口（WebView2 窗口 + 托盘 + 单实例）
│   ├── desktop_controls.py  关闭偏好、动态托盘菜单与操作互斥
│   ├── windows_titlebar.py   自定义标题栏的 .NET 装载入口
│   ├── windows_titlebar.cs   原生命中测试、标题栏绘制与系统窗口命令
│   ├── 管理面板.pyw           桌面进程内管理路由与机器人进程管理
│   ├── backup_restore.py    配置备份、恢复校验、失败回滚
│   ├── process_manager.py   机器人进程、PID 文件与存活检测
│   ├── local_bridge.py      桌面进程内路由调用与 Windows 本机管道
│   ├── onboarding.py        向导输入校验、来源复用与路由合并（纯数据逻辑）
│   ├── onboarding_api.py    向导进度、账号保存、授权及 RSS 检测接口
│   ├── autostart.pyw         开机自启入口
│   ├── restart_worker.pyw    面板重启 worker
│   ├── wrelay_panel.ico      Windows 安装器与快捷方式图标
│   └── web/                  面板前端（纯静态 HTML/JS/CSS）
│       ├── index.html
│       ├── app.js
│       ├── local-api.js     页面请求与桌面 Python 桥接
│       ├── onboarding.js    按用途分流的首次配置与验证流程
│       ├── onboarding.css   复用桌面端设计变量的向导样式
│       ├── desktop.js         桌面入口标记、窗口主题与外部链接桥接
│       ├── discord-page.js    转发页展示与可访问交互
│       ├── ui-components.js   公共外链按钮与 Modal 展示层
│       ├── ui-components.css  公共组件及根滚动条规则
│       ├── style.css
│       └── wrelay-logo.svg
├── scripts/                  构建、诊断与验证脚本（如 prepare_runtime.ps1）
├── data/                     运行时数据（settings、去重记录、统计等）
├── docs/                     文档（BUILDING.md、DESIGN.md、SETUP.md 等）
├── .env.prod                 环境变量（Discord Token、QQ AppID 等，不进版本库）
├── .env.example              环境变量示例
└── pyproject.toml            项目依赖
```

---

## 2. 各模块职责总览

| 模块 | 职责 |
|------|------|
| **Discord 侧** | 接收 Discord 消息事件、通过 REST API 抓取消息内容、下载媒体 |
| **QQ 侧** | 向 QQ 群发送消息、接收 QQ 私聊命令、自动发现群/用户 OpenID |
| **Relay** | 自动把 Discord / X RSS / Bilibili RSS 消息转发到 QQ 群；不做 QQ → Discord 或双向同步 |
| **桌面管理程序** | WebView2 加载本地页面；桌面进程内的管理路由负责配置、机器人启停、日志与看护，不监听管理端口 |
| **数据文件** | JSON 配置/状态、SQLite 跨来源投递记录及统计、身份信息，是 bot 与面板之间的 IPC 通道 |

---

## 3. Discord → QQ 实时转发流程

```
Discord 消息事件
    │
    ▼
plugins/relay.py
    │ ① 检查频道是否启用（config.get_active_channels）
    │ ② 检查是否有启用的 QQ 群（config.get_active_groups）
    │ ③ 去重判断（dedup.select_target_groups）
    │    └── 读取 history.load_last_messages()
    │    └── 按群分别判断：该消息是否已经发过
    │
    ▼
plugins/fetch.py
    │ ④ normalize_event() 把事件对象归一化为 dict
    │ ⑤ build_parts() 渲染为 QQ 消息段
    │    └── 文字：@提及转换、Markdown 清理、Emoji 提取
    │    └── 媒体：并发下载图片/音频（media.fetch_bytes_many）
    │    └── 失败降级：下载失败 → 文字 + 原链接
    │
    ▼
plugins/sender.py
    │ ⑥ send_relay_message()
    │    └── 文字超长自动分片（3900 字符/片）
    │    └── 媒体上传失败 → 降级为链接
    │    └── 多群并行发送（asyncio.gather）
    │    └── 429 限流退避重试（最多 3 次）
    │    └── 返回 {群openid: 是否送达}
    │    └── 记录最终投递结果、目标群与重试次数
    │
    ▼
plugins/relay.py
    │ ⑦ 只给发送成功的群记录去重 ID（dedup.apply_success）
    │ ⑧ 保存到 history.save_last_messages()
    │ ⑨ 记录统计（stats.record）
    └── 失败的群不记录 → 下次补发时只重试失败群
```

同一条消息按多段文字或媒体发送时，某群某一段和对应链接降级都失败后，当前发送轮次会跳过该群剩余片段；失败队列仍按整条消息重试，因此已成功发送的前置片段可能再次出现。

实时 Discord 入口按频道 FIFO 排队，全部频道共享 128 个待处理容量、最多 4 个频道并发。关闭开始后会拒绝已在等待容量的提交并释放在途标记，避免 worker 在 shutdown 后重建；未完成消息由下一次连接的补发流程恢复。

`config.get_active_groups()` 对启用群按 OpenID 保序去重。重复配置项只产生一个运行时发送目标，也只在使用活跃群生成的手动目标列表中出现一次；全部禁用时仍返回空列表。

`sender.send_relay_message()` 在实际投递前也会对显式传入或重试队列恢复出的目标按 OpenID 保序去重，确保任何调用方对同一群一次只触发一份投递。

共享发送入口 `retry.send_and_record()` 在申请群级在途标记前先对目标 OpenID 保序去重，避免重复目标把同一群的第二次占用误报为并发发送，并影响失败计数或重试判断。

Discord Embed 的链接仅在正文确有非空白文本时才因“正文已包含内容”而抑制；只有空白正文时仍保留 Embed URL，避免渲染后变成无内容消息。

---

## 4. Discord → QQ 启动补发流程

```
Discord 或 QQ Bot 连接成功（on_bot_connect 事件）
    │
    ▼
plugins/backfill.py → _backfill_missed()
    │ ① 检查补发总开关（config.get_backfill_enabled）
    │ ② 遍历每个启用频道
    │    └── 读取去重记录，算各群最落后的 last_id（dedup.compute_base_id）
    │    └── 无记录（首次启用）→ 只建基线，不补发，防刷屏
    │    └── 有记录 → REST 抓取 base_id 之后的消息（fetch.fetch_channel_messages_after）
    │    └── 按时间正序逐条交给 _backfill_message：检查实时在途标记、游标/筛选并渲染
    │    └── _deliver_backfill_message 发送并只为失败群登记延迟重试
    │    └── QQ 未连接时停止当前频道本批处理，交由后续重连补发
    │    └── 每条最多补发 get_backfill_limit() 条（默认 10）
    │    └── 逐条重读去重记录，实时转发已处理的群自动跳过
    │    └── 单页请求 helper 处理 Discord REST 状态、429 退避与 JSON 解码；分页入口维护锚点和完整缺口
    └── 与实时转发共用去重记录，正常情况下不会重复发送
```

面板清除待补发会先查询各频道最新消息，再通过 `history.update_last_messages()`
在最新游标状态上一次性合并基线；查询等待期间已由实时转发推进到更高消息 ID 的群会保留较高游标，避免清除操作造成游标回退。
每轮启动补发在线程池一次读取开关、额度、频道和目标群路由；补发期间再次触发时，下轮重新读取快照。面板的待补发统计与清除接口也用单次路由快照，避免按频道同步重读 settings。
补发后台任务由模块持有并记录未预期退出；应用关闭时取消并等待进行中的补发任务，尚未成功推进游标的缺口留给下次连接继续处理。

> 启动并发说明：on_bot_connect 会同时放行实时接收与补发任务。实时路径在
> 事件入队时登记共享的在途标记，直到消息处理完成才释放；补发逐条处理时
> 发现该标记会跳过该消息，避免媒体下载与游标落盘之间的重复投递。实时
> 路径失败仍按原有延迟重试与后续补发策略处理，不会因跳过而丢失消息。
>
> 抓取失败语义：分页抓取任一页失败（请求异常/超时/非 200/429 重试耗尽）
> 时整轮放弃并返回空，绝不返回不完整列表推进去重游标，宁可下一轮重新补发。
> 分页包含非对象消息或无效十进制消息 ID 时也整轮放弃，避免损坏 ID 在比较或
> 写入游标时抛错；单次收集超过上限（1000 条）且未确认翻到缺口底部时同样整轮放弃，
> 极大缺口需在面板「清除待补发」处置。补发翻页请求间隔 0.5s，429 按
> Retry-After 退避重试。

Discord REST 查询与 QQ 发送会将非有限或超过 30 秒的 Retry-After 值限制为安全退避时间，
避免单个错误响应长期挂起轮询或频道发送队列。

QQ Bot 断开时，实时路径不推进游标；QQ Bot 重新连接也会启动补发，以免 Discord
网关持续在线时缺口没有新的触发事件。实时发送失败时，`plugins/retry.py` 会把规范化消息、失败群、重试次数和下次
重试时间写入 `data/retry_pending.json`；异步流程先在事件循环内生成有界快照，再在线程池原子落盘并等待结果；即使写入任务连续收到取消，也要等该快照落盘后才释放串行锁。运行期若队列读取暂时失败，异步登记路径也会在线程池重读 JSON，并串行恢复队列，避免磁盘读取阻塞事件循环。进程关闭时停止并等待频道重试 worker，保留持久队列由下次启动恢复。重试队列与成功游标分离：某个群成功后
立即清除该群欠账，其余群继续重试；进程重启后恢复队列。队列只保存可重建消息，
不保存媒体字节；初次失败后在第 1/2/3 次重试前分别等待 30/60/120 秒，间隔从发送失败结束后起算。内容重建失败
或 3 次重试耗尽后移除，最终由低游标补发兜底。队列最多保存 10 条、每频道最多 5 条；
进程启动恢复旧队列时也会应用这两个上限。
恢复时单条下次重试时间必须是有限数值，且等待最多限制到当前最大退避 120 秒；`groups` 必须是非空字符串列表。NaN、Infinity、转换溢出或群列表结构无效时只跳过该条，由低游标补发兜底。
只有实际尝试发送的目标群才消耗一次重试次数；若目标群正由另一条发送路径处理，该群继续留在欠账队列且不消耗次数。混合发送中，已耗尽次数的真实失败群移除并交由低游标补发，仍忙碌的群重置次数后继续排队。
发送环节抛出异常时按一次发送失败进入相同退避流程，避免频道重试任务因单次异常退出；
再次登记已有欠账也会确保对应频道的重试任务已启动。
频道重试循环遇到未预期的处理异常时会保留队列、记录错误，并将已到期条目推迟到退避后再试；单频道错误不会静默终止该频道 worker。
补发正在运行时收到的新连接触发会合并为一轮后续补发，避免连接事件被运行中标记吞掉。

## 4.1 X RSS → QQ 轮询流程

```
定时器（最短 30 秒）
    │
    ▼
plugins/x_rss.py
    │ ① 读取启用的 X 来源与 X 转发组
    │ ② 请求 RSSHub，复用 ETag / 304 缓存；429/5xx/连接异常同轮退避重试
    │    RSSHub 不可用时回退 X 公开时间线（同一推文结构）
    │ ③ 解析原创、转发、引用、图片和视频
    │ ④ 以 x_rss:{handle} 作为独立来源游标去重
    │ ⑤ 复用 fetch/media/sender 投递到 QQ 群
    └── 发送失败或 QQ 未连接时不推进游标，后续轮询继续补投
```

X 与 Bilibili RSS 的轮询间隔下限为 30 秒；无效数值（包括正无穷）回退到 300 秒默认值，避免间隔读取异常结束后台任务。X/Bilibili RSS 的 ETag 与解析结果缓存绑定各自 feed URL；同一 X 来源 key 或 Bilibili feed `state_key` 改 URL、或升级时遇到未记录过 URL 的旧状态，会先清除旧 ETag/内存结果并完整拉取一次，避免把旧源内容用于新源的 304 响应。两条 RSS 链路只把字符串 ETag 发给服务器；损坏状态中的其他类型会被忽略并执行完整拉取，不会让该来源在请求构造阶段退出。X RSS 与 Discord 使用独立的转发组配置。RSSHub 不可用、解析失败或发送失败
只影响对应来源，不会阻断 Discord → QQ 链路；RSSHub 临时错误会在同一轮最多尝试 3 次，
最终失败仍不推进游标；RSSHub 不可用时会回退到 X 公开时间线。
两条 RSS 链路处理成功的 200 响应时，若响应没有新 ETag，会清除旧验证器；后续请求不再携带失效的 If-None-Match 值。
X 公开时间线的 MP4 变体按有效非负 bitrate 选择；无效数值（含 NaN/Infinity）按 0 处理，不会中断整份时间线解析。
X 公开时间线单条视频的 `video_info` 或 `variants` 结构无效时只跳过该视频媒体，推文正文仍保留。
公开时间线中结构无效的单条 tweet entry 会被跳过，后续有效推文仍继续解析。
公开时间线的主推文、转推和引用推文作者字段若不是对象，按空作者处理并保留正文及引用内容。
X 与 Bilibili 的后台轮询任务由各自模块在应用启动钩子中创建，不依赖 QQ Bot 连接钩子；这样即使连接钩子组中其他回调失败或被取消，RSS 轮询也会运行。`plugins/rss_polling.py` 共用两条链路的轮询循环和线程池间隔读取：QQ 未连接时不拉取上游，并每 5 秒检查连接状态；断线重连后立即恢复拉取，已连接时仍遵循来源配置的正常轮询间隔。间隔读取异常使用该来源的默认间隔。应用关闭时各模块先取消并等待轮询及尚在执行的 RSS 请求，再关闭 HTTP 客户端，避免遗留后台任务或关闭期间继续发起请求。
每轮轮询按当前配置回收已删除 X/Bilibili 来源的解析结果缓存；Bilibili 来源取消某投稿类型后，也会清除该类型的缓存。仍配置但停用的来源缓存保留，以供重新启用后复用。
活跃源按 RSS 状态身份去重：X key 大小写不敏感，Bilibili source ID 精确匹配；重复配置仅轮询首项并记录警告，避免并发争用同一 ETag、文章缓存和游标。
每轮来源列表和目标群路由在工作线程中基于同一份 settings 快照生成，避免按来源重复检查文件版本、阻塞事件循环或在一次筛选中混用配置版本；开始具体投递时仍读取当前路由。
X/Bilibili 每条 RSS 内容的筛选与翻译选项也从同一份 settings 快照生成并在线程池读取，避免同一条内容因两次配置读取而混用版本。
X 每条推文在发送前仍重新读取最新目标群路由，但在工作线程执行；Bilibili 队列调度为来源和目标群只读取一次共同快照，避免对每个排队投稿同步重读路由。
X/Bilibili 状态接口的来源配置在线程池读取；Bilibili 来源、保护选项和转发组由一次 settings 快照生成，避免管理面板轮询状态时同步执行多次文件摘要检查。X 单条推文抓取也在线程池读取 RSSHub 地址，避免命令触发的设置文件检查阻塞事件循环。

X 来源保留各自的 `x_rss:{handle}` 游标；此外，投递时按 X 原生推文 ID 与 QQ 群检查共享成功记录，避免同一推文从多个来源进入同一群。某群已有成功记录时会推进当前来源游标并跳过发送，其他尚未收到该推文的群仍会正常发送。记录保存在 `data/delivery_dedup.sqlite3`，保留 90 天并限制最多 100,000 条。

若已确认发送成功但 SQLite 写入失败，当前进程会用同样的保留上限暂存该投稿与群，按单调时钟 TTL 和最近写入顺序清理、淘汰。数据库读取失败时，只有全部目标群都在这份内存记录中才会安全跳过；未知目标会保留为错误并等待恢复。内存兜底不跨进程重启，SQLite 恢复后仍以持久记录为准。

---

## 4.2 Bilibili RSS → QQ 轮询流程

```
定时器（最短 30 秒）
    │
    ▼
plugins/bilibili.py
    │ ① 读取启用的 Bilibili 来源与 Bilibili 转发组
    │ ② UP 主按投稿类型生成视频 / 专栏 / 动态路由；标签与关键词仅生成视频搜索路由
    │    RSS 请求按来源串行并留出间隔；429/5xx/连接异常最多退避重试 4 次
    │ ③ 按投稿类型分别请求 RSS，复用独立 ETag / 304 缓存并对临时错误退避重试
    │    单 Feed helper 负责 URL/ETag 校验、304 缓存回退、解析结果缓存和单类型错误记录
    │ ④ plugins/bilibili_parser.py 解析投稿类型、标题、作者、标签、链接和封面
    │ ⑤ 以 bilibili:{kind}:{value} 和类型子键保存 feed 状态与游标基线
    │ ⑥ 按关键词和投稿类型筛选；视频本体开关仅作用于视频投稿
    │ ⑦ 进入有限积压队列，按最短间隔和每小时上限复用 translation/sender 投递
    │    forward_video 开启时仅对视频投稿尝试发送单段视频；超限、多段或失败时附视频链接
    └── 首次启用来源或群只建基线；专栏/动态 feed 首次启用时单独建基线
         发送失败、QQ 未连接时不推进游标
```

Bilibili 与 Discord、X 使用独立的转发组和来源游标；全局来源、QQ 群启用状态以及当前
转发组成员关系必须同时满足才会投递。消息文本使用“自动转发来自 Bilibili 中[UP 主名称]的投稿”
来源头，优先使用 RSS 投稿作者，缺少作者时回退到注册来源值；只渲染标题和
原始投稿地址（纯文本链接），并发送 RSS 提供的封面图片；不发送作者行、标签、其他分类、简介及播放统计。UP 主来源可选择视频、专栏和动态；标签与关键词来源只支持视频。来源可单独开启 `forward_video`，此时仅视频投稿会尝试从 RSS 视频地址或 Bilibili 播放接口获取单段视频，复用媒体下载器的大小上限（默认 30 MiB）；超过上限、多段视频或下载失败时附上视频链接。投稿链接中的 BV 号大小写敏感（base58 变体），解析时必须原样保留：大写化会得到另一个不存在的投稿号，`view` / `playurl` 接口返回 `-400 请求错误`，视频只能降级为链接，私聊手动转发也会直接报「获取Bilibili投稿失败」。`view` 接口的常见业务错误码会转换为可读原因后再展示（例如 `62012：仅UP主自己可见`、`62004：稿件审核中`、`62002：稿件不可见`、`-404：投稿不存在或已删除`），未收录的码仍保留接口原始 message；这类投稿需要有效登录态才能抓取，请求不携带 cookie 时只会得到该提示。
RSSHub 请求失败、解析失败或发送失败只影响对应 Bilibili 来源，不会阻断其他自动链路。
面板在 Bilibili 转发组来源行的漏斗筛选设置中单独配置视频本体转发，每个来源各自保存开关。
来源注册只收集范围和 UID、标签名或关键词，列表及来源选择器直接显示该真实值，不提供可编辑备注。来源筛选沿用 X 的“消息筛选”窗口，只设置关键词、投稿类型和视频本体开关；关键词在标题、简介、作者与分类标签中不区分大小写匹配。旧版标签、标题包含/排除和作者白名单筛选字段不再参与筛选，编辑保存时清除这些旧字段。
若 RSSHub 日志显示 Bilibili 上游 `412 Precondition Failed`，RSSHub 通常会对外返回 503；此时
需要使用带 Chromium 的 RSSHub 镜像，或为 RSSHub 配置有效的 `BILIBILI_COOKIE_1`。

X RSS 使用独立的持久化投递队列：`settings.json` 中的 `x_protection` 控制所有 X 来源合计的
最短发送间隔、每小时成功投递上限、每来源积压上限和超限策略。默认值为 5 秒、300 条/小时、
每来源 100 条；这些默认值比 Bilibili 的节奏宽松。队列保存在 `data/x_protection.json`，
单独后台任务按全局限额排空队列；QQ 未连接或投递失败时保留条目，恢复后继续处理。队列文件
整体损坏或版本不支持时暂停 X 自动投递并保留原文件，修复后重新读取。

Bilibili 另有一层独立的投递保护：`settings.json` 中的 `bilibili_protection` 控制全链路
最短发送间隔、每小时成功投稿上限、每个来源的积压上限和超限策略。积压队列保存在
`data/bilibili_protection.json`；启动恢复时校验游标格式，损坏条目单独跳过，不阻断其他来源。
跨来源投递去重与 X 共用 `data/delivery_dedup.sqlite3`，键由 Bilibili 原生投稿 ID 和 QQ 群组成；重复命中的来源会推进自己的游标并移除相应队列项，不会再次发送或消耗一次实际发送限额。失败群不记为已送达，仍可补发。Discord 继续使用原有频道/消息/群游标与重试保护；授权 QQ→QQ 手动转发不参与 RSS 去重，用户可以再次主动转发同一条消息。
若整个文件无法读取、JSON 无效或顶层格式错误，Bilibili 自动投递会暂停且不推进游标；
原文件不会被覆盖，修复文件后轮询会重新加载。此类故障通过 `protection_error` 与
`protection_failed` 显示为“投递保护异常”，不再归类为 RSSHub 请求失败。共享 JSON 写入函数
在原子替换前执行 flush 与 fsync，确保临时文件内容已同步至磁盘；这不能替代备份。
选择“保留最新、跳过旧投稿”时，队列超限会优先丢弃旧内容；
选择“保留最旧、跳过新投稿”时，新内容先记录为延后跳过，只有保留的旧内容处理完后才推进游标，
避免游标提前越过仍需发送的投稿。关键词和投稿类型筛选在内容进入积压队列前执行。
投递调度通过统一的队列移除函数处理已投递、已去重和被筛选条目；仅在来源队列清空后推进延后跳过记录，各分支仍在原位置保存状态。

## 4.3 统一翻译路由与 AI 模型轮换

Discord、X RSS、Bilibili RSS 和 QQ 手动转发在完成消息渲染后，统一通过 `plugins/translation.py`
按当前链路配置尝试首选服务和后备服务：

Discord 实时转发、启动补发和失败重试共用 `plugins/forward_service.py`；频道筛选与翻译配置读取都在线程池执行，避免 settings 文件版本检查占用事件循环。
统一翻译路由及 AI/TMT 适配器读取服务配置、生成缓存身份和记录 AI 档案状态时也在线程池读取 settings，避免翻译请求中的文件版本检查占用事件循环。

```
消息正文
    │
    ▼
plugins/translation.py
    ├── 腾讯 TMT → plugins/tencent_tmt.py
    └── AI API  → plugins/ai_client.py
                    │
                    ├── 首次请求配置模型
                    ├── 模型不存在、模型并发受限，或明确的模型容量/节点不可用
                    │   → 查询 /models，按优先级逐个尝试可用模型
                    └── 成功后记录实际使用的模型
```

AI 模型轮换只针对明确的模型级错误：400 模型不存在/不支持、包含模型并发限制的
429，以及明确表示无可用 worker、服务不可用或模型过载的 500/503。普通网络超时、
普通配额 429、空响应和未指向模型的服务端错误不会触发轮换。候选模型逐个失败后，
再视为本次 AI 服务失败；所有服务均失败时，
翻译路由返回空结果，由调用方保留原文。
轮换所需的 `/models` 查询失败会封装为 `AIClientError`，交给统一翻译路由尝试后备服务。
AI completion 的 `message.content` 必须是字符串；非文本 JSON 值视为无效响应并进入后备路由，不会将 Python 对象表示当译文发送。
AI 模型目录按 API 地址使用 128 项 LRU，临时备用模型状态按配置档案使用 64 项 LRU；淘汰只会令旧地址或档案在再次使用时重新查询模型或重新建立降级状态。
AI 配置校验限制超时为 1–300 秒、最大输出长度为 1–1,000,000 tokens；旧版兼容配置和每个 AI 档案使用同一上限，超界 settings 不会进入运行时配置。
settings 保存统一由 `plugins/settings_model.py::validate_settings()` 按配置域顺序校验；字段规则拆分到账号/路由、RSS 来源、定时消息、翻译、AI 和转发组校验函数，定时消息再分别验证间隔与日历计划。面板保存时对 Discord、X、Bilibili、QQ→QQ 四个转发组列表分别限制为最多 30 组；主入口仍返回首个错误并保留原错误信息。
同一消息的翻译缓存也纳入原文摘要和当前 AI 档案 ID、API 地址和模型（以及 TMT 地区/项目）身份；正文编辑或切换服务档案/配置后不会继续复用旧译文。
缓存尚未建立时，同一消息与完整路由身份的并发翻译调用共用一个在途结果，避免同一 AI/TMT 请求同时重复执行；请求失败后后续调用仍可重试。

`plugins/health_api.py` 提供最近一次真实翻译成功使用的服务和模型，管理面板据此显示
“当前工作模型”。面板中的 AI API 检测是对每个配置的独立直连检测，不触发运行时模型
轮换；检测记录保存在 `settings.json`，与运行中的工作模型状态相互独立。

---

## 5. QQ 私聊命令与手动转发流程

```
QQ 私聊消息
    │
    ├── 以 "relay " 开头 ──────────────► plugins/command.py
    │                                      │ ① 白名单校验（config.get_active_user_openids）
    │                                      │ ② 未授权 → 回复拒绝
    │                                      │ ③ Discord 链接 → fetch_message + build_parts
    │                                      │    X 状态 / Bilibili 投稿链接 → 对应抓取和媒体渲染器
    │                                      │    Bilibili 手动链接按全局消息类型筛选
    │                                      │    普通文字 → 直接构造消息
    │                                      └── ④ send_relay_message → QQ 群
    │
    ├── 以 "register" 开头 ────────────► plugins/registration.py
    │                                      │ 用户注册（不需白名单）：
    │                                      │    register <QQ号> <昵称>
    │                                      │ 绑定当前 User OpenID 写入
    │                                      └── data/qq_registrations.json，等待面板审核
    │
    └── 其他命令 ──────────────────────► plugins/manage.py
                                           │ ① 白名单校验（同上）
                                           │ ② 未授权 → 回复拒绝
                                           │ ③ 命令分发：status / mode / groups /
                                           │    channels / users / backfill / restart
                                           │ ④ 通过 Windows 本机管道调用桌面管理路由
                                           └── ⑤ 回复执行结果
```

QQ 私聊管理命令依赖桌面进程持续运行；窗口隐藏到托盘时本机管道仍可用，退出桌面端后机器人可继续转发，但管理命令暂不可用。
管理命令使用的 QQ 白名单在线程池读取；等待 settings 文件版本检查时事件循环仍可处理转发任务。多个 QQ Bot 的重启连接确认回调串行消费同一标记，避免并发重复回复。
群、频道和权限列表读取设置失败时，QQ 命令会报告管理 API 错误，不会显示成空列表；`status`、`backfill` 状态及 `timer` 查询/开启前校验读取失败时也会报告错误，不会伪装为已停止、默认开启或空配置。

`relay list` 查看启用群，`relay <序号1> <序号2>` 或 `relay all` 选择目标，数字序号只接受范围内 ASCII 十进制（先限制长度再转换，避免 Unicode 数字转换异常及超长整数输入），下一条消息
转发到 QQ 群。若下一条是 Discord、X/Twitter 或 Bilibili 投稿链接，会抓取单条原消息并按对应媒体规则发送；Bilibili 视频默认尝试上传，复用媒体大小上限，超限、多段或下载失败时附视频链接。三种私聊手动转发都由 `config.get_active_user_openids` 执行同一套 QQ 用户白名单校验；这是授权用户触发的 QQ → QQ 手动操作，不是 QQ → Discord 自动同步。
私聊转发按用户串行处理，以保持两步指令和 pending 顺序；授权、目标群、pending 超时、来源筛选及翻译配置均在线程池读取，等待 settings 文件检查时事件循环仍可调度其他消息。
私聊 pending 的消费入口负责超时、目标群和投递；Discord/X/Bilibili 内容分别由来源 helper 准备，返回具名的文本、媒体、翻译身份和配置。抓取失败或筛选为空时由入口统一回复既有提示并终止本次转发。

QQ 私聊命令由多个 `on_message` matcher 共用同一事件流：`plugins/command.py`（priority=14）、`plugins/registration.py`（priority=15）、`plugins/manage.py`（priority=20）。这类全局 matcher 的 `bot` 参数必须注解为通用 `nonebot.adapters.Bot`，再在函数体内用 `isinstance` 判断具体平台；直接注解 `QQBot` / `DiscordBot` 会让 NoneBot 的参数校验在进入函数体前对另一平台的事件抛 `TypeMisMatch`，日志表现为大量 `Running Matcher ... failed`。relay 消费消息后除调用 `stop_propagation()`，还在 `plugins/c2c_consumed.py` 登记事件 ID，低优先级 matcher 先检查该标记；在 `stop_propagation` 异常链可能丢失的运行环境（Nuitka 编译产物）中，缺少该兜底会让同一条消息被回复两次（例如转发后额外收到「未知命令」）。

QQ 群手动转发在 `plugins/qq_fwd.py`：`relay list` 列出共享转发组中的目标，`relay <序号>` 绑定当前群和发送者，只消费下一条普通消息。目标条目和关联群从同一 settings 快照在线程池读取；同一群内同一成员的命令、下一条消息和 pending 超时清扫共用一把锁，清扫在锁内重新检查创建时间后才删除，避免误删刚续设的目标。发送前仍会复查目标。QQ 附件下载受并发和大小限制，应用关闭时会先回收在途下载再关闭 HTTP 客户端；关闭期间的 QQ 重连事件不会重新启动 pending 超时清扫循环。序号只接受 ASCII 十进制数字；无法解析或超出 Python 列表可索引范围的输入按无效命令回复，不会中断事件处理。重复 OpenID 仅在首次符合启用目标条件时去重，前置禁用重复项不会遮住后续启用项。源群有重复登记时，只要任一匹配项启用即可发起转发；全部禁用仍会拒绝。
旧配置迁移只复制结构有效的 QQ 群成员列表；旧组数据存在但成员结构无效且没有可迁移的有效关系时，迁移为空组，不扩大到全部启用群。

一次性 pending 在处理下一条消息时立即消费；媒体构造或翻译抛出异常时会记录错误并回复「转发失败」，避免异常离开事件处理器后用户无反馈。发送仍由 sender 统一处理。
私聊与群手动转发的 pending 等待时长用单调时钟判定，不受系统日期校准影响。QQ 私聊“上一条消息”时间窗无效时回退到 1800 秒，群选择超时无效时回退到 60 秒；数值配置溢出不会中断消息处理。群 pending 超时清扫循环由 QQ Bot 连接事件启动并保留任务引用；重复连接不会创建第二个循环，进程关闭时会取消并等待任务结束。

QQ 群消息中的 `register-group <群号> <群名称>`（plugins/registration.py）：
发送者 `member_openid` 必须在 `qq_user_openids` 白名单内（白名单在线程池读取），申请绑定当前
Group OpenID 写入 `data/qq_registrations.json`，等待面板审核。
中文"注册"指令已删除，中文消息按未知命令正常回复，不触发注册。

群内 @ 机器人的未知消息由 `plugins/manage.py:group_at_hint` 回复“未知命令，输入 list 查看可用命令”。
优先将 QQ 适配器 `mention_user` 消息段的 `user_id` 与机器人 `self_id` 精确比对；目标 ID 缺失时才回退到 `is_bot=true`。因此即使适配器漏掉辅助标记，确实 @机器人的消息仍可触发；@其他成员、普通群消息以及无法确认提及目标的旧空文本事件不触发。
`list`、`relay` 和 `register` / `register-group` 前缀仍交给已有命令处理器，有进行中的 relay pending 时由转发流程消费。
`scripts/test_manage_group_at_hint.py` 覆盖明确 @ 机器人、@其他成员、普通群聊、已知命令、无法确认目标的旧空文本事件、机器人消息和 relay pending 分支。

QQ 私聊的 `backfill on/off` 走字段级接口 `POST /api/settings/backfill-toggle`：
只提交 `backfill_enabled` 一个字段，由面板读取当前配置后仅修改该字段再
走统一保存流程（结构校验 + `.bak` + 原子替换；值未变化时不写盘），
避免回传整份配置覆盖面板刚保存的其他字段。读取、校验和保存事务在线程池运行，
慢磁盘或配置读取不会阻塞面板事件循环；revision 冲突仍返回 409。

---

## 6. 权限 / 自动发现 / 身份库的关系

```
机器人收到 QQ 群消息 / 被拉进群 / 收到私聊
    │
    ├──► 自动发现（plugins/command.py）
    │      把 group_openid 写入 data/qq_group_openids.json
    │      把 user_openid  写入 data/qq_user_openids.json
    │      ⚠️ 只记录，不授权，也不进同步名单。需先注册再经面板审核
    │      面板同步预览/同步与注册审核会校验现存发现文件；文件缺失视为空，损坏 JSON、错误容器或无效 Bot 归属映射会明确报错并保留原文件
    │
    ├──► 注册申请（plugins/registration.py）
    │      用户私聊：register <QQ号> <昵称> → 绑定当前 User OpenID
    │      群聊（白名单用户）：register-group <群号> <群名称> → 绑定当前 Group OpenID
    │      写入 data/qq_registrations.json（同一 openid 只有一条记录）
    │      面板「注册审核」：√ 通过 → 按同步流程加入 settings（默认禁用），
    │                        并把注册信息带入条目（用户：昵称→name、QQ号→qq_id；
    │                        群：群名→name、群号→qq_id）
    │                        × 拒绝 → 申请消失（重新注册会再次出现）
    │
    ├──► 身份记录（plugins/identities.py）
    │      把昵称/群名、最后活动时间写入 data/qq_identities.json；无效、非有限或超前超过 5 分钟的事件时间回退到当前时间
    │      QQ 高频消息触发的 OpenID 与身份文件操作在线程池执行，避免阻塞 asyncio 消息循环
    │      身份资料落盘失败只记录警告并跳过展示信息更新，不影响已完成的自动发现
    │      面板读取身份库时，缺失文件视为空资料；损坏 JSON 或无效容器结构会报告错误，不伪装成名称和备注被清空
    │      面板在单次身份资料快照上同时生成用户和群信息，避免一次响应混用不同写入时点
    │      ⚠️ 纯展示用，绝不参与权限判断
    │
    └──► 权限判断（plugins/config.py）
           唯一权限来源：data/settings.json 里的
           qq_group_openids[*].enabled = true
           qq_user_openids[*].enabled = true
           ⚠️ 自动发现/注册审核通过的新群/新用户默认 enabled=false
              必须在管理面板手动勾选才放行
```

**四个关键原则：**

- `qq_user_openids` 和 `qq_group_openids`（settings.json 中的列表）是**唯一的权限来源**。
- 自动发现**不会自动授权**，也**不直接进同步名单**；只有主动注册后才会出现在面板审核列表里。
- 注册 ≠ 授权，审核通过 ≠ 自动启用：审核通过只是把 openid 加入 settings（`enabled: false`），必须由管理员手动勾选。
- `qq_identities.json` 是**身份资料库**（昵称、群名、备注），只用于面板展示，不参与任何放行判断。


---

## 7. 管理面板核心卡片与诊断工具

### 7.1 QQ 接收群

多 QQ Bot 配置使用面板左上角的 QQ Bot 下拉框作为工作区。Discord → QQ 和 X → QQ
页面只展示当前 Bot 发现并归属的群，不再在每个群行单独绑定 Bot；同一个实体群可以因
不同 Bot 使用不同的 `group_openid`，分别加入多个 Bot 工作区。新发现或注册审核通过的群
记录发现它的 Bot；旧配置中 `bot_id` 为空的群继续兼容为第一个已连接 Bot。
若 settings 中同一 OpenID 重复，运行时优先采用首个启用条目的 Bot 绑定；没有启用项时按首条登记绑定处理重试目标。
多个 Bot 并行发送时，单个 Bot 批次的意外异常只将该 Bot 负责的群标记为失败；其他 Bot 的成功结果仍进入去重与投递追踪。

机器人状态接口同时返回整体 QQ 连接状态和已连接 Bot ID 列表。面板优先按当前工作区的
逐 Bot 状态显示；单 Bot 在旧版健康接口下可回退到整体 QQ 状态，多 Bot 缺少逐 Bot 状态
时显示「状态未知」，避免把其他 Bot 的连接状态误算过来。

| 项目 | 说明 |
|------|------|
| 数据来源 | `settings.json → qq_group_openids` |
| 展示辅助 | `qq_identities.json → groups`（群名） |
| 每行字段 | openid、名称（群名，注册审核通过时带入）、账号（群号）、启用/禁用、测试标记 |
| 同步按钮 | "注册审核"：读取 `data/qq_registrations.json`（群注册申请），√ 通过 → 按同步流程加入 settings（默认禁用，群名/群号自动带入条目），× 拒绝 → 移除申请；管理员勾选后生效 |
| 影响范围 | 实时转发目标群、启动补发目标群 |

### 7.2 Discord 转发频道

| 项目 | 说明 |
|------|------|
| 数据来源 | `settings.json → discord_channels` |
| 展示辅助 | Discord API 真实频道名（面板点"刷新频道记录"时拉取，缓存于 bot 进程） |
| 每行字段 | 频道 ID、显示名、启用/禁用、测试标记 |
| 同步按钮 | "刷新频道记录"→ 执行 `scripts/diagnose_channels.py`，更新本机 `data/channels_audit.json` 与仅本地保存的 `data/channels_audit.md`，展示哪些频道 bot 有读权限 |
| 影响范围 | 哪些 Discord 频道的消息会被转发 |

### 7.3 私聊权限

| 项目 | 说明 |
|------|------|
| 数据来源 | `settings.json → qq_user_openids` |
| 展示辅助 | `qq_identities.json → users`（昵称、管理员备注） |
| 每行字段 | openid、名称（昵称，注册审核通过时带入）、账号（QQ号）、启用/禁用 |
| 同步按钮 | "注册审核"：读取 `data/qq_registrations.json`（用户注册申请），√ 通过 → 按同步流程加入 settings（默认禁用，昵称/QQ号自动带入条目），× 拒绝 → 移除申请 |
| 影响范围 | 哪些 QQ 用户可以使用 relay 命令和管理命令（status/restart/mode 等） |

### 7.4 运行日志、消息追踪与连接诊断

设置 → 启动与维护 → 运行日志窗口内的「消息追踪」按钮打开二级窗口，读取 `message_trace.py` 保存的最近
50 条投递记录，展示来源、消息摘要、目标群、最终结果与重试次数。设置 → 启动与维护中的
「连接诊断」按钮只读显示进程、Discord、QQ 连接和待重试投递数量，不会发送测试消息。
面板看门狗在机器人进程正常但 QQ 或 Discord 连续不可用 2 分钟时重启整个机器人进程，尝试恢复网关连接；
因此另一平台也会短暂重连。持续故障时，两类自动恢复重启的间隔分别从 5 分钟逐步延长，最长每小时一次；双链路同时故障时共享重启冷却，避免连续重启。
「失败通知」开关控制失败提醒；仅白名单中已启用且勾选「告警」的用户会收到通知。开关、冷却时间及收件人从同一 settings 快照在线程池读取；同一失败项和收件人的并发回调串行检查冷却，避免重复提醒。冷却按收件人分别记录且只在私聊发送成功后开始，暂时发送失败的收件人可在后续失败回调中重试。

---

## 8. 重要数据文件

私聊 Bilibili 投稿链接的消息类型筛选保存在 settings.json 的 bilibili_c2c_relay_filter 中，由管理面板的 Bilibili「手动转发」卡读写；视频本体归入“文件”类型，旧配置缺少此字段时按全部消息类型启用处理。

定时消息的年度计划必须使用日历中存在的日期。管理面板会拒绝不存在的月日组合；遗留无效计划会在配置未变期间记录一次错误并暂停该条，不会每分钟重算；2 月 29 日仍只在闰年触发。
定时消息配置读取时，间隔及月/年日期字段的整数转换溢出会回退到已有安全默认值；磁盘配置仍先经过整体结构校验。每条到期消息发送前重新在线程池读取启用群，并与该计划显式目标群取交集，避免等待 settings 文件检查时阻塞事件循环。

定时消息循环按条隔离计划时间计算与投递异常：计算抛错的计划暂停到配置变化，发送后的重算抛错也只暂停该条；发送失败则推进到下一次计划时间。其他计划继续处理，不会因一条反复抛错而每秒重试或阻塞同轮任务。

QQ 定时消息可在每条计划内保存一张 PNG、JPEG、GIF 或 WebP 附件，正文允许为空以发送纯图片。图片 data URL 与消息计划一同保存在 `settings.json`，每张最多 2 MiB、全部最多 3 MiB，因此沿用现有配置备份与恢复。投递先发送正文，再发送图片；本地图片上传失败按投递失败记录，不会生成空链接降级。定时循环的计划签名忽略图片数据，避免每秒对较大的 Base64 附件重复格式化；面板每 3 秒轮询的 `/api/status` 只返回计划摘要，不回传图片数据。

`plugins/json_io.update_json` 根据调用方传入的默认值校验文件顶层类型。文件缺失时使用默认值；已有文件若无法解析或顶层类型不符，会中止读改写并保留原内容，避免注册、身份或追踪数据被默认空结构覆盖。

`settings.json` 的兼容迁移只遍历类型正确的列表；已有字段类型错误时保留给结构校验处理，
由配置读取层切换到内存默认值并保留原文件，不会在迁移阶段因遍历 `null` 而提前抛出异常。
旧版定时消息间隔迁移遇到无法转换的数值（包括 Infinity）时使用保守的每日或每小时默认间隔，
不会让配置兼容阶段异常退出。
转发组的 channels、sources、groups 字段若不是列表，会在各链路归一化时按空成员处理；
不会把字符串拆成单字符 ID，也不会因此扩大路由范围。

| 文件 | 写入方 | 读取方 | 内容 |
|------|--------|--------|------|
| `data/settings.json` | 面板（主要；保存时先轮转 `settings.json.bak` 备份）；bot（仅文件完全不存在时首次默认初始化） | bot（高频，带线程安全快照缓存；损坏/结构无效时内存降级，不覆盖原文件）、面板 | 启用的群/频道/用户列表、补发开关、模式预设 |
| `data/settings.json.bak` | 面板（每次保存前轮转写入） | 面板 / 人工（配置损坏或误写时恢复） | 上一版 settings.json 的完整备份（仅保留最近一代） |
| `data/restore_backups/restore-before-*.zip` | 面板 | 面板恢复流程 / 管理员 | 每次恢复前的白名单快照；多文件恢复失败时用于自动回滚，最多保留最近 10 份 |
| `data/discord_last.json` | bot | bot | Discord/X/Bilibili 每个来源或频道每个群最后成功转发的消息 ID（去重用）；读取时校验旧版及按群游标的非负十进制 ID（最多 32 位），结构错误时中止游标读改写并保留原文，避免运行期比较崩溃或游标重置 |
| `data/delivery_dedup.sqlite3` | bot（retry.py 经 delivery_dedup.py） | bot（X/Bilibili 自动投递） | X/Bilibili 原生投稿 ID 与 QQ 群的成功投递记录；保留 90 天，最多 100,000 条；失败和未确认的群不记为成功 |
| `data/qq_group_openids.json` | bot（自动发现）；面板（审核通过时补回缺失 openid） | 面板（同步预览、审核补回校验） | 机器人发现的所有群 openid 及 `group_bot_ids` 归属映射（原始记录，非权限，不进同步名单） |
| `data/qq_user_openids.json` | bot（自动发现）；面板（审核通过时补回缺失 openid） | 面板（同步预览、审核补回校验） | 机器人发现的所有用户 openid（原始记录，非权限，不进同步名单） |
| `data/qq_registrations.json` | bot（注册指令）；面板（审核通过/拒绝时移除申请并写回） | 面板（注册审核列表） | 待审核的注册申请：用户（QQ号/昵称）、群（群号/群名/操作人），非权限 |
| `data/qq_identities.json` | bot（自动记录） | 面板（展示） | 用户昵称 / 群名 / 最后活动时间 / 管理员备注 |
| `data/stats.json` | bot | 面板 | 转发成功/失败累计 + 当日条数；负值、布尔或其他异常计数安全归零后继续累计；异步写入被取消时仍等待当前原子更新完成再释放计数锁；损坏 JSON 或错误顶层结构时保留原文件，计数暂留进程内，持久化异常不会阻断转发或失败群重试 |
| `data/channels_audit.json` | bot（诊断脚本） | 面板 | 频道权限扫描快照（可读/可见频道列表） |
| `data/runtime_log_level.json` | 面板 | bot | 运行时日志级别（面板在线程池原子写入后 bot 3 秒内自动生效） |
| `data/bot.pid` | 面板（临时文件 + 原子替换） | 面板 | 机器人 PID 与 Windows 进程创建时间；兼容并升级旧纯数字 PID 文件，防止 PID 被同路径 Python 复用后误认；文件确实缺失才视为未启动，损坏/不可读或进程权限不足时保留现场并阻止启停 |
| `data/restart_pending.json` | bot（manage.py） | bot（manage.py） | 跨进程重启确认标记（非空发起者 OpenID、可选 Bot ID 和有限 wall-clock 时间）；文件读写在线程池执行；标记写入取消时等原子写完成后清理，避免无重启请求的孤儿标记；确认回调取消时等待标记清理完成后再释放并发锁；超过 5 分钟或超前当前时间 60 秒以上的标记、无效 OpenID 或非字符串 Bot ID 只清除不发送，允许 60 秒内的时钟回拨；成功时仅由发起命令的 QQ Bot 在重连后发送确认并清除；无 Bot ID 的旧标记仍由首个重连 Bot 处理 |
| `data/retry_pending.json` | bot（retry.py） | bot（retry.py）、面板 | 失败消息摘要、失败群、重试次数和下次重试时间；异步写入使用有界快照并在线程池完成；不保存媒体字节；文件整体损坏时保留原文件并将新欠账留给补发兜底，单条非法时间、重试次数、群列表、频道 ID 或消息 ID 只跳过该条 |
| `data/message_trace.json` | bot（message_trace.py） | bot（health_api.py）、面板 | 最近投递结果、目标群、失败原因和重试次数；自动限制记录数量，单条字段异常时安全归一化，不阻断整页历史 |
| `data/panel_auth.json` | 桌面端 | 桌面端、bot（manage.py） | 本机管道和管理路由认证令牌，不应外传 |
| `data/x_rss.json` | bot（x_rss.py） | bot（x_rss.py） | RSS ETag/Last-Modified 等轮询缓存；异步轮询的状态读改写在线程池执行；文件 JSON、顶层或 `sources` 容器损坏时状态更新不覆盖原文件，当前轮退回无 ETag 完整拉取；来源游标由 `discord_last.json` 负责，跨来源投稿判重由 `delivery_dedup.sqlite3` 负责 |
| `data/x_protection.json` | bot（x_rss.py） | bot（x_rss.py）、面板状态接口 | X 全来源限速时间窗、来源积压队列和延后跳过记录；后台投递任务逐条排队发送；损坏或版本不支持时暂停 X 自动投递并保留原文件 |
| `data/bilibili.json` | bot（bilibili.py） | bot（bilibili.py） | Bilibili 按来源及投稿类型保存的 ETag/Last-Modified、轮询缓存、专栏/动态基线和已启用投稿类型；异步轮询的状态读改写在线程池执行；文件整体或 `sources` 映射损坏时暂停轮询，避免清空基线后跳过投稿；单条来源状态损坏时只重建该来源元数据并为新投稿类型建立安全基线；状态写入失败会中止当前来源本轮，留待下轮重试 |
| `data/bilibili_protection.json` | bot（bilibili.py） | bot（bilibili.py）、面板状态接口 | Bilibili 限速时间窗、来源积压队列和延后跳过记录；异步流程读写均在线程池执行；损坏时暂停自动投递并保留原文件 |

---

## 9. 重要代码文件

| 文件 | 职责 | 修改频率 |
|------|------|----------|
| `bot.py` | 入口：适配器注册、日志轮转、运行时日志级别监听；控制台和文件日志均关闭异常局部变量诊断，避免 traceback 输出适配器对象中的 Token | 低 |
| `plugins/config.py` | settings.json 消费者：带文件版本缓存的配置读取；Discord 实时转发和 QQ 默认发送路由从单次快照取得频道/目标群/筛选及 Bot 绑定；文件缺失时初始化默认值，损坏/结构无效时内存降级（不写盘） | 低 |
| `plugins/relay.py` | 实时转发主逻辑：在线程池读取频道/群/筛选快照 → 去重 → 渲染 → 在线程池复查群状态 → 发送 → 记录 | 中 |
| `plugins/command.py` | QQ 私聊 relay 命令 + OpenID 自动发现 + 身份记录 | 中 |
| `plugins/registration.py` | QQ 注册指令（注册/注册群）+ 注册申请存储；异步处理器在线程池读取群注册白名单并取消安全地等待申请写入 | 中 |
| `plugins/manage.py` | QQ 私聊管理命令（在线程池读取授权白名单，通过本机管道调用桌面管理路由） | 中 |
| `plugins/fetch.py` | Discord 消息抓取 + 归一化 + 渲染为 QQ 消息段（唯一渲染逻辑）；消息抬头/时间/回复引用由上下文 helper 组装；正文提及/Markdown/Emoji、Embed 标题/链接/图片与附件分类分别由专责 helper 处理；图片/音频/视频附件共用下载、富媒体构造与链接回退流程；单条消息 200 响应 JSON 无效或根节点非对象时返回格式错误；频道元数据查询合并并限制 5 个并发，成功缓存最多 2048 项，失败（包括无效 JSON）缓存 30 秒，渲染回退为频道 ID；REST 消息作者结构无效时显示 unknown、username 类型无效时筛选值归一为空、正文类型无效时归一为空、Embed/附件集合无效时归一为空列表，媒体字段只保留字符串；内联及回退回复引用的作者结构无效时显示 unknown，昵称仅接受非空字符串，提及列表结构无效时忽略映射、正文类型无效时跳过引用行；关闭时回收元数据与其他 REST 请求后关闭 HTTP 客户端 | 中 |
| `plugins/forward_service.py` | Discord 实时转发、补发和重试共用的消息准备与翻译流程；频道筛选及翻译配置读取在线程池执行 | 中 |
| `plugins/sender.py` | QQ 群发送：在线程池读取目标群及 Bot 绑定路由；分片、图片媒体、重试、限流退避、链接降级 | 低 |
| `plugins/retry.py` | 发送失败欠账队列、持久化恢复与延迟重试；到期重试入口负责内容重建和发送，发送结果 helper 单独更新逐群欠账、重试次数及退避；`send_and_record` 可接收 RSS 幂等键，跨来源按群过滤已确认投递；关闭时回收频道 worker 并保留队列供下次启动恢复 | 中 |
| `plugins/delivery_dedup.py` | SQLite 持久记录 X/Bilibili 投稿 ID 与已成功接收的 QQ 群；保留 90 天并限制 100,000 条；写入异常时用有界进程内记录兜底，数据库读写在线程池执行 | 低 |
| `plugins/message_trace.py` | 投递结果追踪与持久化；异步写入在线程完成后再传播取消 | 低 |
| `plugins/failure_notification.py` | 管理员失败通知；在线程池读取开关/收件人快照，按收件人和失败项冷却去重并串行处理同 key 并发回调，最多保留 5000 条成功通知记录 | 低 |
| `plugins/x_rss.py` | X RSS 轮询、来源游标、持久化积压和投递；投递按推文 ID 与 QQ 群执行跨来源判重，独立后台任务按 `x_protection` 速率排空队列；应用启动时创建轮询及投递任务；来源、路由、筛选、翻译和保护配置读取在线程池执行；关闭时回收轮询、投递和请求任务后关闭客户端 | 中 |
| `plugins/x_rate_limit.py` | X 限速配置归一化、积压裁剪和成功投递时间窗计算；纯逻辑，不读写文件 | 低 |
| `plugins/bilibili.py` | Bilibili RSS 轮询、来源筛选、限速队列、游标和投递；投递按投稿 ID 与 QQ 群执行跨来源判重；应用启动时创建轮询任务并每 5 秒检查 QQ 连接；异步流程的间隔、状态/来源/路由快照、发送路由、内容筛选/翻译/保护配置及保护队列读取在线程池执行；保护队列保存独立快照并在取消时等待落盘后释放投递锁；关闭时回收轮询与单条手动抓取请求后关闭客户端 | 中 |
| `plugins/rss_polling.py` | X/Bilibili 共用的 RSS 轮询循环、每 5 秒连接状态检查和线程池间隔读取；各来源模块管理任务引用、启动与关闭状态 | 低 |
| `plugins/translation.py` | 统一翻译路由、后备服务和最近成功服务状态；同一消息与路由身份的并发请求合并为一个在途翻译 | 中 |
| `plugins/ai_client.py` | OpenAI 兼容 AI 请求、模型可用性检查和自动轮换；关闭时回收请求后关闭共享客户端 | 中 |
| `plugins/tencent_tmt.py` | 腾讯 TMT 请求适配和错误转换；关闭时回收请求后关闭共享客户端 | 低 |
| `plugins/scheduled_message.py` | 按显式目标群名单和日历计划进行 QQ 定时消息投递，支持正文与本地图片附件；计划及发送前启用群配置在线程池读取；关闭期间拒绝连接事件重启循环 | 中 |
| `plugins/scheduled_message_media.py` | 定时消息 PNG/JPEG/GIF/WebP data URL 与单图/总量大小校验 | 低 |
| `plugins/x_rss_parser.py` | RSS 条目解析；只接受 RSS 根节点，避免把格式正确的 HTML 错误页缓存为空 feed | 中 |
| `plugins/bilibili_parser.py` | Bilibili RSS 条目解析与稳定游标生成；拒绝非 RSS 文档根节点 | 中 |
| `plugins/bilibili_rate_limit.py` | Bilibili 限速配置归一化（非有限/溢出整数回退默认值）、积压裁剪和时间窗计算 | 低 |
| `plugins/media.py` | 文本美化（Markdown → 纯文本）+ Emoji 提取 + 并发受限、批次内按 URL 去重的媒体下载；关闭时回收在途下载后关闭共享 HTTP 客户端 | 低 |
| `plugins/dedup.py` | 去重纯函数：该不该发、发给谁、成功后怎么记 | 低 |
| `plugins/filter.py` | Discord/X 消息筛选与 Bilibili 关键词、投稿类型筛选纯函数 | 低 |
| `plugins/history.py` | discord_last.json 读写（带 mtime 缓存；异步读写在线程池执行并串行；读取或写入被取消时均等待已启动的磁盘操作结束后才释放游标锁） | 低 |
| `plugins/backfill.py` | 启动后自动补发离线缺失消息；每轮在工作线程读取开关、额度、频道和目标群配置快照 | 中 |
| `plugins/backfill_api.py` | 补发 HTTP 接口（面板调用）+ 频道权限审计；补发路由快照、频道名配置和审计 JSON 在线程池读取，刷新超时会终止并回收诊断子进程 | 中 |
| `plugins/identities.py` | OpenID 身份资料库（昵称/群名/备注） | 低 |
| `plugins/stats.py` | 转发统计；异步首次读取和计数落盘在线程池执行，取消时等待已启动的读写完成再释放计数锁，防止阻塞事件循环或新旧计数快照倒序落盘 | 低 |
| `plugins/json_io.py` | JSON 原子读写与跨进程锁；提供线程池 I/O 完成等待器，调用重复取消也会等底层操作完成后再传播取消，即使后台操作失败也保留调用方取消；读改写遇到已存在的损坏文件会中止，不以默认值覆盖 | 低 |
| `plugins/health_api.py` | Bot 健康状态接口（整体连接状态、逐 QQ Bot 连接列表、翻译运行状态）；认证令牌与消息追踪 JSON 在线程池读取 | 低 |
| `panel/管理面板.pyw` | 桌面进程内的 FastAPI 路由、配置读写、进程管理和看门狗；settings 完整保存事务在线程池执行，避免磁盘读写阻塞面板事件循环；不启动 HTTP 监听 | 高 |
| `panel/backup_restore.py` | 面板设置备份、归档校验、流式回退快照和多文件失败回滚；运行状态恢复要求机器人停止 | 中 |
| `panel/process_manager.py` | 机器人进程生命周期、原子 PID/创建时间记录、Windows 进程复用校验、权限未知状态收口及有界停止退出确认 | 中 |
| `panel/web/app.js` | 面板前端逻辑、QQ Bot 工作区切换与状态显示 | 高 |

---

## 10. 常见需求应该去哪里改

| 需求 | 去哪改 |
|------|--------|
| **修改 QQ 群接收权限** | `panel/管理面板.pyw`（API + 页面数据）+ `panel/web/app.js`（UI） |
| **修改 Discord 频道列表** | 同上；频道权限扫描逻辑在 `scripts/diagnose_channels.py` |
| **修改转发逻辑**（消息格式、渲染、过滤规则） | `plugins/relay.py`（实时）+ `plugins/fetch.py`（渲染）+ `plugins/sender.py`（发送） |
| **修改补发逻辑** | `plugins/backfill.py`（自动补发）+ `plugins/backfill_api.py`（手动触发接口） |
| **修改失败重试逻辑** | `plugins/retry.py`（队列/恢复/退避）+ `plugins/sender.py`（发送返回值） |
| **修改 X RSS 转发** | `plugins/x_rss.py`（轮询/队列/投递）+ `plugins/x_rss_parser.py`（解析）+ `plugins/x_rate_limit.py`（保护策略） |
| **修改 Bilibili RSS 转发** | `plugins/bilibili.py`（多投稿类型轮询/投递/限速队列）+ `plugins/bilibili_parser.py`（解析）+ `plugins/bilibili_rate_limit.py`（保护策略）+ `plugins/filter.py`（关键词/投稿类型筛选） |
| **修改自动翻译或 AI 模型轮换** | `plugins/translation.py`（服务路由）+ `plugins/ai_client.py`（AI 请求/轮换）+ `plugins/tencent_tmt.py`（TMT） |
| **修改 OpenID 自动发现** | `plugins/command.py`（`save_group_openid` / `save_user_openid`） |
| **修改注册/审核机制** | `plugins/registration.py`（指令 + 存储）+ `panel/管理面板.pyw`（`/api/registrations*`）+ `panel/web/app.js`（审核弹窗） |
| **修改身份资料库** | `plugins/identities.py` |
| **修改面板 UI** | `panel/web/index.html` + `panel/web/app.js` + `panel/web/style.css` |
| **修改面板 API** | `panel/管理面板.pyw`（FastAPI 路由） |
| **修改 QQ 私聊命令** | `plugins/manage.py`（管理命令）+ `plugins/command.py`（relay 命令） |
| **修改消息文本格式**（Markdown 清理、Emoji 处理） | `plugins/media.py`（`cleanup_markdown` / `parse_discord_emoji` 等） |
| **修改去重规则** | `plugins/dedup.py`（纯函数） |
| **修改频道筛选规则**（按用户名/关键词过滤） | `plugins/filter.py`（纯函数）+ `plugins/config.py`（`get_channel_filter`） |
| **修改发送重试/限流策略** | `plugins/sender.py`（`RETRY_DELAY` / `MAX_SEND_ATTEMPTS` / `RATE_LIMIT_BACKOFFS`） |
| **修改日志级别/轮转** | `bot.py`（`_setup_log_sink`）；运行时级别由面板写入 `data/runtime_log_level.json` |
| **添加新的 settings.json 字段** | `plugins/config.py`（`_default_settings` + getter 函数）+ `panel/管理面板.pyw`（`get_default_settings` + 读取/保存逻辑） |

---

## 11. 高风险区域（不要随便改）

### 11.1 `plugins/dedup.py` — 去重核心

**为什么危险：** 纯函数逻辑，但直接决定"消息会不会重复发送"。改错任何一个比较条件（`int(message_id) > int(last_id)`），会导致消息重复或丢失。`relay.py` 和 `backfill.py` 都依赖它，改动影响面覆盖全部转发路径。

### 11.2 `plugins/config.py` — 配置缓存

**为什么危险：** 带 mtime/size 的内存缓存是 bot 进程的性能关键路径（每条消息都调）。改缓存逻辑可能导致：读到旧配置（用户改了面板但不生效）、频繁读盘（性能下降）、或读到损坏数据（转发异常）。当前 `_ensure_settings_file` 仅在文件完全不存在时初始化默认值；损坏/结构无效时内存降级、不覆盖原文件——若改回「无效即用默认值覆盖写盘」则会清空用户配置。

### 11.3 `plugins/sender.py` — 发送 + 重试

**为什么危险：** 429 限流退避、分片发送、媒体降级三条路径交织。改错重试逻辑可能导致：限流时雪崩式重试（被封号）、长消息截断丢失内容、或媒体全部降级为链接（体验变差）。返回的 `ok_map` 直接影响去重记录，改错会导致重复发送。

### 11.4 `panel/管理面板.pyw` — settings.json 写入

**为什么危险：** 面板是 settings.json 的唯一正常写入方。`save_settings` 的原子替换（唯一 tmp + `os.replace`，见 `plugins/json_io.py`）保证配置不会写坏，保存前还会把上一版轮转备份为 `settings.json.bak`，误写/损坏可从 `.bak` 恢复。如果改成非原子写入，面板崩溃时可能写坏配置文件，导致 bot 启动后读不到配置。`migrate_forwarding_groups`（main() 启动时执行）里的迁移逻辑（旧 `is_test` / `test_group_openid` → 「测试组」成员）是幂等的，重复执行无害但不能删（否则旧配置用户升级后会丢失测试隔离配置）。
原子写入即使在 JSON 序列化或文件替换失败时，也会尝试清理唯一临时文件；失败时目标文件仍保留原内容。
`file_revision()` 只把文件确实不存在（包括检查期间被删除）记为 `missing`；权限或其他读取错误会继续抛出，避免乐观版本检查把不可读的现存文件当作首次创建并替换。
`/api/settings/save` 异步路由在工作线程中完成版本读取、结构校验、备份与原子写入；保留版本冲突检查，避免慢磁盘阻塞面板事件循环。
`/api/settings/scheduled-message` 也在线程池完成字段合并、旧版 timer 字段同步、校验及 revision 保护保存；只提交的定时消息字段与其他 settings 字段保持隔离。
`/api/settings/sync` 在线程池读取当前配置与自动发现数据、只合并请求选择且仍待授权的群/用户，并完成 revision 保护保存和响应快照读取；同步发现对象不会自动启用或授权。
该接口零字节空请求仍按默认同步全部发现对象；空白文本、非对象/无效 JSON、未知 kind 或非字符串列表 openids 均返回 400，且在读取配置前拒绝。
`/api/registrations/review` 的审核文件读写、自动发现补录及 settings 更新也整体在线程池执行；通过后仍按默认禁用条目进入现有同步名单，settings 与注册文件各自的 revision 冲突检查保持有效。

自动发现数据缺失时按空记录处理；已有发现文件若读取失败、JSON/列表结构无效，或群 `group_bot_ids` 归属映射损坏，则同步预览、同步或相关注册审核返回明确错误，不会把它伪装成空名单，也不会覆盖损坏文件。旧版群文件缺少 `group_bot_ids` 字段时仍按空映射兼容。
`/api/translation/ai-test-record` 仅持久化经过字段白名单清理的检测结果，settings 读取、配置校验与 revision 保护保存在线程池执行；检测记录不保存密钥或编辑中的配置内容。
`/api/translation/env` 在工作线程写入腾讯 TMT 密钥并读取脱敏配置状态；接口只返回是否已配置，不回传 SecretId/SecretKey。
`/api/translation/ai-env` 同样在线程池更新 `.env.prod` 并读取变量状态；环境变量名经白名单格式校验，API 密钥不进入响应。
`/api/bot/qq-config` 在线程池读取既有 Bot 配置与群绑定并原子更新 `.env.prod`；修改列表时保留已有空白 Secret 与 sandbox 配置，仍绑定群的 Bot 不可移除，AppSecret 不回传。
`/api/bot/discord-config` 在线程池读取与原子更新 Discord Token；只改首个 Bot 的 token 字段，保留 intents 等字段，空 Token 保持原值，响应不含 Token。
`/api/backup/restore` 异步接收并限制上传至 5 MiB，归档校验、恢复前快照、多文件替换及失败回滚在线程池执行；恢复运行状态时仍持有机器人控制锁并要求机器人停止。
备份清单严格要求 `version` 为整数 1（布尔值不算整数版本）且 `include_runtime_state` 为布尔值；归档只接受固定文件白名单，JSON 状态按 JSON 校验，RSS 跨来源去重数据库按 SQLite 完整性校验。包含运行状态时通过 SQLite 在线备份接口生成一致快照；恢复时原子替换数据库文件。
`/api/autostart/set` 要求 `enabled` 为 JSON 布尔值，在线程池创建/删除当前用户 Run 注册项并回读状态；正式版值名为 `WRelay`，命令指向带应用图标的 `WRelay.exe --autostart`，因此任务管理器显示应用名称与图标。源码版仍通过 `pythonw.exe` 调用 `panel/autostart.pyw`。升级时识别并迁移旧版 VBS 或快捷方式启动项，写入注册项后清理旧文件；卸载时删除对应 Run 值及旧启动文件。设置页开关与机器人启动流程保持不变。
注册审核、开机自启及日志级别写接口要求对象 JSON；数组或 `null` 在任何审核、文件更新副作用前返回 400。
注册申请 JSON 缺失时视为空列表；若文件存在但无法读取或其顶层/用户/群结构无效，列表与审核接口返回错误，不伪装为空申请或“不存在”，原文件保留供修复。
注册审核列表在单次读取的文件快照上同时生成用户和群申请，避免一次响应混用两个写入时点。

### 11.5 `plugins/backfill.py` — 补发与去重的交叉

**为什么危险：** 补发和实时转发共用 `discord_last.json`，且补发是异步后台任务。如果改补发的去重逻辑（比如不逐条重读记录），可能与实时转发竞争，导致同一条消息被发两次。`_backfill_running` 互斥锁保证不会并发补发，去掉会导致刷屏。

---

## 12. 已完成的模块化

### `plugins/dedup.py` — 去重逻辑抽离

- 原本去重判断散落在 `relay.py` 和 `backfill.py` 中，现已抽为纯函数模块。
- 不碰 I/O、不碰 fetch、不碰 send，只回答"该不该发 / 发给谁 / 成功后怎么记"。
- 所有持久化由调用方通过 `history.py` 完成。
- 好处：可以独立测试去重逻辑，新增转发路径时复用同一套去重。

### `panel/web/` — 面板前端独立

- 面板前端从 `管理面板.pyw` 中分离为独立的 `index.html` + `app.js` + `style.css` + `desktop.js`。
- `ui-components.js` / `ui-components.css` 是公共展示层：复用既有节点、ID 和事件，统一外链按钮与网页 Modal 的 Header / Body / Footer；导航、表单、状态行和提示样式共用当前主题变量。小/中/大窗口宽度为 520/640/880px，Bilibili 来源注册窗口宽 560px；来源筛选复用 X 的“消息筛选”窗口。窗口最高 85vh，仅 Body 滚动。普通配置支持遮罩关闭，确认与恢复窗口保留明确关闭操作；不调用 API 或改变配置结构。
- `desktop_app.pyw` 在主线程创建 WebView2 窗口并加载 `panel/web/index.html`，不向 `file://` 地址追加查询参数，以免 WebView2 将参数误当成本地文件名。`local-api.js` 通过 PyWebView 桥将管理请求交给桌面进程内的 FastAPI 路由执行；桥接 8 秒内未就绪时显示失败，不启动 Uvicorn 或监听 `18090`。机器人自身的 `18082` 接口保持不变。
- `panel/local_bridge.py` 提供进程内路由调用和带令牌验证的 Windows 本机管道；QQ 私聊管理命令通过该管道请求桌面进程，普通操作等待响应最多 12 秒，补发操作最多 35 秒。桌面入口通过原生桥向本地页面提供该窗口启动时生成的随机凭证，不放入文件 URL；`file://` 协议用于识别桌面环境，首次进入运行总览。
- `/api/app/info` 从 `pyproject.toml` 读取当前应用版本；`/api/app/update-check` 只读查询 GitHub 最新稳定发行版并返回发布页，不下载或安装更新。
- 第二次启动桌面入口会唤回已有窗口；若单实例锁仍被持有但窗口已不存在，会等待窗口初始化后通过本机管理管道请求桌面进程重启，不停止机器人。关闭行为在设置中选择托盘/退出/每次询问，默认托盘。偏好单独保存在 `data/desktop_preferences.json` 并通过桌面 JS 桥立即生效；普通退出保留机器人，确认后的停止并退出复用既有停止 API，停止失败则不退出。
- 桌面窗口保留 PyWebView 可缩放窗口样式和 960×640 最小尺寸；`windows_titlebar.py` 使用现有 .NET 运行时在内存中编译 `windows_titlebar.cs`，绘制 38px（按 DPI 缩放）的 Windows 标题栏。原生命中测试将拖动、边缘缩放和系统菜单交给 Windows，最大化区域返回 HTMAXBUTTON 供系统 Snap 使用；按钮通过 WM_SYSCOMMAND 操作，关闭统一进入桌面关闭偏好处理。最大化图标直接读取 IsZoomed。JS 桥负责主题同步及关闭偏好读写，不承接拖动/resize。模块加载失败时记录桌面日志并保留系统标题栏。
- 窗口位置、尺寸与最大化状态保存到 `data/desktop_window.json`。启动时以 PyWebView 的逻辑像素坐标核对可用显示器，旧位置不可见时将窗口放到当前可用屏幕。
- 原生窗口从最大化或最小化还原时，标题栏模块在同步窗口位置消息期间保留 Windows 已选定的外部尺寸，阻止 WinForms 将已移除的标准边框高度再次叠加；不覆盖位置，也不约束后续普通缩放。相关回归检查位于 `scripts/test_desktop_titlebar.py`。
- `panel/web/discord-page.js` 为四个转发功能页共用的展示适配层（沿用 Discord 模板文件名），通过页面配置管理共用控件与卡片的位置还原、离线补发参数/操作分组、状态栏连接/统计分组、QQ 定时消息卡的下方布局及开关可访问状态；不调用 API 或修改 settings。桌面功能页顺序为状态卡、转发组、统一尺寸的下方卡片区；四页卡片采用统一间距，CSS 在 `desktop-host` 下复用同一套功能页规则，总览及设置不采用该布局。表格渲染、复制、排序及保存仍由原 `app.js` 处理。机器人进程控制主入口在总览，Windows 原生托盘提供快捷操作；全局配置与维护按钮直接归属设置页，使用原 ID 与事件处理，不再由功能页的运行工具容器搬运。Discord/QQ/翻译配置入口均唯一，设置页提供失败通知开关、重启管理程序和关于作者行。界面动画偏好通过 `localStorage` 的 `wrelay_status_sheen` 保存，由前端状态渲染和 CSS 控制状态卡渐变扫光，不调用后端 API。全局运行日志从设置的“启动与维护”打开 `runtimeLogModal`，保留原日志节点 ID 与请求处理，消息追踪/清空确认位于日志窗口上层；功能页不再创建日志卡片。根滚动容器为 HTML，使用 scrollbar-gutter: stable，不支持时回退 overflow-y: scroll。
- 桌面自定义标题栏与任务栏/托盘使用 `panel/wrelay_panel.ico`；应用内品牌标识使用 `wrelay-logo.svg`。
- 转发组、来源、接收群和白名单表格共用列宽、行高、状态复选框与操作按钮规则；表格按卡片宽度布局，不设置强制最小宽度。长 ID 显示省略号、悬停查看完整值，并提供复制按钮。
- 桌面窗口在窄宽度下将侧栏收起为图标导航、转发列表切换单栏；更窄时隐藏次要的 QQ 账号列，保持主要 ID 和操作可见。
- 桌面页面共用颜色、间距、圆角和控件尺寸 tokens；机器人统计与控制区分行显示，转发组操作集中在标签右侧。
- 夜间标题使用 `#818DF9`；日间大标题为 `#3C478A`、卡片标题为 `#5E6CD8`。暂停组未选中时使用淡橙色，选中时保留警示橙色，暂停后的播放按钮保持灰色。
- 桌面端窗口以 `docs/DESIGN.md` 的概念图为视觉参考，原有配置字段和消息链路保持独立。
- 好处：改 UI 不需要改 Python 代码，也不需要重启面板进程。

---

## 13. 明确记录

| 条目 | 说明 |
|------|------|
| `qq_user_openids`（settings.json） | **私聊权限的唯一来源**。`enabled: true` 的用户才能使用 relay 和管理命令 |
| `qq_group_openids`（settings.json） | **转发目标群的唯一来源**。`enabled: true` 的群才会收到转发消息；新发现/审核通过的群自动记录创建该 `group_openid` 的 QQ Bot，面板按 Bot 工作区管理；空 `bot_id` 仅兼容旧配置并使用第一个已连接 Bot |
| 自动发现不自动授权 | bot 发现新群/新用户后写入 `data/qq_group_openids.json` / `data/qq_user_openids.json`，但只记录不进同步名单，也不加入 settings |
| 注册审核 | 主动注册（`register` / `register-group` 指令）后才出现在面板"注册审核"弹窗；通过 = 按现有同步流程加入 settings（默认 `enabled: false`）并把昵称/群名写入条目 `name`、QQ号/群号写入 `qq_id`（可选字段，旧条目/手动添加项没有时显示为空；`remark` 原样保留），拒绝 = 申请消失；同一 openid 同时只有一条注册记录（重新注册覆盖） |
| `qq_registrations.json` | 注册申请存储，与白名单数据独立；**不参与任何权限判断**，审核通过 ≠ 自动启用 |
| `qq_identities.json` | 纯身份资料库（昵称、群名、管理员备注），只用于面板展示，**绝不参与任何权限判断** |
| 不存在主动反查群名 | QQ 官方 API 不提供群名/昵称查询接口。群名只在机器人收到群消息时从事件中提取并记录，无法通过 openid 主动查询 |
| 自动转发方向 | 自动链路为 Discord → QQ、X RSS → QQ、Bilibili RSS → QQ；项目不做 QQ → Discord 或双向同步；QQ → QQ 仅限授权用户手动 relay 命令 |


桌面偏好与托盘补充：`panel/desktop_controls.py` 封装原子偏好写入、关闭选择、动态菜单和操作互斥；`desktop_app.pyw` 负责 PyStray 原生 Windows 菜单、WinForms 确认框、既有窗口恢复与 JS 页面切换。托盘每 3 秒读取现有进程状态与健康缓存，更新状态项和 Tooltip；进程操作调用既有认证本地 API，不新增转发 API。菜单回调在后台线程执行，原生确认在窗口 UI 线程执行，关闭事件先取消默认关闭再调度选择，避免事件/UI 线程互相等待。退出会停止托盘轮询并销毁窗口，不主动终止机器人；停止并退出必须确认且验证机器人确已停止。管理程序 `--autostart --restart` 分支不再拉起已停止机器人，保留仍运行机器人的看护意图；自动拉起间隔、Discord 不可用宽限期和重启冷却均用单调时钟计算。设置中的失败通知依旧是授权 QQ 私聊提醒，并非系统 Toast。

## 14. 首次配置向导

- `panel/web/onboarding.js` 独立管理七步流程：用途、账号、授权用户、接收群、内容来源、确认启用、验证结果；复用 `app.js` 的认证请求、确认窗口和主页面刷新入口。
- `panel/onboarding_api.py` 在静态资源挂载前注册 `/api/onboarding` 及 progress、credentials、authorize、probe、apply、receipt 子接口，全部沿用面板 Origin/API 令牌校验。进度、收件确认、用户授权、配置应用和账号凭据事务在线程池执行文件操作；来源检测也在线程池读取 settings 并验证有大小上限的 RSS XML，不阻塞面板事件循环。只有显式保存账号才写凭据文件，先校验两个账号输入，再合并为一次原子替换；不逐步自动重启。RSS 检测对 Bilibili HTTP 503 按 UP 主与标签／关键词来源给出针对性排查提示。
- 向导的六个写/检测接口要求对象 JSON；无效 JSON 或数组、`null` 等非对象请求统一返回 400，且不会触发进度、凭据、授权、来源检测、应用或收件确认事务。
- `panel/onboarding.py` 负责白名单字段、RSS URL、来源复用、群归属和路由合并校验。Bilibili 来源在向导中直接用 UID、标签名或关键词作为名称，不提供自定义备注。apply 基于最新 settings 快照和 revision 保存；不从自动发现记录创建权限。已有来源保留筛选项，相同来源与目标集合复用现有路由，重复应用同一 flow_id 幂等。
- `data/onboarding.json` 只保存非密钥草稿、flow_id、应用时间、旧投递记录标识和用户收件确认；不是运行时权限来源，也不纳入配置备份。步骤切换和“稍后配置”时保存，强制退出可能丢失当前未保存输入。存在真实账号或登记实体时不自动弹出欢迎页；从设置始终可重新进入。
- 向导状态文件缺失时按首次空状态处理；现存文件读取失败或顶层、状态字段类型无效时，读取及依赖状态的写接口返回明确错误并保留原文件。凭据保存会先校验该状态，避免状态损坏时只写入账号文件却返回失败。
- QQ 向导使用已有授权私聊流程，启用用户意味着授予现有白名单管理命令权限。群聊互转实现不在本次接入范围；不写 qq_forwarding_groups，不新增自动 QQ 同步。QQ 私聊仍可选择全局已启用群，向导选择不构成单用户权限隔离。
- `plugins/startup_config.py` 提供凭据就绪与 Discord 用途判断。bot 首次检查要求 QQ 凭据；存在已启用 Discord 频道才强制要求 Discord Token。无有效 Discord Token 且无启用频道时，仅在进程环境中以空 DISCORD_BOTS 屏蔽模板占位。已配置的真实 Discord 账号仍沿用原连接机制；面板看护在未配置 Discord 时跳过其断线重启判断。
- 结果页按来源、保存时间、旧记录排除和所选全部目标群匹配成功投递；配置保存、服务连接和用户手动收件确认分开表达。没有新增自动发送测试消息的接口。

相关验证：`.venv/Scripts/python.exe -X utf8 scripts/test_onboarding.py`（已加入统一回归入口）。测试使用临时目录和模拟服务，覆盖用途分支、启动必填项、授权边界、凭据原子写入、配置冲突、来源检测和重复保存。

界面隔离预览：`.venv/Scripts/python.exe -X utf8 scripts/preview_onboarding.py`，访问 `http://127.0.0.1:18109/?desktop=1`；示例 AppID 为 1001，密钥可填任意示例文字。机器人与 RSS 请求均由模拟响应提供，注册申请在模拟启动后出现，数据仅写临时目录，Ctrl+C 退出。

账号修改后向导保存 `restart_needed` 标记；通过 `/api/onboarding/start` 主动启动／重启成功后才清除，并清空旧健康缓存。重新进入账号步骤仍会要求使新凭据生效，避免把旧进程连接当作新配置的检测结果。

## 15. Windows 编译安装包

`scripts/build_compiled.py` 暂存自有 Python 模块后使用 Nuitka 编译，正式版 `WRelay.iss` 与测试版 `WRelay-Test.iss` 都只收录审计后的产物。单一入口 `scripts/compiled_entry.py` 按文件名分派桌面、机器人和重启程序；频道扫描也经编译入口执行。Nuitka 显式包含 WebSockets asyncio 子包，避免 NoneBot 的动态导入缺少 WebSocket 驱动。`plugins/paths.py` 在编译模式按可执行文件定位配置和资源，并从 build-info.json 区分正式版与测试版；进程管理直接启动 WRelay.Bot.exe。

`plugins/compiled_plugins.py` 仅在编译模式桥接 NoneBot 的源文件插件加载器，保留插件上下文与加载失败回滚；业务插件清单不变，不启用自动历史补发。标题栏从预编译 DLL 加载，源码模式保留原有 C# 编译方式。编译版按安装目录生成独立的桌面管理管道；正式版使用 WRelay 窗口标题、自启项和安装标识，测试版使用独立名称。正式版安装时将已有源码版自启入口指向编译程序。Bot 在日志系统初始化前的错误写入 `data/logs/bot-startup.log`。服务端口仍与源码版相同，不能同时运行。

开发环境、回归检查与 Windows 安装包构建步骤见 [BUILDING.md](docs/BUILDING.md)。HTML/CSS/JavaScript 仍随包分发；编译不能保证无法逆向。

## 自动维护（0.1.1）

- `panel/maintenance.py` 在线程中每 30 秒检查状态；每小时通过已有 `BackupRestoreService` 创建完整白名单快照，保留最近 30 份。重新启动沿用最近有效快照时间。JSON 文件按跨进程锁读取，数据库以 SQLite 在线备份产生一致快照；多文件快照不是一次全链路投递事务，恢复旧状态仍有重复或漏发风险。
- 备份格式 v2 在清单记录每个数据文件的 SHA-256；导出前校验，写入自动备份后重新读取校验。导入兼容 v1 的结构校验，v1 不提供文件哈希。`plugins/state_validation.py` 严格检查游标、重试条目、队列版本/条目/时间戳及来源状态；SQLite 使用 integrity_check 和 delivered_items 列检查。Bilibili 队列及来源基线与 X 来源缓存进入运行状态白名单。
- `/api/maintenance/status`、`backups`、`settings`、`backup`、`restore` 沿用本地令牌/Origin 检查。恢复只接收自动备份列表中的文件名，先校验再停止机器人；与自动备份共享维护锁，与进程控制共享机器人锁。恢复失败保留现有回滚保障；回滚不完整时保持机器人停止。
- X/Bilibili 的 `source_health` 在每轮完成时按来源更新连续失败计数，成功归零；维护刷新不增加计数。三次失败、保护错误、磁盘文件校验失败及自动备份失败形成维护告警，成功后清除。`/api/maintenance/alert` 受 Bot 本地令牌保护，复用现有 QQ 告警授权与冷却，不发送原始 RSS URL、账号凭据或完整异常堆栈。
- `panel/desktop_guardian.py` 在独立无控制台进程中持有原桌面的 Windows 进程句柄及随机退出事件，不会因 PID 重用结束其他进程。认证管道 ping 连续 120 秒失败或桌面异常退出触发恢复，30 秒冷却、600 秒内最多 5 次。正常退出先设置事件；恢复使用 `--autostart --restart --recover-desktop`，结合 `data/desktop_recovery.json` 的运行意图，不主动拉起被用户停止的机器人。看护日志有 1 MiB 轮转，且源码/正式/测试目录分别隔离。
- 编译入口增加 Guardian 角色，安装包增加 WRelay.Guardian.exe。签名构建仍是默认流程；只有显式 `--unsigned` 才跳过正式签名，构建信息如实记录未签名。
