# Vox Client - Voice Transcription Hotkey Tool

Lightweight voice transcription client that captures audio via hotkey, sends it to a transcription server, and pastes the result.

## Features

- **Hotkey activation**: Left Alt + Right Alt (hold to record, release to transcribe)
- **Audio processing**: Noise gate, normalization, resampling
- **Auto-paste**: Copies to clipboard and pastes via ydotool
- **Wayland compatible**: Works on GNOME Wayland via evdev
- **Audio quality stats**: Peak, SNR, quality indicators

## Quick Start

### 1. Install system dependencies

```bash
# Ubuntu/Debian
sudo apt install wl-copy xclip ydotool

# Add yourself to input group (required for evdev keyboard access)
sudo usermod -a -G input $USER
# Log out and back in for group change to take effect
```

### 2. Set up Python environment

```bash
cd vox-client
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

### 3. Configure server URL

Edit the `vox` launcher script and set `DEFAULT_SERVER` to your transcription server:

```bash
DEFAULT_SERVER="http://192.168.1.100:5000"
```

### 4. Run

```bash
./vox
```

## Usage

1. Press and hold **Left Alt**
2. While holding Left Alt, press **Right Alt** to start recording
3. Speak into the microphone
4. Release **Right Alt** to stop recording and transcribe
5. Text is automatically copied to clipboard and pasted

### Command-line options

```bash
./vox --help
./vox --server http://192.168.1.100:5000  # Custom server
./vox --no-paste                           # Don't auto-paste
./vox --list-devices                       # List audio devices
./vox --device 3                           # Use specific audio device
```

## Installation (Optional)

### Global command

Create a symlink for global access:

```bash
ln -s $(pwd)/vox ~/.local/bin/vox
```

### Systemd user service

```bash
# Copy and edit the service file
cp config/vox.service ~/.config/systemd/user/
# Edit the ExecStart path in the service file

# Enable and start
systemctl --user daemon-reload
systemctl --user enable --now vox
```

### Desktop entry

```bash
# Copy and edit the desktop file
cp config/vox.desktop ~/.local/share/applications/
# Edit the Exec path in the desktop file
```

## Server API

The client expects a transcription server with this endpoint:

```
POST /transcribe
Content-Type: multipart/form-data
Body: audio=<wav file>

Response:
{
  "text": "Transcribed text",
  "language": "en",
  "duration_ms": 1234
}
```

Compatible with the [Vox Server](https://github.com/yevgenyt/vox) or any server implementing this API.

## Audio Processing Pipeline

1. Capture at 48kHz (microphone native)
2. Convert to mono
3. Resample to 16kHz (whisper requirement)
4. Apply noise gate (5ms attack, 300ms release)
5. Normalize to 0.95 peak
6. Apply gain (0.77x default)
7. Send as 16-bit PCM WAV

## Requirements

### System
- Linux with Wayland (tested on GNOME)
- Python 3.10+
- User in `input` group

### System packages
- `wl-copy` - Wayland clipboard
- `xclip` - XWayland clipboard (for Electron apps)
- `ydotool` - Keyboard simulation for paste

### Python packages
See `requirements.txt`:
- sounddevice - Audio capture
- numpy, scipy - Audio processing
- requests - HTTP client
- evdev - Keyboard hotkey detection

## Troubleshooting

### "No keyboard found"
Add yourself to the `input` group and log out/in:
```bash
sudo usermod -a -G input $USER
```

### Paste not working
Make sure ydotool is installed. The client sends both `Ctrl+Shift+V` (for Electron apps) and `Ctrl+V` (for native apps).

### Audio device issues
List available devices and select one:
```bash
./vox --list-devices
./vox --device 3
```

## Files

```
vox-client/
├── client.py           # Main client application
├── vox                 # Launcher script
├── requirements.txt    # Python dependencies
├── README.md          # This file
├── LESSONS.md         # Development lessons learned
└── config/
    ├── vox.service    # Systemd user service template
    └── vox.desktop    # Desktop entry template
```

## License

MIT
