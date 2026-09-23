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
│   ├── bilibili.py           Bilibili RSS → QQ 轮询与投递
│   ├── scheduled_message.py  QQ 定时消息投递
│   ├── x_rss_parser.py       RSS 内容解析
│   ├── bilibili_parser.py    Bilibili RSS 内容解析
│   ├── command.py            QQ 私聊 relay 命令（手动转发）
│   ├── manage.py             QQ 私聊管理命令（status/restart/mode 等）
│   ├── registration.py       QQ 注册指令（register / register-group）+ 申请存储
│   ├── fetch.py              Discord 消息抓取 + 渲染（统一解析层）
│   ├── sender.py             QQ 群发送（重试、分片、降级）
│   ├── retry.py              失败消息延迟重试与持久化队列
│   ├── message_trace.py      投递结果追踪与持久化
│   ├── failure_notification.py 管理员失败通知与冷却
│   ├── media.py              文本美化 + Emoji 解析 + 媒体下载
│   ├── history.py            去重记录读写（discord_last.json）
│   ├── dedup.py              去重判断逻辑（纯函数，不碰 I/O）
│   ├── filter.py             频道消息筛选（纯函数，relay/backfill 共用）
│   ├── backfill.py           启动后自动补发离线期间缺失的消息
│   ├── backfill_api.py       补发相关的 HTTP 接口（供面板调用）
│   ├── health_api.py         Bot 健康状态接口（供面板显示连接状态）
│   ├── identities.py         OpenID 身份资料库（昵称/群名/备注）
│   ├── stats.py              转发统计（成功/失败/当日条数）
│   └── json_io.py            JSON 原子读写工具
├── panel/
│   ├── 管理面板.pyw           管理面板主程序（FastAPI + 进程管理）
│   ├── autostart.pyw         开机自启入口
│   ├── restart_worker.pyw    面板重启 worker
│   └── web/                  面板前端（纯静态 HTML/JS/CSS）
│       ├── index.html
│       ├── app.js
│       └── style.css
├── scripts/                  独立诊断脚本（如 diagnose_channels.py）
├── data/                     运行时数据（settings、去重记录、统计等）
├── docs/                     文档（SETUP.md、CHANNELS.md）
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
| **管理面板** | 本地 Web 界面，管理群/频道/权限的启用状态、启停机器人、查看日志 |
| **数据文件** | JSON 格式的配置、去重记录、统计、身份信息，是 bot 与面板之间的 IPC 通道 |

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

---

## 4. Discord → QQ 启动补发流程

```
Discord Bot 连接成功（on_bot_connect 事件）
    │
    ▼
plugins/backfill.py → _backfill_missed()
    │ ① 检查补发总开关（config.get_backfill_enabled）
    │ ② 遍历每个启用频道
    │    └── 读取去重记录，算各群最落后的 last_id（dedup.compute_base_id）
    │    └── 无记录（首次启用）→ 只建基线，不补发，防刷屏
    │    └── 有记录 → REST 抓取 base_id 之后的消息（fetch.fetch_channel_messages_after）
    │    └── 按时间正序逐条转发，走 build_parts → send_relay_message
    │    └── 每条最多补发 get_backfill_limit() 条（默认 10）
    │    └── 逐条重读去重记录，实时转发已处理的群自动跳过
    └── 与实时转发共用去重记录，正常情况下不会重复发送
```

> 启动并发说明：on_bot_connect 会同时放行实时接收与补发任务。实时路径在
> 事件入队时登记共享的在途标记，直到消息处理完成才释放；补发逐条处理时
> 发现该标记会跳过该消息，避免媒体下载与游标落盘之间的重复投递。实时
> 路径失败仍按原有延迟重试与后续补发策略处理，不会因跳过而丢失消息。
>
> 抓取失败语义：分页抓取任一页失败（请求异常/超时/非 200/429 重试耗尽）
> 时整轮放弃并返回空，绝不返回不完整列表推进去重游标，宁可下一轮重新
> 补发；单次收集超过上限（1000 条）且未确认翻到缺口底部时同样整轮放弃，
> 极大缺口需在面板「清除待补发」处置。补发翻页请求间隔 0.5s，429 按
> Retry-After 退避重试。

实时发送失败时，`plugins/retry.py` 会把规范化消息、失败群、重试次数和下次
重试时间写入 `data/retry_pending.json`。重试队列与成功游标分离：某个群成功后
立即清除该群欠账，其余群继续重试；进程重启后恢复队列。队列只保存可重建消息，
不保存媒体字节，内容重建失败或达到 3 次上限后移除，最终由低游标补发兜底。

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

