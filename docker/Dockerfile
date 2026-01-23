# Voice Transcriber Server
# whisper.cpp with Vulkan GPU acceleration + FastAPI

FROM ubuntu:24.04

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
    # Audio conversion
    ffmpeg \
    # Python
    python3 \
    python3-pip \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

# Build whisper.cpp with Vulkan
WORKDIR /opt
RUN git clone https://github.com/ggerganov/whisper.cpp.git && \
    cd whisper.cpp && \
    cmake -B build -DGGML_VULKAN=1 && \
    cmake --build build -j$(nproc)

# Download whisper model (small multilingual)
RUN cd /opt/whisper.cpp && \
    ./models/download-ggml-model.sh small

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
ENV WHISPER_MODEL=/opt/whisper.cpp/models/ggml-small.bin

EXPOSE 5000

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "5000"]
