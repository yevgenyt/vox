#!/usr/bin/env python3
"""
Voice transcriber client.

Hotkey: Left Alt + Right Alt (hold to record, release Right Alt to transcribe)
Requires: User in 'input' group for evdev access
"""

import argparse
import io
import subprocess
import sys
import tempfile
import threading
import time
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


def process_audio(audio: np.ndarray, channels: int) -> np.ndarray:
    """
    Process audio for whisper.

    Pipeline: mono -> resample -> noise gate -> normalize -> gain
    """
    if audio.size == 0:
        return audio

    # Convert to mono
    if channels > 1:
        audio = np.mean(audio, axis=1)
    else:
        audio = audio.flatten()

    # Resample 48kHz -> 16kHz
    audio = resample_poly(audio, SAMPLE_RATE_WHISPER, SAMPLE_RATE_CAPTURE)

    # Apply noise gate
    audio = apply_noise_gate(audio)

    # Normalize to peak
    peak = np.max(np.abs(audio))
    if peak > 0:
        audio = audio * (NORMALIZE_PEAK / peak)

    # Apply gain
    audio = audio * DEFAULT_GAIN

    # Clip to valid range
    audio = np.clip(audio, -1.0, 1.0)

    return audio


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
    # Wayland native
    try:
        subprocess.Popen(
            ["wl-copy", "--type", "text/plain", "--", text],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        pass

    # XWayland (for Electron apps like Cursor)
    try:
        proc = subprocess.Popen(
            ["xclip", "-selection", "clipboard"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        proc.communicate(input=text.encode("utf-8"))
    except FileNotFoundError:
        pass


def paste():
    """Paste from clipboard using ydotool."""
    time.sleep(0.2)
    try:
        subprocess.run(
            ["ydotool", "key", "--delay", "100", "--key-delay", "20", "ctrl+shift+v"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except FileNotFoundError:
        print("Warning: ydotool not found, skipping auto-paste", file=sys.stderr)
    except subprocess.TimeoutExpired:
        pass


def find_keyboard() -> InputDevice | None:
    """Find keyboard device for hotkey detection."""
    for path in list_devices():
        try:
            device = InputDevice(path)
            caps = device.capabilities()

            # Check if device has key events
            if ecodes.EV_KEY in caps:
                keys = caps[ecodes.EV_KEY]
                # Look for Alt keys
                if ecodes.KEY_LEFTALT in keys and ecodes.KEY_RIGHTALT in keys:
                    return device
        except (PermissionError, OSError):
            continue

    return None


def run_client(server_url: str, device: int | None, auto_paste: bool):
    """Main client loop."""
    print(f"Server: {server_url}")
    print("Finding keyboard...")

    keyboard = find_keyboard()
    if keyboard is None:
        print("Error: No keyboard found. Make sure you're in the 'input' group:", file=sys.stderr)
        print("  sudo usermod -a -G input $USER", file=sys.stderr)
        print("  (then log out and back in)", file=sys.stderr)
        sys.exit(1)

    print(f"Keyboard: {keyboard.name}")
    print("Hotkey: Left Alt + Right Alt (hold to record)")
    print("Ready.\n")

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
                            print("Recording...", end="", flush=True)
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
                                print(" (empty)")
                                continue

                            duration = len(audio) / SAMPLE_RATE_CAPTURE
                            print(f" {duration:.1f}s")

                            # Process audio
                            print("Processing...", end="", flush=True)
                            processed = process_audio(audio, recorder.channels)
                            print(" done")

                            # Transcribe
                            print("Transcribing...", end="", flush=True)
                            try:
                                result = transcribe(processed, server_url)
                                text = result.get("text", "").strip()
                                lang = result.get("language", "?")
                                ms = result.get("duration_ms", 0)

                                print(f" [{lang}] {ms}ms")

                                if text:
                                    print(f">>> {text}\n")

                                    # Copy and paste (add trailing space for follow-up text)
                                    copy_to_clipboard(text + " ")
                                    if auto_paste:
                                        paste()
                                else:
                                    print("(no speech detected)\n")

                            except requests.RequestException as e:
                                print(f" Error: {e}", file=sys.stderr)

        except OSError as e:
            # Keyboard disconnected
            print(f"\nKeyboard disconnected: {e}", file=sys.stderr)
            print("Attempting to reconnect...", file=sys.stderr)

            # Reset state
            pressed_keys.clear()
            if is_recording:
                is_recording = False
                recorder.stop()

            # Try to find keyboard again
            time.sleep(2)
            keyboard = find_keyboard()
            while keyboard is None:
                print("Waiting for keyboard...", file=sys.stderr)
                time.sleep(3)
                keyboard = find_keyboard()

            print(f"Reconnected: {keyboard.name}")
            print("Ready.\n")


def main():
    parser = argparse.ArgumentParser(description="Voice transcriber client")
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

    args = parser.parse_args()

    if args.list_devices:
        print("Audio input devices:")
        devices = sd.query_devices()
        for i, dev in enumerate(devices):
            if dev["max_input_channels"] > 0:
                print(f"  {i}: {dev['name']}")
        return

    run_client(
        server_url=args.server,
        device=args.device,
        auto_paste=not args.no_paste,
    )


if __name__ == "__main__":
    main()
