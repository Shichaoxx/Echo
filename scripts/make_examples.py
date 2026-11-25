"""Recreate the fictional Mandarin recordings, then transcribe locally with Whisper Turbo."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import subprocess
import tempfile
import wave

ROOT = Path(__file__).resolve().parents[1]
VOICES = {"面试官": "Reed", "候选人": "Tingting", "馆员": "Tingting", "志愿者": "Reed"}
MODEL = "mlx-community/whisper-large-v3-turbo"


def mandarin_voices():
    names = subprocess.check_output(["say", "-v", "?"], text=True)
    choices = {}
    for line in names.splitlines():
        match = re.match(r"(.+?)\s+zh_CN\s+#", line)
        if match:
            name = match.group(1).strip()
            choices[name.split(" (", 1)[0]] = name
    missing = set(VOICES.values()) - set(choices)
    if missing:
        raise RuntimeError("Install these Mandarin system voices: " + ", ".join(sorted(missing)))
    return choices


def synthesize(script, audio, scratch, voices):
    parts = []
    for index, line in enumerate(script.read_text().splitlines()):
        if not line.strip():
            continue
        speaker, utterance = line.split("：", 1)
        part = scratch / f"line-{index:03}.aiff"
        subprocess.run(["say", "-v", voices[VOICES[speaker]], "-r", "160", "-o", str(part),
                        utterance + " [[slnc 700]]"], check=True)
        parts.append(part)
    # Normalize each system voice to mono PCM before concatenating.
    combined = scratch / "interview.wav"
    with wave.open(str(combined), "wb") as writer:
        writer.setparams((1, 2, 16000, 0, "NONE", "uncompressed"))
        for part in parts:
            pcm = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(part),
                                  "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                                 capture_output=True, check=True).stdout
            writer.writeframes(pcm)
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(combined),
                    "-c:a", "libmp3lame", "-b:a", "40k", "-y", str(audio)], check=True)


def transcribe(audio, target, model):
    import mlx_whisper
    result = mlx_whisper.transcribe(str(audio), path_or_hf_repo=str(model), language="zh",
                                    task="transcribe", condition_on_previous_text=False,
                                    temperature=0, verbose=False)
    duration = float(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries",
                     "format=duration", "-of", "csv=p=0", str(audio)], text=True))
    segments = [dict(start=round(s["start"], 3), end=round(s["end"], 3), text=s["text"])
                for s in result["segments"]]
    if not segments or any(s["start"] < 0 or s["end"] > duration + 0.001 for s in segments):
        raise ValueError("Model produced an empty transcript or times beyond the recording")
    digest = hashlib.sha256(audio.read_bytes()).hexdigest()
    provenance = hashlib.sha256(json.dumps(segments, ensure_ascii=False).encode()).hexdigest()
    envelope = dict(run_id=f"whisper-{digest[:8]}-{provenance[:8]}", source_sha256=digest, model=MODEL,
                    result=dict(source_start=0, duration=duration, text=result["text"], language="zh",
                                timestamp_kind="model_segment", segments=segments))
    target.write_text(json.dumps(envelope, ensure_ascii=False, indent=2) + "\n")
    print(f"{audio.parent.name}: {duration:.1f} seconds, {len(segments)} ASR segments")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", required=True, type=Path, help="Existing local Whisper Turbo MLX model")
    parser.add_argument("--output", required=True, type=Path, help="A new directory for the regenerated examples")
    args = parser.parse_args()
    model = args.model_dir.expanduser().resolve()
    if not (model / "config.json").is_file() or not any(
            (model / name).is_file() for name in ("weights.safetensors", "weights.npz")):
        parser.error("Model directory must contain a local Whisper configuration and weights")
    voices = mandarin_voices()
    args.output.mkdir(parents=True, exist_ok=False)
    for script in sorted((ROOT / "examples").glob("*/script.txt")):
        dest = args.output / script.parent.name
        dest.mkdir()
        with tempfile.TemporaryDirectory() as name:
            synthesize(script, dest / "audio.mp3", Path(name), voices)
        transcribe(dest / "audio.mp3", dest / "source.json", model)


if __name__ == "__main__":
    main()
