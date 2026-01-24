# Vox Transcription API - Integration Guide

API documentation for external clients connecting to the Vox transcription service.

## Connection Details

| Setting | Value |
|---------|-------|
| **Tailscale IP** | `100.74.245.36` |
| **Port** | `5000` |
| **Base URL** | `http://100.74.245.36:5000` |

**Note**: Use the Tailscale IP for cross-segment network access. Clients must have Tailscale installed and be on the same Tailscale network.

---

## Endpoints

### Health Check

Verify the service is running.

```
GET /health
```

**Response**:
```json
{"status": "ok"}
```

**Example**:
```bash
curl http://100.74.245.36:5000/health
```

---

### Transcribe Audio

Transcribe an audio file to text.

```
POST /transcribe
POST /transcribe?debug=true
```

**Request**:
- Content-Type: `multipart/form-data`
- Body: `audio` field containing the audio file

**Query Parameters**:
| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `debug` | boolean | `false` | Include server-side processing logs in response |

**Supported Formats**:
- WAV (preferred, 16kHz mono)
- MP3
- OGG
- FLAC
- M4A
- AAC
- WMA
- OPUS

**Response**:
```json
{
  "text": "Transcribed text here.",
  "language": "en",
  "duration_ms": 1234
}
```

**Fields**:
| Field | Type | Description |
|-------|------|-------------|
| `text` | string | Transcribed text |
| `language` | string | Detected language code (e.g., "en", "de", "es") |
| `duration_ms` | integer | Processing time in milliseconds |
| `logs` | array | *(Only with `?debug=true`)* Server-side processing logs |

---

## Examples

### cURL

```bash
# Transcribe a WAV file
curl -X POST http://100.74.245.36:5000/transcribe \
  -F "audio=@recording.wav"

# Transcribe an MP3 file
curl -X POST http://100.74.245.36:5000/transcribe \
  -F "audio=@voice_message.mp3"

# Transcribe with debug logs (for troubleshooting)
curl -X POST "http://100.74.245.36:5000/transcribe?debug=true" \
  -F "audio=@recording.wav"
```

### Python

```python
import requests

def transcribe(audio_path: str) -> dict:
    url = "http://100.74.245.36:5000/transcribe"

    with open(audio_path, "rb") as f:
        response = requests.post(
            url,
            files={"audio": f},
            timeout=120
        )

    response.raise_for_status()
    return response.json()

# Usage
result = transcribe("recording.wav")
print(result["text"])
```

### Node.js

```javascript
const FormData = require('form-data');
const fs = require('fs');
const axios = require('axios');

async function transcribe(audioPath) {
  const form = new FormData();
  form.append('audio', fs.createReadStream(audioPath));

  const response = await axios.post(
    'http://100.74.245.36:5000/transcribe',
    form,
    {
      headers: form.getHeaders(),
      timeout: 120000
    }
  );

  return response.data;
}

// Usage
transcribe('recording.wav').then(result => {
  console.log(result.text);
});
```

### n8n Workflow

For n8n integration, use the HTTP Request node:

1. **Method**: POST
2. **URL**: `http://100.74.245.36:5000/transcribe`
   - For debugging: `http://100.74.245.36:5000/transcribe?debug=true`
3. **Body Content Type**: Multipart Form Data
4. **Body Parameters**:
   - Parameter Type: `Form Binary Data`
   - Name: `audio`
   - Input Data Field Name: *(name of the binary field from previous node)*

**Troubleshooting n8n**: Add `?debug=true` to the URL to see server logs in the response, which helps diagnose issues with audio file handling.

---

## Limits

| Limit | Value |
|-------|-------|
| Max file size | 25 MB |
| Request timeout | 120 seconds |

---

## Error Responses

| Status | Meaning |
|--------|---------|
| 400 | Bad request (missing or invalid file) |
| 413 | File too large (>25 MB) |
| 422 | Validation error (missing `audio` field) |
| 500 | Server error (transcription failed) |

**Error format**:
```json
{
  "detail": "Error message here"
}
```

---

## Network Requirements

1. **Tailscale** must be installed on the client machine
2. Client must be authenticated to the same Tailscale network
3. Verify connectivity: `ping 100.74.245.36`
4. Test service: `curl http://100.74.245.36:5000/health`

### Installing Tailscale

```bash
# Ubuntu/Debian
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Follow the authentication URL to join the network.
