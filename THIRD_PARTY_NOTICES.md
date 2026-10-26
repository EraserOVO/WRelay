# 第三方组件声明

WRelay 自身按 GPL-3.0-or-later 发布。以下第三方组件保留各自许可证：

- **nonebot-adapter-qq 1.7.2+wrelay.1**：基于上游 1.7.2 的 wheel，仅修改发行元数据以放宽已修复依赖的版本约束；上游 MIT 许可证副本位于 vendor/qq-adapter-LICENSE，补丁来源和复现方式见 vendor/README.md。
- **MingCute icons**：使用 home_1_line、discord_line、social_x_line、bilibili_line、youtube_line、qq_line、settings_1_line 等 SVG 图标，按 Apache-2.0 许可。许可证副本位于 third_party_licenses/Apache-2.0.txt。上游项目：[mingcute-design/mingcute-icons](https://github.com/mingcute-design/mingcute-icons)。
- **Python 运行时依赖**：版本锁定于 requirements-lock.txt。依赖作者保留各自许可证；Windows 编译包会收集锁定依赖及 Python 运行时中的许可证文件，并附上适配包内路径的声明、QQ 适配器 MIT 许可证和 MingCute Apache-2.0 许可证，统一放在 `THIRD_PARTY_NOTICES/`。

各平台 API、RSSHub 服务以及翻译服务也受其各自条款约束；它们不是 WRelay 的组成部分。