X RSS 与 Discord 使用独立的转发组配置。RSSHub 不可用、解析失败或发送失败
只影响对应来源，不会阻断 Discord → QQ 链路；RSSHub 临时错误会在同一轮最多尝试 3 次，
最终失败仍不推进游标；RSSHub 不可用时会回退到 X 公开时间线。

---

## 4.2 Bilibili RSS → QQ 轮询流程

```
定时器（最短 30 秒）
    │
    ▼
plugins/bilibili.py
    │ ① 读取启用的 Bilibili 来源与 Bilibili 转发组
    │ ② 按来源类型生成 RSSHub 路由：UP 主投稿 / 标签搜索 / 关键词搜索
    │    RSS 请求按来源串行并留出间隔；429/5xx/连接异常最多退避重试 4 次
    │ ③ 请求 RSS，复用 ETag / 304 缓存并对临时错误退避重试
    │ ④ plugins/bilibili_parser.py 解析标题、作者、标签、视频链接和封面
    │ ⑤ 以 bilibili:{kind}:{value} 作为独立来源游标去重
    │ ⑥ 按来源标签/关键词/标题/作者筛选
    │ ⑦ 进入有限积压队列，按最短间隔和每小时上限复用 translation/sender 投递
    └── 首次启用来源或群只建基线；发送失败、QQ 未连接时不推进游标
```

Bilibili 与 Discord、X 使用独立的转发组和来源游标；全局来源、QQ 群启用状态以及当前
转发组成员关系必须同时满足才会投递。消息文本使用“自动转发来自 Bilibili 中[UP 主名称]的投稿”
来源头，优先使用 RSS 投稿作者，缺少作者时回退到注册来源；只渲染标题和
原始投稿地址（纯文本链接），并发送一张 RSS 提供的封面图片；不发送作者行、标签、其他分类、简介及播放统计，也不下载视频本体，避免大文件和临时 CDN 地址影响
轮询稳定性。
RSSHub 请求失败、解析失败或发送失败只影响对应 Bilibili 来源，不会阻断其他自动链路。
若 RSSHub 日志显示 Bilibili 上游 `412 Precondition Failed`，RSSHub 通常会对外返回 503；此时
需要使用带 Chromium 的 RSSHub 镜像，或为 RSSHub 配置有效的 `BILIBILI_COOKIE_1`。

Bilibili 另有一层独立的投递保护：`settings.json` 中的 `bilibili_protection` 控制全链路
最短发送间隔、每小时成功投稿上限、每个来源的积压上限和超限策略。积压队列保存在
`data/bilibili_protection.json`。选择“保留最新、跳过旧投稿”时，队列超限会优先丢弃旧内容；
选择“保留最旧、跳过新投稿”时，新内容先记录为延后跳过，只有保留的旧内容处理完后才推进游标，
避免游标提前越过仍需发送的投稿。来源条目还支持标题必须包含、标题排除关键词和作者白名单，
作为标签/关键词筛选之后的第二层内容筛选。

## 4.3 统一翻译路由与 AI 模型轮换

Discord、X RSS、Bilibili RSS 和 QQ 手动转发在完成消息渲染后，统一通过 `plugins/translation.py`
按当前链路配置尝试首选服务和后备服务：

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
                                           │ ④ 通过 HTTP 调用面板 18090 API
                                           └── ⑤ 回复执行结果
```

`relay list` 查看启用群，`relay <序号1> <序号2>` 或 `relay all` 选择目标，下一条消息
转发到 QQ 群。若下一条是 Discord 消息链接，会从 Discord API 抓取并复用正常
渲染流程；这是授权用户触发的 QQ → QQ 手动操作，不是 QQ → Discord 自动同步。

QQ 群消息中的 `register-group <群号> <群名称>`（plugins/registration.py）：
发送者 `member_openid` 必须在 `qq_user_openids` 白名单内，申请绑定当前
Group OpenID 写入 `data/qq_registrations.json`，等待面板审核。
中文"注册"指令已删除，中文消息按未知命令正常回复，不触发注册。

QQ 私聊的 `backfill on/off` 走字段级接口 `POST /api/settings/backfill-toggle`：
只提交 `backfill_enabled` 一个字段，由面板读取当前配置后仅修改该字段再
走统一保存流程（结构校验 + `.bak` + 原子替换；值未变化时不写盘），
避免回传整份配置覆盖面板刚保存的其他字段。

---

## 6. 权限 / 自动发现 / 身份库的关系

```
机器人收到 QQ 群消息 / 被拉进群 / 收到私聊
    │
    ├──► 自动发现（plugins/command.py）
    │      把 group_openid 写入 data/qq_group_openids.json
    │      把 user_openid  写入 data/qq_user_openids.json
    │      ⚠️ 只记录，不授权，也不进同步名单。需先注册再经面板审核
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
    │      把昵称/群名、最后活动时间写入 data/qq_identities.json
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
| 同步按钮 | "刷新频道记录"→ 执行 `scripts/diagnose_channels.py`，更新 `data/channels_audit.json`，展示哪些频道 bot 有读权限 |
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

