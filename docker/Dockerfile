# Vox - Voice Transcription Server
# whisper.cpp with Vulkan GPU acceleration + FastAPI

FROM ubuntu:26.04

ENV DEBIAN_FRONTEND=noninteractive

# Install build dependencies and Vulkan
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    git \
    curl \
    # Vulkan support
    libvulkan-dev \
    vulkan-tools \
    mesa-vulkan-drivers \
    glslang-tools \
    glslc \
    spirv-headers \
    spirv-tools \
    # Audio conversion
    ffmpeg \
    # Python
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Build whisper.cpp with Vulkan
WORKDIR /opt
RUN git clone --branch v1.9.3 --depth 1 https://github.com/ggerganov/whisper.cpp.git && \
    cd whisper.cpp && \
    cmake -B build -DGGML_VULKAN=1 && \
    cmake --build build -j$(nproc)

# Download whisper models (small for speed, medium for accuracy, small.en for English-only, large-v3-turbo for quality)
RUN cd /opt/whisper.cpp && \
    ./models/download-ggml-model.sh small && \
    ./models/download-ggml-model.sh small.en && \
    ./models/download-ggml-model.sh medium && \
    ./models/download-ggml-model.sh large-v3-turbo

# Quantize large-v3-turbo to q8_0. On the bandwidth-bound Radeon 890M iGPU
# (shared LPDDR5) this runs ~22% faster than f16 with no measurable quality
# loss, and the newer RADV (Ubuntu 26.04) handles the INT8 dot-product path
# cleanly. The f16 model stays available for maximum-fidelity jobs.
RUN cd /opt/whisper.cpp && \
    ./build/bin/whisper-quantize \
        models/ggml-large-v3-turbo.bin \
        models/ggml-large-v3-turbo-q8_0.bin q8_0

# Parakeet TDT 0.6B v3 (NVIDIA, multilingual) as an opt-in model via
# `?model=parakeet-tdt-0.6b-v3-q8_0`. Official ggml-org conversion; runs through
# parakeet-cli (built above). ~2x faster than large-v3-turbo-q8_0 on the 890M.
RUN curl -fL -o /opt/whisper.cpp/models/ggml-parakeet-tdt-0.6b-v3-q8_0.bin \
    https://huggingface.co/ggml-org/parakeet-GGUF/resolve/main/ggml-parakeet-tdt-0.6b-v3-q8_0.bin

# Set up Python environment
WORKDIR /app
RUN python3 -m venv /app/venv
ENV PATH="/app/venv/bin:$PATH"

# Install Python dependencies
COPY server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy server code
COPY server/ .

# Note: Running as root for GPU access in Podman rootless mode
# Podman rootless already provides isolation via user namespaces

# Environment variables for whisper.cpp
ENV WHISPER_CLI=/opt/whisper.cpp/build/bin/whisper-cli
ENV PARAKEET_CLI=/opt/whisper.cpp/build/bin/parakeet-cli
ENV WHISPER_MODEL_DIR=/opt/whisper.cpp/models
ENV WHISPER_DEFAULT_MODEL=large-v3-turbo-q8_0

EXPOSE 5000

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "5000"]
