# Vox Development Lessons Learned

Decisions and gotchas to avoid repeating mistakes.

---

## Clipboard & Paste

### What works
- **ydotool** with BOTH shortcuts - send both to cover all apps
- **wl-copy** + **xclip** together - covers both Wayland and XWayland apps

### What doesn't work
- **wtype** - GNOME doesn't support virtual keyboard protocol
- **ydotool with keycodes** (e.g., `29:1 47:1`) - types numbers instead of sending keys
- **Ctrl+V alone** - doesn't work in Cursor/Electron
- **Ctrl+Shift+V alone** - doesn't work in Wine/Notepad++

### Paste function (working version)
Send BOTH shortcuts - one will work, the other is ignored.
**Use longer delays** to avoid timing issues (Shift+V instead of Ctrl+Shift+V):
```python
time.sleep(0.3)  # Wait before pasting
# Ctrl+Shift+V for Electron apps (Cursor, VS Code)
subprocess.run(["ydotool", "key", "--delay", "150", "--key-delay", "50", "ctrl+shift+v"], ...)
time.sleep(0.15)  # Wait between attempts
# Ctrl+V for regular apps (Notepad++, Wine, native apps)
subprocess.run(["ydotool", "key", "--delay", "150", "--key-delay", "50", "ctrl+v"], ...)
```

---

## Keyboard Detection

### Problem
Logitech MX Master 2S mouse reports having:
- Alt keys (KEY_LEFTALT, KEY_RIGHTALT)
- Letter keys (KEY_A, KEY_Z, KEY_SPACE)
- Relative axes (EV_REL)

The keyboard (WK75 BT1) also reports having EV_REL.

### Solution
Filter by device **name** - skip devices containing "mouse", "touchpad", "trackpad".

### What doesn't work
- Filtering by EV_REL - keyboards can have it too
- Filtering by letter keys - some mice report having them

---

## Audio Stats

### SNR Calculation
Use **percentile-based** method (20th/80th) not mean-based:
- More stable across different speech patterns
- Less affected by silence/speech ratio

### Quality thresholds
- <10dB: Noisy
- 10-20dB: OK
- 20-30dB: Good
- >30dB: Excellent

---

## Container (Podman)

### GPU passthrough
Use numeric group IDs, not names (container doesn't have render/video groups):
```bash
--group-add 44 --group-add 991
```

### Required packages for Vulkan build
- `glslc` (not just `glslang-tools`)

---

## Client Script (vox)

### Symlink resolution
Must use `readlink -f` to resolve symlinks when running from ~/.local/bin:
```bash
SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
SCRIPT_DIR="$(dirname "$SCRIPT_PATH")"
```

---

## Keyboard Reconnection

### Problem
When keyboard disconnects, `read_loop()` raises `OSError: [Errno 19] No such device`.

### Solution
Wrap the event loop in try/except, reset state, and poll for keyboard reconnection:
```python
while True:
    try:
        for event in keyboard.read_loop():
            ...
    except OSError:
        # Reset state, wait, find_keyboard() again
```

---

## ydotool Timing Issues

### Problem
With short delays, `ctrl+shift+v` becomes `Shift+V` (types uppercase V).

### Solution
Use longer delays:
- `--delay 150` (wait before pressing)
- `--key-delay 50` (between key events)
- `time.sleep(0.3)` before starting
- `time.sleep(0.15)` between paste attempts

---

## Transcription Output

### Trailing space
Add trailing space after transcribed text so it doesn't blend with follow-up typing:
```python
copy_to_clipboard(text + " ")
```

---

## Environment

- **Session**: Wayland (GNOME)
- **Podman** instead of Docker
- User must be in `input` group for evdev keyboard access
- **ydotoold** not required but may improve reliability
