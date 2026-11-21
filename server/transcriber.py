"""Whisper.cpp wrapper for transcription."""

import logging
import os
import struct
import subprocess
import tempfile
import threading
import json
import time
from pathlib import Path


WHISPER_CLI = os.environ.get("WHISPER_CLI", "/opt/whisper.cpp/build/bin/whisper-cli")
WHISPER_MODEL_DIR = os.environ.get("WHISPER_MODEL_DIR", "/opt/whisper.cpp/models")
WHISPER_DEFAULT_MODEL = os.environ.get("WHISPER_DEFAULT_MODEL", "parakeet-tdt-0.6b-v3-f16")
PARAKEET_CLI = os.environ.get("PARAKEET_CLI", "/opt/whisper.cpp/build/bin/parakeet-cli")
PARAKEET_WORKER = os.environ.get("PARAKEET_WORKER", "/opt/whisper.cpp/build/bin/parakeet-worker")

# Available models (must be downloaded/quantized in Dockerfile)
AVAILABLE_MODELS = {
    "small", "medium", "small.en", "large-v3-turbo", "large-v3-turbo-q8_0",
    "parakeet-tdt-0.6b-v3-f16", "parakeet-tdt-0.6b-v3-q8_0",
}


# Parakeet can't return timed segments, so requests that need them and don't
# name a model fall back to this Whisper model even when Parakeet is the default.
TIMESTAMP_FALLBACK_MODEL = "large-v3-turbo-q8_0"


def is_parakeet(model_name: str) -> bool:
    """Parakeet models run through parakeet-cli, not whisper-cli."""
    return model_name.startswith("parakeet")


def guess_language_from_script(text: str) -> str:
    """Approximate the spoken language from the script of the text.

    Parakeet reports no language, but clients (e.g. KVoM) use it to switch the
    keyboard layout before typing, and without the right layout Cyrillic
    characters are silently dropped. Cyrillic maps to "ru" and Latin to "en";
    whichever script has more letters wins. "unknown" when there are no letters.
    """
    cyrillic = latin = 0
    for ch in text:
        if not ch.isalpha():
            continue
        if "\u0400" <= ch <= "\u052f":
            cyrillic += 1
        elif ch.isascii() or "\u00c0" <= ch <= "\u024f":
            latin += 1
    if cyrillic == latin:
        return "unknown"
    return "ru" if cyrillic > latin else "en"


