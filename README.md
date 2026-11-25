# Echo

Echo 把一场面试整理成一篇可以回听的纪要。打开文档，先读总结，再按问题回看回答和追问。点「查看原文」展开逐字引文，点时间戳播放它在录音中的位置。

## 先试一篇纪要

在 Mac 上安装 Node.js 22.12+、Python 3.10+ 和 FFmpeg（含 FFprobe），在仓库目录执行：

```sh
./start.command --demo
```

打开 [本机演示](http://127.0.0.1:18772/)。首次运行会安装前端依赖并构建页面。两场虚构面试已随仓库提供，包含合成语音、实际 Whisper 转写和 Codex 根据该转写整理的纪要。浏览、回听和修订这两篇文档都可以离线完成。

在《夜班公交的到站提醒》中，打开车辆提前折返的追问。**02:59.8** 提出反例，**03:17** 开始回答。点原文的时间可以分别核对两段声音，再收起播放器继续阅读。点左上角 **Echo** 返回最近纪要，可打开第二篇《阅览室的周末预约》。生成材料与录音来源见[示例说明](examples/README.md)。

## 用自己的录音

1. 执行 `./start.command`，打开[个人资料库](http://127.0.0.1:18771/)。在「最近纪要」选择「导入录音」。Echo 会复制音频或带音轨的视频，原文件留在原处。
2. 点击「开始本地转写」。首次配置见下节。如果已有 0.x 的转写，选择同一原录音，再导入该次转写的 `transcript.json`。Echo 会核对媒体摘要、片段位置和实际覆盖范围。旧文件缺少媒体摘要时仍可读文字。
3. 选择「交给 Codex 生成」，把交接文字复制给 Codex，请它返回所要求的 JSON 文件。回到本场面试导入。Echo 在本机校验引用、解析时间并保存纪要。
4. 点击「修订正文」可以调整文字和金句解读。「操作」里可以切换历史修订、导出 Markdown、打开逐字稿，或再次生成。重新转写会留下独立版本。旧纪要保留生成时的逐字引文。

### 本机语音识别

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
