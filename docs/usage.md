# 使用指南

## 录屏与模型

把录制完成的 MOV、MP4、MKV、WAV、MP3、M4A 或 WebM 放入目录后扫描。文件默认需稳定 60 秒。推荐使用「示例面试 / session.mov」这类目录结构；不要将真实文件加入代码仓库。

Qwen 默认使用标准精度。可以指定语言、技术术语提示、起始位置、片段长度和 10–60 秒分段。默认 25 秒；时长不是总转写范围。近乎静音过滤阈值为 -55 dBFS，特别轻的声音可能被跳过，可关闭。空文本与过滤块会记录在 JSON 中。

## 队列与进度

「队列模式」默认开启，按提交顺序逐场处理。任务保存提交时的模型、语言、提示和范围；后续修改界面不改变已有任务。关闭开关只影响后续提交，已排队任务继续。取消仅适用于等待项。

当前直接转写会先完成，队列不会抢占。失败后继续下一项。队列持久保存在 SQLite；重启电脑后打开工作台恢复等待任务。意外中断的执行项需核对已有结果后重新提交。

进度每 3 秒更新。Qwen 百分比按音频块计算，剩余时间是基于观察速度的估算；加载与 Whisper 推理阶段不提供虚构百分比。关闭浏览器不终止后台进程。程序没有配置开机启动。

## 命令行

```sh
.venv/bin/python interview.py scan --source "/path/to/recordings"
.venv/bin/python interview.py list
.venv/bin/python interview.py models
.venv/bin/python interview.py transcribe <ID> --start 300 --limit 180
.venv/bin/python interview.py transcribe <ID> --language zh --chunk-seconds 25
.venv/bin/python interview.py transcribe <ID> --model mlx-community/whisper-large-v3-mlx
.venv/bin/python interview.py note <ID> '补充复盘记录'
.venv/bin/python interview.py history
.venv/bin/python interview.py watch --source "/path/to/recordings" --interval 60
```

`watch --process` 是直接串行处理模式，不通过 UI 队列；已有完整尝试不会无限重试。建议不要同时启用自动处理和手动批量提交，以免重复工作。仅扫描模式可共存。

复用旧结果更新核对规则：`transcribe <ID> --reuse-run <RUN_ID>`，同时提供与原记录一致的模型、起点、范围、语言和提示。使用 `--force` 会保留旧版并新建结果。

## 数据位置与备份

- `.local/ledger.sqlite3`：档案、运行、进度、事件、笔记与队列。
- `.local/outputs/<ID>/<RUN_ID>/`：转写、字幕、核对稿和回听音频。
- `.local/preferences.json`：主题及模型偏好。
- `.local/worker.log`、`.local/queue.log`：诊断日志。
- `INTERVIEW_SOURCE_DIR` 优先于本机 `.local/source_path.txt`，最后回落到 `recordings/`。路径配置不会进入发布包。

备份前先等待任务结束并停止界面、队列和扫描进程，再复制本地数据目录及原始录屏。模型缓存可以排除；Git 同步不等于个人资料备份。

## 排错

| 现象 | 检查 |
| --- | --- |
| 没有扫描到录屏 | 目录、支持的扩展名、文件是否已稳定 |
| 首次使用较久 | 网络、磁盘空间，模型可能仍在下载 |
| 术语错误或静音幻觉 | 用有对话的片段试转；补充提示；人工回听 |
| 队列没有前进 | 当前任务、进程锁及本机 queue.log |
| 进程中断 | 先核对已有版本，再重新提交；不自动盲目重跑 |
| 源文件发生变化 | 重新扫描后提交新任务 |
| 时间戳看起来很粗 | Qwen 当前是音频块范围，非逐词对齐 |
