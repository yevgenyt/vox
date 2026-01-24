# Vox - Voice Transcriber

Containerized voice transcription service using whisper.cpp with Vulkan GPU acceleration.

## Quick Start

### Server

```bash
cd docker
podman-compose up -d
```

Or manually:
```bash
podman build -t vox -f docker/Dockerfile .
podman run -d --name vox --network host \
  --device /dev/dri \
  --group-add 44 --group-add 991 \
  vox
```

### Client (Vox)

First-time setup:
```bash
cd client
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

Run from anywhere:
```bash
vox
```

Or from the client directory:
```bash
./vox
```

## Usage

1. Start the server container
2. Run Vox client
3. Press **Left Alt + Right Alt** to record
4. Release **Right Alt** to transcribe and paste

## Running as a Service

Once stable, enable Vox to start automatically on login:

```bash
systemctl --user enable --now vox
```

Manage the service:
```bash
systemctl --user status vox    # Check status
systemctl --user stop vox      # Stop
systemctl --user start vox     # Start
systemctl --user restart vox   # Restart
journalctl --user -u vox -f    # View logs
```

To disable and run manually again:
```bash
systemctl --user disable --now vox
./vox
```

## Requirements

### Server
- Podman with GPU support
- AMD GPU with Vulkan (or modify for NVIDIA/CUDA)

### Client
- Python 3.10+
- User in `input` group: `sudo usermod -a -G input $USER` (then log out/in)
- System packages: `wl-copy`, `xclip`, `ydotool`

## API

```bash
curl -X POST http://localhost:5000/transcribe \
  -F "audio=@recording.wav"
```

Response:
```json
{
  "text": "Hello world.",
  "language": "en",
  "duration_ms": 850
}
```

### Debug Mode

Add `?debug=true` to include server-side logs in the response:

```bash
curl -X POST "http://localhost:5000/transcribe?debug=true" \
  -F "audio=@recording.wav"
```

Response with logs:
```json
{
  "text": "Hello world.",
  "language": "en",
  "duration_ms": 850,
  "logs": [
    "10:30:45 INFO Received file: recording.wav",
    "10:30:45 INFO Saved 12345 bytes to /tmp/tmpXXXX.wav",
    "10:30:45 INFO Processing /tmp/tmpXXXX.wav (12345 bytes)",
    "10:30:45 INFO Running whisper-cli with model ggml-small.bin",
    "10:30:46 INFO Transcribed in 850ms, language=en, text_len=12"
  ]
}
```

## Architecture

See [PROJECT.md](PROJECT.md) for detailed architecture and knowledge transfer from the standalone version.
