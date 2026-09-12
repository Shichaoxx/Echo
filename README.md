# 回声 · Echo

**把面试录屏整理成可检索、可回听、持续积累的本地资料库。**

运行在 Apple Silicon Mac 上的本地面试复盘工作台。用本地模型把录屏转成文字，方便回听和记笔记，不需要云端推理 API key。v1.0.0 是首个正式源码版本，通过浏览器访问本机服务。

`v1.1.0-alpha.1` · `macOS / Apple Silicon` · `Python 3.13 已验证` · `MIT`

> 当前分支是 [macOS App 开发版](docs/desktop.md)。稳定源码版本为 `v1.0.1`；原有前端草稿保留在历史开发工作区。

## 功能

- **持续追加**：增量扫描录屏，文件变化生成新版本，历史记录与笔记保留。
- **本地转写**：Qwen3-ASR、Whisper Large-v3、Whisper Turbo，支持中文、中英混合、英语及自动识别。
- **任务队列**：持久排队、取消等待任务、重复提交去重，失败后继续下一项。
- **实时进度**：阶段、处理块数、耗时及估算剩余时间，完成后自动载入结果。
- **回听复盘**：检索、时间定位、核对提示、追加笔记，导出 TXT / SRT / JSON / Markdown。
- **目录可配置**：分别设置媒体输入、转写输出与运行数据目录；工作台与命令行使用同一配置。
- **资料整理**：按时间或公司浏览，维护个人背景、标签与可恢复归档。
- **深浅外观**：多套 Light / Dark 主题与毛玻璃外观，适配窄窗口。

## 快速开始

需要 Apple Silicon Mac、Python 3.13 和 FFmpeg。Windows、Linux 及 Intel Mac 的模型推理尚不支持；无模型测试可在 Linux 运行。

先获取源码，再安装运行：

```sh
brew install python@3.13 ffmpeg
git clone https://github.com/Shichaoxx/Echo.git
cd Echo
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
mkdir -p recordings
./start.command
```

打开 [本地工作台](http://127.0.0.1:8501/)，在侧栏「导入与目录设置」填写媒体输入目录和结果输出目录，保存后扫描。选择面试，先试转一段实际对话，再提交完整转写。

模型首次使用从 Hugging Face 下载，需要网络和足够磁盘空间。就绪后推理在本机执行。关闭浏览器不会停止已经启动的后台任务；电脑休眠会影响处理。

默认媒体输入为 `recordings/`，运行数据为 `.local/`，结果输出为 `.local/outputs/`，均不进入 Git。可在界面保存输入与输出目录，也可用环境变量临时覆盖：

```sh
INTERVIEW_SOURCE_DIR="/path/to/recordings" ./start.command
INTERVIEW_OUTPUT_DIR="/path/to/transcripts" ./start.command
INTERVIEW_DATA_DIR="/path/to/local-data" ./start.command
```

输入目录需已存在；自定义目录的父目录也需已存在。数据根或其上级目录在运行期间被移动、删除后，后台会报错停止访问，不再自动补建旧目录。已有 `.local` 外部链接失效时，请修正链接或数据目录配置后重新启动。详细优先级、迁移和备份见 [使用指南](docs/usage.md)。

## 工作流程

```mermaid
flowchart LR
    A[本地录屏目录] --> B[增量扫描与校验]
    B --> C[面试档案]
    C --> D{队列模式}
    D -->|开启| E[持久 FIFO 队列]
    D -->|关闭且空闲| F[本地转写]
    E --> F
    F --> G[独立结果版本]
    G --> H[检索与回听]
    G --> I[导出字幕与核对稿]
    H --> J[追加复盘笔记]
```

## 面试归档

双击 `scripts/archive.command`，按场次整理录屏入口、全部转写版本、复盘与练习，并保存台账快照。已有录屏路径保持不变，手写复盘不会被覆盖。目录约定与备份边界见 [归档指南](docs/archive.md)。

## 模型选择

| 模型 | 精度 | 权重约大小 | 当前时间戳 |
| --- | --- | --- | --- |
| Qwen3-ASR 1.7B（默认） | BF16 | 4.08 GB | 音频块范围 |
| Whisper Large-v3 | FP16 | 3.08 GB | 模型片段时间戳 |
| Whisper Turbo | FP16 | 1.61 GB | 模型片段时间戳 |

权重大小不等于运行内存需求。Qwen 与 Turbo 已进行本机推理验证；Large-v3 已接入，尚未完成本项目的本机端到端验证。项目不提供未经人工参考稿验证的准确率排名。

Qwen 默认按最多 25 秒分块，尽量在低音量处切分，整场都会处理。各块独立识别，时间戳尚未逐词对齐，字幕也使用块级范围。技术缩写、人名和产品名仍需回听。

## 数据与隐私

程序默认仅监听 `127.0.0.1`。数据库包含源文件路径，转写稿、日志、队列参数和截图可能包含私人信息，不应提交或附在公开 Issue 中。

`.gitignore` 只是辅助措施，不能清除 Git 历史，也不能阻止强制添加文件。源码发布检查与不携带历史的源码包用于控制发布内容。参阅 [隐私边界](docs/privacy.md) 和 [发布流程](docs/releases.md)。

## 当前边界

- 解析音轨，不区分说话人，不识别屏幕代码，不生成面试评分。
- 核对提示不是准确率；近乎静音过滤可关闭。
- GPU 转写串行执行；队列用于连续提交，不是并行推理。
- 中断后从头重试，不支持音频中途续算；移动文件会建立新档案。
- 本地数据未加密，不提供账号认证；请勿直接暴露到公网。
- 媒体输入支持录屏和音频；PDF、Word 等正文文档尚未接入转写流程。

## 下一版本

下一阶段开发 macOS App：独立窗口、目录选择与本地服务生命周期管理，沿用现有台账、队列和转写能力。v1.0.0 发布物为源码包，桌面 App 与安装包将单独迭代和验证。

## 文档与维护

```sh
.venv/bin/python -m unittest -q
.venv/bin/python scripts/check_public.py
```

| 文档 | 内容 |
| --- | --- |
| [使用指南](docs/usage.md) | 模型、队列、命令行、备份和排错 |
| [架构与流程图](docs/architecture.md) | 模块、状态机、数据流和版本关系 |
| [版本记录](docs/changelog.md) | 版本变化与兼容性 |
| [版本与打包](docs/releases.md) | 更新版本、打包源码 |

## 许可与上游

代码采用 [MIT License](LICENSE)。模型权重与依赖按各自许可证分发，发布包不包含模型。

感谢 [Qwen3-ASR](https://huggingface.co/Qwen/Qwen3-ASR-1.7B)、[MLX Audio](https://github.com/Blaizzy/mlx-audio)、[MLX Whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper) 和 [Streamlit](https://github.com/streamlit/streamlit)。

## 交互与 Codex 复盘

全局队列显示执行、等待、完成与异常记录；等待任务可修改参数并保留审计记录，运行任务参数固定。转写设置按面试保留会话草稿，提交前显示范围与模型，并校验视频时长。

Codex 在原存档目录维护 `复盘.md` 和根目录的 `面试总结.md`，Echo 读取展示。统一协议见 [Codex 复盘标准](docs/codex-review.md)。

## 项目目录

`echo/` 存放业务实现，`assets/` 存放样式，`tests/` 存放测试，`scripts/` 存放维护工具，`docs/` 存放文档。根目录仅保留常用入口与项目配置。双击 `start.command` 启动；原 CLI 命令保持兼容。发布产物位于 `.local/releases/`。