运行日志卡片内的「消息追踪」按钮打开二级窗口，读取 `message_trace.py` 保存的最近
50 条投递记录，展示来源、消息摘要、目标群、最终结果与重试次数。机器人状态卡片的
「连接诊断」按钮只读显示进程、Discord、QQ 连接和待重试投递数量，不会发送测试消息。
「失败通知」开关控制失败提醒；仅白名单中已启用且勾选「告警」的用户会收到通知，通知按冷却时间合并重复失败。

---

## 8. 重要数据文件

| 文件 | 写入方 | 读取方 | 内容 |
|------|--------|--------|------|
| `data/settings.json` | 面板（主要；保存时先轮转 `settings.json.bak` 备份）；bot（仅文件完全不存在时首次默认初始化） | bot（高频，带缓存；损坏/结构无效时内存降级，不覆盖原文件）、面板 | 启用的群/频道/用户列表、补发开关、模式预设 |
| `data/settings.json.bak` | 面板（每次保存前轮转写入） | 面板 / 人工（配置损坏或误写时恢复） | 上一版 settings.json 的完整备份（仅保留最近一代） |
| `data/discord_last.json` | bot | bot | Discord/X/Bilibili 每个来源或频道每个群最后成功转发的消息 ID（去重用） |
| `data/qq_group_openids.json` | bot（自动发现）；面板（审核通过时补回缺失 openid） | 面板（同步预览、审核补回校验） | 机器人发现的所有群 openid 及 `group_bot_ids` 归属映射（原始记录，非权限，不进同步名单） |
| `data/qq_user_openids.json` | bot（自动发现）；面板（审核通过时补回缺失 openid） | 面板（同步预览、审核补回校验） | 机器人发现的所有用户 openid（原始记录，非权限，不进同步名单） |
| `data/qq_registrations.json` | bot（注册指令）；面板（审核通过/拒绝时移除申请并写回） | 面板（注册审核列表） | 待审核的注册申请：用户（QQ号/昵称）、群（群号/群名/操作人），非权限 |
| `data/qq_identities.json` | bot（自动记录） | 面板（展示） | 用户昵称 / 群名 / 最后活动时间 / 管理员备注 |
| `data/stats.json` | bot | 面板 | 转发成功/失败累计 + 当日条数 |
| `data/channels_audit.json` | bot（诊断脚本） | 面板 | 频道权限扫描快照（可读/可见频道列表） |
| `data/runtime_log_level.json` | 面板 | bot | 运行时日志级别（面板写入后 bot 3 秒内自动生效） |
| `data/bot.pid` | 面板 | 面板 | 机器人进程 PID（进程管理用） |
| `data/restart_pending.json` | bot（manage.py） | bot（manage.py） | 跨进程重启确认标记 |
| `data/retry_pending.json` | bot（retry.py） | bot（retry.py）、面板 | 失败消息摘要、失败群、重试次数和下次重试时间；不保存媒体字节 |
| `data/message_trace.json` | bot（message_trace.py） | bot（health_api.py）、面板 | 最近投递结果、目标群、失败原因和重试次数；自动限制记录数量 |
| `data/panel_auth.json` | 面板 | 面板、bot（manage.py） | 本机面板 API 认证令牌，不应外传 |
| `data/x_rss.json` | bot（x_rss.py） | bot（x_rss.py） | RSS ETag/Last-Modified 等轮询缓存 |
| `data/bilibili.json` | bot（bilibili.py） | bot（bilibili.py） | Bilibili RSS ETag/Last-Modified 等轮询缓存 |
| `data/bilibili_protection.json` | bot（bilibili.py） | bot（bilibili.py）、面板状态接口 | Bilibili 限速时间窗、来源积压队列和延后跳过记录 |

---

## 9. 重要代码文件

