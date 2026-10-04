# QQ 适配器临时依赖兼容补丁

当前锁定 `cryptography==50.0.1`，修复以下 Dependabot 告警：

- [GHSA-jwv3-5hgf-82ww](https://github.com/advisories/GHSA-jwv3-5hgf-82ww)：证书路径构建拒绝服务。
- [GHSA-g6cj-pr64-35w5](https://github.com/advisories/GHSA-g6cj-pr64-35w5)：PKCS#7 解密错误与时序泄漏。
- [GHSA-m2h6-j472-rp4c](https://github.com/advisories/GHSA-m2h6-j472-rp4c)：通配符 DNS 名称约束绕过。

截至 2026-09-28，PyPI 上 QQ 适配器最新版 1.7.3 仍限制 `cryptography<49.0.0`。
为保持当前运行代码不变，本地补丁以现用的官方 **1.7.2** wheel 为基础，不升级到 1.7.3。
本补丁并非上游官方版本；待上游正式支持安全版本后，应重新验证并移除此补丁。

## 来源与修改范围

- 上游：[nonebot/adapter-qq](https://github.com/nonebot/adapter-qq)，MIT 许可证见 `qq-adapter-LICENSE`（来自上游 v1.7.2）。
- 官方原包：`nonebot_adapter_qq-1.7.2-py3-none-any.whl`，下载地址固定于 `scripts/patch_qq_wheel.py`。
- 原包 SHA-256：`487aa6473c1c9874fe5ed088817589f7c6a09967b8ac468b29a48e41cce4f1e6`。
- 补丁包：`wheels/nonebot_adapter_qq-1.7.2+wrelay.1-py3-none-any.whl`。
- 补丁包 SHA-256：`a8e29ec72993a9ec3e541a35d63889350780299200bf5f99cad316f51366a6b5`。

只修改 `.dist-info` 元数据：版本标记为 `1.7.2+wrelay.1`，cryptography 约束改为
`>=50.0.1,<51.0.0`，补齐上游 MIT 许可证，并重新生成 RECORD。
所有 Python 文件和其它运行资源保持原包字节不变。没有绕过 pip 的依赖检查。

## 安装与复现

源码安装必须同时使用锁文件，以便 pip 找到本地补丁包：

```powershell
python -m pip install -r requirements-lock.txt -e .
python -m pip check
python scripts/test_qq_crypto_compat.py
```

维护者可运行 `python scripts/patch_qq_wheel.py` 重新下载官方原包、校验固定 SHA-256 后复现补丁。
离线时可加 `--upstream-wheel <官方原包路径>`。输出 ZIP 使用固定时间戳、顺序与无压缩存储。
不得手工修改 wheel；更新脚本或许可证后必须复现并更新这里的摘要。

测试使用虚构密钥检查 QQ webhook 握手签名、有效回调、篡改内容、错误密钥和缺失签名，
并验证 RFC 8032 标准向量；不启动真实机器人、不向 QQ 发送消息。

2026-09-28 在 Windows x64 / Python 3.12 独立环境完成验证：完整锁定依赖安装、`pip check`、
4 项密码学兼容测试、现有 QQ 引用消息兼容与多 Bot 路由测试均通过；桌面/机器人打包依赖全部可导入。
已逐字节比对官方原包的 19 个运行文件，并确认补丁 wheel 可复现、版本检查能拒绝旧的 48.0.1。
本次未执行真实账号联网测试，也未构建或安装新的桌面安装包。
