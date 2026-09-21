# WRelay 安装指南（Windows）

WRelay 包含三条独立链路：Discord → QQ、X/Twitter RSS → QQ，以及授权用户通过
QQ 私聊命令执行的 QQ → QQ 手动转发。不提供 QQ → Discord 自动转发，也不做双向同步。

---

## 1. 前置条件

- Windows 10 / 11
- Python 3.10 或更高版本（安装时勾选 **Add to PATH**，可到 [python.org](https://www.python.org/downloads/) 下载）
- 能访问 Discord（国内网络需要代理，见第 6 节）

---

## 2. 一键安装

1. 把整个项目放到一个目录（例如 `C:\WRelay`），或 `git clone` 到本地。
2. **双击 `install.bat`**，脚本会自动：
   - 安装包版本使用随包提供的 `python\` 运行时，并自动从 `.env.example` 生成 `.env.prod`
   - 源码版本若没有内置 Python，则创建虚拟环境 `.venv`
   - 源码版本按 `requirements-lock.txt` 安装已验证的固定版本依赖
   - 以可编辑模式安装项目（`pip install -r requirements-lock.txt -e .`）
   - 源码版本若不存在 `.env.prod`，从 `.env.example` 复制生成
3. 用记事本打开 **`.env.prod`**，按第 3、4、5 节填入你自己的配置。
4. **双击 `启动面板.bat`** 打开管理面板（浏览器访问 `http://127.0.0.1:18090`）。
5. 在面板中：勾选你的 Discord 频道和 QQ 群 → 点击「启动机器人」。

如果还要转发 X/Twitter，请先准备可访问的 RSSHub，在面板的 X 页面登记来源、
加入 X 转发组并启用目标 QQ 群，再启动机器人。

> 安装后每次使用只需双击 `启动面板.bat`（机器人由面板托管，崩溃会自动重启）。

---

## 3. 开发者构建安装包

需要先安装 Inno Setup，然后在项目根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_installer.ps1
```

脚本会检查 `.runtime\python`、`requirements-lock.txt` 等构建输入，自动查找
`ISCC.exe`，编译 `WRelay.iss`，并输出安装包大小和 SHA256。若编译器不在常见安装目录，
可以先设置 `INNO_SETUP_ISCC` 为 `ISCC.exe` 的完整路径。

`pyproject.toml` 也提供标准 Python 构建元数据，可用于构建插件依赖包；它不是完整的桌面应用安装方式。
完整的 Bot、管理面板、内置 Python 和 Windows 快捷方式请使用上面的 Inno Setup 安装包。

发布前请记录构建脚本输出的 SHA256，并在分发端验证：

```powershell
Get-FileHash .\WRelay-Setup.exe -Algorithm SHA256
```

发布包中不得包含 `.env.prod`、`data/` 运行数据或其他真实凭据。当前项目未配置代码签名证书，
因此不能把“文件未被篡改”仅建立在 Windows 发布者名称上；应同时提供 SHA256，并从可信渠道分发校验值。

---

## 4. `.env.prod` 配置详解

| 配置项 | 含义 | 说明 |
|---|---|---|
| `DISCORD_BOTS` | Discord Bot 的 token | 见第 4 节 |
| `QQ_BOTS` | QQ 官方机器人的 AppID / AppSecret | 见第 5 节；`id` 与 `token` 都填 AppID |
| `HTTP_PROXY` | 本地代理地址 | 国内访问 Discord 必需，见第 6 节 |
| `LOG_LEVEL` | 日志级别 | 默认 `INFO`；排查问题时可改 `DEBUG` |
| `HOST` / `PORT` | Bot 本地 HTTP 服务地址 | 保持 `127.0.0.1` / `18082`；管理面板借此读取状态、消息追踪和执行补发，不要改为局域网地址 |
| `API_TIMEOUT` | 媒体下载超时（秒） | 默认 120 |
| `MEDIA_CONCURRENCY` | 同时下载的媒体数 | 默认 4 |
| `MEDIA_MAX_BYTES` | 单文件大小上限（字节） | 默认 30MB，超限降级为链接 |

> `.env.prod` 包含密钥，**绝对不要提交到 git 或外传**。

---

## 5. 创建 Discord Bot

1. 打开 [Discord Developer Portal](https://discord.com/developers/applications)，点 **New Application**。
2. 左侧 **Bot** 页 → **Reset Token** → 复制 token，填到 `.env.prod` 的 `DISCORD_BOTS`。
3. 在同一页面打开 **Message Content Intent**（特权网关意图，否则收不到消息正文）。
4. 邀请机器人进服务器：**OAuth2 → URL Generator**，勾选 `bot` 权限，再勾选需要的权限（`Send Messages`、`Read Messages/View Channels`、`Read Message History` 等），用生成的链接邀请。
5. 获取频道 ID：
   - 打开 Discord 用户设置 → **高级 → 开发者模式**
   - 右键目标频道 → **复制频道 ID**，粘贴到管理面板「Discord 转发频道」里

---

## 6. 创建 QQ 官方机器人

1. 登录 [QQ 开放平台](https://q.qq.com)，创建应用/机器人，获得 **AppID** 和 **AppSecret**。
2. 在 `.env.prod` 的 `QQ_BOTS` 中：`id` 与 `token` 都填 AppID，`secret` 填 AppSecret。
3. 把机器人拉进目标 QQ 群，在群里 @机器人 发一条消息。
4. 控制台会出现 `[QQ group_openid] ...`，同时写入 `data/qq_group_openids.json`。
5. 打开管理面板 →「QQ 接收群」→ 点「同步自动发现的群」，勾选启用该群。

> 说明：
> - QQ 官方机器人使用 **group_openid**（不是群号），每个机器人的 openid 不同，无法推算，只能靠入群后自动发现。
> - 公域机器人需要群主开启「主动消息」权限，且可能有时段/频率限制；想稳定使用建议申请转私域。
> - 机器人无法给未配置 openid 的群发消息。

### X/Twitter RSS 转发

1. 准备一个可从本机访问的 RSSHub 实例（默认配置通常为 `http://127.0.0.1:1200`）。
2. 在管理面板的 X 页面登记 X 用户名或 RSSHub 来源 URL。
3. 创建/选择 X 转发组，把 X 来源和已启用的 QQ 群加入同一组并保存。
4. 启动机器人后，面板会显示轮询状态、最近结果和失败原因。

RSSHub 请求失败、QQ 暂时未连接或发送失败时不会推进来源游标，恢复后会继续
尝试。对临时的 HTTP 429、5xx 和连接异常，WRelay 会在同一轮内退避重试最多 3 次；
其他 4xx 会立即报告。RSS 使用 ETag/304 缓存减少重复请求；RSSHub 必须能被运行
WRelay 的机器访问。若 RSSHub 暂时不可用，WRelay 会回退到 X 公开时间线继续轮询；
若回退接口也被限流或持续失败，再检查网络、RSSHub 服务和地址。

### 管理面板诊断与投递追踪

- 「机器人状态」卡片中的「连接诊断」只读显示机器人进程、Discord、QQ 连接和待重试投递数量，
  可手动刷新，不会发送测试消息。
- 「运行日志」卡片中的「消息追踪」打开二级窗口，可查看最近 50 条投递结果、目标群、
  失败原因和重试次数。
- 「失败通知」位于「开机自启」左侧。启用后，仅白名单中已启用且勾选「告警」的用户会
  按冷却时间收到投递失败通知；重复失败会合并，不会连续刷屏。

### QQ 私聊手动转发

白名单用户私聊机器人发送 `relay-list` 查看目标群，发送 `relay 1` 选择第一个
群，也可以发送 `relay 1 2` 同时选择多个群，或发送 `relay all` 选择全部已启用群；随后发送的下一条文字会转发到所选 QQ 群。
下一条消息也可以是 Discord 消息链接，机器人会抓取原消息并复用图片、音频等解析逻辑。
该功能是授权的 QQ → QQ 手动操作，不会把 QQ 消息自动发回 Discord。

### QQ 定时消息

管理面板的「定时消息」卡片可设置消息内容、目标群、重复方式、发送时间和启用状态。
重复方式支持“每隔 N 天”或“每 N 周指定星期”（可设置起算日期）；目标群从已审核群中选择，
只有同时启用的目标群才会接收消息。修改后无需重启机器人。白名单用户也可以私聊机器人使用
`timer` 查询，或使用 `timer on/off`、`timer text 内容`、`timer days 天数 [HH:MM]`、
`timer weekly 周几... [HH:MM]`、`timer time HH:MM`、`timer groups 序号1 序号2`
和 `timer clear` 控制。

### 公告自动翻译（可选）

管理面板的翻译默认全部关闭。Discord → QQ 与 QQ 手动转发共用 Discord 页
的「自动翻译」总开关；X → QQ 使用 X 页的「X 自动翻译」独立总开关。
两者共用 AI/TMT 的服务配置。服务不再单独设置启用按钮：选中的主服务和
失败后服务会按配置自动尝试，未配置或调用失败时继续发送原文。
AI 服务采用 OpenAI 兼容接口，可填写百炼、DeepSeek 等服务的地址和模型名；
密钥也可在面板的「翻译服务配置」中填写，或手动写入 `.env.prod`：

```text
WRELAY_AI_API_KEY=你的AI密钥
WRELAY_TENCENT_SECRET_ID=你的腾讯云SecretId
WRELAY_TENCENT_SECRET_KEY=你的腾讯云SecretKey
```

- 获取腾讯密钥：登录[腾讯云 API 密钥管理](https://console.cloud.tencent.com/cam/capi)，
  进入「访问管理 → 访问密钥 → API 密钥管理」并点击「新建密钥」。腾讯云只会在
  创建时显示 SecretKey，请立即保存；也可以直接在面板「翻译服务配置」中填写，
  面板会安全写入 `.env.prod` 并重启机器人。
- 开启对应链路的翻译总开关后，仅以外文为主的消息会被翻译；主服务失败会按该链路中的
  后备服务设置降级，所有翻译服务不可用时仍发送原文。
- 面板「翻译服务配置」中的「检测当前 API」和「检测全部 API」只检测保存的 AI 配置，
  不会触发运行时自动轮换；检测记录只显示类型和原因。窗口顶部的「当前工作模型」
  表示机器人最近一次真实翻译成功使用的模型，因此可能与检测记录不同。
- AI API 遇到 400 模型不存在/不支持、模型并发受限的 429，或明确表示模型容量/节点
  不可用的 500/503 时，会查询服务商的可用模型并自动轮换重试一次。普通网络超时、
  普通配额 429、空响应和未指向模型的服务端错误不会轮换。
- 腾讯 TMT 的文本接口可用性以腾讯云控制台和账号实际权限为准；建议同时配置 AI 后备。
- AI API 仅用于转发消息翻译；WRelay 不提供 QQ AI 对话，也不会把 QQ 消息
  自动发送到 Discord。

如果 AI 检测提示「响应内容为空」，表示接口返回了响应但没有提供可用的
`choices[0].message.content` 文本，不等同于模型不存在；可检查服务商响应格式、模型权限
和输出长度设置。

---

## 7. 代理（国内访问 Discord 必需）

Discord API 和 CDN 在国内无法直连，需要 HTTP 代理：

1. 运行你的代理软件（Clash / v2rayN 等），确认本地监听端口（Clash 默认 `7890`）。
2. 在 `.env.prod` 填写，例如：

   ```
   HTTP_PROXY=http://127.0.0.1:7890
   ```

3. 代理不对时，表现为：机器人连不上 Discord、图片/音频下载失败。

---

## 8. 常见问题

| 现象 | 排查 |
|---|---|
| 面板提示找不到运行环境 | 源码模式先运行 `install.bat`；安装包模式确认随包的 `python\` 目录完整 |
| 机器人启动失败 | 查看面板「运行日志」；确认 `.env.prod` 的 token / AppID / AppSecret 填对 |
| 收不到转发消息 | 群主是否开启「主动消息」权限；公域机器人有时段/频率限制；频道 ID / 群 openid 是否已启用 |
| 图片 / 音频发不出来 | 检查代理；文件超过 `MEDIA_MAX_BYTES` 会降级为文字 + 链接 |
| 视频发不出来 | 检查媒体下载权限、代理和文件大小；下载/上传失败时会保留视频链接，详情可在消息追踪查看 |
| RSSHub 请求失败 | 查看 X RSS 状态卡片和运行日志；429/5xx 会自动短暂重试，持续 502 通常需要检查 RSSHub 服务本身 |
| 想重置配置 | 删除 `data/settings.json` 后重启机器人，会生成空配置重新开始 |

---

## 9. 卸载与数据保留

- 升级时直接运行新的 `WRelay-Setup.exe` 并选择原安装目录，不要先卸载旧版本；安装器会先尝试关闭正在运行的 WRelay 程序。
- 升级前建议备份 `data/` 和 `.env.prod`。升级会覆盖程序文件，但默认保留用户数据和配置。
- 旧版本如果安装在早期的 `Program Files` 目录，不会自动迁移到当前用户目录；请先备份数据，再按新目录安装并手动迁移需要的配置。
- 卸载程序默认保留 `data/` 和 `.env.prod`，包括频道配置、去重游标、重试队列、面板认证信息和机器人凭据。
- 这样可以避免卸载或升级时误删运行状态；重新安装到同一目录后可继续使用原配置。
- 如果要彻底移除 WRelay，请先卸载程序，再确认不需要这些数据后，手动删除安装目录中残留的 `data/` 和 `.env.prod`。
- 删除前请注意：`.env.prod` 含机器人密钥，`data/` 含运行配置和身份资料，建议先备份或使用安全删除方式。

---

## 10. 安全提醒

- `.env.prod` 含密钥，**绝不提交、不分享**。
- `data/` 目录是运行时数据（配置、去重记录、重试队列、消息追踪、日志），误删会丢失运行状态，
  但不影响程序本身；`data/panel_auth.json` 不应外传。
- 失败消息会持久化到 `data/retry_pending.json`，进程重启后继续重试；媒体文件本身不会写入该文件。
- `data/message_trace.json` 只保存最近投递追踪摘要，不保存媒体字节；可在面板消息追踪窗口查看。
- 自动链路为 Discord → QQ 和 X RSS → QQ；不做 QQ → Discord，QQ → QQ 仅限授权用户手动命令。
