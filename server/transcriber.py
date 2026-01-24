"""Whisper.cpp wrapper for transcription."""

import logging
import os
import subprocess
import time
from pathlib import Path


WHISPER_CLI = os.environ.get("WHISPER_CLI", "/opt/whisper.cpp/build/bin/whisper-cli")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "/opt/whisper.cpp/models/ggml-small.bin")

# Fallback logger (no-op if none provided)
_null_logger = logging.getLogger("null")
_null_logger.addHandler(logging.NullHandler())

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


def transcribe(audio_path: str | Path, logger: logging.Logger = None) -> dict:
    """
    Transcribe audio file using whisper.cpp.

    Args:
        audio_path: Path to audio file (WAV, MP3, OGG, etc.)
        logger: Optional logger for debug output

    Returns:
        dict with keys: text, language, duration_ms
    """
    if logger is None:
        logger = _null_logger

    audio_path = Path(audio_path)

    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    logger.info(f"Processing {audio_path} ({audio_path.stat().st_size} bytes)")

    # Convert to WAV if needed
    converted = False
    if audio_path.suffix.lower() in NEEDS_CONVERSION:
        audio_path = convert_to_wav(audio_path, logger)
        converted = True

    logger.info(f"Running whisper-cli with model {Path(WHISPER_MODEL).name}")
    start_time = time.time()

    try:
        # Run whisper-cli
        result = subprocess.run(
            [
                WHISPER_CLI,
                "-m", WHISPER_MODEL,
                "-f", str(audio_path),
                "-l", "auto",   # Auto-detect language
                "-bs", "1",     # Greedy decoding (faster)
                "-nt",          # No timestamps
                "-np",          # No prints (clean output)
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

        duration_ms = int((time.time() - start_time) * 1000)

        if result.returncode != 0:
            logger.error(f"whisper-cli failed (exit {result.returncode}): {result.stderr}")
            raise RuntimeError(f"whisper-cli failed: {result.stderr}")

        # Parse output - whisper-cli outputs text directly with -np flag
        text = result.stdout.strip()

        # Detect language from stderr (whisper prints "auto-detected language: xx")
        language = "unknown"
        for line in result.stderr.split("\n"):
            if "auto-detected language:" in line.lower():
                # Extract language code
                parts = line.split(":")
                if len(parts) >= 2:
                    language = parts[-1].strip().split()[0].lower()
                break

        logger.info(f"Transcribed in {duration_ms}ms, language={language}, text_len={len(text)}")
        logger.debug(f"whisper stderr: {result.stderr[:500]}" if result.stderr else "whisper stderr: (empty)")

        return {
            "text": text,
            "language": language,
            "duration_ms": duration_ms,
        }

    finally:
        # Clean up converted file
        if converted and audio_path.exists():
            audio_path.unlink(missing_ok=True)
            logger.debug(f"Cleaned up converted file {audio_path}")
