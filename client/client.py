#!/usr/bin/env python3
"""
Vox client - Voice transcription hotkey tool.

Hotkey: Left Alt + Right Alt (hold to record, release Right Alt to transcribe)
Requires: User in 'input' group for evdev access
"""

import argparse
import atexit
import fcntl
import gc
import io
import os
import resource
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import wave
from pathlib import Path

import numpy as np
import requests
import sounddevice as sd
from evdev import InputDevice, ecodes, list_devices
from scipy.signal import resample_poly


# Audio settings
SAMPLE_RATE_CAPTURE = 48000
SAMPLE_RATE_WHISPER = 16000
BLOCK_SIZE = 8192

# Noise gate settings (from PROJECT.md)
NOISE_GATE_ATTACK_MS = 5
NOISE_GATE_RELEASE_MS = 300
NOISE_GATE_THRESHOLD = 2.0
NOISE_GATE_REDUCTION = 0.1

# Processing settings
NORMALIZE_PEAK = 0.95
DEFAULT_GAIN = 0.77

# Lock file to prevent multiple instances
LOCK_FILE = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "vox-client.lock"
_lock_file_handle = None


def _get_memory_mb() -> float:
    """Get current RSS memory usage in MB."""
    usage = resource.getrusage(resource.RUSAGE_SELF)
    return usage.ru_maxrss / 1024  # ru_maxrss is in KB on Linux


def _log_memory(context: str = ""):
    """Log memory usage with context."""
    mem_mb = _get_memory_mb()
    prefix = f"[{context}] " if context else ""
    print(f"📈 {prefix}Memory: {mem_mb:.1f}MB", file=sys.stderr, flush=True)


def _signal_handler(signum, frame):
    """Log signal receipt before exiting."""
    sig_name = signal.Signals(signum).name
    print(f"\n⚠️  Received {sig_name} (signal {signum})", file=sys.stderr)
    print(f"   Stack trace:", file=sys.stderr)
    traceback.print_stack(frame, file=sys.stderr)
    print(f"   Exiting gracefully...", file=sys.stderr)
    sys.exit(0)


def _setup_signal_handlers():
    """Set up handlers for termination signals."""
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGHUP, _signal_handler)