| 文件 | 职责 | 修改频率 |
|------|------|----------|
| `bot.py` | 入口：适配器注册、日志轮转、运行时日志级别监听 | 低 |
| `plugins/config.py` | settings.json 消费者：带 mtime 缓存的配置读取；文件缺失时初始化默认值，损坏/结构无效时内存降级（不写盘） | 低 |
| `plugins/relay.py` | 实时转发主逻辑：频道过滤 → 去重 → 渲染 → 发送 → 记录 | 中 |
| `plugins/command.py` | QQ 私聊 relay 命令 + OpenID 自动发现 + 身份记录 | 中 |
| `plugins/registration.py` | QQ 注册指令（注册/注册群）+ 注册申请存储 | 中 |
| `plugins/manage.py` | QQ 私聊管理命令（通过 HTTP 调用面板 API） | 中 |
| `plugins/fetch.py` | Discord 消息抓取 + 归一化 + 渲染为 QQ 消息段（唯一渲染逻辑） | 中 |
| `plugins/sender.py` | QQ 群发送：分片、重试、限流退避、降级 | 低 |
| `plugins/retry.py` | 发送失败欠账队列、持久化恢复与延迟重试 | 中 |
| `plugins/message_trace.py` | 投递结果追踪与持久化 | 低 |
| `plugins/failure_notification.py` | 管理员失败通知与冷却 | 低 |
| `plugins/x_rss.py` | X RSS 轮询、游标和投递 | 中 |
| `plugins/bilibili.py` | Bilibili RSS 轮询、来源筛选、限速队列、游标和投递 | 中 |
| `plugins/translation.py` | 统一翻译路由、后备服务和最近成功服务状态 | 中 |
| `plugins/ai_client.py` | OpenAI 兼容 AI 请求、模型可用性检查和自动轮换 | 中 |
| `plugins/tencent_tmt.py` | 腾讯 TMT 请求适配和错误转换 | 低 |
| `plugins/scheduled_message.py` | 按显式目标群名单和日历计划进行 QQ 定时消息投递 | 中 |
| `plugins/x_rss_parser.py` | RSS 条目解析 | 中 |
| `plugins/bilibili_parser.py` | Bilibili RSS 条目解析与稳定游标生成 | 中 |
| `plugins/bilibili_rate_limit.py` | Bilibili 限速配置归一化、积压裁剪和时间窗计算 | 低 |
| `plugins/media.py` | 文本美化（Markdown → 纯文本）+ Emoji 提取 + 媒体下载 | 低 |
| `plugins/dedup.py` | 去重纯函数：该不该发、发给谁、成功后怎么记 | 低 |
| `plugins/filter.py` | 频道消息筛选，以及 Bilibili 标签/关键词/标题/作者筛选纯函数 | 低 |
| `plugins/history.py` | discord_last.json 读写（带 mtime 缓存） | 低 |
| `plugins/backfill.py` | 启动后自动补发离线缺失消息 | 中 |
| `plugins/backfill_api.py` | 补发 HTTP 接口（面板调用）+ 频道权限审计 | 中 |
| `plugins/identities.py` | OpenID 身份资料库（昵称/群名/备注） | 低 |
| `plugins/stats.py` | 转发统计 | 低 |
| `plugins/json_io.py` | JSON 原子读写工具 | 低 |
| `plugins/health_api.py` | Bot 健康状态接口（整体连接状态、逐 QQ Bot 连接列表、翻译运行状态） | 低 |
| `panel/管理面板.pyw` | 面板主程序：FastAPI + 配置读写 + 进程管理 + 看门狗 | 高 |
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
| **修改 X RSS 转发** | `plugins/x_rss.py`（轮询/游标）+ `plugins/x_rss_parser.py`（解析） |
| **修改 Bilibili RSS 转发** | `plugins/bilibili.py`（轮询/投递/限速队列）+ `plugins/bilibili_parser.py`（解析）+ `plugins/bilibili_rate_limit.py`（保护策略）+ `plugins/filter.py`（标签/关键词/标题/作者筛选） |
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

- 面板前端从 `管理面板.pyw` 中分离为独立的 `index.html` + `app.js` + `style.css`。
- 通过 FastAPI `StaticFiles` 挂载，面板重启后刷新浏览器即可看到最新前端。
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
| 自动转发方向 | 自动链路为 Discord → QQ、X RSS → QQ；项目不做 QQ → Discord 或双向同步；QQ → QQ 仅限授权用户手动 relay 命令 |
