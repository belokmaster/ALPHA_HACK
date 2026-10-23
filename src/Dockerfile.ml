# Build the existing foundation image first: docker compose build app
FROM alfa-foundation:local
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr=5.3.0-2 tesseract-ocr-eng=1:4.1.0-2 tesseract-ocr-rus=1:4.1.0-2 \
    && rm -rf /var/lib/apt/lists/*
RUN uv sync --frozen --extra ml
ENV OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 TOKENIZERS_PARALLELISM=false HF_HUB_OFFLINE=1
USER app
