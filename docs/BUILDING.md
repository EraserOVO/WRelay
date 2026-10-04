# 开发、测试与构建

## 源码运行

- Windows 10/11；源码运行需要 64 位 Python 3.10 或更高版本。
- 在项目目录运行 install.bat 安装锁定依赖并创建本机配置，再运行 启动面板.bat。
- install.bat 会从 .env.example 创建 .env.prod。请只在本机填写凭据，不要提交该文件。
- 完整安装与账号配置步骤见 [安装指南](SETUP.md)。

## 检查改动

运行自动回归入口：

```powershell
python scripts/run_tests.py
```

GitHub Actions 在 Ubuntu 和 Windows 上检查 Python 3.10 与 3.12，安装锁定依赖、检查空白错误、编译源文件并执行回归。工作流中的第三方 Actions 固定到完整提交 SHA；更新时应先核对上游正式版本。Windows 工作流还会构建未签名测试安装包并运行安装冒烟检查。手工界面检查和需要真实账号的投递检查不会由 CI 执行；请仅使用获授权的测试账号和群。

## Windows 安装包

构建依赖和流水线定义在 requirements-build.txt、scripts/build_installer.ps1、scripts/build_compiled.py、WRelay.iss 与 WRelay-Test.iss。测试版构建命令为：

```powershell
.\scripts\build_installer.ps1 -Edition test
```

正式构建与测试构建分开；签名发布需要由发布者配置受信任的 Authenticode 证书、时间戳服务和签名工具。仓库不应保存私钥或证书密码。安装包会附第三方组件许可证材料，具体文件由构建脚本从当前锁定依赖生成。

测试包说明见 [测试包使用说明](TEST_PACKAGE.md)。测试安装包与任何公开发行版应使用可追溯的源提交构建，并在发布前完成依赖、素材、隐私与许可证复核。
