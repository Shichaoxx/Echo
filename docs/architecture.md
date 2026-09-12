# 架构与流程

## 模块

| 模块 | 职责 |
| --- | --- |
| app.py | Streamlit 工作台、偏好、提交、局部进度刷新 |
| interview.py | 扫描、文件校验、数据库、转写生命周期、导出 |
| asr_models.py | 模型注册、下载、Whisper/Qwen 适配与分段 |
| queue_jobs.py | 持久 FIFO、去重、取消、后台调度 |
| task_status.py | 耗时与基于实际进度的时间估算 |
| themes.py / style.css | 六套配色与布局 |

## 数据流与网络边界

```mermaid
flowchart TB
    subgraph Local[用户电脑]
        UI[Streamlit 本地界面] --> DB[(SQLite)]
        UI --> Q[队列调度器]
        Scan[增量扫描] --> DB
        Files[录屏目录] --> Scan
        Q --> Lock[worker.lock 单任务锁]
        CLI[直接 CLI 转写] --> Lock
        Lock --> Audio[FFmpeg 提取音轨]
        Files --> Audio
        Audio --> ASR[本地 ASR 模型]
        Cache[本地模型缓存] --> ASR
        ASR --> Export[追加结果版本与核对稿]
        Export --> DB
        Export --> Output[本地导出目录]
        DB --> UI
    end
    HF[Hugging Face 模型下载] --> Cache
```

下载模型需要网络；转写音频通过本地文件交给模型。界面不上传录音用于云端推理。不能据此推断依赖没有任何网络行为，详见隐私文档。

## 队列状态机

```mermaid
stateDiagram-v2
    [*] --> queued: 提交并固定参数
    queued --> cancelled: 用户取消
    queued --> running: 按序取出且 GPU 空闲
    running --> queued: 获取转写锁时发生竞争
    running --> completed: 完成或复用已有版本
    running --> failed: 输入或推理失败
    running --> interrupted: 调度进程重启后恢复记录
    completed --> [*]
    cancelled --> [*]
    failed --> [*]
    interrupted --> [*]
```

`queue.lock` 保证单调度器，`worker.lock` 保证单 GPU 转写。正在执行的旧任务无需改造或重启；调度器等待其释放锁。失败不阻塞后续任务，重试通过再次提交产生新记录。

## 转写时序

```mermaid
sequenceDiagram
    actor User as 用户
    participant UI as 工作台
    participant DB as SQLite
    participant Queue as 调度器
    participant ASR as 本地转写
    User->>UI: 提交模型、范围与提示
    UI->>DB: 固定参数与源文件哈希
    Queue->>DB: 取最早等待项
    Queue->>ASR: 空闲后获取转写锁
    ASR->>ASR: 校验源文件、提取音轨、逐块识别
    loop 阶段或块更新
        ASR->>DB: 写入进度
        UI->>DB: 每 3 秒读取
    end
    ASR->>DB: 保存版本与完成事件
    Queue->>DB: 关联结果版本
    UI->>User: 自动载入结果
```

## 版本与数据关系

```mermaid
erDiagram
    INTERVIEWS ||--o{ RUNS : versions
    INTERVIEWS ||--o{ NOTES : append
    INTERVIEWS ||--o{ EVENTS : audit
    INTERVIEWS ||--o{ QUEUE_JOBS : submissions
    RUNS ||--o| PROGRESS : status
```

每场档案以路径建档，内容哈希用于变化识别。相同模型、来源及参数已有完成结果时复用；强制重跑保留旧版本。队列完成记录中的 run_id 是应用层关联。raw_segments 保留模型原始片段，展示片段包含核对注释和原录屏时间偏移。

数据库以 CREATE TABLE IF NOT EXISTS 增量建表。v0.1.0 没有单独的迁移版本表；未来破坏性变更必须提供备份与迁移方案。

## 源码目录

```text
Echo/
├── start.command            双击启动
├── app.py                   Streamlit 稳定入口
├── interview.py             原 CLI 兼容入口
├── queue_jobs.py            后台队列兼容入口
├── echo/                    业务实现
│   ├── workspace.py         详情与阅读界面
│   ├── ui/                  全局队列及共享显示函数
│   ├── interview.py         转写与台账
│   ├── asr_models.py        模型适配
│   └── ...                  队列、主题、复盘读取
├── assets/                  样式
├── tests/                   合成数据测试
├── scripts/                 归档、扫描、发布等维护工具
├── docs/                    使用、协议、开发与变更记录
└── .local/                  本机数据入口（不进入 Git）
```

旧 CLI 和已启动队列继续使用根目录兼容入口。业务模块通过 `echo` 包导入；ROOT 始终指向项目根目录，因此 `.local` 与录屏配置不受源码移动影响。`python -m unittest -q` 从 tests 包发现测试。

## 资料库与个人上下文

`echo/library.py` 管理本机 `profile.json` 和 `library.json` 的原子写入、日期解析、公司归一及分组。覆盖值以稳定 interview_id 关联，不修改媒体台账；日期无法推断时留空，年份未知独立分组。Profile 由用户填写，供 Codex 作为待核对背景使用。

`echo/ui/library_panel.py` 和 `echo/ui/profile.py` 负责导航、档案表单与个人资料页。`echo/ui/transcript.py` / `assets/transcript.js` 在客户端处理连续阅读、搜索和片段高亮，使用 textContent 渲染转写。时间戳通过局部播放器 ID 发送定位事件；`player_controls.js` 管理唯一音频实例。`assets/workspace.css` 提供基于主题变量的毛玻璃布局，保留原主题样式和队列组件。
