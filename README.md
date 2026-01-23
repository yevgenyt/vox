# Voice Transcriber Server

Containerized voice transcription service using whisper.cpp with Vulkan GPU acceleration.

## Quick Start

### Server

```bash
cd docker
docker-compose up -d
```

### Client

```bash
cd client
pip install -r requirements.txt
python client.py
```

## Usage

1. Start the server container
2. Run the client on any machine (local or LAN)
3. Press **Left Alt + Right Alt** to record
4. Release **Right Alt** to transcribe and paste

## Requirements

- Docker with GPU support
- AMD GPU with Vulkan (or modify for NVIDIA/CUDA)
- Python 3.10+

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

## Architecture

See [PROJECT.md](PROJECT.md) for detailed architecture and knowledge transfer from the standalone version.
