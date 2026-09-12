# macOS App 开发版

`desktop/v1.1.0` 从已验证的 v1.0.1 基线开始。当前版本为 `1.1.0-alpha.1`，提供独立 macOS 窗口、原生菜单、工作空间选择和本地服务生命周期管理。转写、资料库与目录设置复用已发布的业务核心，现阶段窗口内仍是 Streamlit 工作台。

## 使用与构建

这是面向本机开发的 Apple Silicon 构建，需要 macOS 13 或以上、Xcode Command Line Tools，以及已经安装依赖的 Echo 项目 `.venv`。它尚未把 Python、FFmpeg 和模型打进安装包，也未进行 Apple Developer 签名或公证。

```sh
python3 scripts/build_macos.py
open dist/Echo.app
```

首次打开时选择 Echo 项目文件夹。所选项目应在桌面开发分支上，并包含 `app.py`、`echo/` 和 `.venv/bin/python`。App 记住工作空间；输入录音目录和结果输出目录仍在工作台中配置。也可从菜单重新选择工作空间。

关闭最后一个窗口或选择“退出 Echo”会停止这个 App 启动的服务和任务。已保存版本保留；被中断的任务可重新提交，核心沿用中断恢复逻辑。其他终端独立启动的服务不归该 App 管理。

## 结构与边界

- `desktop/macos/Main.swift`：AppKit 窗口、WKWebView、原生菜单、保存对话框、工作空间书签。
- `desktop/supervisor.py`：启动一个仅监听本机的动态端口服务，检查健康状态，以 JSON 行向 App 报告就绪或错误，管理整个后端进程组。
- `echo/paths.py`：核心目录配置与进程启动约定。桌面管理的队列和转写进程继承服务进程组；CLI 继续使用独立后台模式。
- `scripts/build_macos.py`：编译原生外壳，生成 `.app` 并作本机 ad-hoc 签名。不打包媒体、数据库、配置或模型。

App 只在自己的本地端口载入工作台；用户点击的外部网页使用默认浏览器打开。启动过程无需云端推理账户。服务日志位于当前工作空间数据目录的 `desktop.log`。

## 后续迭代

1. 抽出与 Streamlit 无关的应用服务接口，逐步替换窗口内的界面。
2. 原生目录选择、任务侧栏、资料阅读区与统一状态反馈。
3. 独立运行时、FFmpeg 与模型管理；消除对源码目录和既有 `.venv` 的依赖。
4. 签名、公证、安装与更新流程，完成可分发的正式桌面版本。

这些能力属于后续工作，当前开发版不把它们描述为已完成。
