"""FastAPI server for voice transcription."""

import logging
import tempfile
from contextvars import ContextVar
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from transcriber import transcribe

# Per-request log capture
request_logs: ContextVar[list[str]] = ContextVar("request_logs", default=[])


class RequestLogHandler(logging.Handler):
    """Handler that captures logs to the current request's log list."""

    def emit(self, record):
        try:
            logs = request_logs.get()
            logs.append(self.format(record))
        except LookupError:
            pass  # No request context


# Configure logging
logger = logging.getLogger("vox")
logger.setLevel(logging.DEBUG)
handler = RequestLogHandler()
handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
logger.addHandler(handler)

# Also log to console
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
logger.addHandler(console_handler)


# 25 MB limit (~5 min of 48kHz stereo audio)
MAX_UPLOAD_SIZE = 25 * 1024 * 1024

app = FastAPI(
    title="Vox",
    description="Voice transcription service using whisper.cpp with Vulkan GPU acceleration",
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
async def transcribe_audio(
    audio: UploadFile = File(...),
    debug: bool = Query(False, description="Include debug logs in response"),
):
    """
    Transcribe uploaded audio file.

    Accepts audio file (WAV preferred, 16kHz mono recommended).
    Returns transcribed text with detected language.
    Add ?debug=true to include processing logs in response.
    """
    # Initialize per-request log capture
    logs = []
    request_logs.set(logs)

    # Validate file
    if not audio.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    logger.info(f"Received file: {audio.filename}")

    # Save uploaded file to temp location
    suffix = Path(audio.filename).suffix or ".wav"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = Path(tmp.name)
        try:
            # Write uploaded content
            content = await audio.read()
            tmp.write(content)
            tmp.flush()
            logger.info(f"Saved {len(content)} bytes to {tmp_path}")

            # Transcribe
            result = transcribe(tmp_path, logger=logger)

            if debug:
                result["logs"] = logs

            return JSONResponse(content=result)

        except FileNotFoundError as e:
            logger.error(f"File not found: {e}")
            if debug:
                return JSONResponse(status_code=400, content={"detail": str(e), "logs": logs})
            raise HTTPException(status_code=400, detail=str(e))
        except RuntimeError as e:
            logger.error(f"Runtime error: {e}")
            if debug:
                return JSONResponse(status_code=500, content={"detail": str(e), "logs": logs})
            raise HTTPException(status_code=500, detail=str(e))
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            if debug:
                return JSONResponse(status_code=500, content={"detail": f"Transcription failed: {e}", "logs": logs})
            raise HTTPException(status_code=500, detail=f"Transcription failed: {e}")
        finally:
            # Clean up temp file
            tmp_path.unlink(missing_ok=True)
            logger.debug(f"Cleaned up {tmp_path}")
