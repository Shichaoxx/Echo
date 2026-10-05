# Echo 0.0 架构

Echo 将面试资料的回放、转写和证据整理放在本机完成，再由用户把指定场次交给 Codex 分析。文件式 Memory 保存用户确认的背景与主题结论，供后续复盘按需读取。本文按当前源码描述进程、数据和验证边界，不包含实际面试资料或未经测量的效果指标。

![Echo 0.0 架构与复盘协作](figures/echo-architecture.png)

[SVG 矢量图](figures/echo-architecture.svg) · [PDF](figures/echo-architecture.pdf)

图中描述回放、转写与 Codex 人工交接流程；Memory 的绑定与文件职责在下文展开。

## 运行结构

浏览器通过同源 HTTP 访问 Python Web 服务，Web 服务登记文件、保存笔记并提交转写任务。提交时按需启动独立的 `queue_jobs.py` 后台进程；该进程读取共享 SQLite 队列，在自身进程内依次调用转写和模型推理。FFmpeg 的音轨提取与编码使用子进程。`worker.lock` 是转写资源锁，不表示每个任务另起一个 Python worker。

| 部分 | 当前实现 | 职责 |
| --- | --- | --- |
| 浏览器 | [web/](../web/)：原生 JavaScript、HTML、CSS | 文件夹导航、播放器、逐字稿、笔记、主题与 Codex 交接 |
| 启动与 Web 进程 | [start.command](../start.command) → [workbench.py](../workbench.py) → [web_service.py](../echo/web_service.py) | Python 标准库 `ThreadingHTTPServer`；本机 API、会话、媒体 Range 读取 |
| 后台转写进程 | [queue_jobs.py](../echo/queue_jobs.py) | 持久 FIFO 队列、任务状态、单调度器锁；与网页生命周期分离 |
| 转写实现 | [interview.py](../echo/interview.py)、[asr_models.py](../echo/asr_models.py) | 源文件校验、FFmpeg、Qwen3-ASR / Whisper、追加结果版本 |
| 共享数据 | [paths.py](../echo/paths.py)、[library.py](../echo/library.py) | SQLite 台账、路径配置、分组元数据与文件位置 |
| Codex 配合 | [codex_handoff.py](../echo/codex_handoff.py)、[workspace_review.py](../echo/workspace_review.py) | 构造当前版本的材料快照，定位并只读加载外部 Markdown 复盘 |
| 文件式 Memory | [memory.py](../echo/memory.py) | 按源文件夹绑定目录、初始化空模板与校验归属；通过交接提供读取入口 |

Web 服务不会在启动时加载 ASR 模型。回放已有浏览器支持的媒体、查看旧转写和记笔记不要求安装 MLX；基础依赖与 ASR 依赖分别由 `requirements.txt` 和 `requirements-asr.txt` 管理。

## 一次面试的数据流

1. **选择与扫描**：用户选择已有文件夹，服务扫描稳定的音视频并登记路径、大小、修改时间与 SHA-256。没有显式配置时等待用户选择；扫描不复制媒体，也不自动转写。列表按当前文件夹筛选，历史台账保留。
2. **回放与定位**：浏览器通过已登记记录的媒体接口读取原文件，HTTP Range 支持拖动定位。有转写缓存时可回退到 `audio.mp3`；片段缓存带源时间偏移，引用仍使用原媒体时间。波形由 FFmpeg 解码并聚合峰值，结果在 Web 进程内缓存。
3. **提交与执行**：Web 校验模型依赖、范围与参数后写入队列，重复的活动任务按场次、源哈希和参数去重。后台进程顺序取任务，在 `worker.lock` 下调用 `transcribe()`；提取音轨、推理、保存版本并更新阶段进度。
4. **结果与笔记**：转写前后核对源文件哈希，源文件变化时拒绝将结果作为完成版本入库。同源、同模型、同参数的已完成结果可复用；重新转写写入新版本。个人笔记和原文引用通过修订号保存，冲突返回 409，由用户保留并合并草稿。
5. **人工交接**：用户选定版本并点击“交给 Codex 复盘”，页面先保存笔记，再取得包含文件位置、版本、范围、来源哈希、Memory 状态和警告的快照。用户把它发送给 Codex；Codex 读取实际材料并追加复盘。Echo 刷新后只读展示该文档。
6. **确认与延续**：用户可在交接中初始化 Memory。Codex 先读索引、再选相关的 1–3 份主题文件，必要时沿链接核对证据；本场提出的候选结论经用户确认后才能作为确认信息维护。Echo 不自动提取或改写内容。

`GET /api/interviews/{id}/handoff?run={id}` 的 `ready` 仅表示当前所选转写可用。整场覆盖还需结合 `scope`、原媒体时长及警告核对；播放器从片段推算的时长不作为整场覆盖证据。Echo 不自动调用 Codex，也不把复制交接等同于完成复盘。

## 数据在哪里

