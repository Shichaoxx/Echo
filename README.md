# Echo

Echo 把一场面试整理成一篇可以回听的纪要。打开文档，先读总结，再按问题回看回答和追问。点「查看原文」展开逐字引文，点时间戳播放它在录音中的位置。

## 安装 Mac 应用

Echo 支持运行 macOS 15 或更高版本的 Apple Silicon Mac（M 系列芯片）。从[发布页](https://github.com/Shichaoxx/Echo/releases/tag/v1.0.0-alpha.2)下载并打开 `Echo-1.0.0-alpha.2-macOS-arm64.dmg`。首次安装，把 **Echo.app** 拖入「应用程序」。升级时，先退出 Echo，再把 **Echo.app** 拖到原来的安装目录（系统的 `/Applications` 或个人的 `~/Applications`），以免在两个目录中各留一份。出现同名提示时选择「替换」，再从安装目录打开 **Echo**。

如果之前装过 **Echo 1.app**，新版会直接打开原有的 1.x 资料库。确认能看到自己的纪要后，可以把「应用程序」里旧的 **Echo 1.app** 移到废纸篓。

从 0.x 升级时，旧录音和逐字稿仍在原资料库，导入方法见下文。如果曾启用旧版后台服务，可在访达按 ⇧⌘G，前往 `~/Library/LaunchAgents/`。如果找到 `io.github.shichaoxx.echo.desktop.plist`，运行以下命令退出旧服务，再在访达中将这一个 plist 移到废纸篓，以免下次登录时重新启动。

```sh
launchctl bootout "gui/$(id -u)/io.github.shichaoxx.echo.desktop"
```

如果提示找不到该服务，表示它目前没有载入，可以直接移走上述 plist。

第一次打开会进入个人资料库。可直接导入录音，也可以从菜单 **资料库 → 打开示例资料库** 体验两篇可回听的纪要。安装包包含 Whisper Turbo 模型、Python 和媒体工具，首次转写可离线完成。通过 Codex 整理纪要时，你自己选择要交给它的逐字稿。

此预览安装包使用本机临时签名，未经 Apple 公证。从网上下载后，如果 macOS 提示无法验证开发者，可先核对随包提供的 SHA-256，再按系统提示操作。[Apple 关于打开下载应用的说明](https://support.apple.com/zh-cn/102445)

## 从源码运行演示

在 Mac 上安装 Node.js 22.12+、Python 3.10+ 和 FFmpeg（含 FFprobe），在仓库目录执行：

```sh
./start.command --demo
```

打开 [本机演示](http://127.0.0.1:18772/)。首次运行会安装前端依赖并构建页面。两场虚构面试已随仓库提供，包含合成语音、实际 Whisper 转写和 Codex 根据该转写整理的纪要。浏览、回听和修订这两篇文档都可以离线完成。

在《夜班公交的到站提醒》中，打开车辆提前折返的追问。**02:59.8** 提出反例，**03:17** 开始回答。点原文的时间可以分别核对两段声音，再收起播放器继续阅读。点左上角 **Echo** 返回最近纪要，可打开第二篇《阅览室的周末预约》。生成材料与录音来源见[示例说明](examples/README.md)。

## 处理自己的录音

1. 打开 Echo，在「最近纪要」选择「导入录音」。从源码运行时，可执行 `./start.command` 并打开[个人资料库](http://127.0.0.1:18771/)。Echo 会复制音频或带音轨的视频，原文件留在原处。
2. 点击「开始本地转写」。从源码运行时，首次配置见下节。如果已有 0.x 的转写，选择同一原录音，再导入该次转写的 `transcript.json`。Echo 会核对媒体摘要、片段位置和实际覆盖范围。旧文件缺少媒体摘要时仍可读文字。
3. 选择「交给 Codex 生成」，把交接文字复制给 Codex，请它返回所要求的 JSON 文件。回到本场面试导入。Echo 在本机校验引用、解析时间并保存纪要。
4. 点击「修订正文」可以调整文字和金句解读。「操作」里可以切换历史修订、导出 Markdown、打开逐字稿，或再次生成。重新转写会留下独立版本。旧纪要保留生成时的逐字引文。

### 为源码运行配置本机语音识别

转写使用 Apple Silicon、Metal、FFmpeg 和本地 MLX Whisper Turbo 权重。在仓库下建立 Python 环境并安装推理依赖，模型目录应包含 `config.json` 和权重文件。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-asr.txt
ECHO_WHISPER_MODEL="/path/to/whisper-turbo" ./start.command
```

启动脚本也能使用旧 Echo 已安装在 macOS 应用支持目录中的 Turbo 权重。录音、版本和引文保存在新版独立的资料库。已核对的原声能否在页面播放，还取决于浏览器对录音编码的支持。

## 数据与运行

默认个人库位于 `~/Library/Application Support/Echo 1.x/library`，演示库位于其同级的 `demo`。每个库下的 `media/` 存放媒体，`echo-v1.sqlite3` 存放转写、纪要修订和任务状态。备份时先结束转写并停止服务，再复制整个资料库。分享交接文字会一并分享里面的逐字稿。

前端使用 React、TypeScript 和 Vite。本机 Python 服务仅监听回环地址。同一次转写内的片段索引和原媒体时间可定位历史引文；服务从固定的转写中提取引文，回听时校验媒体。目录、图表、章节和导出共同引用同一份纪要修订。

也可以分别构建和运行：

```sh
npm --prefix frontend ci
npm --prefix frontend run build
python3 -m server --data-dir "/path/to/new/workspace" --port 18771 --seed-examples
python3 -m unittest discover -s tests
cd frontend && npx playwright test
```

浏览器回归测试使用临时资料库，具体启动选项见 Playwright 配置。旧版工程保存在 `archive/0.x-final` 和 `archive/1.x-prototype`。当前开发分支为 `v1/rebuild`。

## 构建 macOS 安装包

使用 Apple Silicon Mac、Xcode Command Line Tools、Python 3.12+ 和前述 Node.js 环境。在仓库根目录执行：

```sh
python3 scripts/build_macos.py --model-dir "/path/to/whisper-turbo"
```

可指定包含 `config.json` 和 `weights.safetensors` 的已有模型目录，也可省略 `--model-dir`，由脚本下载固定版本并校验 SHA-256。安装镜像及校验文件写入 `dist/v1.0.0-alpha.2/`。第三方软件的许可证及来源随应用分发。

如有 Developer ID 身份和已配置的公证凭据，可传入 `--identity` 和 `--notary-profile` 构建签名、公证的发行包。要验收最终产物，挂载 DMG 后，用下面的命令从空的临时资料库导入示例录音并实际完成本机转写：

```sh
python3 tests/packaged_asr.py "/Volumes/Echo/Echo.app"
```
