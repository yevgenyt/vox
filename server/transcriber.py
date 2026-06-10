"""Whisper.cpp wrapper for transcription."""

import logging
import os
import struct
import subprocess
import tempfile
import json
import time
from pathlib import Path


WHISPER_CLI = os.environ.get("WHISPER_CLI", "/opt/whisper.cpp/build/bin/whisper-cli")
WHISPER_MODEL_DIR = os.environ.get("WHISPER_MODEL_DIR", "/opt/whisper.cpp/models")
WHISPER_DEFAULT_MODEL = os.environ.get("WHISPER_DEFAULT_MODEL", "large-v3-turbo")

# Available models (must be downloaded in Dockerfile)
AVAILABLE_MODELS = {"small", "medium", "small.en", "large-v3-turbo"}

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

        # Get model path (validates model name)
        model_path = get_model_path(model)
        model_name = model_path.stem.replace("ggml-", "")
        logger.info(f"Running whisper-cli with model {model_name}")
        start_time = time.time()

        # Build whisper-cli command
        json_out_path = None
        if timestamps:
            json_out_path = audio_path.with_suffix("")
            cmd = [
                WHISPER_CLI,
                "-m", str(model_path),
                "-f", str(audio_path),
                "-l", "auto",
                "-bs", "1",
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
                "-nt",          # No timestamps
                "-np",          # No prints (clean output)
            ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        duration_ms = int((time.time() - start_time) * 1000)

        if result.returncode != 0:
            logger.error(f"whisper-cli failed (exit {result.returncode}): {result.stderr}")
            raise RuntimeError(f"whisper-cli failed: {result.stderr}")

        # Detect language from stderr
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
    logger.info(f"CLI: {WHISPER_CLI}")

    start_time = time.time()

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        # Create a short silent audio file
        _create_silent_wav(tmp_path, duration_ms=500)
        logger.info(f"Created warm-up audio: {tmp_path} ({tmp_path.stat().st_size} bytes)")

        # Run transcription to load model into GPU memory
        result = subprocess.run(
            [
                WHISPER_CLI,
                "-m", str(model_path),
                "-f", str(tmp_path),
                "-l", "en",     # Skip auto-detect for faster warm-up
                "-bs", "1",
                "-nt",
                "-np",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

        duration_ms = int((time.time() - start_time) * 1000)

        if result.returncode != 0:
            logger.error(f"Warm-up failed: {result.stderr}")
            return {"success": False, "duration_ms": duration_ms, "model": model_name, "error": result.stderr}

        logger.info(f"Model warm-up complete in {duration_ms}ms")
        return {"success": True, "duration_ms": duration_ms, "model": model_name}

    finally:
        tmp_path.unlink(missing_ok=True)
