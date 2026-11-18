# Windows 发布完整性说明

WRelay 当前通过 GitHub Actions 构建并发布未签名的 Windows 安装包。公开仓库、版本标签和 SHA-256 校验值便于用户检查来源与文件完整性；SHA-256 不证明发布者身份，也不能保证软件没有缺陷。

## 发布流程

1. 维护者从匹配的版本标签手动运行 `Build and publish unsigned Windows release`。若该标签还没有 GitHub Release，工作流会从该标签的 `CHANGELOG.md` 创建草稿；已有 Release 必须仍为草稿。
2. GitHub 托管的 Windows runner 从标签源码构建安装包，并明确跳过 Authenticode 签名。
3. 工作流计算安装包的 SHA-256，将 `WRelay-Setup.exe` 和 `WRelay-Setup.sha256` 上传到对应 Release，然后发布 Release。

## 用户提示

- 安装包未进行 Authenticode 签名，Windows 可能显示「未知发布者」或 SmartScreen 提示。
- 从 WRelay 官方 GitHub Release 获取安装包和对应的 `.sha256` 文件。可在 PowerShell 中运行 `Get-FileHash .\WRelay-Setup.exe -Algorithm SHA256`，将结果与校验文件中的哈希值比较。
- 哈希一致只能说明文件与所比较的校验值相同；如果安装包和校验值来自同一被篡改渠道，校验不能证明来源可信。

## 构建范围与隐私

工作流只构建发布安装包并将安装包及校验文件上传到 GitHub Release，不调用外部签名服务。它不包含 `.env.prod`、`data/` 或运行时用户数据。WRelay 的本地运行与数据传输范围见[隐私与本地数据说明](docs/PRIVACY.md)。

本机仍可在维护者配置受信任的 Authenticode 证书后进行单独签名；该签名不属于当前官方 GitHub 发布流程。

## 问题反馈

请通过 [GitHub Security Advisories](https://github.com/EraserOVO/WRelay/security/advisories/new) 或仓库 issue tracker 报告疑似发布完整性问题。