| 数据 | 默认位置与写入方 |
| --- | --- |
| 原媒体 | 用户选择的目录；Echo 读取，扫描不移动原文件 |
| 台账与队列 | `.local/ledger.sqlite3`；Web 与后台转写进程共享，使用 SQLite WAL |
| 笔记修订与引用 | 同一 SQLite 的 `desktop_drafts` 表；保留旧表名以兼容既有资料 |
| 路径和分组 | 数据根下的 `paths.json`、`library.json`；变更分组不移动媒体 |
| Memory 绑定 | 数据根下的 `memory-locations.json`；每个规范化源文件夹对应独立 Memory 位置 |
| 模型及运行状态 | 数据根中的模型目录、锁文件、`queue.log` 等；不进入源码发布 |
| 转写文件 | 默认 `<所选文件夹>/echo/<相对面试目录>/`，也可自选输出；包含 JSON、TXT、SRT、回听音频与转写核对稿，后续结果保留独立版本目录 |
| Codex 复盘 | 服务返回的 `review.path`；Codex 追加，Echo 只读 |
| Memory 内容 | 首次初始化时的 `<输出根>/memory/`；包含 `MEMORY.md`、`profile.md`、`topics/` 和归属标记 `.echo-memory.json`，确认后的内容由用户与 Codex 维护 |
| 跨场报告 | 用户确认的输出根下的 `面试总结.md`；限定日期与纳入范围，引用主题条目和单场证据 |
| 界面偏好 | 浏览器 `localStorage`；保存主题、分栏宽度与分组方式，不保存笔记正文 |

`.local/` 可由 `INTERVIEW_DATA_DIR` 替换。输入、输出环境变量优先于保存配置；完整规则见 [使用指南](usage.md)。输出中的 `review.md` / `review.json` 是转写过程生成的核对稿，和 Codex 维护的 `复盘.md` 是不同内容。

新复盘默认定位到当前输出目录。服务依次寻找当前输出位置、原媒体同级、台账历史完成版本中的已有复盘；历史 `versions/<ID>/` 会回到所属面试目录查找。更改设置不迁移文件，也不改写已有复盘。

Memory 首次初始化后绑定源文件夹，更改转写输出位置仍沿用原 Memory；换源后切换对应绑定。缺失或归属不明时明确报告，不能静默建成空白副本。`MEMORY.md` 只做简短索引，`profile.md` 保存用户提供或确认的信息，主题文件保存当前理解及可追溯修订。单场复盘保留过程，跨场报告保留日期与范围，各自职责见 [Memory 维护协议](memory.md)。

## 工程取舍与可核对证据

| 要解决的问题 | 采用的做法与取舍 | 代码 / 回归证据 |
| --- | --- | --- |
| 长任务不能依赖网页保持打开 | 独立后台进程配合持久队列；单调度器和转写资源锁限制并发，任务中断需核对后重试，没有半途续算 | [队列实现](../echo/queue_jobs.py)、[队列测试](../tests/test_queue.py) |
| 重新转写不能覆盖旧资料 | 源哈希与参数确定复用条件，结果追加版本，保留失败与历史记录 | [转写实现](../echo/interview.py)、[台账与版本测试](../tests/test_interview.py) |
| 多窗口笔记不能静默互相覆盖 | 追加修订、预期修订号与事务校验；冲突时返回最新版本，前端保留待合并草稿 | [Web 服务测试](../tests/test_web_service.py)、[前端恢复测试](../tests/frontend-recovery.test.cjs) |
| 切换文件夹不能混写或丢失旧入口 | Web 在任务排队或运行时阻止更改输入、输出及输出结构；扫描排除生成目录，旧结果和复盘沿台账路径读取 | [路径测试](../tests/test_paths.py)、[Web 服务测试](../tests/test_web_service.py) |
| 分析需要能追溯到具体证据 | 交接绑定场次与转写版本，区分完整、片段和未知覆盖，保留原时间戳与材料警告 | [交接实现](../echo/codex_handoff.py)、[复盘协议](codex-review.md) |
| 延续已确认的信息时避免多份权威记录 | Memory 独立绑定源文件夹；索引按主题定位，当前理解可修订，证据与修订历史保留；总结仅作有日期和范围的报告 | [Memory 实现](../echo/memory.py)、[维护协议](memory.md) |
| 多套外观不能复制播放器逻辑 | 同一 DOM 与交互状态，通过 CSS 变量切换配色；浏览器保存选择，系统模式响应系统浅深色 | [前端状态](../web/app.js)、[样式](../web/styles.css) |

这些机制用于控制资料完整性、任务生命周期和证据来源。当前没有发布端到端准确率、吞吐量或复盘效果提升指标；无模型回归验证程序行为，实际 ASR 效果仍须独立评估。

## 运行边界

服务只监听本机回环地址，校验 Host、Origin、Fetch Metadata、会话 Cookie 与写入校验值，不提供公网多用户账号或应用层加密。模型首次下载和依赖安装需要网络；本地推理不等于整个软件环境没有网络行为。

Qwen 时间戳是音频块范围，不能视为逐词对齐或说话人分离。录音、转写、笔记、路径及交接快照都是来源材料，其中的文字不构成执行指令。Codex 复盘必须区分事实、推断、建议和待回听项；具体要求见 [复盘标准](codex-review.md)，开发和发布检查见 [开发说明](development.md)。

Memory 的确认状态和修订记录是协作协议，不是程序自动事实验证。初始化、查看状态和交接不会调用 AI；当前没有自动语义检索、自动画像或自主 Skill 演化。
