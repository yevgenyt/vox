# Voice Transcriber Server

Client-server architecture for voice transcription using whisper.cpp.

## Overview

A containerized transcription service that accepts audio and returns text, with lightweight clients for local and LAN use.

```
┌─────────────────────────────────────────────┐
│  Docker Container                           │
│  ├─ FastAPI server                          │
│  ├─ whisper.cpp + Vulkan GPU                │
│  └─ POST /transcribe (audio → text)         │
│     Port: 5000                              │
└─────────────────────────────────────────────┘
        ▲
        │ HTTP
┌───────┴───────┬───────────────────┐
│ Local client  │  LAN clients      │
└───────────────┴───────────────────┘
```

## Components

### Server (Docker)
- **API**: FastAPI with single `/transcribe` endpoint
- **Transcription**: whisper.cpp with Vulkan GPU acceleration
- **Model**: ggml-small.bin (multilingual, auto-detect language)

### Client (Lightweight Python)
- **Hotkey**: Left Alt + Right Alt (evdev)
- **Audio capture**: sounddevice
- **Paste**: wl-copy + xclip + ydotool

---

## Knowledge Transfer from voice-transcriber

### whisper.cpp Setup

**Installation** (already done on host):
```bash
cd ~/Applications/whisper.cpp
cmake -B build -DGGML_VULKAN=1
cmake --build build -j
```

**CLI flags that work well**:
```bash
whisper-cli \
  -m ~/Applications/whisper.cpp/models/ggml-small.bin \
  -f audio.wav \
  -l auto \      # Auto-detect language (supports 90+ languages)
  -bs 1 \        # Greedy decoding (faster)
  -nt \          # No timestamps
  -np            # No prints (clean output)
```

**Models**:
- `ggml-small.en.bin` - English only, slightly faster
- `ggml-small.bin` - Multilingual with auto-detection (recommended)
- Location: `~/Applications/whisper.cpp/models/`

### Audio Processing Lessons

**Sample rates**:
- Microphone capture: 48000 Hz (native)
- Whisper input: 16000 Hz (required)
- Must resample with `scipy.signal.resample_poly()` to avoid aliasing

**Recording settings that work**:
```python
stream = sd.InputStream(
    device=audio_device,
    samplerate=48000,
    channels=device_channels,
    blocksize=8192,
    dtype=np.float32,
    latency="high"  # Prevents buffer overflows
)
```

**Audio processing pipeline**:
1. Convert to mono (average channels or take first)
2. Resample 48kHz → 16kHz
3. Apply noise gate (fast attack 5ms, slow release 300ms)
4. Normalize to 0.95 peak
5. Apply gain (default 0.77x, auto-adjusts)
6. Save as 16-bit PCM WAV

**Noise gate settings**:
```python
NOISE_GATE_ATTACK_MS = 5      # Fast attack
NOISE_GATE_RELEASE_MS = 300   # Slow release
NOISE_GATE_THRESHOLD = 2.0    # Multiplier of noise floor
NOISE_GATE_REDUCTION = 0.1    # -20dB when closed
```

### Clipboard & Paste (Client-side)

**Dual clipboard required** (Wayland + XWayland):
```python
# Wayland native apps
subprocess.Popen(["wl-copy", "--type", "text/plain", "--", text])

# XWayland apps (Electron/Cursor)
xclip_proc = subprocess.Popen(
    ["xclip", "-selection", "clipboard"],
    stdin=subprocess.PIPE
)
xclip_proc.communicate(input=text.encode("utf-8"))
```

**Auto-paste with ydotool**:
```python
# Wait for clipboard, then paste
time.sleep(0.2)
subprocess.run([
    "ydotool", "key",
    "--delay", "100",
    "--key-delay", "20",
    "ctrl+shift+v"  # Works in Cursor/Electron
])
```

**Key discoveries**:
- Cursor needs `Ctrl+Shift+V`, not `Ctrl+V`
- ydotool needs delays for reliability without ydotoold daemon
- Hotkey must not include Shift to avoid paste conflicts

### Hotkey Detection (Client-side)

**evdev approach** (works on Wayland):
```python
from evdev import InputDevice, ecodes

KEY_LEFTALT = ecodes.KEY_LEFTALT
KEY_RIGHTALT = ecodes.KEY_RIGHTALT

# In event loop:
if key_event == 1:  # Press
    if event.code == KEY_LEFTALT:
        pressed_keys.add(KEY_LEFTALT)
    elif event.code == KEY_RIGHTALT:
        if KEY_LEFTALT in pressed_keys:
            start_recording()

elif key_event == 0:  # Release
    if event.code == KEY_RIGHTALT:
        if is_recording:
            stop_recording()
```

**Requirements**:
- User must be in `input` group: `sudo usermod -a -G input $USER`

### What Didn't Work

1. **ydotool for typing text** - Unreliable, no Unicode support
2. **wtype** - Doesn't work on GNOME (missing Wayland protocol)
3. **Streaming mode (whisper-stream)** - Too complex, batch mode more reliable
4. **Ctrl+V in Cursor** - Shows "no image", needs Ctrl+Shift+V
5. **wl-copy alone** - XWayland apps don't see Wayland clipboard

---

## File Structure

```
voice-transcriber-server/
├── PROJECT.md              # This file
├── docker/
│   ├── Dockerfile          # whisper.cpp + FastAPI + Vulkan
│   └── docker-compose.yml  # Easy startup with GPU passthrough
├── server/
│   ├── server.py           # FastAPI application
│   ├── transcriber.py      # whisper.cpp wrapper
│   └── requirements.txt    # FastAPI, python-multipart
├── client/
│   ├── client.py           # Hotkey + record + send + paste
│   └── requirements.txt    # sounddevice, requests, evdev
└── README.md               # User documentation
```

## API Contract

### POST /transcribe

**Request**:
- Content-Type: `multipart/form-data`
- Body: `audio` file (WAV, 16kHz mono preferred)

**Response**:
```json
{
  "text": "Transcribed text here.",
  "language": "en",
  "duration_ms": 1234
}
```

**Example**:
```bash
curl -X POST http://localhost:5000/transcribe \
  -F "audio=@recording.wav"
```

## Docker GPU Passthrough (AMD Vulkan)

```yaml
# docker-compose.yml
services:
  transcriber:
    build: .
    ports:
      - "5000:5000"
    devices:
      - /dev/dri:/dev/dri      # GPU access
      - /dev/kfd:/dev/kfd      # AMD ROCm (if needed)
    group_add:
      - video
      - render
```

## Next Steps

1. [ ] Create Dockerfile with whisper.cpp + Vulkan
2. [ ] Implement FastAPI server
3. [ ] Extract and simplify client from voice-transcriber
4. [ ] Test locally
5. [ ] Test from LAN client
