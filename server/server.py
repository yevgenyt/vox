"""FastAPI server for voice transcription."""

import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from transcriber import transcribe


# 25 MB limit (~5 min of 48kHz stereo audio)
MAX_UPLOAD_SIZE = 25 * 1024 * 1024

app = FastAPI(
    title="Voice Transcriber",
    description="Transcription service using whisper.cpp with Vulkan GPU acceleration",
    version="1.0.0",
)


@app.middleware("http")
async def limit_upload_size(request: Request, call_next):
    """Reject uploads larger than MAX_UPLOAD_SIZE."""
    if request.method == "POST":
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > MAX_UPLOAD_SIZE:
            return JSONResponse(
                status_code=413,
                content={"detail": f"File too large. Maximum size: {MAX_UPLOAD_SIZE // (1024*1024)} MB"}
            )
    return await call_next(request)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


@app.post("/transcribe")
async def transcribe_audio(audio: UploadFile = File(...)):
    """
    Transcribe uploaded audio file.

    Accepts audio file (WAV preferred, 16kHz mono recommended).
    Returns transcribed text with detected language.
    """
    # Validate file
    if not audio.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    # Save uploaded file to temp location
    suffix = Path(audio.filename).suffix or ".wav"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)
        try:
            # Write uploaded content
            content = await audio.read()
            tmp.write(content)
            tmp.flush()

            # Transcribe
            result = transcribe(tmp_path)

            return JSONResponse(content=result)

        except FileNotFoundError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Transcription failed: {e}")
        finally:
            # Clean up temp file
            tmp_path.unlink(missing_ok=True)