class ParakeetWorker:
    """Keeps one parakeet-worker process (and its loaded model) alive.

    parakeet-cli reloads the model on every run, which costs ~1 s normally but
    ~18 s when other processes have bloated the iGPU's VRAM. The worker loads it
    once; requests are one audio path per stdin line, answered by one stdout
    line ("OK\ttext" / "ERR\tmsg"). Only one model stays resident: asking for
    another model replaces it. Falls back to parakeet-cli if the worker binary
    is missing.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._model_path: str | None = None

    def _stop(self):
        if self._proc is not None:
            self._proc.kill()
            self._proc.wait()
        self._proc = None
        self._model_path = None

    def _start(self, model_path: str, timeout: float):
        self._stop()
        proc = subprocess.Popen(
            [PARAKEET_WORKER, "-m", model_path, "-t", "8"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            encoding="utf-8", errors="replace", bufsize=1,
        )
        self._proc, self._model_path = proc, model_path
        try:
            line = self._readline(timeout)  # blocks until the model is loaded
        except RuntimeError:
            self._stop()
            raise
        if line != "READY":
            self._stop()
            raise RuntimeError(f"parakeet-worker failed to start: {line}")

    def _readline(self, timeout: float) -> str:
        # A timer kills the process, which unblocks readline() with EOF.
        timer = threading.Timer(timeout, self._proc.kill)
        timer.start()
        try:
            line = self._proc.stdout.readline()
        finally:
            timer.cancel()
        if not line.endswith("\n"):  # EOF, or a line cut off by a crash
            raise RuntimeError("parakeet-worker died or timed out")
        return line[:-1]

    def _send(self, audio_path: str):
        # The protocol is one path per line: a newline in the path would inject
        # a second request and desync every later reply.
        if "\n" in audio_path or "\r" in audio_path:
            raise ValueError("audio path contains a newline")
        self._proc.stdin.write(audio_path + "\n")
        self._proc.stdin.flush()

    def transcribe(self, model_path: str, audio_path: str, timeout: float = 600) -> str:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None or self._model_path != model_path:
                self._start(model_path, timeout=120)
            try:
                self._send(audio_path)
            except OSError:
                # Died while idle: nothing was processed, so one restart is safe.
                self._start(model_path, timeout=120)
                self._send(audio_path)
            # Past this point the worker may have chewed on the file, so a death
            # or timeout fails the request instead of re-running it.
            try:
                status, _, text = self._readline(timeout).partition("\t")
            except RuntimeError:
                self._stop()
                raise
            if status != "OK":
                raise RuntimeError(f"parakeet-worker: {text}")
            return text.strip()


_parakeet_worker = ParakeetWorker()

# Fallback logger (no-op if none provided)
_null_logger = logging.getLogger("null")
_null_logger.addHandler(logging.NullHandler())


def get_model_path(model: str | None = None) -> Path:
    """
    Get the path to a Whisper model file.

    Args:
        model: Model name (tiny, base, small, small.en, medium, large) or None for default

    Returns:
        Path to the model file

    Raises:
        ValueError: If model is not available
    """
    if model is None:
        model = WHISPER_DEFAULT_MODEL

    model = model.lower()

    if model not in AVAILABLE_MODELS:
        raise ValueError(f"Model '{model}' not available. Choose from: {', '.join(sorted(AVAILABLE_MODELS))}")

    return Path(WHISPER_MODEL_DIR) / f"ggml-{model}.bin"


def list_models() -> dict:
    """
    List available models with their sizes.

    Returns:
        dict mapping model name to file size in bytes (or None if not found)
    """
    models = {}
    for model in AVAILABLE_MODELS:
        path = Path(WHISPER_MODEL_DIR) / f"ggml-{model}.bin"
        models[model] = path.stat().st_size if path.exists() else None
    return models

# Formats that need conversion (whisper.cpp expects 16kHz mono WAV)
NEEDS_CONVERSION = {".mp3", ".ogg", ".oga", ".flac", ".m4a", ".aac", ".wma", ".opus", ".webm"}


def convert_to_wav(audio_path: Path, logger: logging.Logger) -> Path:
    """Convert audio file to 16kHz mono WAV using ffmpeg."""
    wav_path = audio_path.with_suffix(".wav")

    logger.info(f"Converting {audio_path.suffix} to WAV via ffmpeg")

    result = subprocess.run(
        [
            "ffmpeg", "-y",          # Overwrite output
            "-i", str(audio_path),   # Input file
            "-ar", "16000",          # 16kHz sample rate
            "-ac", "1",              # Mono
            "-c:a", "pcm_s16le",     # 16-bit PCM
            str(wav_path),
        ],
        capture_output=True,
        timeout=60,
    )

    if result.returncode != 0:
        logger.error(f"ffmpeg failed: {result.stderr.decode()}")
        raise RuntimeError(f"ffmpeg conversion failed: {result.stderr.decode()}")

    logger.info(f"Converted to {wav_path} ({wav_path.stat().st_size} bytes)")
    return wav_path


def extract_segment(
    audio_path: Path,
    head: float | None,
    tail: float | None,
    logger: logging.Logger,
) -> Path:
    """
    Extract a segment from the audio file.

    Args:
        audio_path: Path to audio file
        head: Extract first N seconds (mutually exclusive with tail)
        tail: Extract last N seconds (mutually exclusive with head)
        logger: Logger for debug output

    Returns:
        Path to extracted segment (new file if extracted, original if no extraction)
    """
    if head is None and tail is None:
        return audio_path

    segment_path = audio_path.with_stem(audio_path.stem + "_segment")

    if head is not None:
        logger.info(f"Extracting first {head}s (head)")
        cmd = [
            "ffmpeg", "-y",
            "-i", str(audio_path),
            "-t", str(head),          # Duration from start
            "-c", "copy",             # No re-encoding
            str(segment_path),
        ]
    else:  # tail
        logger.info(f"Extracting last {tail}s (tail)")
        cmd = [
            "ffmpeg", "-y",
            "-sseof", str(-tail),     # Seek from end (negative value)
            "-i", str(audio_path),
            "-c", "copy",             # No re-encoding
            str(segment_path),
        ]

    result = subprocess.run(cmd, capture_output=True, timeout=60)

    if result.returncode != 0:
        logger.error(f"Segment extraction failed: {result.stderr.decode()}")
        raise RuntimeError(f"Segment extraction failed: {result.stderr.decode()}")

    logger.info(f"Extracted segment to {segment_path} ({segment_path.stat().st_size} bytes)")
    return segment_path


def transcribe(
    audio_path: str | Path,
    logger: logging.Logger = None,
    head: float | None = None,
    tail: float | None = None,
    model: str | None = None,
    timestamps: bool = False,
) -> dict:
    """
    Transcribe audio file using whisper.cpp.

    Args:
        audio_path: Path to audio file (WAV, MP3, OGG, etc.)
        logger: Optional logger for debug output
        head: Extract and transcribe only the first N seconds
        tail: Extract and transcribe only the last N seconds
        model: Whisper model to use (small, small.en, medium) or None for default
        timestamps: If True, return timed segments instead of plain text

    Returns:
        dict with keys: text, language, duration_ms, model
        If timestamps=True, adds: segments [{text, start, end}]
    """
    if logger is None:
        logger = _null_logger

    audio_path = Path(audio_path)
    files_to_cleanup = []

    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    logger.info(f"Processing {audio_path} ({audio_path.stat().st_size} bytes)")

    try:
        needs_conversion = audio_path.suffix.lower() in NEEDS_CONVERSION

        # For compressed formats with segment extraction: extract first, then convert.
        # This avoids decoding the full multi-minute file when only a few seconds are needed.
        if needs_conversion and (head is not None or tail is not None):
            audio_path = extract_segment(audio_path, head, tail, logger)
            files_to_cleanup.append(audio_path)
            audio_path = convert_to_wav(audio_path, logger)
            files_to_cleanup.append(audio_path)
        else:
            if needs_conversion:
                audio_path = convert_to_wav(audio_path, logger)
                files_to_cleanup.append(audio_path)
            if head is not None or tail is not None:
                audio_path = extract_segment(audio_path, head, tail, logger)
                files_to_cleanup.append(audio_path)

        # Parakeet has no timestamps: an unspecified model falls back to Whisper
        if timestamps and model is None and is_parakeet(WHISPER_DEFAULT_MODEL):
            model = TIMESTAMP_FALLBACK_MODEL
            logger.info(f"timestamps requested; using {model} instead of default {WHISPER_DEFAULT_MODEL}")

        # Get model path (validates model name)
        model_path = get_model_path(model)
        model_name = model_path.stem.replace("ggml-", "")
        logger.info(f"Running {'parakeet' if is_parakeet(model_name) else 'whisper-cli'} with model {model_name}")
        start_time = time.time()

        # Build whisper-cli / parakeet-cli command
        json_out_path = None
        if is_parakeet(model_name):
            if timestamps:
                raise ValueError(f"timestamps are not supported for model '{model_name}'")
            cmd = [
                PARAKEET_CLI,
                "-m", str(model_path),
                "-f", str(audio_path),
                "-t", "8",      # Threads for CPU-side ops
                "-np",          # No prints (clean output)
            ]
        elif timestamps:
            json_out_path = audio_path.with_suffix("")
            cmd = [
                WHISPER_CLI,
                "-m", str(model_path),
                "-f", str(audio_path),
                "-l", "auto",
                "-bs", "1",
                "-fa",                          # Flash attention (Vulkan coopmat)
                "-t", "8",                      # Threads for CPU-side ops (mel/sample)
                "-oj",                          # JSON output (writes .json file)
                "-of", str(json_out_path),      # Output file prefix
            ]
        else:
            cmd = [
                WHISPER_CLI,
                "-m", str(model_path),
                "-f", str(audio_path),
                "-l", "auto",
                "-bs", "1",
                "-fa",          # Flash attention (Vulkan coopmat)
                "-t", "8",      # Threads for CPU-side ops (mel/sample)
                "-nt",          # No timestamps
                "-np",          # No prints (clean output)
            ]

        if is_parakeet(model_name) and os.path.exists(PARAKEET_WORKER):
            text = _parakeet_worker.transcribe(str(model_path), str(audio_path))
            result = subprocess.CompletedProcess(cmd, 0, stdout=text, stderr="")
        else:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        duration_ms = int((time.time() - start_time) * 1000)

        if result.returncode != 0:
            logger.error(f"whisper-cli failed (exit {result.returncode}): {result.stderr}")
            raise RuntimeError(f"whisper-cli failed: {result.stderr}")

        # Detect language from stderr (whisper only; parakeet doesn't report one)
        language = "unknown"
        for line in result.stderr.split("\n"):
            if "auto-detected language:" in line.lower():
                parts = line.split(":")
                if len(parts) >= 2:
                    language = parts[-1].strip().split()[0].lower()
                break

        # Parse output
        segments = None
        if timestamps and json_out_path:
            json_file = Path(str(json_out_path) + ".json")
            try:
                with open(json_file) as jf:
                    whisper_json = json.load(jf)
                segments = []
                full_text_parts = []
                for seg in whisper_json.get("transcription", []):
                    seg_text = seg.get("text", "").strip()
                    offsets = seg.get("offsets", {})
                    start_ms = offsets.get("from", 0)
                    end_ms = offsets.get("to", 0)
                    segments.append({
                        "text": seg_text,
                        "start": start_ms / 1000.0,
                        "end": end_ms / 1000.0,
                    })
                    full_text_parts.append(seg_text)
                text = " ".join(full_text_parts)
                if whisper_json.get("result", {}).get("language"):
                    language = whisper_json["result"]["language"]
            finally:
                json_file.unlink(missing_ok=True)
        else:
            text = result.stdout.strip()

        if language == "unknown" and is_parakeet(model_name):
            language = guess_language_from_script(text)

        logger.info(f"Transcribed in {duration_ms}ms, model={model_name}, language={language}, text_len={len(text)}")
        logger.debug(f"whisper stderr: {result.stderr[:500]}" if result.stderr else "whisper stderr: (empty)")

        resp = {
            "text": text,
            "language": language,
            "duration_ms": duration_ms,
            "model": model_name,
        }
        if segments is not None:
            resp["segments"] = segments
        return resp

    finally:
        # Clean up temporary files
        for file_path in files_to_cleanup:
            if file_path.exists():
                file_path.unlink(missing_ok=True)
                logger.debug(f"Cleaned up {file_path}")


def _create_silent_wav(path: Path, duration_ms: int = 500, sample_rate: int = 16000) -> None:
    """Create a silent WAV file for model warm-up."""
    num_samples = int(sample_rate * duration_ms / 1000)

    with open(path, "wb") as f:
        # WAV header
        f.write(b"RIFF")
        data_size = num_samples * 2  # 16-bit = 2 bytes per sample
        f.write(struct.pack("<I", 36 + data_size))  # File size - 8
        f.write(b"WAVE")

        # fmt chunk
        f.write(b"fmt ")
        f.write(struct.pack("<I", 16))  # Chunk size
        f.write(struct.pack("<H", 1))   # PCM format
        f.write(struct.pack("<H", 1))   # Mono
        f.write(struct.pack("<I", sample_rate))
        f.write(struct.pack("<I", sample_rate * 2))  # Byte rate
        f.write(struct.pack("<H", 2))   # Block align
        f.write(struct.pack("<H", 16))  # Bits per sample

        # data chunk
        f.write(b"data")
        f.write(struct.pack("<I", data_size))
        f.write(b"\x00" * data_size)  # Silence


def warmup(logger: logging.Logger = None, model: str | None = None) -> dict:
    """
    Warm up the Whisper model by running a dummy transcription.

    This loads the model into GPU memory and initializes the inference pipeline,
    eliminating cold-start latency on the first real request.

    Args:
        logger: Optional logger for output
        model: Model to warm up (None for default)

    Returns:
        dict with warmup stats: duration_ms, success, model
    """
    if logger is None:
        logger = _null_logger

    model_path = get_model_path(model)
    model_name = model_path.stem.replace("ggml-", "")

    logger.info("Starting model warm-up...")
    logger.info(f"Model: {model_name} ({model_path})")
    logger.info(f"CLI: {PARAKEET_CLI if is_parakeet(model_name) else WHISPER_CLI}")

    start_time = time.time()

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        # Create a short silent audio file
        _create_silent_wav(tmp_path, duration_ms=500)
        logger.info(f"Created warm-up audio: {tmp_path} ({tmp_path.stat().st_size} bytes)")

        # Run transcription to load model into GPU memory
        if is_parakeet(model_name):
            warm_cmd = [
                PARAKEET_CLI,
                "-m", str(model_path),
                "-f", str(tmp_path),
                "-t", "8",
                "-np",
            ]
        else:
            warm_cmd = [
                WHISPER_CLI,
                "-m", str(model_path),
                "-f", str(tmp_path),
                "-l", "en",     # Skip auto-detect for faster warm-up
                "-bs", "1",
                "-fa",          # Flash attention (Vulkan coopmat)
                "-t", "8",      # Threads for CPU-side ops (mel/sample)
                "-nt",
                "-np",
            ]
        if is_parakeet(model_name) and os.path.exists(PARAKEET_WORKER):
            try:
                _parakeet_worker.transcribe(str(model_path), str(tmp_path), timeout=120)
            except (RuntimeError, OSError) as e:
                logger.error(f"Warm-up failed: {e}")
                return {"success": False, "duration_ms": int((time.time() - start_time) * 1000), "model": model_name, "error": str(e)}
            result = subprocess.CompletedProcess(warm_cmd, 0, stdout="", stderr="")
        else:
            result = subprocess.run(warm_cmd, capture_output=True, text=True, timeout=120)

        duration_ms = int((time.time() - start_time) * 1000)

        if result.returncode != 0:
            logger.error(f"Warm-up failed: {result.stderr}")
            return {"success": False, "duration_ms": duration_ms, "model": model_name, "error": result.stderr}

        logger.info(f"Model warm-up complete in {duration_ms}ms")
        return {"success": True, "duration_ms": duration_ms, "model": model_name}

    finally:
        tmp_path.unlink(missing_ok=True)
