FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for Docker layer caching
COPY requirements.txt .

# Install CPU-only PyTorch first to reduce Docker build size from ~2.5GB to ~200MB
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r requirements.txt

# Pre-download the pinned multilingual encoder into the image (no runtime download).
ARG EMBEDDING_MODEL_NAME=intfloat/multilingual-e5-small
ARG EMBEDDING_MODEL_REVISION=614241f622f53c4eeff9890bdc4f31cfecc418b3
RUN python -c "import sys; from sentence_transformers import SentenceTransformer; SentenceTransformer(sys.argv[1], revision=sys.argv[2], device='cpu')" "$EMBEDDING_MODEL_NAME" "$EMBEDDING_MODEL_REVISION"

# Copy application code
COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

