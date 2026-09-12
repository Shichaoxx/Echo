"""Explicit local ASR backends. No model code executes during UI import."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import wave
import tempfile


@dataclass(frozen=True)
class ModelSpec:
    name: str
    backend: str
    precision: str
    folder: str
    description: str
    timing: str


DEFAULT_MODEL = "mlx-community/Qwen3-ASR-1.7B-bf16"
TURBO_MODEL = "mlx-community/whisper-large-v3-turbo"
MODELS = {
    DEFAULT_MODEL: ModelSpec("Qwen3-ASR 1.7B · 标准精度", "qwen", "BF16", "model-qwen3-1.7b",
        "中文及中英混合转写；标准精度权重约 4.08 GB。首次使用需要下载。", "音频块时间范围，非逐句对齐"),
    "mlx-community/whisper-large-v3-mlx": ModelSpec("Whisper Large-v3 · 完整版", "whisper", "FP16", "model-whisper-large-v3",
        "完整 Whisper Large-v3，权重约 3.08 GB；保留模型生成的片段时间戳。", "模型片段时间戳"),
    TURBO_MODEL: ModelSpec("Whisper Turbo · 快速版（旧模型）", "whisper", "FP16", "model-turbo",
        "原有快速模型，权重约 1.61 GB；可用于对照旧结果。", "模型片段时间戳"),
}
LANGUAGES = {"zh": "中文 / 中英混合", "en": "英语", "auto": "自动识别"}


def model_name(key):
    return MODELS[key].name if key in MODELS else key


def model_ready(key, data):
    spec = MODELS[key]
    folder = Path(data) / spec.folder
    if not (folder / "config.json").is_file():
        return False
    if spec.backend == "whisper":
        return any((folder / name).is_file() for name in ("weights.npz", "weights.safetensors"))
    if not ((folder / "tokenizer.json").is_file() or all((folder / f).is_file() for f in ("vocab.json", "merges.txt", "tokenizer_config.json"))):
        return False
    index = folder / "model.safetensors.index.json"
    if index.exists():
        try:
            files = set(json.loads(index.read_text())["weight_map"].values())
            return bool(files) and all((folder / f).is_file() for f in files)
        except (ValueError, KeyError, TypeError):
            return False
    return (folder / "model.safetensors").is_file()


def ensure_model(key, data):
    folder = Path(data) / MODELS[key].folder
    if not model_ready(key, data):
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
        from huggingface_hub import snapshot_download
        snapshot_download(key, local_dir=str(folder), allow_patterns=["*.json", "*.safetensors", "*.npz", "*.txt", "*.jinja"])
        if not model_ready(key, data):
            raise RuntimeError("模型文件不完整，请重试下载")
    return str(folder)


def chunk_ranges(samples, rate, seconds):
    """Cover all samples once, choosing a quiet boundary before each time limit."""
    import numpy as np
    start = 0
    cap = int(seconds * rate)
    window = max(1, int(rate * 0.1))
    while start < len(samples):
        end = min(start + cap, len(samples))
        if end < len(samples):
            begin_search = max(start + cap // 2, end - 3 * rate)
            region = samples[begin_search:end]
            count = len(region) // window
            if count:
                energy = (region[:count * window].reshape(count, window) ** 2).mean(axis=1)
                end = begin_search + (int(np.argmin(energy)) + 1) * window
        yield start, end
        start = end


def qwen_transcribe(model, samples, *, rate, language, prompt, chunk_seconds,
                    silence_filter, progress, max_tokens=1024):
    import numpy as np
    segments, skipped = [], []
    ranges = list(chunk_ranges(samples, rate, chunk_seconds))
    for number, (begin, end) in enumerate(ranges, 1):
        chunk = samples[begin:end]
        rms = float(np.sqrt(np.mean(chunk ** 2))) if len(chunk) else 0
        if silence_filter and rms < 10 ** (-55 / 20):
            skipped.append({"start": begin / rate, "end": end / rate, "reason": "音轨能量低于 -55 dBFS"})
        else:
            result = model.generate(chunk, language={"zh": "Chinese", "en": "English", "auto": None}[language],
                                    system_prompt=prompt or None, max_tokens=max_tokens,
                                    temperature=0.0, chunk_duration=chunk_seconds + 1, verbose=False)
            if getattr(result, "generation_tokens", 0) >= max_tokens:
                raise RuntimeError(f"第 {number} 段达到输出上限，请缩短分段时长后重试；未将截断内容标为完成")
            text = result.text.strip()
            if text:
                segments.append({"start": begin / rate, "end": end / rate, "text": text, "timestamp_kind": "audio_chunk"})
            else:
                skipped.append({"start": begin / rate, "end": end / rate, "reason": "模型返回空文本（不代表确认静音）"})
        progress("转写中", number, len(ranges))
    return {"text": "\n".join(s["text"] for s in segments), "segments": segments,
            "skipped_regions": skipped, "timestamp_kind": "audio_chunk", "language": language}


def transcribe_audio(audio, *, key, data, language, prompt, chunk_seconds, silence_filter, progress):
    spec = MODELS[key]
    progress("准备模型（首次使用需下载）", 0, 0)
    path = ensure_model(key, data)
    if spec.backend == "whisper":
        import mlx_whisper
        progress("Whisper 转写中", 0, 0)
        result = mlx_whisper.transcribe(str(audio), path_or_hf_repo=path,
            language=None if language == "auto" else language, task="transcribe", fp16=True,
            initial_prompt=prompt, verbose=False, condition_on_previous_text=False)
        result["timestamp_kind"] = "model_segment"
        return result
    import numpy as np
    from mlx_audio.stt import load
    with wave.open(str(audio), "rb") as w:
        rate = w.getframerate()
        samples = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
    model = load(path)
    return qwen_transcribe(model, samples, rate=rate, language=language, prompt=prompt,
        chunk_seconds=chunk_seconds, silence_filter=silence_filter, progress=progress)


def load_default_model(data):
    try:
        key = json.loads((Path(data) / "preferences.json").read_text()).get("asr_model")
        return key if key in MODELS else DEFAULT_MODEL
    except (OSError, ValueError, AttributeError):
        return DEFAULT_MODEL


def save_default_model(data, key):
    if key not in MODELS:
        raise ValueError("不支持的模型")
    data = Path(data)
    data.mkdir(parents=True, exist_ok=True)
    path = data / "preferences.json"
    try:
        prefs = json.loads(path.read_text())
        if not isinstance(prefs, dict):
            prefs = {}
    except (OSError, ValueError):
        prefs = {}
    prefs["asr_model"] = key
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=data, delete=False) as f:
        json.dump(prefs, f, ensure_ascii=False, indent=2)
        temp = f.name
    os.replace(temp, path)
