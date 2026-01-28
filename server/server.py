"""FastAPI server for voice transcription."""

import logging
import tempfile
from contextvars import ContextVar
from pathlib import Path

from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from transcriber import transcribe, warmup, list_models, AVAILABLE_MODELS, WHISPER_DEFAULT_MODEL
from watchdog import WatchdogService
from watchdog_config import WatchdogConfig
from stats import transcriber_stats

# Global watchdog instance for test-alert endpoint
_watchdog: WatchdogService | None = None

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
logger = logging.getLogger("voxbox")
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle startup and shutdown events."""
    # Startup: warm up the model
    warmup_logger = logging.getLogger("voxbox.warmup")
    warmup_logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    warmup_logger.addHandler(handler)

    result = warmup(logger=warmup_logger)
    if result["success"]:
        warmup_logger.info(f"Server ready (model loaded in {result['duration_ms']}ms)")
    else:
        warmup_logger.warning(f"Model warm-up failed: {result.get('error', 'unknown error')}")

    # Start watchdog service
    global _watchdog
    watchdog_config = WatchdogConfig.from_env()
    _watchdog = WatchdogService(watchdog_config)
    await _watchdog.start()

    yield

    # Shutdown: stop watchdog
    await _watchdog.stop()
    _watchdog = None


app = FastAPI(
    title="Voxbox",
    description="Voice transcription service using whisper.cpp with Vulkan GPU acceleration",
    version="1.0.0",
    lifespan=lifespan,
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


@app.get("/stats")
async def stats():
    """Get transcriber statistics."""
    return transcriber_stats.get_stats()


@app.post("/test-alert")
async def test_alert(message: str = Query("Test alert from voxbox", description="Custom message for the test alert")):
    """Send a test alert to verify N8N webhook connectivity."""
    if _watchdog is None:
        raise HTTPException(status_code=503, detail="Watchdog service not initialized")

    result = await _watchdog.send_test_alert(message)

    if not result["success"]:
        raise HTTPException(status_code=400, detail=result["error"])

    return result


@app.get("/models")
async def get_models():
    """List available Whisper models."""
    models = list_models()
    return {
        "default": WHISPER_DEFAULT_MODEL,
        "available": list(AVAILABLE_MODELS),
        "models": {name: {"size_bytes": size} for name, size in models.items()},
    }


@app.post("/transcribe")
async def transcribe_audio(
    audio: UploadFile = File(...),
    debug: bool = Query(False, description="Include debug logs in response"),
    head: float | None = Query(None, gt=0, description="Transcribe only the first N seconds"),
    tail: float | None = Query(None, gt=0, description="Transcribe only the last N seconds"),
    model: str | None = Query(None, description="Whisper model to use (small, medium). Default from server config."),
):
    """
    Transcribe uploaded audio file.

    Accepts audio file (WAV preferred, 16kHz mono recommended).
    Returns transcribed text with detected language.

    Options:
    - ?debug=true - Include processing logs in response
    - ?head=10 - Transcribe only the first 10 seconds
    - ?tail=10 - Transcribe only the last 10 seconds
    - ?model=small - Use specific model (small, medium)
    """
    # Initialize per-request log capture
    logs = []
    request_logs.set(logs)

    # Validate mutually exclusive parameters
    if head is not None and tail is not None:
        raise HTTPException(status_code=400, detail="Cannot specify both 'head' and 'tail'")

    # Validate model
    if model is not None and model.lower() not in AVAILABLE_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{model}' not available. Choose from: {', '.join(sorted(AVAILABLE_MODELS))}"
        )

    # Validate file
    if not audio.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    logger.info(f"Received file: {audio.filename}")
    if model is not None:
        logger.info(f"Model override: {model}")
    if head is not None:
        logger.info(f"Segment: head={head}s")
    elif tail is not None:
        logger.info(f"Segment: tail={tail}s")

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

            # Transcribe with stats tracking
            transcriber_stats.start_transcription()
            try:
                result = transcribe(tmp_path, logger=logger, head=head, tail=tail, model=model)
                transcriber_stats.end_transcription(result["duration_ms"], success=True)
            except Exception:
                transcriber_stats.end_transcription(0, success=False)
                raise

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
