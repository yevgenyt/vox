"""Whisper.cpp wrapper for transcription."""

import os
import subprocess
import tempfile
import time
from pathlib import Path


WHISPER_CLI = os.environ.get("WHISPER_CLI", "/opt/whisper.cpp/build/bin/whisper-cli")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "/opt/whisper.cpp/models/ggml-small.bin")


def transcribe(audio_path: str | Path) -> dict:
    """
    Transcribe audio file using whisper.cpp.

    Args:
        audio_path: Path to audio file (WAV preferred)

    Returns:
        dict with keys: text, language, duration_ms
    """
    audio_path = Path(audio_path)

    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    start_time = time.time()

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

    return {
        "text": text,
        "language": language,
        "duration_ms": duration_ms,
    }
