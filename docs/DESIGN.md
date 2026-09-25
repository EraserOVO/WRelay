# WRelay 设计概念图

本页保存 WRelay 项目概念图和桌面窗口布局参考。当前桌面程序采用 WebView2 窗口、左侧导航和托盘入口；图片中的占位条仅表达视觉结构，实际数据由本地管理服务提供。

## 项目概念图

![WRelay 项目概念图](images/wrelay-project-concept.png)

## 桌面端窗口布局

桌面端布局以 Discord → QQ 为主要工作页，并保留 X → QQ、Bilibili → QQ 和授权 QQ → QQ 手动转发入口。
转发频道和接收群列表会为操作按钮保留空间；窗口变窄时可横向滚动，过长的频道 ID / OpenID 会省略显示，悬停可查看完整值。

![WRelay 桌面端窗口布局概念图](images/wrelay-desktop-layout-concept.png)