def acquire_lock() -> bool:
    """Acquire exclusive lock to prevent multiple instances.

    Returns:
        True if lock acquired, False if another instance is running.
    """
    global _lock_file_handle
    try:
        _lock_file_handle = open(LOCK_FILE, "w")
        fcntl.flock(_lock_file_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_file_handle.write(str(os.getpid()))
        _lock_file_handle.flush()
        atexit.register(release_lock)
        return True
    except (IOError, OSError):
        if _lock_file_handle:
            _lock_file_handle.close()
            _lock_file_handle = None
        return False


def release_lock():
    """Release the lock file."""
    global _lock_file_handle
    if _lock_file_handle:
        try:
            fcntl.flock(_lock_file_handle, fcntl.LOCK_UN)
            _lock_file_handle.close()
            LOCK_FILE.unlink(missing_ok=True)
        except (IOError, OSError):
            pass
        _lock_file_handle = None


class AudioRecorder:
    """Records audio from microphone."""

    def __init__(self, device=None):
        self.device = device
        self.recording = False
        self.audio_chunks = []
        self.stream = None
        self.channels = None

    def start(self):
        """Start recording."""
        self.audio_chunks = []
        self.recording = True

        # Get device info
        if self.device is not None:
            info = sd.query_devices(self.device)
        else:
            info = sd.query_devices(kind="input")

        self.channels = info["max_input_channels"]

        self.stream = sd.InputStream(
            device=self.device,
            samplerate=SAMPLE_RATE_CAPTURE,
            channels=self.channels,
            blocksize=BLOCK_SIZE,
            dtype=np.float32,
            latency="high",
            callback=self._callback,
        )
        self.stream.start()

    def _callback(self, indata, frames, time_info, status):
        """Audio stream callback."""
        if self.recording:
            self.audio_chunks.append(indata.copy())

    def stop(self) -> np.ndarray:
        """Stop recording and return audio data."""
        self.recording = False

        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None

        if not self.audio_chunks:
            return np.array([], dtype=np.float32)

        return np.concatenate(self.audio_chunks, axis=0)


def process_audio(audio: np.ndarray, channels: int) -> tuple[np.ndarray, dict]:
    """
    Process audio for whisper.

    Pipeline: mono -> resample -> noise gate -> normalize -> gain

    Returns:
        Tuple of (processed_audio, stats_dict)
    """
    stats = {"peak": 0.0, "snr_db": 0.0, "quality": "Empty"}

    if audio.size == 0:
        return audio, stats

    # Convert to mono
    if channels > 1:
        audio = np.mean(audio, axis=1)
    else:
        audio = audio.flatten()

    # Resample 48kHz -> 16kHz
    audio = resample_poly(audio, SAMPLE_RATE_WHISPER, SAMPLE_RATE_CAPTURE)

    # Calculate stats before processing
    peak = np.max(np.abs(audio))
    stats["peak"] = peak

    # Calculate SNR (signal-to-noise ratio)
    # Method: compare 20th percentile (noise floor) vs 80th percentile (signal)
    # This is more stable than mean-based approaches
    frame_size = int(SAMPLE_RATE_WHISPER * 0.02)  # 20ms frames
    n_frames = len(audio) // frame_size
    if n_frames >= 10:
        frame_rms = np.array([
            np.sqrt(np.mean(audio[i * frame_size : (i + 1) * frame_size] ** 2))
            for i in range(n_frames)
        ])
        # Use percentiles for stability
        noise_floor = np.percentile(frame_rms, 20)  # 20th percentile = quiet parts
        signal_level = np.percentile(frame_rms, 80)  # 80th percentile = loud parts

        # Clamp noise floor to avoid division by zero or unrealistic values
        noise_floor = max(noise_floor, 1e-6)

        snr = signal_level / noise_floor
        stats["snr_db"] = 20 * np.log10(snr)

        # Also store raw levels for debugging
        stats["noise_floor"] = noise_floor
        stats["signal_level"] = signal_level

    # Determine quality based on both peak and SNR
    # SNR benchmarks: <10dB noisy, 10-20dB acceptable, >20dB good, >30dB excellent
    if peak < 0.01:
        stats["quality"] = "Silent"
    elif peak < 0.05:
        stats["quality"] = "Too quiet"
    elif stats["snr_db"] < 10:
        stats["quality"] = "Noisy"
    elif stats["snr_db"] < 20:
        stats["quality"] = "OK"
    elif stats["snr_db"] < 30:
        stats["quality"] = "Good"
    else:
        stats["quality"] = "Excellent"

    # Apply noise gate
    audio = apply_noise_gate(audio)

    # Normalize to peak
    if peak > 0:
        audio = audio * (NORMALIZE_PEAK / peak)

    # Apply gain
    audio = audio * DEFAULT_GAIN

    # Clip to valid range
    audio = np.clip(audio, -1.0, 1.0)

    return audio, stats


def apply_noise_gate(audio: np.ndarray) -> np.ndarray:
    """Apply noise gate with fast attack, slow release."""
    if audio.size == 0:
        return audio

    # Calculate noise floor from quietest 10% of frames
    frame_size = int(SAMPLE_RATE_WHISPER * 0.02)  # 20ms frames
    n_frames = len(audio) // frame_size

    if n_frames < 10:
        return audio

    frame_levels = []
    for i in range(n_frames):
        frame = audio[i * frame_size : (i + 1) * frame_size]
        frame_levels.append(np.sqrt(np.mean(frame**2)))

    frame_levels.sort()
    noise_floor = np.mean(frame_levels[: max(1, n_frames // 10)])
    threshold = noise_floor * NOISE_GATE_THRESHOLD

    # Apply gate with attack/release
    attack_samples = int(SAMPLE_RATE_WHISPER * NOISE_GATE_ATTACK_MS / 1000)
    release_samples = int(SAMPLE_RATE_WHISPER * NOISE_GATE_RELEASE_MS / 1000)

    gate_level = NOISE_GATE_REDUCTION
    output = np.zeros_like(audio)

    for i, sample in enumerate(audio):
        level = abs(sample)

        if level > threshold:
            # Fast attack
            gate_level = min(1.0, gate_level + 1.0 / attack_samples)
        else:
            # Slow release
            gate_level = max(NOISE_GATE_REDUCTION, gate_level - 1.0 / release_samples)

        output[i] = sample * gate_level

    return output


def save_wav(audio: np.ndarray, path: Path):
    """Save audio as 16-bit PCM WAV."""
    # Convert to 16-bit integers
    audio_int16 = (audio * 32767).astype(np.int16)

    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE_WHISPER)
        wf.writeframes(audio_int16.tobytes())


def transcribe(audio: np.ndarray, server_url: str) -> dict:
    """Send audio to server for transcription."""
    # Save to in-memory WAV
    audio_int16 = (audio * 32767).astype(np.int16)

    wav_buffer = io.BytesIO()
    with wave.open(wav_buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE_WHISPER)
        wf.writeframes(audio_int16.tobytes())

    wav_buffer.seek(0)

    # Send to server
    response = requests.post(
        f"{server_url}/transcribe",
        files={"audio": ("recording.wav", wav_buffer, "audio/wav")},
        timeout=120,
    )
    response.raise_for_status()

    return response.json()


def copy_to_clipboard(text: str):
    """Copy text to clipboard (Wayland + XWayland)."""
    # Wayland native - must complete before paste
    try:
        subprocess.run(
            ["wl-copy", "--type", "text/plain", "--", text],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # XWayland (for Electron apps like Cursor)
    try:
        proc = subprocess.Popen(
            ["xclip", "-selection", "clipboard"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        proc.communicate(input=text.encode("utf-8"), timeout=2)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass


def paste(method: str = "shift"):
    """Paste from clipboard using ydotool.

    Args:
        method: Paste method - 'shift' (Ctrl+Shift+V), 'standard' (Ctrl+V), or 'both'
    """
    time.sleep(0.3)
    try:
        if method in ("shift", "both"):
            # Ctrl+Shift+V for Electron apps (Cursor, VS Code) and plain text paste
            subprocess.run(
                ["ydotool", "key", "--delay", "150", "--key-delay", "50", "ctrl+shift+v"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            if method == "both":
                time.sleep(0.15)

        if method in ("standard", "both"):
            # Ctrl+V for regular apps (Notepad++, Wine, native apps)
            subprocess.run(
                ["ydotool", "key", "--delay", "150", "--key-delay", "50", "ctrl+v"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
    except FileNotFoundError:
        print("⚠️  ydotool not found, skipping auto-paste", file=sys.stderr)
    except subprocess.TimeoutExpired:
        pass


def find_keyboard() -> InputDevice | None:
    """Find keyboard device for hotkey detection."""
    for path in list_devices():
        try:
            device = InputDevice(path)
            name_lower = device.name.lower()

            # Skip devices that are clearly mice/touchpads
            if any(x in name_lower for x in ["mouse", "touchpad", "trackpad"]):
                continue

            caps = device.capabilities()

            # Check if device has key events
            if ecodes.EV_KEY in caps:
                keys = caps[ecodes.EV_KEY]
                # Must have Alt keys AND typical keyboard keys (letters)
                has_alt_keys = (
                    ecodes.KEY_LEFTALT in keys and
                    ecodes.KEY_RIGHTALT in keys
                )
                has_letter_keys = (
                    ecodes.KEY_A in keys and
                    ecodes.KEY_Z in keys and
                    ecodes.KEY_SPACE in keys
                )
                if has_alt_keys and has_letter_keys:
                    return device
        except (PermissionError, OSError):
            continue

    return None


def run_client(server_url: str, device: int | None, auto_paste: bool, paste_method: str = "shift"):
    """Main client loop."""
    print(f"🌐 Server: {server_url}")
    print("🔍 Finding keyboard...")

    keyboard = find_keyboard()
    if keyboard is None:
        print("❌ No keyboard found. Make sure you're in the 'input' group:", file=sys.stderr)
        print("   sudo usermod -a -G input $USER", file=sys.stderr)
        print("   (then log out and back in)", file=sys.stderr)
        sys.exit(1)

    print(f"⌨️  Keyboard: {keyboard.name}")
    print("🎯 Hotkey: Left Alt + Right Alt")
    print("✅ Ready\n")

    recorder = AudioRecorder(device)
    pressed_keys = set()
    is_recording = False

    while True:
        try:
            for event in keyboard.read_loop():
                if event.type != ecodes.EV_KEY:
                    continue

                key_event = event.value  # 0=release, 1=press, 2=hold

                if key_event == 1:  # Press
                    if event.code == ecodes.KEY_LEFTALT:
                        pressed_keys.add(ecodes.KEY_LEFTALT)

                    elif event.code == ecodes.KEY_RIGHTALT:
                        if ecodes.KEY_LEFTALT in pressed_keys and not is_recording:
                            # Start recording
                            is_recording = True
                            print("🎤 Recording... (release hotkey to stop)", end="", flush=True)
                            recorder.start()

                elif key_event == 0:  # Release
                    if event.code == ecodes.KEY_LEFTALT:
                        pressed_keys.discard(ecodes.KEY_LEFTALT)

                    elif event.code == ecodes.KEY_RIGHTALT:
                        if is_recording:
                            # Stop recording and transcribe
                            is_recording = False
                            audio = recorder.stop()

                            if audio.size == 0:
                                print("\r🎤 Recording... (empty)\n")
                                continue

                            duration = len(audio) / SAMPLE_RATE_CAPTURE
                            print(f"\r  🟢 [{duration:.1f}s]")

                            # Process audio
                            processed, stats = process_audio(audio, recorder.channels)

                            # Show audio stats
                            quality_icons = {
                                "Excellent": "✓",
                                "Good": "✓",
                                "OK": "○",
                                "Noisy": "⚠",
                                "Too quiet": "⚠",
                                "Silent": "✗",
                                "Empty": "✗",
                            }
                            icon = quality_icons.get(stats["quality"], "?")
                            noise_floor = stats.get("noise_floor", 0) * 1000  # Show as milli-units
                            print(f"📊 Peak: {stats['peak']:.3f} | Noise: {noise_floor:.2f}m | SNR: {stats['snr_db']:.1f}dB | {icon} {stats['quality']}")

                            # Transcribe
                            print("\n⏳ Transcribing...", end="", flush=True)
                            try:
                                result = transcribe(processed, server_url)
                                text = result.get("text", "").strip()

                                if text:
                                    print(f"\r📝 \"{text}\"\n")

                                    # Copy and paste (add trailing space for follow-up text)
                                    copy_to_clipboard(text + " ")
                                    if auto_paste:
                                        paste(paste_method)
                                else:
                                    print("\r📝 (no speech detected)\n")

                            except requests.RequestException as e:
                                print(f"\r❌ Error: {e}\n", file=sys.stderr)
                            finally:
                                # Free memory after transcription
                                del audio, processed
                                gc.collect()
                                _log_memory("post-transcribe")

        except OSError as e:
            # Keyboard disconnected
            print(f"\n⚠️  Keyboard disconnected: {e}", file=sys.stderr)
            print("🔄 Attempting to reconnect...", file=sys.stderr)

            # Reset state
            pressed_keys.clear()
            if is_recording:
                is_recording = False
                recorder.stop()

            # Try to find keyboard again
            time.sleep(2)
            keyboard = find_keyboard()
            if keyboard is None:
                print("⏳ Waiting for keyboard...", end="", file=sys.stderr, flush=True)
                while keyboard is None:
                    time.sleep(3)
                    keyboard = find_keyboard()
                    if keyboard is None:
                        print(".", end="", file=sys.stderr, flush=True)
                print(file=sys.stderr)  # Newline after dots

            print(f"⌨️  Reconnected: {keyboard.name}")
            print("✅ Ready\n")


def main():
    _setup_signal_handlers()

    parser = argparse.ArgumentParser(description="Vox - Voice transcription client")
    parser.add_argument(
        "--server",
        default="http://localhost:5000",
        help="Transcription server URL (default: http://localhost:5000)",
    )
    parser.add_argument(
        "--device",
        type=int,
        default=None,
        help="Audio input device index (default: system default)",
    )
    parser.add_argument(
        "--no-paste",
        action="store_true",
        help="Disable auto-paste after transcription",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List audio input devices and exit",
    )
    parser.add_argument(
        "--paste-method",
        choices=["shift", "standard", "both"],
        default="shift",
        help="Paste method: 'shift' (Ctrl+Shift+V, default), 'standard' (Ctrl+V), 'both' (old behavior)",
    )

    args = parser.parse_args()

    if args.list_devices:
        print("Audio input devices:")
        devices = sd.query_devices()
        for i, dev in enumerate(devices):
            if dev["max_input_channels"] > 0:
                print(f"  {i}: {dev['name']}")
        return

    if not acquire_lock():
        print("❌ Another instance of Vox client is already running.", file=sys.stderr)
        print(f"   Lock file: {LOCK_FILE}", file=sys.stderr)
        print("   Stop the other instance first, or delete the lock file if it's stale.", file=sys.stderr)
        sys.exit(1)

    run_client(
        server_url=args.server,
        device=args.device,
        auto_paste=not args.no_paste,
        paste_method=args.paste_method,
    )


if __name__ == "__main__":
    main()
