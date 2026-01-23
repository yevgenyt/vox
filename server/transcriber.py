"""Whisper.cpp wrapper for transcription."""

import os
import subprocess
import tempfile
import time
from pathlib import Path


WHISPER_CLI = os.environ.get("WHISPER_CLI", "/opt/whisper.cpp/build/bin/whisper-cli")
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "/opt/whisper.cpp/models/ggml-small.bin")

# Formats that need conversion (whisper.cpp expects 16kHz mono WAV)
NEEDS_CONVERSION = {".mp3", ".ogg", ".flac", ".m4a", ".aac", ".wma", ".opus"}


def convert_to_wav(audio_path: Path) -> Path:
    """Convert audio file to 16kHz mono WAV using ffmpeg."""
    wav_path = audio_path.with_suffix(".wav")

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
        raise RuntimeError(f"ffmpeg conversion failed: {result.stderr.decode()}")

    return wav_path


def transcribe(audio_path: str | Path) -> dict:
    """
    Transcribe audio file using whisper.cpp.

    Args:
        audio_path: Path to audio file (WAV, MP3, OGG, etc.)

    Returns:
        dict with keys: text, language, duration_ms
    """
    audio_path = Path(audio_path)

    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    # Convert to WAV if needed
    converted = False
    if audio_path.suffix.lower() in NEEDS_CONVERSION:
        audio_path = convert_to_wav(audio_path)
        converted = True

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

    finally:
        # Clean up converted file
        if converted and audio_path.exists():
            audio_path.unlink(missing_ok=True)
