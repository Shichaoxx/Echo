"""Create an isolated, fictional test library; never modify existing data."""
import argparse
import array
import math
import os
from pathlib import Path
import shutil
import sys
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SEGMENTS = [
    (0, 4.02, "你是怎样把一个复杂问题拆解成可执行步骤的？"),
    (4.62, 13.30, "我先确认目标，再把流程拆成可以单独验证的环节。通过一个小范围实验，检查最重要的假设。"),
    (13.90, 16.67, "你用什么标准判断这个方案有效？"),
    (17.27, 25.61, "我们提前约定衡量标准，并记录实际使用中的困难。除了观察结果，也持续调整操作流程。"),
    (26.21, 29.63, "如果重新做一次，你会优先改进什么？"),
    (30.23, 39.77, "我会更早邀请使用者参与测试，并把每次修改的原因记录下来。这样，团队可以从过程里持续积累经验。"),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--synthetic-audio", type=Path, help="Optional generated speech sample; never use a personal recording.")
    args = parser.parse_args()
    data = args.data_dir.expanduser().absolute()
    if not data.parent.is_dir() or data.is_symlink() or (data.exists() and any(data.iterdir())):
        parser.error("Choose a new directory with an existing parent, or an empty directory.")
    if args.synthetic_audio and not args.synthetic_audio.is_file():
        parser.error("Synthetic audio does not exist.")
    data.mkdir(exist_ok=True)
    os.environ["INTERVIEW_DATA_DIR"] = str(data)
    os.environ.pop("INTERVIEW_SOURCE_DIR", None)
    os.environ.pop("INTERVIEW_OUTPUT_DIR", None)
    from echo import interview as core, paths
    source = data / "incoming"
    source.mkdir()
    output = data / "outputs"
    paths.save_directories(data, source, output)
    audio = source / "虚构示例-项目方案讨论.wav"
    if args.synthetic_audio:
        shutil.copyfile(args.synthetic_audio, audio)
    else:
        rate = 16000
        samples = array.array("h", (int(1400 * math.sin(2 * math.pi * 220 * n / rate)) for n in range(rate * 40)))
        if sys.byteorder != "little":
            samples.byteswap()
        with wave.open(str(audio), "wb") as writer:
            writer.setparams((1, 2, rate, 0, "NONE", "not compressed"))
            writer.writeframes(samples.tobytes())
    core.scan(source, settle_seconds=0)
    item = core.interviews()[0]
    result = {"text": "\n".join(text for _, _, text in SEGMENTS),
              "segments": [dict(start=start, end=end, text=text) for start, end, text in SEGMENTS]}
    core.transcribe(item["id"], engine=lambda *a, **kw: result, prompt="Fictional UI fixture, no model inference.")
    core.add_note(item["id"], "虚构历史笔记：先说明目标，再解释关键取舍。")
    print("Created one fictional record and one completed fixture run. No model inference was used.")


if __name__ == "__main__":
    main()
