# 虚构示例资料库

两场中文对话均为本项目编写，使用 macOS 系统语音合成。日期、人物角色、线路和场馆均属演示设定。

| 场次 | 原声时长 | Whisper 片段 | 可核对的内容 |
| --- | --- | --- | --- |
| 夜班公交的到站提醒 | 6 分 53 秒 | 93 | 02:59.8 提出车辆提前折返的反例；03:17.0 开始回答；05:15.0 说明影子运行的指标。 |
| 阅览室的周末预约 | 1 分 22 秒 | 16 | 00:35.2 提出十五分钟签到窗口；01:02.8 讨论靠窗十二席的试行范围。 |

每个目录的 `script.txt` 是合成用的原稿，`audio.mp3` 是实际用于识别与回听的录音，`source.json` 保存本地 Whisper Turbo 的原始片段文字、时间和音频摘要。识别结果会保留同音字等误差。纪要里的 `evidence` 从零开始计数，指向此版本中的片段。Codex 读取这些实际片段后编写了 `document.json`；界面通过正式导入流程验证引用并展示。现有示例可以直接使用，查看和回听无需安装识别模型。

在 Apple Silicon 上重做识别时，需要 macOS 的中文语音 Tingting 与 Reed、FFmpeg，以及配有 Metal 的 Python + MLX Whisper 环境。把模型目录替换为本地已有的 Whisper Turbo 权重，选择一个尚未存在的输出目录：

```sh
python scripts/make_examples.py --model-dir /path/to/whisper-turbo --output /tmp/echo-new-examples
```

再次识别可能得到不同的分段。要为新版本导入纪要，先获取该版本的交接包，依据实际片段重新核对引用。
