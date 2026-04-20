FROM python:3.11-slim

WORKDIR /app

# System deps required by torch, surya, and pypdfium2
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    libglib2.0-0 \
    libgl1 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    && rm -rf /var/lib/apt/lists/*

# Install PyTorch CPU-only first (saves ~2 GB vs the default CUDA wheel)
RUN pip install --no-cache-dir \
    torch==2.7.1 torchvision==0.22.1 \
    --index-url https://download.pytorch.org/whl/cpu

# Install the rest of the dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY main.py .
COPY src/ ./src/

# Surya model cache directory — mount a volume here to avoid re-downloading
# models on every container restart.
ENV MODEL_CACHE_DIR=/app/.cache/datalab/models
RUN mkdir -p /app/.cache/datalab/models

# Use CPU on Linux/Docker; override with TORCH_DEVICE=cuda if a GPU is available
ENV TORCH_DEVICE=cpu
ENV RECOGNITION_BATCH_SIZE=8
ENV DETECTOR_BATCH_SIZE=2

# Surya models download automatically on first request into MODEL_CACHE_DIR.
# Mount that directory as a Docker volume to persist them across restarts.

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
